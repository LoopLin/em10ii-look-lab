#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_zonal.py — 读一张 B02 色相靶照片，反推「12 区 ↔ 色相带」的映射。

这个脚本只做三件事：
  1. 在照片里找到靶图，采样 32 个色块 + 3 个灰块；
  2. 给每个色块算 Cb/Cr（以及 Lab 的 C*/h），与「中性包」的参考照相减，
     输出 32 个块的 Δ（这就是「哪些块动了、动了多少」）；
  3. 把每列色相（45° 间隔）上的变化量当成 8 个观测，去套三族「区号↔段号」
     假说 × 12 种旋转 × 360 种相位，看哪一族能把它解释成一条
     「变化量 ∝ 表值」的直线。

B35 包喂进 12 区表的编码（见 tools/build_beta_zonalmap.py）：
    第 c 段的值 = 0x3F00 - drop(c)*0x200,
    drop = [6, 9, 4, 2, 8, 0, 3, 10, 7, 1, 5, 11]
它是一条**排列码**而不是阶梯：照片上 8 个柱子的高低次序本身就是一个 8 长排列，
与 72 个候选预测（3 族 × 12 旋转 × 相位类）逐一比对，只有真映射能拟合得动。

用法：
    python tools/read_zonal.py PHOTO.JPG                     # 以靶图 PNG 为基准
    python tools/read_zonal.py PHOTO.JPG --ref NEUTRAL.JPG   # 以中性包实拍为基准（推荐）
    python tools/read_zonal.py PHOTO.JPG --json out.json --csv out.csv
    python tools/read_zonal.py --selftest                    # 合成图自检（不需要相机）

判读口径：R² 最高的那一族 + 那一对 (α, δ) 就是映射；第二名的 R² 与它差多少
决定这次实验「定案 / 待定」。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

try:
    from PIL import Image
except ImportError:                                    # pragma: no cover
    sys.exit('需要 Pillow: python -m pip install pillow')

# ---------------------------------------------------------------- 靶图几何
IMG_W, IMG_H = 1920, 1080
LEFT, TOP, CELL, GAP = 60, 130, 200, 12
PITCH = CELL + GAP                                    # 212
COLS, ROWS = 8, 4
GRAY_Y, GRAY_H = 998, 120
SAT = [0.25, 0.50, 0.75, 1.00]                        # 行 0..3
HUE_NOMINAL = [45.0 * k for k in range(8)]            # 列 0..7：R000..Y315

# 靶图 PNG 的 8 根柱子在 BT.601 空间里的色相 = 标称色相 + 110.3°（实测，四行一致）。
# 只用来自检合成照片；判读真照片时一律用测出来的色相。
HUE_OFFSET = 110.3
GRAY_V = [255, 128, 32]

DEFAULT_REF_CANDIDATES = [
    os.path.join('assets', 'B02_chroma_target.png'),
    r'<HOME>/Desktop/B02_chroma_target.png',
]


def resolve_ref(path=None):
    if path:
        if not os.path.exists(path):
            raise SystemExit('找不到基准照片：%s' % path)
        return path
    for c in DEFAULT_REF_CANDIDATES:
        if os.path.exists(c):
            return c
    raise SystemExit('找不到靶图基准；用 --ref 指定一张')


def _load(path):
    im = Image.open(path)
    if im.mode != 'RGB':
        im = im.convert('RGB')
    return np.asarray(im, dtype=np.float32)


# ---------------------------------------------------------------- 实验编码
STEP = 0x200
# 段 c 的降档数。排列码（不是阶梯）：秩等价 0/72、线性 |r| 最大 0.8018，
# 见 tools/build_beta_zonalmap.py 文件头。改编码必须两边一起改。
DROPS = [6, 9, 4, 2, 8, 0, 3, 10, 7, 1, 5, 11]
NEUTRAL = 0x3F00
ZONE_DEG = 360.0 / 12.0
# 判据：真值 R² 门槛。合成自检里最差的两张（块内不均 16.5，重采样伪影）是
# 0.884 / 0.891，族仍判对、领先 0.29+；错误候选在本编码下最高只能到 R²≈0.64。
R2_GATE = 0.85


