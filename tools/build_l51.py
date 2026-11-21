# -*- coding: utf-8 -*-
"""L51：相对归一化 —— 让灰阶形状贴合参考 look，白点锚到本机实测白。

目标 D(i) = Ywhite * tone[i] / tone[i_white]（look 的相对形状）
硬件 f_mode：由 L47 灰阶 6 点线性插值（每档一条）
表 T(i) = f^-1(D(i))
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

raw = open('build/L47_nfunnel.BIN', 'rb').read()
blocks = oc.parse(raw)
pay0 = blocks[0].payload
pay = bytearray(pay0)

a = np.asarray(Image.open(r'I:\DCIM\100OLYMP\PB210162.JPG').convert('RGB').resize((960, 720))).astype(float)
fy0, fy1, fx0, fx1 = jc._frame(a)
W, H = fx1 - fx0, fy1 - fy0
px0, px1 = fx0 + 0.065 * W, fx1 - 0.062 * W
py0, py1 = fy0 + 0.018 * H, fy1 - 0.009 * H
geo = (px0, px1, py0, py1)

def gray(jpg):
    im = np.asarray(Image.open(jpg).convert('RGB').resize((960, 720))).astype(float)
    out = []
    for c in range(6):
        cx = px0 + (c + 0.5) * (px1 - px0) / 6
        cy = py0 + 3.5 * (py1 - py0) / 4
        s = 0.05 * (px1 - px0)
        out.append(np.median(im[int(cy - s):int(cy + s) + 1, int(cx - s):int(cx + s) + 1].reshape(-1, 3), axis=0))
    return np.array(out)

for nm, addr, look, stem in TONE:
    jpg = r'I:\DCIM\100OLYMP\PB210%s.JPG' % stem
    orf = r'I:\DCIM\100OLYMP\PB210%s.ORF' % stem
    Ttab = np.array(struct.unpack_from('<256H', pay0, addr - BASE), float)
    d = json.load(open('build/looks_final/%s.json' % look, encoding='utf-8'))
    M = np.array(d['matrix_q8'], float).reshape(3, 3) / 256.0
    tone = np.array(d['tone256'], float)
    gj = gray(jpg)
    with rawpy.imread(orf) as r:
        rgb = r.postprocess(use_camera_wb=True, gamma=(1, 1), no_auto_bright=True,
                            output_bps=16, half_size=True).astype(float) / 65535.0
    Hh, Ww, _ = rgb.shape
    Ts, Ys, iw = [], [], None
    for c in range(6):
        cx = px0 + (c + 0.5) * (px1 - px0) / 6
        cy = py0 + 3.5 * (py1 - py0) / 4
        px, py = int(cx / 960 * Ww), int(cy / 720 * Hh)
        v = np.median(rgb[max(0, py-6):py+6, max(0, px-6):px+6].reshape(-1, 3), axis=0)
        lin = float(np.dot(M @ v, [0.2126, 0.7152, 0.0722]))
        i = int(round(255 * ((1.055 * lin ** (1/2.4) - 0.055) if lin > 0.0031308 else 12.92 * lin)))
        i = max(0, min(255, i))
        if c == 0:
            iw = i
        Ts.append(Ttab[i]); Ys.append(float(np.dot(gj[c], [0.2126, 0.7152, 0.0722])))
    order = np.argsort(Ts)
    Tp, Yp = np.array(Ts)[order], np.array(Ys)[order]
    Ywhite = Yp[-1]
    scale = Ywhite / tone[iw]
    D = np.clip(tone * scale, 0, 255)
    Tnew = np.interp(D, Yp, Tp, left=0.0, right=1023.0)
    Tnew = np.clip(np.round(Tnew), 0, 1023).astype(int)
    for i in range(1, 256):
        if Tnew[i] < Tnew[i-1]:
            Tnew[i] = Tnew[i-1]
    struct.pack_into('<256H', pay, addr - BASE, *Tnew)
    print('%s iw=%d Ywhite=%.1f fT=%s fY=%s -> T[0]=%d T[128]=%d T[255]=%d'
          % (nm, iw, Ywhite, Tp.astype(int).tolist(), np.round(Yp, 0).tolist(), Tnew[0], Tnew[128], Tnew[255]))

blocks[0] = oc.replace_payload(blocks[0], bytes(pay))
out = oc.reconstruct(blocks)
open('build/L51_relnorm.BIN', 'wb').write(out)
print('size', len(out), 'sha', hashlib.sha256(out).hexdigest()[:16])
