# -*- coding: utf-8 -*-
"""用 L58 标定出的索引仿射 + 历次真机数据（L52/L53/L54/L55），
离线拟合每档的 f(T)->Y，再反解四张表，给出预测残差。不刷机。
"""
import sys, json, struct
import numpy as np
sys.path.insert(0, 'tools')
import judge_chart as jc, rawpy, oly_container as oc
from PIL import Image

BASE = 0x60021020
SLOT = {'V': 0x605DAF44, 'N': 0x605DB144, 'M': 0x605DB344, 'P': 0x605DB544}
LOOK = {'V': 'ektachrome_100_vs', 'N': 'elite_color_200',
        'M': 'classic_chrome', 'P': 'pro_neg_hi'}
# L58 标定结果（i_true = a*i + b），来自灰阶秩相关 0.89~0.94
CAL = {'V': (0.90, -19.0), 'N': (0.85, -15.0), 'M': (1.15, -24.0), 'P': (1.55, -20.0)}
BUILDS = [('build/L52_evcal.BIN', {'V': '174', 'N': '175', 'M': '176', 'P': '177'}),
          ('build/L53_newton.BIN', {'V': '178', 'N': '179', 'M': '180', 'P': '181'}),
          ('build/L54_newton2.BIN', {'V': '182', 'N': '183', 'M': '184', 'P': '185'}),
          ('build/L55_newton3.BIN', {'V': '186', 'N': '187', 'M': '188', 'P': '189'})]
D = r'I:\DCIM\100OLYMP\PB21%04d'
bb = jc._frame(np.asarray(Image.open(D % 186 + '.JPG').convert('RGB').resize((960, 720))).astype(float))
fy0, fy1, fx0, fx1 = bb
W, H = fx1 - fx0, fy1 - fy0
PX0, PX1 = fx0 + 0.065 * W, fx1 - 0.062 * W
PY0, PY1 = fy0 + 0.018 * H, fy1 - 0.009 * H


def idx_gray(orf, look):
    d = json.load(open('build/looks_final/%s.json' % look, encoding='utf-8'))
    Mx = np.array(d['matrix_q8'], float).reshape(3, 3) / 256.0
    with rawpy.imread(orf) as r:
        rgb = r.postprocess(use_camera_wb=True, gamma=(1, 1), no_auto_bright=True,
                            output_bps=16, half_size=True).astype(float) / 65535.0
    y = rgb.reshape(-1, 3) @ Mx.T
    s = np.where(y > 0.0031308, 1.055 * np.maximum(y, 0) ** (1 / 2.4) - 0.055, 12.92 * y)
    ix = np.clip(np.round(s * 255), 0, 255).astype(int).reshape(rgb.shape)
    Hh, Ww, _ = rgb.shape
    out = []
    for c in range(6):
        cx = PX0 + (c + 0.5) * (PX1 - PX0) / 6
        cy = PY0 + 3.5 * (PY1 - PY0) / 4
        px, py = int(cx / 960 * Ww), int(cy / 720 * Hh)
        out.append(int(np.median(ix[py - 6:py + 6, px - 6:px + 6, 0])))
    return out


def y_gray(jpg):
    a = np.asarray(Image.open(jpg).convert('RGB').resize((960, 720))).astype(float)
    out = []
    for c in range(6):
        cx = PX0 + (c + 0.5) * (PX1 - PX0) / 6
        cy = PY0 + 3.5 * (PY1 - PY0) / 4
        s = 0.05 * (PX1 - PX0)
        v = np.median(a[int(cy - s):int(cy + s) + 1, int(cx - s):int(cx + s) + 1].reshape(-1, 3), axis=0)
        out.append(float(np.dot(v, [0.2126, 0.7152, 0.0722])))
    return out


lines = []
pairs = {m: [] for m in SLOT}
for pkg, stems in BUILDS:
    pay = oc.parse(open(pkg, 'rb').read())[0].payload
    for m in SLOT:
        T = np.array(struct.unpack_from('<256H', pay, SLOT[m] - BASE), float)
        n = stems[m]
        a, b = CAL[m]
        for i, y in zip(idx_gray(D % int(n) + '.ORF', LOOK[m]), y_gray(D % int(n) + '.JPG')):
            it = int(round(i * a + b))
            it = min(max(it, 0), 255)
            pairs[m].append((float(T[it]), y, it))
        lines.append('%s %s: %s' % (pkg.split('/')[-1][:3], m,
                     ' '.join('%d/%.0f' % (p[2], p[0]) for p in pairs[m][-6:])))

# 每档拟合 f
F = {}
for m in SLOT:
    pts = sorted(pairs[m])
    Tt = np.array([p[0] for p in pts])
    Yt = np.array([p[1] for p in pts])
    bx, by = [], []
    for k in range(0, 1100, 100):
        s = (Tt >= k) & (Tt < k + 100)
        if s.sum() >= 2:
            bx.append(np.median(Tt[s]))
            by.append(np.median(Yt[s]))
    bx = np.array(bx)
    by = np.maximum.accumulate(np.array(by))
    F[m] = (bx, by)
    lines.append('f[%s] 锚点 %d: %s' % (m, len(bx), ' '.join('%.0f:%.0f' % p for p in zip(bx, by))))

# 反解 + 预测
src = 'build/L55_newton3.BIN'
blocks = oc.parse(open(src, 'rb').read())
pay = bytearray(blocks[0].payload)
for m in SLOT:
    bx, by = F[m]
    d = json.load(open('build/looks_final/%s.json' % LOOK[m], encoding='utf-8'))
    Dref = np.array(d['tone256'], float) / 1023.0 * 255.0
    a, b = CAL[m]
    Tn = np.zeros(256)
    for i in range(256):
        it = min(max(int(round(i * a + b)), 0), 255)
        Tn[i] = np.interp(Dref[it], by, bx)
    Tn[0] = 0.0
    Tn = np.maximum.accumulate(np.clip(Tn, 0, 1023))
    struct.pack_into('<256H', pay, SLOT[m] - BASE, *np.round(Tn).astype(int))
    n = {'V': '186', 'N': '187', 'M': '188', 'P': '189'}[m]
    pred, targ = [], []
    for i in idx_gray(D % int(n) + '.ORF', LOOK[m]):
        it = min(max(int(round(i * a + b)), 0), 255)
        pred.append(float(np.interp(Tn[it], bx, by)))
        targ.append(float(Dref[it]))
    lines.append('%s 预测 %s | 目标 %s | 最大差 %.1f' % (
        m, ' '.join('%.0f' % v for v in pred), ' '.join('%.0f' % v for v in targ),
        max(abs(p - t) for p, t in zip(pred, targ))))
out = oc.reconstruct([oc.replace_payload(blocks[0], bytes(pay))] + blocks[1:])
open('build/L60_offline.BIN', 'wb').write(out)
lines.append('候选包 build/L60_offline.BIN（未上卡，仅离线）')
print('\n'.join(lines))
open('docs/analysis/l58-sawtooth.md', 'w', encoding='utf-8').write(
    '# L58 锯齿探针：索引标定与离线候选\n\n> 本文件由 `tools/finish_l58.py` 与后续离线脚本生成。\n\n'
    + '\n'.join('- ' + l for l in lines) + '\n')
