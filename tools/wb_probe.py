# -*- coding: utf-8 -*-
"""wb_probe —— 相机 JPG 的白平衡到底记在哪里，rawpipe 的白平衡环该怎么写。

三个问题，三段答案（对应 `docs/color/wb-gap.md`）：

  A. `exif`    JPG / ORF 的 Olympus MakerNote 里有哪些 WB 字段？哪些逐张变？
               结论：JPG 只有 0x2040:0x0100（= libraw 的 cam_mul，16 对全同），
               没有 WB 预设表、没有 0x0101/0x0125/0x0126；那三个只在 ORF 里，
               且逐张不同 —— 但它们不是相机实际用的 WB（见 C）。

  B. `neutral` 中性区反解白平衡（**不依赖 CCM/tone/sat**）。
               原理：相机矩阵 0x2040:0x0200 的行和恰为 256/256=1 ⇒ 中性进中性出，
               所以「JPG 里 Cb≈Cr≈128 的像素」在 RAW 侧必须满足 R·gR = G = B·gB。
               13–14 张可测图给出 gR=2.083±0.026、gB=1.742±0.030，
               相对 cam_mul 只有 ×1.010±0.012 / ×1.018±0.017。

  C. `variants` 把几套白平衡/色彩矩阵写法逐张跑一遍 RGB RMS。
               结论：白平衡环已经是 cam_mul，改它没用（最优全局修正 = ×1.000）；
               真正的改进在色彩矩阵：用 ORF 自带的 0x2040:0x0200 + sat 拟合。

  D. `fit`     逐张 (R,B) 二维增益拟合（慢，≈2.5 min/16 张），用来证明
               cam_mul 本身就在 RMS 的局部最优上、且解不稳定 ⇒ 不存在「固定修正」。

方法要点
--------
* MakerNote 偏移规则（E-M10 II 实测）：头 12 字节 `OLYMPUS\\0II\\x03\\0`，顶层
  IFD 起于 mn[12]；**所有 value / 子 IFD 指针都是相对 mn 起始(0) 的偏移**（含头）。
  子目录靠 type=13(IFD) 的 tag 递归，JPG 与 ORF 共用同一张私有 tag 表。
* 不改 `tools/rawpipe.py`：解码/渲染全部复用它的函数；白平衡在浮点域自己乘
  （`load_orfs(..., wb='none')` ⇒ lin 不带 WB，之后乘 diag(g)）。
* 4640×3472 的图一律按 STEP=6 抽样（768×576）后再算，全程只跑小图。

用法
----
    python tools/wb_probe.py                  # exif + neutral + variants（默认，≈90 s）
    python tools/wb_probe.py exif             # 只列 tag（最快，不解码 RAW）
    python tools/wb_probe.py neutral          # 只做中性区反解
    python tools/wb_probe.py variants         # 只做变体对比（RGB RMS 表）
    python tools/wb_probe.py fit              # 逐张 2D 增益拟合（慢）
    --groups 01,10   只跑这些组       --step 6   抽样步长
    --json F         另存机器可读结果

输出是 Markdown 表，可直接抄进文档。
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys

import numpy as np

for _s in (sys.stdout, sys.stderr):       # 控制台可能是 GBK，强制 UTF-8
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import rawpipe as rp                      # noqa: E402
import rawpipe_check as rpc               # noqa: E402

# ================================================================ EXIF 解析

TSIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4,
         12: 8, 13: 4}


def _tiff(buf, base):
    """返回 (u16, u32, ifd, val)；所有偏移都加 base（TIFF 头所在文件偏移）。"""
    e = '<' if buf[base:base + 2] == b'II' else '>'

    def u16(o):
        return struct.unpack_from(e + 'H', buf, o)[0]

    def u32(o):
        return struct.unpack_from(e + 'I', buf, o)[0]

    def ifd(off):
        n = u16(base + off)
        out = []
        for i in range(n):
            p = base + off + 2 + 12 * i
            tag, typ, cnt = struct.unpack_from(e + 'HHI', buf, p)
            out.append((tag, typ, cnt, p + 8))
        q = base + off + 2 + 12 * n
        nxt = u32(q) if q + 4 <= len(buf) else 0
        return out, nxt

    def val(typ, cnt, vo):
        sz = TSIZE.get(typ, 1) * cnt
        raw = buf[vo:vo + 4] if sz <= 4 else buf[base + u32(vo):base + u32(vo) + sz]
        if len(raw) < sz:
            return None
        if typ == 3:
            return tuple(struct.unpack(e + '%dH' % cnt, raw[:2 * cnt]))
        if typ == 4:
            return tuple(struct.unpack(e + '%dI' % cnt, raw[:4 * cnt]))
        if typ in (5, 10):
            return tuple(round(a / b, 6) if b else 0.0 for a, b in
                         (struct.unpack_from(e + 'II', raw, 8 * k) for k in range(cnt)))
        if typ == 9:
            return tuple(struct.unpack(e + '%di' % cnt, raw[:4 * cnt]))
        if typ == 2:
            return raw.split(b'\0')[0].decode('latin1', 'replace')
        return raw[:sz]

    return u16, u32, ifd, val


def maker_note(path):
    """JPG（APP1→ExifIFD→0x927C）或 ORF（本身是 TIFF）→ maker note bytes。"""
    with open(path, 'rb') as f:
        buf = f.read()
    base = 0
    if buf[:2] == b'\xff\xd8':                       # JPEG
        i, base = 2, None
        while i < len(buf) - 4 and buf[i] == 0xFF:
            m, ln = buf[i + 1], struct.unpack_from('>H', buf, i + 2)[0]
            if m == 0xE1 and buf[i + 4:i + 10] == b'Exif\0\0':
                base = i + 10
                break
            i += 2 + ln
        if base is None:
            return None
    _, u32, ifd, _ = _tiff(buf, base)
    es, _ = ifd(u32(base + 4))                        # IFD0
    for tag, _typ, _cnt, vo in es:
        if tag != 0x8769:                             # ExifIFD
            continue
        ex, _ = ifd(u32(vo))
        for t2, ty2, cn2, v2 in ex:
            if t2 != 0x927C:                          # MakerNote
                continue
            ln = TSIZE.get(ty2, 1) * cn2
            off = vo if ln <= 4 else base + u32(v2)
            return buf[off:off + ln]
    return None


def _mn_ifd(mn, off):
    n = struct.unpack_from('<H', mn, off)[0]
    out = [(struct.unpack_from('<HHI', mn, off + 2 + 12 * i) + (off + 2 + 12 * i + 8,))
           for i in range(n)]
    q = off + 2 + 12 * n
    return out, (struct.unpack_from('<I', mn, q)[0] if q + 4 <= len(mn) else 0)


def _mn_val(mn, typ, cnt, vo):
    sz = TSIZE.get(typ, 1) * cnt
    raw = mn[vo:vo + 4] if sz <= 4 else mn[struct.unpack_from('<I', mn, vo)[0]:]
    if len(raw) < sz:
        return None
    raw = raw[:sz]
    if typ == 3:
        return tuple(struct.unpack('<%dH' % cnt, raw))
    if typ == 4:
        return tuple(struct.unpack('<%dI' % cnt, raw))
    if typ in (5, 10):
        return tuple(round(a / b, 6) if b else 0.0 for a, b in
                     (struct.unpack_from('<II', raw, 8 * k) for k in range(cnt)))
    if typ == 9:
        return tuple(struct.unpack('<%di' % cnt, raw))
    if typ == 2:
        return raw.split(b'\0')[0].decode('latin1', 'replace')
    return raw


def olympus_tags(path):
    """Olympus MakerNote → {'子IFD:tag'(hex): value}；子 IFD 递归一层。"""
    mn = maker_note(path)
    if not mn or mn[:7] != b'OLYMPUS':
        return {}
    out = {}
    es, _ = _mn_ifd(mn, 12)
    for tag, typ, _cnt, vo in es:
        if typ != 13:
            continue
        try:
            sub, _ = _mn_ifd(mn, struct.unpack_from('<I', mn, vo)[0])
        except Exception:
            continue
        for t2, ty2, cn2, v2 in sub:
            try:
                out['%04X:%04X' % (tag, t2)] = _mn_val(mn, ty2, cn2, v2)
            except Exception:
                out['%04X:%04X' % (tag, t2)] = None
    return out


# 0x2040 RawDevelopment2 的 WB 段（本机实测语义，见 docs/color/wb-gap.md）
WB_KEYS = ('2040:0100', '2040:0101', '2040:0125', '2040:0126')
WB_PRESETS = ['2040:%04X' % t for t in list(range(0x0102, 0x0112)) +
              list(range(0x0120, 0x0122))]


def wb_summary(path):
    """一张 JPG/ORF 的 WB 相关字段，增益已归一到 G=1 口径（R/G, B/G）。"""
    t = olympus_tags(path)
    out = {'n_tags': len(t), 'n_wb_presets': sum(1 for k in WB_PRESETS if t.get(k)),
           'temp': (t.get('2020:0501') or (None,))[0],
           'wb_mode': (t.get('2020:0500') or (None,))[0],
           'black': t.get('2040:0600'), 'matrix_raw': t.get('2040:0200')}
    for k in WB_KEYS:
        v = t.get(k)
        if v and len(v) >= 4:
            out[k] = {'raw': v, 'r': v[0] / 256.0, 'b': v[1] / 256.0}
    return out


def matrix_from_tag(v):
    """0x2040:0x0200（9×int16，行和 = 256）→ 3×3 浮点矩阵。"""
    if not v or len(v) != 9:
        return None
    m = np.array([x - 65536 if x > 32767 else x for x in v], dtype=np.float64)
    return m.reshape(3, 3) / 256.0


# ================================================================ 配对 / 配置

def pairs(groups=None):
    """[(stem, 短名)] —— 与 tools/rawpipe_check.py 用同一批官方固件配对。"""
    out = []
    for name, d, lim in rpc.GROUPS:
        if groups and not any(name.startswith(g) for g in groups):
            continue
        if name == 'tmp_PB120056':
            stem = '.tmp/PB120056'
            if os.path.exists(stem + '.ORF') and os.path.exists(stem + '.JPG'):
                out.append((stem, 'tmp'))
            continue
        for stem in rpc.pairs_in(d, lim):
            out.append((stem, os.path.basename(stem)))
    return out


# docs/color/raw-sim.md 的基线：CCM 与 sat 是在 cam_mul 白平衡下拟合出来的
DOC_CCM = [[2.1304, -0.8696, -0.2609],
           [-0.3200, 1.4000, -0.0800],
           [-0.6400, -0.3200, 1.9600]]
DOC_CFG = {'ccm': DOC_CCM, 'sat': 1.48, 'index_scale': 231.0,
           'x_white': 1.1087, 's': 1.0, 'tone_k': 3, 'chroma_k': 0}
ID3 = np.eye(3).tolist()


def cfg(**kw):
    c = dict(DOC_CFG)
    c.update(kw)
    return c


def load_rec(stem, step, wb='none'):
    """lin **不带** WB（wb='none'），白平衡由调用方乘，便于拟合/对照。"""
    rec = rpc.load_pair(stem, step=step, wb=wb)
    rec['cam'] = [float(x) for x in rec['meta']['camera_whitebalance'][:3]]
    return rec


# ================================================================ 中性区反解

def neutral_mask(rec, thr=3.0, lo=55.0, hi=225.0):
    """JPG 侧「接近中性」的像素：|Cb-128|、|Cr-128| ≤ thr，亮度不顶不黑，RAW 未过曝。

    相机 JPG 的色调链是「亮度过表 + 色度单独处理」，中性像素过完仍是中性 ⇒
    这个掩膜只依赖 JPG，与我们的模拟管线无关，可当独立锚点。
    """
    jpg = rec['jpg']
    _y, cb, cr = rp.rgb_to_ycbcr(jpg)
    y = rp.luma(jpg)
    m = (np.abs(cb - 128.0) <= thr) & (np.abs(cr - 128.0) <= thr) & (y >= lo) & (y <= hi)
    return m & (rec['lin'].max(axis=2) < 0.95)


def neutral_gains(rec, thr=3.0, lo=55.0, hi=225.0):
    """中性区三通道中位数 → 「让它们变中性所需的增益」。

    相机矩阵 0x2040:0x0200 的行和 = 1，中性输入过矩阵仍中性，所以这些像素在
    RAW 侧必须满足 R·gR = G = B·gB ⇒ gR = G/R, gB = G/B（G 归一）。
    与 CCM、tone、sat 全都无关，是这条链路上唯一物理可辨识的白平衡估计。
    """
    m = neutral_mask(rec, thr, lo, hi)
    n = int(m.sum())
    if n < 500:
        return None
    r_, g_, b_ = (float(x) for x in np.median(rec['lin'][m], axis=0))
    if min(r_, g_, b_) <= 1e-6:
        return None
    return {'n': n, 'rgb': (r_, g_, b_), 'gR': g_ / r_, 'gB': g_ / b_}


# ================================================================ 评估

def render_with(rec, cfgd, gains):
    return rp.render(rec['lin'] * np.asarray(gains, np.float32).reshape(1, 1, 3),
                     cfgd).astype(np.float32)


def eval_cfg(rec, cfgd, gains, lowpass=False):
    out = render_with(rec, cfgd, gains)
    if lowpass:
        out, jpg = rpc.pool_mean(out), rec['jpg_lp']
    else:
        jpg = rec['jpg']
    d = out - jpg
    return {'px': float(np.sqrt((d * d).mean())),
            'bias': [float(d[..., i].mean()) for i in range(3)]}


def mean_rms(recs, cfgd, gfun, lowpass=False):
    return float(np.mean([eval_cfg(r, cfgd, gfun(r), lowpass)['px'] for r in recs]))


def pearson(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3 or x.std() < 1e-12 or y.std() < 1e-12:
        return float('nan')
    return float(((x - x.mean()) * (y - y.mean())).mean() / (x.std() * y.std()))


# ================================================================ 子命令

def cmd_exif(args):
    ps = pairs(args.groups)
    print('## A. JPG / ORF 里的 WB 字段（Olympus MakerNote，0x2040 RawDevelopment2）\n')
    print('配对 %d 组；每格 = R/G, B/G（原值 /256）\n' % len(ps))
    rows = {}
    for side, ext in (('JPG', '.JPG'), ('ORF', '.ORF')):
        for stem, nm in ps:
            try:
                rows[(side, nm)] = wb_summary(stem + ext)
            except Exception as e:                    # noqa: BLE001
                rows[(side, nm)] = {'err': str(e)}
    for key in WB_KEYS:
        for side in ('JPG', 'ORF'):
            vals = []
            for _, nm in ps:
                v = rows[(side, nm)].get(key)
                vals.append('%.3f/%.3f' % (v['r'], v['b']) if v else '—')
            present = sum(1 for x in vals if x != '—')
            uniq = len(set(x for x in vals if x != '—'))
            print('| %s | %s | %d/%d | %d | %s' % (
                side, key, present, len(ps), uniq, ' '.join(vals[:8]) + (' …' if len(vals) > 8 else '')))
    print()
    for side in ('JPG', 'ORF'):
        n_preset = sorted({rows[(side, nm)].get('n_wb_presets') for _, nm in ps}, key=str)
        tv = sorted({(rows[(side, nm)].get('temp'), rows[(side, nm)].get('wb_mode'))
                     for _, nm in ps}, key=str)
        print('* %s：WB 预设表 %s 条；0x0501(K)/0x0500(模式) = %s' % (side, n_preset, tv))
    mats = {}
    for stem, nm in ps:
        m = matrix_from_tag(wb_summary(stem + '.ORF').get('matrix_raw'))
        if m is not None:
            mats[nm] = m
    if mats:
        arr = np.array(list(mats.values()))
        print('* 0x2040:0x0200（9×int16，行和=256）在 %d 个 ORF 上%s：' % (
            len(mats), '完全一致' if float(arr.std(0).max()) < 1e-9 else '有差异'))
        print('```')
        print(np.round(next(iter(mats.values())), 5))
        print('```')
    return rows


def cmd_neutral(args):
    ps = pairs(args.groups)
    print('## B. 中性区反解白平衡（不依赖 CCM/tone/sat）\n')
    print('掩膜：|Cb-128|、|Cr-128| ≤ 3，亮度 55..225，RAW<0.95；取中位数。\n')
    print('| 配对 | 中性像素 | gR | gB | cam.R | cam.B | gR/cam | gB/cam | 0x0101 R |')
    print('|---|---|---|---|---|---|---|---|---|')
    rows = []
    for stem, nm in ps:
        rec = load_rec(stem, args.step)
        ng = neutral_gains(rec)
        t1 = wb_summary(stem + '.ORF').get('2040:0101')
        cam = rec['cam']
        if ng is None:
            print('| %s | <500 | — | — | %.4f | %.4f | — | — | %s |' % (
                nm, cam[0], cam[2], '%.3f' % t1['r'] if t1 else '—'))
            continue
        rows.append({'name': nm, 'stem': stem, 'n': ng['n'], 'gR': ng['gR'], 'gB': ng['gB'],
                     'cam': [cam[0], cam[2]],
                     'tag': [t1['r'], t1['b']] if t1 else [None, None]})
        print('| %s | %d | %.4f | %.4f | %.4f | %.4f | %.4f | %.4f | %s |' % (
            nm, ng['n'], ng['gR'], ng['gB'], cam[0], cam[2],
            ng['gR'] / cam[0], ng['gB'] / cam[2], '%.3f' % t1['r'] if t1 else '—'))
    if not rows:
        return rows
    gR = np.array([r['gR'] for r in rows])
    gB = np.array([r['gB'] for r in rows])
    camR = np.array([r['cam'][0] for r in rows])
    camB = np.array([r['cam'][1] for r in rows])
    tR = np.array([r['tag'][0] or np.nan for r in rows])
    tB = np.array([r['tag'][1] or np.nan for r in rows])
    print()
    print('* **gR = %.4f ± %.4f**（%.3f..%.3f）；**gB = %.4f ± %.4f**（%.3f..%.3f）；n=%d 张可测'
          % (gR.mean(), gR.std(), gR.min(), gR.max(),
             gB.mean(), gB.std(), gB.min(), gB.max(), len(rows)))
    print('* 相对 cam_mul：R ×%.4f ± %.4f（÷cam 后 %.1f%% 离散）  B ×%.4f ± %.4f（%.1f%%）'
          % ((gR / camR).mean(), (gR / camR).std(), (gR / camR).std() / (gR / camR).mean() * 100,
             (gB / camB).mean(), (gB / camB).std(), (gB / camB).std() / (gB / camB).mean() * 100))
    print('* 与 0x0101 的相关：gR~tagR r=%.3f，gB~tagB r=%.3f'
          % (pearson(gR, tR), pearson(gB, tB)))
    print('* 掩膜敏感性（gR/gB 均值，阈值越松中性像素越多）：')
    for thr, lo, hi in ((1.5, 40, 220), (3.0, 55, 225), (6.0, 80, 200)):
        vals = [neutral_gains(load_rec(s, args.step), thr, lo, hi) for s, _ in ps]
        vals = [v for v in vals if v]
        if len(vals) < 3:
            continue
        print('  - thr=%.1f lo=%d hi=%d：n=%d，gR %.4f、gB %.4f' % (
            thr, lo, hi, len(vals),
            float(np.mean([v['gR'] for v in vals])), float(np.mean([v['gB'] for v in vals]))))
    return rows


def cmd_variants(args):
    ps = pairs(args.groups)
    recs = []
    for stem, nm in ps:
        rec = load_rec(stem, args.step)
        try:
            if rp.luma(rec['jpg']).mean() < rpc.DARK_P99:   # 暗帧（PB110006）排除
                print('  跳过暗帧 %s（JPG 亮度均值 %.1f）' % (nm, rp.luma(rec['jpg']).mean()))
                continue
        except Exception:
            pass
        rec['ng'] = neutral_gains(rec)
        rec['tag01'] = wb_summary(stem + '.ORF').get('2040:0101')
        recs.append(rec)
    print('## C. 白平衡 / 色彩矩阵写法对比（%d 张，抽样 1/%d）\n' % (len(recs), args.step))
    cam_mat = None
    for r in recs:                                    # 相机矩阵是 16/16 常量，取第一个
        cam_mat = matrix_from_tag(wb_summary(r['stem'] + '.ORF').get('matrix_raw'))
        if cam_mat is not None:
            break
    base = lambda r: (r['cam'][0], 1.0, r['cam'][2])          # noqa: E731
    neu = lambda r: ((r['ng']['gR'], 1.0, r['ng']['gB']) if r['ng'] else base(r))   # noqa: E731
    NF = (float(np.mean([r['ng']['gR'] for r in recs if r['ng']])),
          float(np.mean([r['ng']['gB'] for r in recs if r['ng']])))
    tag01 = lambda r: ((r['tag01']['r'], 1.0, r['tag01']['b']) if r['tag01'] else base(r))  # noqa: E731
    NEW = cfg(ccm=cam_mat.tolist(), sat=2.2) if cam_mat is not None else cfg()
    V = [
        ('当前 rawpipe：cam_mul + doc CCM + sat1.48', cfg(), base),
        ('cam_mul + identity CCM + sat1', cfg(ccm=ID3, sat=1.0), base),
        ('ORF 0x0101 的 WB + doc CCM', cfg(), tag01),
        ('中性反解(逐张) + doc CCM', cfg(), neu),
        ('中性反解(固定 %.3f/%.3f) + doc CCM' % NF, cfg(), lambda r: (NF[0], 1.0, NF[1])),
        ('cam_mul + 相机矩阵(0x0200) + sat2.2', NEW, base),
        ('中性反解(逐张) + 相机矩阵 + sat2.2', NEW, neu),
        ('中性反解(固定) + 相机矩阵 + sat2.2', NEW, lambda r: (NF[0], 1.0, NF[1])),
    ]
    print('| 写法 | 逐像素 RGB RMS | 低通 RGB RMS | 偏差 R | 偏差 G | 偏差 B |')
    print('|---|---|---|---|---|---|')
    out = []
    for name, c, f in V:
        px = mean_rms(recs, c, f)
        lp = mean_rms(recs, c, f, lowpass=True)
        biases = np.mean([eval_cfg(r, c, f(r))['bias'] for r in recs], axis=0)
        print('| %s | %.2f | %.2f | %+.2f | %+.2f | %+.2f |' % (
            name, px, lp, biases[0], biases[1], biases[2]))
        out.append({'name': name, 'px': px, 'lp': lp, 'bias': [float(x) for x in biases]})
    # 「一个固定修正够不够」：在最优 CCM 下扫全局 (kR,kB)
    print()
    print('**固定 WB 修正扫描**（在上面最好那套 CCM 下，把 cam_mul 乘一个全局 (kR,kB)）：\n')
    print('| kR\\kB | ' + ' | '.join('%.2f' % kb for kb in (0.98, 1.00, 1.02)) + ' |')
    print('|---|---|---|---|')
    grid = {}
    for kr in (0.96, 0.98, 1.00, 1.02, 1.04):
        cells = []
        for kb in (0.98, 1.00, 1.02):
            v = mean_rms(recs, NEW, (lambda a, b: (lambda r: (r['cam'][0] * a, 1.0, r['cam'][2] * b)))(kr, kb),
                         lowpass=True)
            grid['%.2f/%.2f' % (kr, kb)] = v
            cells.append('%.2f' % v)
        print('| %.2f | %s |' % (kr, ' | '.join(cells)))
    kbest = min(grid, key=grid.get)
    print('\n最优固定修正 = (kR,kB) = %s ⇒ 低通 RMS %.2f；不修正 (1.00,1.00) = %.2f'
          % (kbest, grid[kbest], grid.get('1.00/1.00', float('nan'))))
    if cam_mat is not None:
        print('\n相机矩阵 0x2040:0x0200（R/G/B 三行和 = %.3f/%.3f/%.3f）：'
              % tuple(cam_mat.sum(1)))
    print('\nP（index_scale）在最好那套下的扫描：', end=' ')
    line = []
    for P in (220, 225, 231, 235, 240):
        line.append('%d:%.2f' % (P, mean_rms(recs, cfg(**{k: v for k, v in NEW.items()
                                                         if k != 'index_scale'},
                                               index_scale=float(P)), base, lowpass=True)))
    print(' '.join(line))
    print('\n（低通 = 渲染后 4×4 池化，与 docs/color/raw-sim.md 口径一致；'
          '逐像素受 JPG 锐化影响，系统性偏高。）')
    return {'variants': out, 'fixed_scan': grid, 'neutral_fixed': NF}


def fit_gains(rec, cfgd, n0=7, n1=3, span=0.35, max_evals=90):
    """(gR,gB) 二维坐标下降，目标 = 逐像素 RGB RMS。返回 (gR,gB,rms,evals)。"""
    cam = np.array(rec['cam'], float)
    gr, gb = float(cam[0]), float(cam[2])
    step_r, step_b = gr * span, gb * span
    best = eval_cfg(rec, cfgd, (gr, 1.0, gb))['px']
    n_eval = 1
    for it in range(3):
        n = n0 if it == 0 else n1
        for r_ in gr + np.linspace(-step_r, step_r, n):
            for b_ in gb + np.linspace(-step_b, step_b, n):
                if n_eval >= max_evals:
                    break
                r_, b_ = max(r_, 0.05), max(b_, 0.05)
                v = eval_cfg(rec, cfgd, (r_, 1.0, b_))['px']
                n_eval += 1
                if v < best:
                    best, gr, gb = v, float(r_), float(b_)
        step_r *= 2.0 / (n - 1) if n > 1 else 1.0
        step_b *= 2.0 / (n - 1) if n > 1 else 1.0
    return gr, gb, best, n_eval


def cmd_fit(args):
    ps = pairs(args.groups)
    print('## D. 逐张 (R,B) 二维增益拟合（目标 = 逐像素 RGB RMS；doc CCM+sat）\n')
    print('| 配对 | cam.R | cam.B | 拟合 gR | 拟合 gB | RMS 用 cam | RMS 用拟合 | 0x0101 R |')
    print('|---|---|---|---|---|---|---|---|')
    res = []
    for stem, nm in ps:
        rec = load_rec(stem, args.step)
        cam = rec['cam']
        gR, gB, best, n_ev = fit_gains(rec, DOC_CFG)
        r0 = eval_cfg(rec, DOC_CFG, (cam[0], 1.0, cam[2]))['px']
        t1 = wb_summary(stem + '.ORF').get('2040:0101')
        res.append({'name': nm, 'cam': [cam[0], cam[2]], 'fit': [gR, gB],
                    'rms_cam': r0, 'rms_fit': best, 'tag01': t1['r'] if t1 else None})
        print('| %s | %.4f | %.4f | %.4f | %.4f | %.2f | %.2f | %s |' % (
            nm, cam[0], cam[2], gR, gB, r0, best, '%.3f' % t1['r'] if t1 else '—'))
    fr = np.array([r['fit'][0] for r in res])
    fb = np.array([r['fit'][1] for r in res])
    cr = np.array([r['cam'][0] for r in res])
    print()
    print('* 平均 RMS %.2f → %.2f（%.2f%%）' % (
        np.mean([r['rms_cam'] for r in res]), np.mean([r['rms_fit'] for r in res]),
        (np.mean([r['rms_fit'] for r in res]) / np.mean([r['rms_cam'] for r in res]) - 1) * 100))
    print('* 停在起点（=cam_mul）的：%d/%d 张' % (
        sum(1 for r in res if abs(r['fit'][0] - r['cam'][0]) < 1e-6 and
            abs(r['fit'][1] - r['cam'][1]) < 1e-6), len(res)))
    print('* 拟合增益极差：gR %.3f..%.3f（×%.2f），gB %.3f..%.3f（×%.2f）' % (
        fr.min(), fr.max(), fr.max() / fr.min(), fb.min(), fb.max(), fb.max() / fb.min()))
    print('* 与 0x0101 的相关：gR~tagR r=%.3f，gB~tagB r=%.3f' % (
        pearson(fr, [r['tag01'] or np.nan for r in res]),
        pearson(fb, [np.nan if not r['tag01'] else r['tag01'] for r in res])))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description='Olympus JPG/ORF 白平衡探测与拟合')
    ap.add_argument('cmd', nargs='?', default='all',
                    choices=['exif', 'neutral', 'variants', 'fit', 'all'])
    ap.add_argument('--groups', default=None, help='只跑这些组前缀，逗号分隔，如 01,10')
    ap.add_argument('--step', type=int, default=rpc.STEP, help='抽样步长（默认 6）')
    ap.add_argument('--json', default=None, help='把结果另存为 JSON')
    args = ap.parse_args(argv)
    args.groups = [g.strip() for g in args.groups.split(',')] if args.groups else None

    payload = {}
    if args.cmd in ('exif', 'all'):
        cmd_exif(args)
        print()
    if args.cmd in ('neutral', 'all'):
        payload['neutral'] = cmd_neutral(args)
        print()
    if args.cmd in ('variants', 'all'):
        payload['variants'] = cmd_variants(args)
    if args.cmd == 'fit':
        payload['fit'] = cmd_fit(args)
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, default=str)
        print('\n已写入 %s' % args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