def code_value(c: int) -> int:
    return NEUTRAL - DROPS[c] * STEP


# 三族「区号 → 段号」假说（12 个区，按区号 0..11 列出它拿到哪一段的编码）
FAMILIES = {
    'H1_contiguous': [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    'H2_interleave': [0, 2, 4, 6, 8, 10, 1, 3, 5, 7, 9, 11],
    'H3_step2':      [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5],
}
FAMILY_NOTE = {
    'H1_contiguous': '一块管两区但前后相连：8 项 = 30°，12 段首尾顺接（chunk c → 区 c）',
    'H2_interleave': '一块的 16 项 = 区 k 的 8 项 + 区 k+6 的 8 项（交错排法）',
    'H3_step2':      '6 块是 6 个维度/参数、项号才是区：每 2 个区共用一档',
}


# ---------------------------------------------------------------- 颜色数学
def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_lab(rgb: np.ndarray) -> np.ndarray:
    """rgb: (...,3) linear 0..1 → Lab (D65)。"""
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = rgb @ m.T
    wp = np.array([0.95047, 1.0, 1.08883])
    t = xyz / wp
    d = 6.0 / 29.0
    f = np.where(t > d ** 3, np.cbrt(t), t / (3 * d * d) + 4.0 / 29.0)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], -1)


