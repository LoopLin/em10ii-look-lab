# -*- coding: utf-8 -*-
"""rawpipe_check —— 用真实配对照片验证「ORF → 相机 JPG」复现精度。

回答四个问题（每个都有可复算的数字）：
  A. 索引刻度 P（= 255·s/x_white）到底是 230 还是 385？——逐组扫 P ∈ [180,420]，
     打印「亮度 RMS vs P」的曲线与每组的 argmin；再证明 P 与「Y 的单位」互为倒数，
     所以只有「Y 的形状口径」可辨识，绝对值不可辨识。
  B. Y 是什么？——四种口径（WB 有/无 × Rec.601/709）各拟合一遍，取能同时对上全部
     配对的那一个；另一种口径的残差一并报出。
  C. 精度：亮度 RMS / RGB RMS / ΔCb / ΔCr；同时对「逐像素」和「低通（4×4 池化）」
     两个尺度报数 —— 相机 JPG 做过锐化与局部对比，逐像素 RMS 里有相当一部分
     是 ORF 里根本没有的高频，不应该算在色调模型账上。
  D. 残差按亮度分布：把像素按 JPG 亮度分箱，报每箱的均值偏差与 RMS，看错在哪里。

方法要点：
  * 解码与 `tools/rawpipe.py` 同一份代码（不重复实现）。
  * 配对帧按步长 STEP 抽样（不做平均，保留高频噪声），报告里写明分辨率。
  * P 的拟合用「按亮度分箱 + 组内方差」的精确分解（与逐像素口径等价，快两个数量级）。
  * CCM 用模式搜索（无 scipy）在低通图上最小化 RGB RMS，最后行归一化以保中性。
  * 「自由曲线下限」= 分位分箱中位数 + 单调插值，在同一批像素上算 RMS：
    这代表「任何一条单值 tone 曲线」的上限精度，用来判断表本身是不是瓶颈。

用法：
    python tools/rawpipe_check.py                     # 全流程
    python tools/rawpipe_check.py --groups 01,10      # 只跑这些组
    python tools/rawpipe_check.py --no-ccm            # 跳过 CCM 拟合
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import numpy as np

for _s in (sys.stdout, sys.stderr):   # 控制台可能是 GBK，强制 UTF-8 输出
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rawpipe as rp  # noqa: E402

STEP = 6                     # 抽样步长（6 ⇒ 768×576 = 442k 像素）
POOL = 4                     # 低通池化倍数（在抽样图上再池化 ⇒ 192×144）
DARK_P99 = 3.0               # 显示亮度 99 分位 < 3/255 视为暗帧
P_LO, P_HI = 180.0, 420.0
P_WIDE = (100.0, 1400.0)     # Y 口径对比时用宽网格（÷16383 口径下 P 要 ×4.27）

# 只挑「官方固件、tone/色度表未被改写」的组；05–09/12–25 是 S* 改表实验，
# 它们的 JPG 出自被改写的曲线，本链路不该去拟合（见 docs/color/raw-sim.md）。
GROUPS = [
    ('01_stock_contrast_series', 'refs/shoots/01_stock_contrast_series', 5),
    ('02_stock_hs_set1', 'refs/shoots/02_stock_hs_set1', 1),
    ('03_stock_hs_set2_plus1ev', 'refs/shoots/03_stock_hs_set2_plus1ev', 1),
    ('04_stock_5shot_contrast_hs', 'refs/shoots/04_stock_5shot_contrast_hs', 1),
    ('10_stock_ev_series', 'refs/shoots/10_stock_ev_series', 4),
    ('11_incamera_raw_develop', 'refs/shoots/11_incamera_raw_develop', 2),
    ('20_S49_gateonly', 'refs/shoots/20_S49_gateonly', 1),
    ('26_stock_vivid_baseline', 'refs/shoots/26_stock_vivid_baseline', 2),
    ('tmp_PB120056', '.tmp', 1),
]

# Y 口径候选：名字 -> (是否乘 WB, 亮度权重)
Y_VARIANTS = {
    'lin_nc601': (False, rp.LUMA_601),   # 线性亮度，不乘 WB，Rec.601
    'lin_wb601': (True, rp.LUMA_601),    # 线性亮度，乘 WB（本报告默认）
    'lin_wb709': (True, rp.LUMA_709),    # 线性亮度，乘 WB，Rec.709
    'lin_nc709': (False, rp.LUMA_709),
}


# ------------------------------------------------------------------ 取材

def pairs_in(directory, limit=None):
    out = []
    for orf in sorted(glob.glob(os.path.join(directory, '*.ORF'))):
        stem = orf[:-4]
        if os.path.exists(stem + '.JPG'):
            out.append(stem)
    return out[:limit] if limit else out


def pool_mean(a, f=POOL):
    """把 (H,W[,C]) 按 f×f 求均值池化。"""
    h, w = a.shape[0] // f * f, a.shape[1] // f * f
    a = a[:h, :w]
    if a.ndim == 2:
        return a.reshape(h // f, f, w // f, f).mean(axis=(1, 3))
    return a.reshape(h // f, f, w // f, f, a.shape[2]).mean(axis=(1, 3))


def load_pair(stem, step=STEP, wb='camera'):
    d = rp.load_orfs(stem + '.ORF', wb=wb)
    lin = rp.crop_to_jpg_frame(d['lin'], d['meta'])
    jpg = rp.load_reference_jpg(stem + '.JPG')
    if jpg.shape[:2] != lin.shape[:2]:
        raise RuntimeError('尺寸不一致 %s: %s vs %s' % (stem, lin.shape, jpg.shape))
    lin = lin[::step, ::step].astype(np.float32)
    jpg = jpg[::step, ::step].astype(np.float32)
    return {
        'stem': stem, 'lin': lin, 'jpg': jpg,
        'wb_gains': np.array(d['meta']['wb_gains'], dtype=np.float32),
        'lin_lp': pool_mean(lin), 'jpg_lp': pool_mean(jpg),
        'y': rp.luma(lin), 'yj': rp.luma(jpg),
        'meta': d['meta'], 'shape': tuple(lin.shape[:2]),
    }


def y_of(rec, wb=True, weights=rp.LUMA_601):
    rgb = rec['lin']
    if not wb:
        rgb = rgb / np.maximum(rec['wb_gains'], 1e-6)
    return rp.luma(rgb, weights)


# ------------------------------------------------------------------ 快速拟合

def hist_stats(y, yj, nb=1024):
    y = np.asarray(y, dtype=np.float64).ravel()
    yj = np.asarray(yj, dtype=np.float64).ravel()
    hi = float(y.max()) + 1e-9
    b = np.clip((y / hi * nb).astype(np.int32), 0, nb - 1)
    n = np.bincount(b, minlength=nb).astype(np.float64)
    s1 = np.bincount(b, weights=yj, minlength=nb)
    s2 = np.bincount(b, weights=yj * yj, minlength=nb)
    return n, s1, s2, (np.arange(nb) + 0.5) / nb * hi, float(y.size)


def rms_from_hist(h, table, P):
    n, s1, s2, cen, N = h
    pred = rp.tone_display_case(np.clip(cen * P, 0, 255), table)
    mean = np.where(n > 0, s1 / np.maximum(n, 1), 0.0)
    within = np.sum(s2 - np.where(n > 0, s1 ** 2 / np.maximum(n, 1), 0.0))
    return float(np.sqrt(max(within + np.sum(n * (pred - mean) ** 2), 0.0) / N))


def sweep_P(h, table, lo=P_LO, hi=P_HI, step=1.0):
    Ps = np.arange(lo, hi + 1e-9, step)
    rs = np.array([rms_from_hist(h, table, P) for P in Ps])
    i = int(np.argmin(rs))
    return Ps, rs, float(Ps[i]), float(rs[i])


def fit_scale(h, table, p_grid=None):
    if p_grid is None:
        p_grid = np.arange(P_WIDE[0], P_WIDE[1], 1.0)
    rs = np.array([rms_from_hist(h, table, P) for P in p_grid])
    i = int(np.argmin(rs))
    return {'rms': float(rs[i]), 'P': float(p_grid[i])}


def scan_tables(h, tables, lo=P_LO, hi=P_HI, pstep=2.0, refine=1.0):
    """对每张表各扫一遍 P，返回 (k, P, RMS)。用来看「换一张表能不能到下界」——
    相机 54 张表就是 54 种出厂曲线，配对帧若出自别的曲线，固定 t3 的残差里
    有一块是「表号错」而不是「模型错」。"""
    best = None
    for k in sorted(tables):
        t = tables[k]
        Ps = np.arange(lo, hi + 1e-9, pstep)
        rs = np.array([rms_from_hist(h, t, P) for P in Ps])
        i = int(np.argmin(rs))
        if best is None or rs[i] < best[0]:
            best = (float(rs[i]), k, float(Ps[i]))
    # 在最优表附近细化
    t = tables[best[1]]
    Ps = np.arange(max(lo, best[2] - pstep), min(hi, best[2] + pstep) + 1e-9, refine)
    rs = np.array([rms_from_hist(h, t, P) for P in Ps])
    i = int(np.argmin(rs))
    return {'k': int(best[1]), 'P': float(Ps[i]), 'rms': float(rs[i])}


def spark(vals, width=48, chars=' .:-=+*#%@'):
    """把一串数压成一行 ASCII 曲线（小=低=好，所以反向映射）。"""
    v = np.asarray(vals, dtype=float)
    idx = np.linspace(0, len(v) - 1, min(width, len(v))).astype(int)
    v = v[idx]
    lo, hi = float(v.min()), float(v.max())
    if hi - lo < 1e-9:
        return chars[-1] * len(v)
    t = (v - lo) / (hi - lo)
    return ''.join(chars[int(round((1 - x) * (len(chars) - 1)))] for x in t)


def block_median_rms(a, b, bs=16):
    """按 bs×bs 块取中位数后比较 —— 接近「色卡/灰阶patch」这种评估方式，
    e1h 一类指标就是在 patch 中位数上算的，逐像素 RMS 里有纹理与锐化差异。"""
    h, w = a.shape[0] // bs * bs, a.shape[1] // bs * bs
    ya = rp.luma(a[:h, :w]).reshape(h // bs, bs, w // bs, bs).transpose(0, 2, 1, 3)
    yb = rp.luma(b[:h, :w]).reshape(h // bs, bs, w // bs, bs).transpose(0, 2, 1, 3)
    ma = np.median(ya.reshape(-1, bs * bs), axis=1)
    mb = np.median(yb.reshape(-1, bs * bs), axis=1)
    return float(np.sqrt(((ma - mb) ** 2).mean()))


def free_curve_floor(rec, nbins=48):
    y, yj = rec['y'].ravel(), rec['yj'].ravel()
    qs = np.quantile(y, np.linspace(0, 1, nbins + 1))
    qs[-1] += 1e-9
    idx = np.clip(np.searchsorted(qs, y) - 1, 0, nbins - 1)
    xs, ys = [], []
    for b in range(nbins):
        m = idx == b
        if m.sum() > 30:
            xs.append(np.median(y[m]))
            ys.append(np.median(yj[m]))
    xs, ys = np.array(xs), np.array(ys)
    ys = np.maximum.accumulate(ys)
    return float(np.sqrt(((np.interp(y, xs, ys) - yj) ** 2).mean()))


# ------------------------------------------------------------------ 误差口径

def metrics(rec, cfg, lowpass=False):
    # 注意：低通口径必须在**渲染之后**池化。tone 表是非线性的，
    # 先池化线性值再过表会把亮暗边平均成中灰、再被曲线压偏（实测能让
    # RGB RMS 从 6.8 虚增到 17.5），所以这里统一「全分辨率渲染 → 池化输出」。
    out = rp.render(rec['lin'], cfg).astype(np.float32)
    if lowpass:
        out = pool_mean(out)
        jpg = rec['jpg_lp']
    else:
        jpg = rec['jpg']
    ys, yj = rp.luma(out), rp.luma(jpg)
    _, cbs, crs = rp.rgb_to_ycbcr(out)
    _, cbj, crj = rp.rgb_to_ycbcr(jpg)
    return {
        'lum_rms': float(np.sqrt(((ys - yj) ** 2).mean())),
        'lum_mae': float(np.abs(ys - yj).mean()),
        'lum_bias': float((ys - yj).mean()),
        'rgb_rms': float(np.sqrt(((out - jpg) ** 2).mean())),
        'dcb': float((cbs - cbj).mean()), 'dcr': float((crs - crj).mean()),
        'dcb_abs': float(np.abs(cbs - cbj).mean()),
        'dcr_abs': float(np.abs(crs - crj).mean()),
    }


def residual_by_luma(rec, cfg, nbins=8):
    out = rp.render(rec['lin'], cfg).astype(np.float32)
    e = rp.luma(out) - rec['yj']
    yj = rec['yj'].ravel()
    e = e.ravel()
    edges = np.linspace(0, 256, nbins + 1)
    b = np.clip(np.digitize(yj, edges) - 1, 0, nbins - 1)
    rows = []
    for i in range(nbins):
        m = b == i
        if m.sum() < 200:
            continue
        rows.append({'lo': float(edges[i]), 'hi': float(edges[i + 1]),
                     'n': int(m.sum()), 'bias': float(e[m].mean()),
                     'rms': float(np.sqrt((e[m] ** 2).mean()))})
    return rows


# ------------------------------------------------------------------ CCM

def fit_ccm(items, base_cfg, step0=0.08, min_step=0.002, max_evals=240, log=None):
    """模式搜索最小化低通 RGB RMS；行归一化保中性。items: [(rec, P), ...]"""

    def obj(M, sat=1.0):
        cfg = dict(base_cfg, ccm=M, sat=sat)
        tot, n = 0.0, 0
        for rec, P in items:
            cfg['index_scale'] = P
            out = rp.render(rec['lin_lp'], cfg).astype(np.float32)
            d = out - rec['jpg_lp']
            tot += float((d * d).sum())
            n += int(d.size)
        return float(np.sqrt(tot / n))

    M = np.eye(3, dtype=np.float32)
    best, evals, st = obj(M), 1, step0
    while st > min_step and evals < max_evals:
        improved = False
        for i in range(9):
            for sgn in (1.0, -1.0):
                M2 = M.copy()
                M2[i // 3, i % 3] += sgn * st
                v = obj(M2)
                evals += 1
                if v < best - 1e-7:
                    best, M, improved = v, M2, True
        if not improved:
            st *= 0.5
    M = M / M.sum(axis=1, keepdims=True)
    best = obj(M)
    sats = np.arange(0.80, 2.801, 0.05)   # 2026-11-15：配相机矩阵时最优在 2.2，旧上界 1.6 会把它顶到边界
    vals = [obj(M, s) for s in sats]
    i = int(np.argmin(vals))
    if log:
        log('    CCM: evals=%d, 低通 rgb_rms %.2f → %.2f（sat=%.2f 时 %.2f）'
            % (evals, obj(np.eye(3)), best, sats[i], vals[i]))
    return {'ccm': M.astype(np.float64).tolist(), 'rgb_rms_lp_identity': obj(np.eye(3)),
            'rgb_rms_lp': best, 'sat': float(sats[i]),
            'rgb_rms_lp_best_sat': float(vals[i]), 'evals': evals}


def table_degeneracy(tables, ks=(0, 1, 2, 3, 5, 6, 7, 15, 23, 42, 43, 53), cgrid=None):
    """证明「表族是同一曲线在不同索引刻度上的副本」：
    对每一对 (a,b) 求 min_c RMS(t_a(i) - t_b(i·c))，看能否近乎重合。"""
    if cgrid is None:
        cgrid = np.arange(0.30, 2.501, 0.01)
    grid = np.arange(256.0)
    out = []
    for ia in range(len(ks)):
        for ib in range(ia + 1, len(ks)):
            a, b = tables[ks[ia]] / 1023.0 * 255.0, tables[ks[ib]] / 1023.0 * 255.0
            best = None
            for c in cgrid:
                bb = np.interp(np.clip(grid * c, 0, 255), grid, b)
                e = float(np.sqrt(((a - bb) ** 2).mean()))
                if best is None or e < best[0]:
                    best = (e, float(c))
            out.append({'a': ks[ia], 'b': ks[ib], 'rms': best[0], 'c': best[1]})
    out.sort(key=lambda r: r['rms'])
    return out


# ------------------------------------------------------------------ 主流程

def main(argv=None):
    ap = argparse.ArgumentParser(description='ORF→JPG 复现精度检查')
    ap.add_argument('--groups', default=None, help='只跑这些组（逗号分隔前缀）')
    ap.add_argument('--step', type=int, default=STEP)
    ap.add_argument('--wb', default='camera', choices=('camera', 'daylight', 'none'))
    ap.add_argument('--no-ccm', action='store_true')
    ap.add_argument('--outdir', default='.tmp/sim')
    ap.add_argument('--tone-k', type=int, default=rp.DEFAULT_TONE_K)
    a = ap.parse_args(argv)

    t0 = time.time()
    os.makedirs(a.outdir, exist_ok=True)
    want = None if not a.groups else tuple(x.strip() for x in a.groups.split(',') if x.strip())

    # ---------- 0. 选片
    recs, used_groups = [], []
    for name, d, lim in GROUPS:
        if want and not any(name.startswith(w) for w in want):
            continue
        if name == 'tmp_PB120056':
            stems = ['.tmp/PB120056'] if (os.path.exists('.tmp/PB120056.ORF')
                                         and os.path.exists('.tmp/PB120056.JPG')) else []
        else:
            stems = pairs_in(d, lim)
        if not stems:
            continue
        used_groups.append(name)
        for s in stems:
            rec = load_pair(s, step=a.step, wb=a.wb)
            rec['group'] = name
            rec['dark'] = float(np.percentile(rec['yj'], 99)) < DARK_P99
            recs.append(rec)
            print('  [load] %-4s %-10s p99=%6.2f %dx%d%s' % (
                name.split('_')[0], os.path.basename(s),
                float(np.percentile(rec['yj'], 99)), rec['shape'][1], rec['shape'][0],
                '  SKIP(暗帧)' if rec['dark'] else ''), flush=True)
    usable = [r for r in recs if not r['dark']]
    if len(usable) < 2:
        print('可用配对不足', file=sys.stderr)
        return 2
    print('配对 %d 张（跳过暗帧 %d 张），组数 %d，抽样步长 %d ⇒ 逐像素 %dx%d、'
          '低通 %dx%d，用时 %.1fs'
          % (len(usable), len(recs) - len(usable), len(used_groups), a.step,
             usable[0]['shape'][1], usable[0]['shape'][0],
             usable[0]['lin_lp'].shape[1], usable[0]['lin_lp'].shape[0],
             time.time() - t0), flush=True)

    tables = {k: rp.tone_table(k) for k in range(rp.TONE_COUNT)}
    doc_t = tables[a.tone_k]

    # ---------- A. Y 口径对比
    print('\n[A] Y 口径对比（各口径自己最优 P，宽网格）', flush=True)
    var_rows = []
    for vname, (usewb, wts) in Y_VARIANTS.items():
        rs, Ps = [], []
        for r in usable:
            h = hist_stats(y_of(r, usewb, wts), r['yj'])
            f = fit_scale(h, doc_t)
            rs.append(f['rms'])
            Ps.append(f['P'])
        var_rows.append({'variant': vname, 'mean_rms': float(np.mean(rs)),
                         'max_rms': float(np.max(rs)),
                         'P_median': float(np.median(Ps)),
                         'P_min': float(np.min(Ps)), 'P_max': float(np.max(Ps)),
                         'rms': [round(x, 2) for x in rs]})
        print('  %-10s 平均RMS %.2f（最差 %.2f）  P 中位 %.0f（%.0f–%.0f）'
              % (vname, np.mean(rs), np.max(rs), np.median(Ps), np.min(Ps), np.max(Ps)),
              flush=True)
    var_rows.sort(key=lambda r: r['mean_rms'])
    best_var = var_rows[0]['variant']
    usewb, wts = Y_VARIANTS[best_var]
    print('  ⇒ 采用 %s（同时对上全部配对的最优口径）' % best_var, flush=True)

    # ---------- B. 逐组 P 扫描
    print('\n[B] 逐组 P 扫描（tone k=%d，%s）' % (a.tone_k, best_var), flush=True)
    sweep_rows, curves = [], {}
    for r in usable:
        h = hist_stats(y_of(r, usewb, wts), r['yj'])
        Ps, rs, Pbest, rbest = sweep_P(h, doc_t)
        curves[os.path.basename(r['stem'])] = {'P': Ps.tolist(), 'rms': rs.round(3).tolist()}
        sweep_rows.append({'stem': os.path.basename(r['stem']), 'group': r['group'],
                           'P_argmin': Pbest, 'rms_at_argmin': rbest,
                           'rms_at_230': rms_from_hist(h, doc_t, 230.0),
                           'rms_at_385': rms_from_hist(h, doc_t, 385.0)})
        print('  %-10s argmin P=%4.0f (RMS %.2f)   @230 %.2f   @385 %.2f   @368 %.2f'
              % (os.path.basename(r['stem']), Pbest, rbest,
                 rms_from_hist(h, doc_t, 230.0), rms_from_hist(h, doc_t, 385.0),
                 rms_from_hist(h, doc_t, 255 * 0.95 / 0.6626)), flush=True)
    print('  曲线（P 180→420，纵向 ▓=坏 ░=好；每组一行，竖线为 argmin）', flush=True)
    Ps_ref = np.arange(P_LO, P_HI + 1, 1.0)
    grid = np.arange(0, len(Ps_ref), max(1, len(Ps_ref) // 48))
    p_arg = {s['stem']: s['P_argmin'] for s in sweep_rows}
    for r in usable:
        h = hist_stats(y_of(r, usewb, wts), r['yj'])
        rs = np.array([rms_from_hist(h, doc_t, P) for P in Ps_ref])
        line = spark(rs[grid])
        pos = int(np.argmin(np.abs(Ps_ref[grid] - p_arg[os.path.basename(r['stem'])])))
        line = line[:pos] + '|' + line[pos + 1:]
        print('  %-10s %s  %.1f→%.1f' % (os.path.basename(r['stem']), line,
                                         rs[grid].min(), rs[grid].max()), flush=True)
    agg_rs = np.mean([[rms_from_hist(hist_stats(y_of(r, usewb, wts), r['yj']), doc_t, P)
                       for P in Ps_ref] for r in usable], axis=0)
    i_agg = int(np.argmin(agg_rs))
    line = spark(agg_rs[grid])
    pos = int(np.argmin(np.abs(Ps_ref[grid] - Ps_ref[i_agg])))
    print('  %-10s %s  ← 15 张平均曲线，谷底 P=%d (%.2f)'
          % ('平均', line[:pos] + '|' + line[pos + 1:], Ps_ref[i_agg], agg_rs[i_agg]),
          flush=True)
    Parg = np.array([s['P_argmin'] for s in sweep_rows])
    P_med = float(np.median(Parg))
    print('  ⇒ argmin 中位 %.0f（范围 %.0f–%.0f），@230 平均 %.2f，@385 平均 %.2f'
          % (P_med, Parg.min(), Parg.max(),
             np.mean([s['rms_at_230'] for s in sweep_rows]),
             np.mean([s['rms_at_385'] for s in sweep_rows])), flush=True)

    # 单位证明：把 Y 除以 16383 而不是 (white-black)，P 与 RMS 各变多少
    r0 = usable[0]
    k14 = (rp.WHITE - rp.BLACK) / 16383.0        # 0.2345
    h_std = hist_stats(y_of(r0, usewb, wts), r0['yj'])
    h_14 = hist_stats(y_of(r0, usewb, wts) * k14, r0['yj'])
    f_std = fit_scale(h_std, doc_t)
    f_14 = {'P': f_std['P'] / k14, 'rms': rms_from_hist(h_14, doc_t, f_std['P'] / k14)}
    unit_demo = {'stem': os.path.basename(r0['stem']),
                 'P_at_3842': f_std['P'], 'rms_at_3842': f_std['rms'],
                 'P_at_16383': f_14['P'], 'rms_at_16383': f_14['rms'],
                 'ratio': f_std['P'] / f_14['P']}
    print('  [单位] ÷3842: P=%.0f RMS %.3f  |  ÷16383: P=%.0f RMS %.3f  '
          '(比值 %.3f = 16383/3842)' % (unit_demo['P_at_3842'], unit_demo['rms_at_3842'],
                                        unit_demo['P_at_16383'], unit_demo['rms_at_16383'],
                                        unit_demo['ratio']), flush=True)

    # ---------- C. 表族退化
    deg = table_degeneracy(tables)
    print('\n[C] 表族退化（两表最佳索引缩放后的 RMSE，越小越说明是同一曲线的副本）', flush=True)
    for d in deg[:6]:
        print('  t%-2d vs t%-2d : c=%.2f  RMSE %.2f' % (d['a'], d['b'], d['c'], d['rms']),
              flush=True)

    # ---------- D. CCM
    base_cfg = {'tone': doc_t, 'chroma_k': 0, 'mode': 'luma',
                'ccm': np.array(rp.DEFAULT_CCM, dtype=np.float32), 'sat': rp.DEFAULT_SAT}
    ccm_res = None
    if not a.no_ccm:
        items = []
        for r in usable:
            f = fit_scale(hist_stats(y_of(r, usewb, wts), r['yj']), doc_t)
            r['P'] = f['P']
            items.append((r, f['P']))
        print('\n[D] 拟合全局 CCM（%d 张，低通域）…' % len(items), flush=True)
        ccm_res = fit_ccm(items, base_cfg, log=lambda s: print(s, flush=True))
        print('    CCM = %s' % np.array(ccm_res['ccm']).round(4).tolist(), flush=True)
    for r in usable:
        r.setdefault('P', fit_scale(hist_stats(y_of(r, usewb, wts), r['yj']), doc_t)['P'])

    # ---------- E. 逐张误差
    cfg_base = dict(base_cfg,
                    ccm=np.array(ccm_res['ccm']) if ccm_res else np.array(rp.DEFAULT_CCM, dtype=np.float32),
                    sat=ccm_res['sat'] if ccm_res else rp.DEFAULT_SAT)
    print('\n[D2] 逐张扫描 54 张表（oracle：允许每张配对自己的表号）', flush=True)
    for r in usable:
        h = hist_stats(y_of(r, usewb, wts), r['yj'])
        r['oracle'] = scan_tables(h, tables)
        print('  %-10s 固定t%-2d@P%.0f → %.2f | oracle t%-2d@P%.0f → %.2f%s'
              % (os.path.basename(r['stem']), a.tone_k, r['P'], rms_from_hist(h, doc_t, r['P']),
                 r['oracle']['k'], r['oracle']['P'], r['oracle']['rms'],
                 '  ← 表号不匹配' if r['oracle']['rms'] < rms_from_hist(h, doc_t, r['P']) - 1.0
                 else ''), flush=True)
    ok = np.mean([r['oracle']['rms'] for r in usable])
    print('  ⇒ oracle 表平均亮度 RMS %.2f（固定 t%d 为 %.2f）'
          % (ok, a.tone_k, np.mean([rms_from_hist(hist_stats(y_of(r, usewb, wts), r['yj']),
                                                  doc_t, r['P']) for r in usable])), flush=True)

    print('\n[E] 逐张误差（P=各自 argmin；CCM/sat 为全局拟合值）', flush=True)
    rows = []
    for r in usable:
        cfg = dict(cfg_base, index_scale=r['P'])
        row = {'stem': os.path.basename(r['stem']), 'group': r['group'],
               'shape': list(r['shape']), 'P': r['P'], 'x_white': 255.0 / r['P'],
               'p99': float(np.percentile(r['yj'], 99)),
               'floor': free_curve_floor(r),
               'oracle': r['oracle'],
               'pix': metrics(r, cfg), 'lp': metrics(r, cfg, lowpass=True),
               'block16': block_median_rms(rp.render(r['lin'], cfg), r['jpg']),
               'resid_by_luma': residual_by_luma(r, cfg)}
        rows.append(row)
        print('  %-10s P=%4.0f | 逐像素 lum %.2f rgb %.1f | 低通 lum %.2f rgb %.1f '
              '| dCb %+.1f dCr %+.1f | floor %.2f'
              % (row['stem'], row['P'], row['pix']['lum_rms'], row['pix']['rgb_rms'],
                 row['lp']['lum_rms'], row['lp']['rgb_rms'], row['lp']['dcb'],
                 row['lp']['dcr'], row['floor']), flush=True)

    # 固定 P=230 的全局配置（M1 口径：一套参数对全部）
    print('\n[F] 全局单参数配置（P=230、全局 CCM）', flush=True)
    cfg_glob = dict(cfg_base, index_scale=230.0)
    for r, row in zip(usable, rows):
        cfg_or = dict(cfg_base, index_scale=r['oracle']['P'],
                      tone=rp.tone_table(r['oracle']['k']))
        row['oracle_pix'] = metrics(r, cfg_or)
        row['oracle_lp'] = metrics(r, cfg_or, lowpass=True)
        row['global_pix'] = metrics(r, cfg_glob)
        row['global_lp'] = metrics(r, cfg_glob, lowpass=True)

    def agg(path, field):
        return float(np.mean([eval('r["%s"]["%s"]' % (path, field)) for r in rows]))

    summary = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'step': a.step, 'pool': POOL, 'wb': a.wb, 'tone_k': a.tone_k,
        'n_pairs': len(rows), 'groups': used_groups,
        'pix_shape': rows[0]['shape'],
        'lp_shape': list(usable[0]['lin_lp'].shape),
        'y_variants': var_rows, 'y_variant_used': best_var,
        'P_argmin_median': P_med, 'P_argmin_range': [float(Parg.min()), float(Parg.max())],
        'x_white_median': 255.0 / P_med, 's_at_xw_06626': P_med * 0.6626 / 255.0,
        'sweep': sweep_rows, 'unit_demo': unit_demo,
        'table_degeneracy_top': deg[:10], 'ccm': ccm_res,
        'mean': {
            'pix_lum': agg('pix', 'lum_rms'), 'pix_rgb': agg('pix', 'rgb_rms'),
            'lp_lum': agg('lp', 'lum_rms'), 'lp_rgb': agg('lp', 'rgb_rms'),
            'lp_dcb': agg('lp', 'dcb'), 'lp_dcr': agg('lp', 'dcr'),
            'lp_dcb_abs': agg('lp', 'dcb_abs'), 'lp_dcr_abs': agg('lp', 'dcr_abs'),
            'pix_lum_bias': agg('pix', 'lum_bias'),
            'floor': float(np.mean([r['floor'] for r in rows])),
            'block16': float(np.mean([r['block16'] for r in rows])),
            'oracle_block16': float(np.mean([block_median_rms(
                rp.render(r['lin'], dict(cfg_base, index_scale=r['oracle']['P'],
                                         tone=rp.tone_table(r['oracle']['k']))), r['jpg'])
                for r in usable])),
            'oracle_pix_lum': float(np.mean([r['oracle_pix']['lum_rms'] for r in rows])),
            'oracle_pix_rgb': float(np.mean([r['oracle_pix']['rgb_rms'] for r in rows])),
            'oracle_lp_lum': float(np.mean([r['oracle_lp']['lum_rms'] for r in rows])),
            'global_pix_lum': float(np.mean([r['global_pix']['lum_rms'] for r in rows])),
            'global_pix_rgb': float(np.mean([r['global_pix']['rgb_rms'] for r in rows])),
            'global_lp_lum': float(np.mean([r['global_lp']['lum_rms'] for r in rows])),
            'global_lp_rgb': float(np.mean([r['global_lp']['rgb_rms'] for r in rows])),
        },
        'rows': rows,
    }
    with open(os.path.join(a.outdir, 'check_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    with open(os.path.join(a.outdir, 'psweep.json'), 'w', encoding='utf-8') as f:
        json.dump({'curves': curves, 'argmin': sweep_rows}, f, ensure_ascii=False)

    # markdown 表
    md = ['| 组 | 帧 | p99 | P | 逐像素 亮度RMS | 逐像素 RGB RMS | 低通 亮度RMS | '
          '低通 RGB RMS | ΔCb | ΔCr | 自由曲线下限 | oracle 表 | oracle 亮度RMS | 16×16块中位|',
          '| :-- | :-- | --: | --: | --: | --: | --: | --: | --: | --: | --: | :-- | --: | --: |']
    for row in rows:
        md.append('| %s | %s | %.0f | %.0f | %.2f | %.1f | %.2f | %.1f | %+.1f | %+.1f | %.2f '
                  '| t%d@%.0f | %.2f | %.2f |'
                  % (row['group'].split('_')[0], row['stem'], row['p99'], row['P'],
                     row['pix']['lum_rms'], row['pix']['rgb_rms'],
                     row['lp']['lum_rms'], row['lp']['rgb_rms'],
                     row['lp']['dcb'], row['lp']['dcr'], row['floor'],
                     row['oracle']['k'], row['oracle']['P'], row['oracle_pix']['lum_rms'],
                     row['block16']))
    m = summary['mean']
    md.append('| **均值** | %d | — | %.0f | **%.2f** | **%.1f** | **%.2f** | **%.1f** | '
              '%+.1f | %+.1f | %.2f | — | **%.2f** | **%.2f** |'
              % (len(rows), P_med, m['pix_lum'], m['pix_rgb'], m['lp_lum'], m['lp_rgb'],
                 m['lp_dcb'], m['lp_dcr'], m['floor'], m['oracle_pix_lum'],
                 m['block16']))
    md_txt = '\n'.join(md)
    with open(os.path.join(a.outdir, 'check_table.md'), 'w', encoding='utf-8') as f:
        f.write(md_txt + '\n')

    # 残差按亮度（汇总）
    nb = 8
    edges = np.linspace(0, 256, nb + 1)
    print('\n[G] 残差按 JPG 亮度分布（逐像素，全部配对汇总）', flush=True)
    print('  %-12s %8s %8s %8s' % ('亮度区间', '占比', '均值偏差', 'RMS'))
    pooled = []
    for r in usable:
        out = rp.render(r['lin'], dict(cfg_base, index_scale=r['P'])).astype(np.float32)
        pooled.append(((rp.luma(out) - r['yj']).ravel(), r['yj'].ravel()))
    for i in range(nb):
        parts = [e[(yj >= edges[i]) & (yj < edges[i + 1])] for e, yj in pooled]
        e_all = np.concatenate(parts) if parts else np.zeros(0)
        n_all = int(e_all.size)
        if n_all < 100:
            continue
        print('  %3.0f–%-3.0f   %7.1f%%  %+8.2f %8.2f'
              % (edges[i], edges[i + 1], 100.0 * n_all / sum(r['yj'].size for r in usable),
                 e_all.mean(), float(np.sqrt((e_all ** 2).mean()))), flush=True)
        summary.setdefault('resid_bins', []).append(
            {'lo': float(edges[i]), 'hi': float(edges[i + 1]), 'n': n_all,
             'bias': float(e_all.mean()), 'rms': float(np.sqrt((e_all ** 2).mean()))})

    # ---------- H. 差分精度：模拟器能不能判「两个方案之间的差别」
    print('\n[H] 差分精度（同场景不同设置的两帧：模拟器预测的差 vs 真实差）', flush=True)
    print('    绝对误差大不妨碍判观感 —— 只要「两个方案的差」的误差远小于差本身。', flush=True)
    diff_rows = []
    for gname in used_groups:
        g_recs = [r for r in usable if r['group'] == gname]
        if len(g_recs) < 2:
            continue
        base = g_recs[len(g_recs) // 2]
        ob = rp.render(base['lin'], dict(cfg_base, index_scale=base['P'])).astype(np.float32)
        for r in g_recs:
            if r is base:
                continue
            oo = rp.render(r['lin'], dict(cfg_base, index_scale=r['P'])).astype(np.float32)
            dt = r['yj'] - base['yj']
            ds = rp.luma(oo) - rp.luma(ob)
            mag = float(np.sqrt((dt ** 2).mean()))
            err = float(np.sqrt(((ds - dt) ** 2).mean()))
            # 块中位口径
            bs = 16
            h, w = oo.shape[0] // bs * bs, oo.shape[1] // bs * bs
            def bm(x):
                return np.median(x[:h, :w].reshape(h // bs, bs, w // bs, bs)
                                 .transpose(0, 2, 1, 3).reshape(-1, bs * bs), axis=1)
            mt = bm(dt); ms = bm(ds)
            mag_b = float(np.sqrt((mt ** 2).mean()))
            err_b = float(np.sqrt(((ms - mt) ** 2).mean()))
            diff_rows.append({'group': gname, 'base': os.path.basename(base['stem']),
                              'stem': os.path.basename(r['stem']),
                              'mag': mag, 'err': err, 'ratio': err / max(mag, 1e-6),
                              'mag_b16': mag_b, 'err_b16': err_b,
                              'ratio_b16': err_b / max(mag_b, 1e-6)})
            print('  %-10s %s − %s : 差本身 %.2f，差里误差 %.2f（%.0f%%）| 块中位：%.2f / %.2f（%.0f%%）'
                  % (gname.split('_')[0], os.path.basename(r['stem']),
                     os.path.basename(base['stem']), mag, err, 100 * err / max(mag, 1e-6),
                     mag_b, err_b, 100 * err_b / max(mag_b, 1e-6)), flush=True)
    if diff_rows:
        # 同一批差分，但允许每帧用自己的 oracle 表（把「表号错」这一项扣掉）
        print('  -- 同上，但每帧用自己的 oracle 表（扣掉表号错这一项）', flush=True)
        dr2 = []
        for gname in used_groups:
            g_recs = [r for r in usable if r['group'] == gname]
            if len(g_recs) < 2:
                continue
            base = g_recs[len(g_recs) // 2]
            ob = rp.render(base['lin'], dict(cfg_base, index_scale=base['oracle']['P'],
                                             tone=rp.tone_table(base['oracle']['k']))).astype(np.float32)
            for r in g_recs:
                if r is base:
                    continue
                oo = rp.render(r['lin'], dict(cfg_base, index_scale=r['oracle']['P'],
                                             tone=rp.tone_table(r['oracle']['k']))).astype(np.float32)
                dt = r['yj'] - base['yj']
                ds = rp.luma(oo) - rp.luma(ob)
                err = float(np.sqrt(((ds - dt) ** 2).mean()))
                bs = 16
                h, w = oo.shape[0] // bs * bs, oo.shape[1] // bs * bs
                def bm(x):
                    return np.median(x[:h, :w].reshape(h // bs, bs, w // bs, bs)
                                     .transpose(0, 2, 1, 3).reshape(-1, bs * bs), axis=1)
                eb = float(np.sqrt(((bm(ds) - bm(dt)) ** 2).mean()))
                dr2.append({'err': err, 'err_b16': eb})
                print('     %-10s %s : 差分误差 %.2f | 块中位 %.2f'
                      % (gname.split('_')[0], os.path.basename(r['stem']), err, eb), flush=True)
        if dr2:
            summary['diff_accuracy_oracle'] = dr2
            print('     ⇒ oracle 表下差分误差中位 %.2f（块中位 %.2f）'
                  % (np.median([d['err'] for d in dr2]),
                     np.median([d['err_b16'] for d in dr2])), flush=True)
        print('  ⇒ 差分误差中位 %.2f/255（占差本身 %.0f%%）；块中位口径 %.2f（%.0f%%）'
              % (np.median([d['err'] for d in diff_rows]),
                 100 * np.median([d['ratio'] for d in diff_rows]),
                 np.median([d['err_b16'] for d in diff_rows]),
                 100 * np.median([d['ratio_b16'] for d in diff_rows])), flush=True)
        summary['diff_accuracy'] = diff_rows
        summary['diff_err_median'] = float(np.median([d['err'] for d in diff_rows]))
        summary['diff_ratio_median'] = float(np.median([d['ratio'] for d in diff_rows]))

    with open(os.path.join(a.outdir, 'check_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print('\n' + md_txt)
    print('\n[汇总] ' + json.dumps(m, ensure_ascii=False))
    print('[判定] M1（亮度 RMS<3）: %s' %
          ('达成' if m['pix_lum'] < 3 else '未达成（%.2f）' % m['pix_lum']))
    print('[用时] %.1fs' % (time.time() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
