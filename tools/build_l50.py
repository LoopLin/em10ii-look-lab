# -*- coding: utf-8 -*-
"""L50：按「每档自己的实测 (T,out) 点」反解目标显示值。

数据：L47 四张（162-165）的 ORF+JPEG —— 已知该档表 T，实测出片 out。
做法：对每档拟合单调 f_mode(T)->Y（线性插值），再对参考 look 的 tone 目标做 f^-1。
"""
import sys, json, struct, hashlib
import numpy as np
sys.path.insert(0, 'tools')
import oly_container as oc, judge_chart as jc, rawpy
from PIL import Image

BASE = 0x60021020
TONE = [('V', 0x605DAF44, 'ektachrome_100_vs', '162'),
        ('N', 0x605DB144, 'elite_color_200',     '163'),
        ('M', 0x605DB344, 'classic_chrome',      '164'),
        ('P', 0x605DB544, 'pro_neg_hi',          '165')]
MAT = {'V': 0, 'N': 1, 'M': 2, 'P': 3}

raw = open('build/L47_nfunnel.BIN', 'rb').read()
blocks = oc.parse(raw)
pay0 = blocks[0].payload

def patch_geo(jpg):
    a = np.asarray(Image.open(jpg).convert('RGB').resize((960, 720))).astype(float)
    return jc._frame(a)

def samples(jpg, bbox):
    a = np.asarray(Image.open(jpg).convert('RGB').resize((960, 720))).astype(float)
    fy0, fy1, fx0, fx1 = bbox
    W, Hh = fx1 - fx0, fy1 - fy0
    px0, px1 = fx0 + 0.065 * W, fx1 - 0.062 * W
    py0, py1 = fy0 + 0.018 * Hh, fy1 - 0.009 * Hh
    out = []
    for r in range(4):
        row = []
        for c in range(6):
            cx = px0 + (c + 0.5) * (px1 - px0) / 6
            cy = py0 + (r + 0.5) * (py1 - py0) / 4
            s = 0.05 * (px1 - px0)
            p = a[int(cy - s):int(cy + s) + 1, int(cx - s):int(cx + s) + 1].reshape(-1, 3)
            row.append(np.median(p, axis=0))
        out.append(row)
    return np.array(out), (px0, px1, py0, py1)

bbox = patch_geo(r'I:\DCIM\100OLYMP\PB210162.JPG')
pay = bytearray(pay0)
for nm, addr, look, stem in TONE:
    jpg = r'I:\DCIM\100OLYMP\PB210%s.JPG' % stem
    orf = r'I:\DCIM\100OLYMP\PB210%s.ORF' % stem
    gj, geo = samples(jpg, bbox)
    Ttab = np.array(struct.unpack_from('<256H', pay0, addr - BASE), float)
    d = json.load(open('build/looks_final/%s.json' % look, encoding='utf-8'))
    mq = d['matrix_q8']; M = np.array(mq, float).reshape(3, 3) / 256.0
    tone = np.array(d['tone256'], float)
    with rawpy.imread(orf) as r:
        rgb = r.postprocess(use_camera_wb=True, gamma=(1, 1), no_auto_bright=True,
                            output_bps=16, half_size=True).astype(float) / 65535.0
    Hh, Ww, _ = rgb.shape
    Ts, Ys = [], []
    for rr in range(3, 4):
        for cc in range(6):
            cx = geo[0] + (geo[1] - geo[0]) * (cc + 0.5) / 6
            cy = geo[2] + (geo[3] - geo[2]) * (rr + 0.5) / 4
            px, py = int(cx / 960 * Ww), int(cy / 720 * Hh)
            v = np.median(rgb[max(0, py-6):py+6, max(0, px-6):px+6].reshape(-1, 3), axis=0)
            y = M @ v
            lin = float(np.dot(y, [0.2126, 0.7152, 0.0722]))
            i = int(round(255 * (1.055 * max(lin, 0) ** (1 / 2.4) - 0.055 if lin > 0.0031308 else 12.92 * lin)))
            i = max(0, min(255, i))
            outY = float(np.dot(gj[rr, cc], [0.2126, 0.7152, 0.0722]))
            Ts.append(Ttab[i]); Ys.append(outY)
    order = np.argsort(Ts)
    Tp = np.array(Ts)[order]; Yp = np.array(Ys)[order]
    # 去重（同 T 取均值）
    uT, inv = np.unique(np.round(Tp), return_inverse=True)
    uY = np.array([Yp[inv == k].mean() for k in range(len(uT))])
    D8 = tone / 1023.0 * 255.0
    Tnew = np.interp(D8, uY, uT, left=0.0, right=1023.0)
    Tnew = np.clip(np.round(Tnew), 0, 1023).astype(int)
    for i in range(1, 256):
        if Tnew[i] < Tnew[i - 1]:
            Tnew[i] = Tnew[i - 1]
    struct.pack_into('<256H', pay, addr - BASE, *Tnew)
    print('%s f points T=%s Y=%s' % (nm, uT.astype(int).tolist(), np.round(uY, 0).tolist()))
    print('   new T[0]=%d T[128]=%d T[255]=%d' % (Tnew[0], Tnew[128], Tnew[255]))

blocks[0] = oc.replace_payload(blocks[0], bytes(pay))
out = oc.reconstruct(blocks)
open('build/L50_permode.BIN', 'wb').write(out)
print('size', len(out), 'sha', hashlib.sha256(out).hexdigest()[:16])
