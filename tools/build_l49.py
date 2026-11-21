# -*- coding: utf-8 -*-
"""L49 = L47 + 四张 tone 表按「参考 look 目标 + 实测硬件曲线 f」逐点反解。

目标：D8(i) = tone256[i]/1023*255（参考 look 的显示值）
硬件：f(T) = JPEG Y，取自 L24 恒等表探针实测点（H）
表：  T(i) = f^-1(D8(i))，单调化后写入
"""
import sys, json, struct, hashlib
import numpy as np
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
TONE = [('V', 0x605DAF44, 'ektachrome_100_vs'),
        ('N', 0x605DB144, 'elite_color_200'),
        ('M', 0x605DB344, 'classic_chrome'),
        ('P', 0x605DB544, 'pro_neg_hi')]

# L24 恒等表探针实测 (T -> JPEG Y)
H = np.array([(0, 0), (224, 36), (320, 67), (416, 105), (516, 141),
              (564, 170), (640, 174), (1023, 214)], float)

def finv(d):
    """显示值 -> 表码（分三段线性反查，端点外钳位）。"""
    t, y = H[:, 0], H[:, 1]
    return np.interp(d, y, t, left=0.0, right=1023.0)

raw = open('build/L47_nfunnel.BIN', 'rb').read()
blocks = oc.parse(raw)
pay = bytearray(blocks[0].payload)

for nm, addr, look in TONE:
    d = json.load(open('build/looks_final/%s.json' % look, encoding='utf-8'))
    tone = np.array(d['tone256'], float)          # 0..1023, 256 项
    D8 = tone / 1023.0 * 255.0                    # 目标显示值
    T = np.array([finv(v) for v in D8])
    T = np.clip(np.round(T), 0, 1023).astype(int)
    for i in range(1, 256):
        if T[i] < T[i - 1]:
            T[i] = T[i - 1]
    off = addr - BASE
    struct.pack_into('<256H', pay, off, *T)
    print('%s T[0]=%d T[32]=%d T[128]=%d T[192]=%d T[255]=%d  (D8 0/128/255 = %.0f/%.0f/%.0f)'
          % (nm, T[0], T[32], T[128], T[192], T[255], D8[0], D8[128], D8[255]))

blocks[0] = oc.replace_payload(blocks[0], bytes(pay))
out = oc.reconstruct(blocks)
open('build/L49_fitfill.BIN', 'wb').write(out)
print('size', len(out), 'sha', hashlib.sha256(out).hexdigest()[:16])