def ycbcr(rgb8: np.ndarray) -> np.ndarray:
    """ITU-R BT.601 的 JPEG 变换（输入 sRGB 编码值 0..255），返回 Y,Cb,Cr。"""
    r, g, b = rgb8[..., 0], rgb8[..., 1], rgb8[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return np.stack([y, cb, cr], -1)


def wb_gains(rgb8: np.ndarray) -> np.ndarray:
    """用灰块估白平衡增益（灰块必须都像灰）。输入 3×3 的灰块均值。"""
    gm = rgb8.mean(0)
    if gm.min() <= 4:
        return np.ones(3)
    g = gm.mean() / np.maximum(gm, 1e-6)
    return g / g.max()


# ---------------------------------------------------------------- 定位与采样
def _mask(img: np.ndarray) -> np.ndarray:
    mx = img.max(2)
    mn = img.min(2)
    return (mx - mn > 20) & (mx > 30)


SPAN_W = COLS * CELL + (COLS - 1) * GAP
SPAN_H = ROWS * CELL + (ROWS - 1) * GAP


def circ_mean(a):
    a = np.asarray(a, dtype=np.float64)
    return math.degrees(math.atan2(np.sin(np.radians(a)).mean(),
                                   np.cos(np.radians(a)).mean())) % 360.0


def _erode(m, r=3):
    """十字形腐蚀：去掉色块边缘那一圈被缩放/旋转糊过的像素。

    边缘像素混进黑缝后 Y 掉一半、色度还在，Sy = 色度/亮度 会虚高一倍；
    不过滤的话「同一条柱子」的 Sy 中位数会被整列抬高（实测 +36%），
    而这个抬高量又跟该列的黑缝占比有关，等于给每列加了一个假的增益。
    """
    o = m.copy()
    for dy in (-r, r):
        o &= np.roll(m, dy, axis=0)
    for dx in (-r, r):
        o &= np.roll(m, dx, axis=1)
    o[:r, :] = False
    o[-r:, :] = False
    o[:, :r] = False
    o[:, -r:] = False
    return o


def _hue_centers(hy, k=8, iters=40):
    """把一组色相聚成 k 个圆上簇，返回 (中心(升序), 每元素簇号)。

    用「色相 = 列身份」而不是几何位置：靶图 8 根柱子色相相差 45°，
    缩图/旋转/透视都不会改变一个像素属于哪一列，而几何定位要靠 12 项
    黑缝（只占节距的 5.6%，投影里几乎看不见，梳子拟合因此提不准齿距）。
    """
    hy = np.asarray(hy, dtype=np.float64) % 360.0
    if hy.size < k * 8:
        k = max(1, hy.size // 8)
    c = np.quantile(hy, np.linspace(0.5 / k, 1 - 0.5 / k, k))
    for _ in range(iters):
        d = np.abs(((hy[:, None] - c[None, :] + 180.0) % 360.0) - 180.0)
        a = d.argmin(1)
        new = c.copy()
        for i in range(k):
            m = a == i
            if m.sum() > 0:
                new[i] = circ_mean(hy[m])
        new = np.sort(new % 360.0)
        if np.abs(((new - c + 180.0) % 360.0) - 180.0).max() < 0.01:
            c = new
            break
        c = new
    d = np.abs(((hy[:, None] - c[None, :] + 180.0) % 360.0) - 180.0)
    return c, d.argmin(1)


def measure_cols(img, label='', dscale=1.0):
    """不靠几何定位：按色相把彩色像素分成 8 列，每列再按 y 等分 4 行。

    返回 {'centers'(8,), 'sy'(8,), 'sy_rows'(8,4), 'counts'(8,),
          'hue_spread'(8,), 'diag'}。
    """
    a = np.asarray(img, dtype=np.float64)
    if a.ndim == 2:
        a = np.stack([a, a, a], -1)
    if a.shape[0] > 1600:
        sc = 1200.0 / a.shape[0]
        a = np.asarray(Image.fromarray(a.clip(0, 255).astype(np.uint8)).resize(
            (int(round(a.shape[1] * sc)), int(round(a.shape[0] * sc))),
            Image.BILINEAR), dtype=np.float64)
        dscale *= sc
    mask = _mask(a)
    core = _erode(mask, 3)
    if core.sum() < 2000:
        core = mask
    ys, xs = np.nonzero(core)
    if len(ys) < 2000:
        raise SystemExit('%s: 只找到 %d 个彩色像素，检查曝光/白平衡' % (label, len(ys)))
    rgb = a[ys, xs] / 255.0
    y = 0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]
    cb = -0.168736 * rgb[:, 0] - 0.331264 * rgb[:, 1] + 0.5 * rgb[:, 2]
    cr = 0.5 * rgb[:, 0] - 0.418688 * rgb[:, 1] - 0.081312 * rgb[:, 2]
    hy = np.degrees(np.arctan2(cr, cb)) % 360.0
    sy = np.hypot(cb, cr) / np.maximum(y, 1e-6)
    # 聚类只在抽样子集上跑（全分辨率 1.3M 像素 × 40 轮要 35 s），
    # 定好中心后再对全部像素做一次最近簇分配。
    step = max(1, len(hy) // 60000)
    cen, _ = _hue_centers(hy[::step], 8)
    lab = np.abs(((hy[:, None] - cen[None, :] + 180.0) % 360.0) - 180.0).argmin(1)
    order = np.argsort(cen)
    sy_col = np.full(8, np.nan)
    sy_rows = np.full((8, 4), np.nan)
    counts = np.zeros(8, dtype=int)
    spread = np.full(8, np.nan)
    for rank, ci in enumerate(order):
        m = lab == ci
        counts[rank] = int(m.sum())
        if counts[rank] < 200:
            continue
        sy_col[rank] = float(np.median(sy[m]))
        spread[rank] = float(np.percentile(
            np.abs(((hy[m] - cen[ci] + 180) % 360) - 180), 90))
        yy = ys[m]
        for r in range(4):
            lo, hi = np.percentile(yy, [25 * r, 25 * (r + 1)])
            sel = (yy >= lo) & (yy <= hi)
            if sel.sum() > 20:
                sy_rows[rank, r] = float(np.median(sy[m][sel]))
    diag = {'dscale': dscale, 'colorful_frac': float(mask.mean()),
            'core_frac': float(core.sum()) / max(float(mask.sum()), 1.0),
            'px': int(len(ys)), 'counts': counts.tolist(),
            'hue_spread': np.round(spread, 1).tolist(),
            'hue_diffs': np.round(np.diff(np.r_[cen[order], cen[order][0] + 360]), 1).tolist()}
    return {'centers': cen[order], 'sy': sy_col, 'sy_rows': sy_rows,
            'counts': counts, 'diag': diag}


def match_columns(ref, out):
    """按色相把两张照片的 8 列对上（返回 ref_col -> out_col 的索引）。"""
    m = []
    for i, h in enumerate(ref['centers']):
        d = np.abs(((out['centers'] - h + 180) % 360) - 180)
        m.append(int(np.argmin(d)))
    return m


def hue_diff(a, b):
    d = (a - b + 180.0) % 360.0 - 180.0
    return d


# ---------------------------------------------------------------- 假说拟合
def fit_family(resp: np.ndarray, hues: np.ndarray, fam_levels12, alpha_deg, rot):
    """把 8 个色相观测套到一条「响应 ∝ 表值」的直线上，返回 (R2, b, a, levels)。"""
    zone = np.floor(((hues - alpha_deg) % 360.0) / ZONE_DEG).astype(int) % 12
    zrot = (zone + rot) % 12
    lv = np.array([DROPS[fam_levels12[z]] for z in zrot], float)
    if np.ptp(lv) == 0:
        return -1.0, 0.0, float(resp.mean()), lv
    x = lv - lv.mean()
    y = resp - resp.mean()
    sxx = float(x @ x)
    b = float(x @ y) / sxx
    a = float(resp.mean())
    pred = a + b * x
    ss_res = float(((resp - pred) ** 2).sum())
    ss_tot = float(((resp - resp.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return r2, b, a, lv


def decode(resp, hues):
    """穷举 (族 × 360 相位)，返回按 R² 排好的候选列表。

    只扫一个相位 γ = α - 30·rot：区号 zone = ⌊(hy-α)/30⌋%12，再看
    (zone+rot)%12 取哪一段，而 γ→γ+30 配 rot→rot+1 给出**完全相同**的预测。
    所以 (α, δ) 本来就只有「复合相位」可辨识，独立扫 360 个 γ 就够，
    省掉 12 倍冗余，也避免「同一个预测被当成两个候选」把领先量算成 0。
    """
    out = []
    for fam, seq in FAMILIES.items():
        for gi in range(360):
            r2, b, a, lv = fit_family(resp, hues, seq, float(gi), 0)
            out.append({'fam': fam, 'gamma': float(gi), 'r2': float(r2),
                        'b': float(b), 'a': float(a), 'lv': lv})
    out.sort(key=lambda d: -d['r2'])
    return out


def _fmt_hue(h):
    return '%6.2f' % h if np.isfinite(h) else '   -  '


def _fmt_hue(h):
    return '%6.2f' % h if np.isfinite(h) else '   -  '


def report_cols(ref, out, mm, verbose=True):
    """打印 8 根色相带的读数（每带 4 个饱和度行），返回 (hues_ref, resp, resp_rows)。"""
    hues = ref['centers']
    sy = out['sy'][mm] / np.maximum(ref['sy'], 1e-9)
    dlogS = np.log(np.maximum(sy, 1e-9))
    rows = np.log(np.maximum(out['sy_rows'][mm] / np.maximum(ref['sy_rows'], 1e-9), 1e-9))
    # 只用 S75/S100 两行（band 2,3）：S25 行的色度只有 ~0.14，
    # 色块里的白字抗锯齿像素在两张照片里「算不算彩色」会变，
    # 逐行中位数能被它带偏（实测 0.145→0.330），高饱和行稳得多。
    resp = np.array([np.nanmedian(rows[i][2:]) for i in range(8)])
    if verbose:
        print()
        print('  8 根色相带（按 YCbCr 色相升序；每带 4 行 = S25/S50/S75/S100）')
        print('  带  hy°      Sy_基准  Sy_新   ΔlogS | ΔlogS 按行(S25→S100)          行内跨行差')
        for i in range(8):
            print('  %d  %s  %6.3f  %6.3f  %+6.3f | %s   %+.3f'
                  % (i, _fmt_hue(hues[i]), ref['sy'][i], out['sy'][mm[i]],
                     dlogS[i],
                     ' '.join('%+6.3f' % v if np.isfinite(v) else '   -  ' for v in rows[i]),
                     np.nanmax(rows[i]) - np.nanmin(rows[i])))
        print('  提示：4 行数值接近 = 乘性色度增益；随饱和度变小 = 加性偏移；'
              '只动高饱和 = 阈值/钳位')
    return hues, resp, rows


def decode_report(resp, hues, verbose=True):
    cands = decode(resp, hues)
    top = cands[0]
    fams = ('H1_contiguous', 'H2_interleave', 'H3_step2')
    fam_best = {f: max((c for c in cands if c['fam'] == f), key=lambda c: c['r2'])
                for f in fams}
    order = sorted(fams, key=lambda f: -fam_best[f]['r2'])
    fam_top, fam_2nd = order[0], order[1]
    margin = fam_best[fam_top]['r2'] - fam_best[fam_2nd]['r2']
    # 相位不是一个点而是一个区间：与最优 R² 相差 <0.02 的 γ 都算「说得通」
    g_ok = sorted({c['gamma'] for c in cands
                   if c['fam'] == fam_top and c['r2'] >= fam_best[fam_top]['r2'] - 0.02})
    g_lo, g_hi = (g_ok[0], g_ok[-1]) if g_ok else (float('nan'), float('nan'))
    spread = float(np.ptp(resp))
    fam_top_best = fam_best[fam_top]
    if verbose:
        print()
        print('  ── 映射反推 ──')
        print('  观测 ΔlogS: ' + ' '.join('%+.3f' % v for v in resp)
              + '   跨度 %.3f' % spread)
        for f in fams:
            c = fam_best[f]
            print('  %-14s 最好 R²=%5.3f (γ=%5.1f°, 斜率 %+.5f/档) %s'
                  % (f, c['r2'], c['gamma'], c['b'], FAMILY_NOTE[f]))
        if spread < 0.02:
            print('  跨度 < 0.02：画面没有可测的色度变化 → 见文档「三种结果」第 3 种')
        print()
        print('  结论：%s  γ=%.1f°  R²=%.3f；次优族 %s 的 R²=%.3f → 领先 %.3f'
              % (fam_top, fam_top_best['gamma'], fam_top_best['r2'],
                 fam_2nd, fam_best[fam_2nd]['r2'], margin))
        print('  相位区间（R² 距最优 <0.02 的 γ）：%s..%s° '
              '—— γ 本身只能定到十几度，因为它就是「每根柱子落在哪个区」，'
              '而柱距 45°、区宽 30°，两者不同步' % (g_lo, g_hi))
        b = fam_top_best['b']
        print('  斜率 %+.5f /档（1 档 = 表值 0x200）→ 0x800 ≈ %+.1f%% 色度半径；'
              '一整块 16 项 = 6 档 ≈ %+.0f%%' % (b, 4 * b * 100.0, 24 * b * 100.0))
        zone = np.floor(((hues - fam_top_best['gamma']) % 360.0) / ZONE_DEG).astype(int) % 12
        seq = FAMILIES[fam_top]
        print('  8 根柱子的落区（用 γ=%.1f°）：' % fam_top_best['gamma'])
        for i in range(8):
            print('    col%d  hy=%6.1f°  → zone %2d  → 块 %2d  → 档 %2d'
                  % (i, hues[i], zone[i], seq[zone[i]], DROPS[seq[zone[i]]]))
    verdict = '定案' if (fam_top_best['r2'] >= R2_GATE and margin >= 0.15) else '待定'
    return {'verdict': verdict, 'r2': fam_top_best['r2'], 'r2_second': fam_best[fam_2nd]['r2'],
            'family': fam_top, 'family_second': fam_2nd, 'margin': margin,
            'gamma': fam_top_best['gamma'], 'phase_deg': fam_top_best['gamma'],
            'gamma_lo': g_lo, 'gamma_hi': g_hi,
            'slope_per_step': fam_top_best['b'], 'intercept': fam_top_best['a'],
            'spread': spread, 'fam_r2': {f: fam_best[f]['r2'] for f in fams},
            'cands': cands}


def run_pair(ref_img, out_img, verbose=True, label_ref='基准', label_out='新包'):
    if verbose:
        print('  === 基准 %s vs 新包 %s ===' % (label_ref, label_out))
    ref = measure_cols(ref_img, label_ref)
    out = measure_cols(out_img, label_out)
    if verbose:
        print('  彩色像素 %d / %d，色块核心占比 %.2f，列色相 90%% 离散 %s'
              % (ref['diag']['px'], out['diag']['px'], out['diag']['core_frac'],
                 out['diag']['hue_spread']))
        print('  列间距（应≈45°）: %s' % out['diag']['hue_diffs'])
    mm = match_columns(ref, out)
    if len(set(mm)) != 8:
        print('  ⚠ 两图的色相列对不上一一对应：%s' % mm)
    hues, resp, rows = report_cols(ref, out, mm, verbose=verbose)
    res = decode_report(resp, hues, verbose=verbose)
    res['mm'] = mm
    res['hues'] = hues
    res['resp'] = resp
    res['resp_rows'] = rows
    res['ref'] = ref
    res['out'] = out
    return res


def _synth(base_img, resp_per_column, noise=2.0, seed=0):
    """合成一张「像相机拍出来的」靶图照片：
    先在靶图坐标里按每列响应改色度，再缩放/旋转/贴到 4:3 暗底画布上，最后加噪。"""
    rng = np.random.default_rng(seed)
    a = base_img.astype(np.float32).copy()
    ycc = ycbcr(a)
    for k in range(COLS):
        x0 = LEFT + k * PITCH
        band = ycc[:, x0:x0 + CELL]
        m = (np.abs(band[..., 1] - 128) > 3) | (np.abs(band[..., 2] - 128) > 3)
        for ch in (1, 2):
            band[..., ch][m] = 128 + (band[..., ch][m] - 128) * (1.0 + resp_per_column[k])
        ycc[:, x0:x0 + CELL] = band
    y, cb, cr = ycc[..., 0], ycc[..., 1], ycc[..., 2]
    rgb = np.stack([y + 1.402 * (cr - 128),
                    y - 0.344136 * (cb - 128) - 0.714136 * (cr - 128),
                    y + 1.772 * (cb - 128)], -1)
    tgt = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    # 相机取景：4:3 画布，靶图占宽的 55%~75%，随机旋转 ±4°
    cw, chh = 2400, 1800
    frac = rng.uniform(0.55, 0.75)
    nw = int(tgt.width * frac)
    nh = int(tgt.height * frac)
    tgt = tgt.resize((nw, nh), Image.BILINEAR)
    ang = rng.uniform(-4.0, 4.0)
    tgt = tgt.rotate(ang, resample=Image.BILINEAR, expand=True, fillcolor=(14, 14, 14))
    canvas = Image.new('RGB', (cw, chh), (14, 14, 14))
    ox = int(rng.integers(40, max(41, cw - tgt.width - 40)))
    oy = int(rng.integers(40, max(41, chh - tgt.height - 40)))
    canvas.paste(tgt, (ox, oy))
    out = np.asarray(canvas, dtype=np.float32)
    out = out * rng.uniform(0.55, 0.85) + rng.normal(0, noise, out.shape)
    return np.clip(out, 0, 255).astype(np.float32)


def selftest(verbose=False):
    """合成一批「真值已知」的照片，检查整条链路能不能把真值认回来。"""
    from PIL import Image as _I
    base_path = resolve_ref(None)
    base = np.asarray(_I.open(base_path).convert('RGB'), dtype=np.float32)
    cases = []
    for fam in ('H1_contiguous', 'H2_interleave', 'H3_step2'):
        for rot in (0, 3, 7):
            cases.append((fam, rot, 12.0 * ((rot * 5 + 3) % 12)))
    ok = 0
    print('自检：%d 例（合成照片 → 全流程 → 认回真值）' % len(cases))
    print('%-14s %4s %7s | %-14s %13s %6s %6s %s'
          % ('真值族', '旋转', '相位', '认回族', '认回γ', 'R²', '领先', '判定'))
    for fam, rot, alpha in cases:
        seq = FAMILIES[fam]
        resp = []
        for k in range(COLS):
            hy = (45.0 * k + HUE_OFFSET) % 360.0
            z = int(np.floor(((hy - alpha) % 360.0) / ZONE_DEG)) % 12
            lv = DROPS[seq[(z + rot) % 12]]
            resp.append(-0.03 * lv)
        seed = (hash(fam) ^ (rot * 7919)) % 97
        img_ref = _synth(base, [0.0] * 8, seed=seed)      # 同机位、无色度变化的基准
        img = _synth(base, resp, seed=seed)
        try:
            res = run_pair(img_ref, img, verbose=False, label_ref='合成基准',
                           label_out='合成')
        except SystemExit as e:
            print('%-14s %4d %7.1f | 失败：%s' % (fam, rot, alpha, e))
            continue
        g_truth = (alpha - ZONE_DEG * rot) % 360.0
        g_err = abs(((res['gamma'] - g_truth + 180.0) % 360.0) - 180.0)
        lo, hi = res['gamma_lo'], res['gamma_hi']
        good = (res['family'] == fam and res['verdict'] == '定案' and g_err <= 15.0)
        res['gamma_err'] = g_err
        res['gamma_truth'] = g_truth
        ok += 1 if good else 0
        print('%-14s %4d %7.1f | %-14s %6.1f(±%4.1f) %6.3f %6.3f %s'
              % (fam, rot, alpha, res['family'], res['gamma'], res['gamma_err'],
                 res['r2'], res['margin'], 'PASS' if good else 'FAIL'))
    print('自检结果：%d/%d PASS' % (ok, len(cases)))
    return ok, len(cases)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description='读靶图照片，反推十张 HSL 表里「块/项」与 12 个色相区的映射。')
    ap.add_argument('photo', nargs='?', help='新包拍的照片（B35）')
    ap.add_argument('--ref', help='基准照片（中性包 / 原厂固件拍的同机位靶图）；'
                                  '缺省用靶图 PNG')
    ap.add_argument('--json', help='把结果写成 JSON')
    ap.add_argument('--csv', help='把 32 块读数写成 CSV')
    ap.add_argument('--selftest', action='store_true', help='跑合成自检')
    ap.add_argument('--hue-basis', choices=('measured', 'nominal'), default='measured')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        ok, n = selftest()
        return 0 if ok == n else 1
    if not a.photo:
        ap.error('要么给照片路径，要么 --selftest')
    ref_path = resolve_ref(a.ref)
    ref_img = _load(ref_path)
    out_img = _load(a.photo)
    print('基准：%s' % ref_path)
    print('新包：%s' % a.photo)
    if ref_path.lower().endswith('.png') and 'B02' in os.path.basename(ref_path):
        print('⚠ 基准是靶图 PNG（不是相机拍的）：它没有经过相机的色彩流水线，'
              '「相对变化」里会混进相机基线的偏差；最稳的比法是拿中性包 B28/B29 '
              '拍的同机位照片当基准。')
    res = run_pair(ref_img, out_img, verbose=not a.quiet)
    if a.csv:
        with open(a.csv, 'w', encoding='utf-8', newline='') as f:
            f.write('column,hue_hy,hue_hue_ref,sat,delta_logS,delta_hy,delta_L\n')
            for i in range(8):
                for r in range(4):
                    f.write('%d,%.2f,%.2f,%d,%.4f,%.4f,%.4f\n'
                            % (i, res['hues'][i], res['hues'][i], r,
                               res['resp_rows'][i, r] if np.isfinite(res['resp_rows'][i, r]) else float('nan'),
                               0.0, 0.0))
        print('  CSV → %s' % a.csv)
    if a.json:
        import json as _j
        dump = {k: v for k, v in res.items()
                if k not in ('ref', 'out', 'best', 'per_fam', 'resp_rows')}
        dump['hues'] = [float(x) for x in res['hues']]
        dump['resp'] = [float(x) for x in res['resp']]
        dump['resp_rows'] = [[None if not np.isfinite(v) else float(v) for v in row]
                             for row in res['resp_rows']]
        dump['hue_basis'] = a.hue_basis
        dump['ref'] = ref_path
        dump['photo'] = a.photo
        with open(a.json, 'w', encoding='utf-8') as f:
            _j.dump(dump, f, ensure_ascii=False, indent=1)
        print('  JSON → %s' % a.json)
    return 0 if res['verdict'] == '定案' else 2


if __name__ == '__main__':
    sys.exit(main())
