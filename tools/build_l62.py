# -*- coding: utf-8 -*-
"""build_l62.py -- L62 = L55 + Vivid 色度 y 轴降饱和（Cb x0.60 / Cr x0.70）

只改 V 档色度块（0x605DEB44, 100 B）的 y[16]+z[17] 两段；N/M/P 逐字节不动。
y 缩放后必须单调不减、z 用 b61.z_chain 重算（z 是 y 的段斜率，不能沿用旧的）。
"""
import sys, struct
sys.path.insert(0, 'tools')
import oly_container as oc
import build_b61 as b61

BASE = 0x60021020
SRC = 'build/L55_newton3.BIN'
OUT = 'build/L62_chromaV.BIN'
V_BLK = b61.CH_ADDRS[0]          # 0x605DEB44
K_CB, K_CR = 0.60, 0.70

blocks = oc.parse(open(SRC, 'rb').read())
pay = bytearray(blocks[0].payload)
off = V_BLK - BASE
blk = bytes(pay[off:off + b61.CH_STRIDE])
assert len(blk) == 100

def seg(ch_off, k):
    x = list(blk[ch_off:ch_off + 16])
    y = list(blk[ch_off + 16:ch_off + 32])
    ny = [0] + [max(1, min(255, int(round(v * k)))) for v in y[1:]]
    for i in range(1, 16):                      # 单调不减
        if ny[i] < ny[i - 1]:
            ny[i] = ny[i - 1]
    z = b61.z_chain(x, ny)
    return bytes(x) + bytes(ny) + bytes(z) + b'\x00', y, ny

cb_new, cb_old, cb_ny = seg(0, K_CB)
cr_new, cr_old, cr_ny = seg(0x32, K_CR)
new = cb_new + cr_new
assert len(new) == 100
pay[off:off + 100] = new
b61.check_block(new, 'L62 V block')

out = oc.reconstruct([oc.replace_payload(blocks[0], bytes(pay))] + blocks[1:])
open(OUT, 'wb').write(out)
import hashlib
print('sha256', hashlib.sha256(out).hexdigest())
print('Cb y:', cb_old)
print('  ->  ', cb_ny)
print('Cr y:', cr_old)
print('  ->  ', cr_ny)
print('diff bytes vs L55:', sum(1 for a, b in zip(blocks[0].payload, pay) if a != b))
