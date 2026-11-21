# -*- coding: utf-8 -*-
"""离线牛顿（带 L58 标定索引）：只算，不刷机。输出候选包与预测残差。"""
import sys, json, struct
import numpy as np
sys.path.insert(0, 'tools')
import judge_chart as jc, rawpy, oly_container as oc
from PIL import Image

BASE = 0x60021020
SLOT = {'V': 0x605DAF44, 'N': 0x605DB144, 'M': 0x605DB344, 'P': 0x605DB544}
LOOK = {'V': 'ektachrome_100_vs', 'N': 'elite_color_200',
        'M': 'classic_chrome', 'P': 'pro_neg_hi'}
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


src = 'build/L55_newton3.BIN'
blocks = oc.parse(open(src, 'rb').read())
pay = bytearray(blocks[0].payload)
lines = []
for m in SLOT:
    a, b = CAL[m]
    d = json.load(open('build/looks_final/%s.json' % LOOK[m], encoding='utf-8'))
    Dref = np.array(d['tone256'], float) / 1023.0 * 255.0
    T = np.array(struct.unpack_from('<256H', pay, SLOT[m] - BASE), float)
    # 各 build 在“标定索引”处的 T 与实测 Y
    data = []
    for pkg, stems in BUILDS:
        p = oc.parse(open(pkg, 'rb').read())[0].payload
        Tb = np.array(struct.unpack_from('<256H', p, SLOT[m] - BASE), float)
        n = stems[m]
        ii = idx_gray(D % int(n) + '.ORF', LOOK[m])
        yy = y_gray(D % int(n) + '.JPG')
        data.append(([Tb[min(max(int(round(i * a + b)), 0), 255)] for i in ii], yy))
    cur_idx = idx_gray(D % 188 + '.ORF' if False else D % int({'V': '186', 'N': '187', 'M': '188', 'P': '189'}[m]) + '.ORF', LOOK[m])
    cur_y = y_gray(D % int({'V': '186', 'N': '187', 'M': '188', 'P': '189'}[m]) + '.JPG')
    pred = []
    for c in range(6):
        Ts = np.array([dd[0][c] for dd in data], float)
        Ys = np.array([dd[1][c] for dd in data], float)
        slope = np.polyfit(Ts, Ys, 1)[0] if (Ts.max() - Ts.min()) > 20 else 0.35
        slope = max(slope, 0.10)
        it = min(max(int(round(cur_idx[c] * a + b)), 0), 255)
        target = float(Dref[it])
        dT = (target - cur_y[c]) / slope
        # 直接改标定索引处的表值（锚点取整十）
        i_anchor = int(min(range(0, 256, 10), key=lambda x: abs(x - it)))
        T[i_anchor] = float(np.clip(T[i_anchor] + dT, 0, 1023))
        pred.append(cur_y[c] + slope * dT)
    T[0] = 0.0
    T = np.maximum.accumulate(np.clip(T, 0, 1023))
    T[255] = max(T[254], min(1023.0, T[255]))
    struct.pack_into('<256H', pay, SLOT[m] - BASE, *np.round(T).astype(int))
    lines.append('%s: 当前 %s | 目标 %s | 预测 %s' % (
        m, ' '.join('%.0f' % v for v in cur_y),
        ' '.join('%.0f' % float(Dref[min(max(int(round(i * a + b)), 0), 255)]) for i in cur_idx),
        ' '.join('%.0f' % v for v in pred)))
out = oc.reconstruct([oc.replace_payload(blocks[0], bytes(pay))] + blocks[1:])
open('build/L61_candidate.BIN', 'wb').write(out)
lines.append('候选 build/L61_candidate.BIN（未上卡）')
print('\n'.join(lines))
