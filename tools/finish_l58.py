# -*- coding: utf-8 -*-
"""L58 锯齿探针的离线收尾：
1) 用灰阶 6 块标定「模型索引 -> 真实索引」的仿射；
2) 用锯齿两端的实际输出拟合 f(T)->Y；
3) 反解四张表，并给出离线预测残差（不再上机）。
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
SHOT = {'V': '196', 'N': '197', 'M': '198', 'P': '199'}
D = r'I:\DCIM\100OLYMP\PB21%04d'
bb = jc._frame(np.asarray(Image.open(D % 186 + '.JPG').convert('RGB').resize((960, 720))).astype(float))
fy0, fy1, fx0, fx1 = bb
W, H = fx1 - fx0, fy1 - fy0
PX0, PX1 = fx0 + 0.065 * W, fx1 - 0.062 * W
PY0, PY1 = fy0 + 0.018 * H, fy1 - 0.009 * H
pay = oc.parse(open('build/L58_sawtooth.BIN', 'rb').read())[0].payload


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


def rank(x):
    return np.argsort(np.argsort(np.asarray(x, float)))


lines = []
cal = {}
for m in 'VNMP':
    ii = np.array(idx_gray(D % int(SHOT[m]) + '.ORF', LOOK[m]), float)
    yy = y_gray(D % int(SHOT[m]) + '.JPG')
    T = np.array(struct.unpack_from('<256H', pay, SLOT[m] - BASE), float)
    best = None
    for a in np.arange(0.8, 1.61, 0.05):
        for b in np.arange(-25, 26, 1):
            idxx = np.clip(np.round(ii * a + b).astype(int), 0, 255)
            r = np.corrcoef(rank(T[idxx]), rank(yy))[0, 1]
            if best is None or r > best[0]:
                best = (r, a, b)
    cal[m] = best
    lines.append('%s 索引仿射: i_true = %.2f*i + %.0f  (灰阶秩相关 %.2f)' % (m, best[1], best[2], best[0]))

# 拟合 f：用四档锯齿的灰阶点，(T_eff, Y)
pts = []
for m in 'VNMP':
    r, a, b = cal[m]
    ii = np.array(idx_gray(D % int(SHOT[m]) + '.ORF', LOOK[m]), float)
    yy = y_gray(D % int(SHOT[m]) + '.JPG')
    T = np.array(struct.unpack_from('<256H', pay, SLOT[m] - BASE), float)
    for i, y in zip(ii, yy):
        it = int(round(i * a + b))
        pts.append((float(T[min(max(it, 0), 255)]), y))
pts.sort()
Tt = np.array([p[0] for p in pts])
Yt = np.array([p[1] for p in pts])
bx, by = [], []
for k in range(0, 1100, 60):
    s = (Tt >= k) & (Tt < k + 60)
    if s.sum() >= 1:
        bx.append(np.median(Tt[s]))
        by.append(np.median(Yt[s]))
bx = np.array(bx)
by = np.maximum.accumulate(np.array(by))
lines.append('f 锚点 %d 个: T %.0f..%.0f -> Y %.0f..%.0f' % (len(bx), bx.min(), bx.max(), by.min(), by.max()))
lines.append('  锚点: ' + ' '.join('%.0f:%.0f' % (a, b) for a, b in zip(bx, by)))

# 反解并预测
out_pay = bytearray(oc.parse(open('build/L58_sawtooth.BIN', 'rb').read())[0].payload)
for m in 'VNMP':
    d = json.load(open('build/looks_final/%s.json' % LOOK[m], encoding='utf-8'))
    Dref = np.array(d['tone256'], float) / 1023.0 * 255.0
    r, a, b = cal[m]
    Tn = np.zeros(256)
    for i in range(256):
        it = min(max(int(round(i * a + b)), 0), 255)
        Tn[i] = np.interp(Dref[it], by, bx)
    Tn[0] = 0.0
    Tn = np.maximum.accumulate(np.clip(Tn, 0, 1023))
    struct.pack_into('<256H', out_pay, SLOT[m] - BASE, *np.round(Tn).astype(int))
    # 预测（用锯齿那批的索引）
    ii = np.array(idx_gray(D % int(SHOT[m]) + '.ORF', LOOK[m]), float)
    pred, targ = [], []
    for i in ii:
        it = min(max(int(round(i * a + b)), 0), 255)
        pred.append(float(np.interp(Tn[it], bx, by)))
        targ.append(float(Dref[it]))
    lines.append('%s 预测 %s | 目标 %s | 最大差 %.1f' % (
        m, ' '.join('%.0f' % v for v in pred), ' '.join('%.0f' % v for v in targ),
        max(abs(p - t) for p, t in zip(pred, targ))))
blocks = oc.parse(open('build/L58_sawtooth.BIN', 'rb').read())
out = oc.reconstruct([oc.replace_payload(blocks[0], bytes(out_pay))] + blocks[1:])
open('build/L59_offline.BIN', 'wb').write(out)
lines.append('候选包 build/L59_offline.BIN（未上卡）')
print('\n'.join(lines))
open('docs/analysis/l58-sawtooth.md', 'w', encoding='utf-8').write('# L58 锯齿探针收尾\n\n' + '\n'.join('- ' + l for l in lines) + '\n')
