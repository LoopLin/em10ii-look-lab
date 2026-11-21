# -*- coding: utf-8 -*-
"""verify_l62.py -- 静态校验 L62 相对 L55 只改了 V 档色度块的 y/z"""
import sys, json
sys.path.insert(0, 'tools')
import oly_container as oc
import build_b61 as b61

BASE = 0x60021020
A, B = 'build/L55_newton3.BIN', 'build/L62_chromaV.BIN'
pa = oc.parse(open(A, 'rb').read())[0].payload
pb = oc.parse(open(B, 'rb').read())[0].payload
assert len(pa) == len(pb)

diff = [i for i in range(len(pa)) if pa[i] != pb[i]]
lo, hi = min(diff), max(diff)
ok = []

def chk(name, cond):
    ok.append((name, bool(cond)))

blk = b61.CH_ADDRS[0] - BASE
chk('差异全部落在 V 色度块内', blk <= lo and hi < blk + 100)
for i in (1, 2, 3):
    o = b61.CH_ADDRS[i] - BASE
    chk('N/M/P 色度块逐字节相同 #%d' % i, pb[o:o + 100] == pa[o:o + 100])

v = pb[blk:blk + 100]
chk('V 块长度 100', len(v) == 100)
for tag, o in (('Cb', 0), ('Cr', 0x32)):
    y = list(v[o + 16:o + 32])
    chk('%s y 单调不减' % tag, all(y[i] <= y[i + 1] for i in range(15)))
    chk('%s y[0]==0' % tag, y[0] == 0)
    x = list(v[o:o + 16])
    chk('%s x 与 L55 相同' % tag, v[o:o + 16] == pa[blk + o:blk + o + 16])
    z = list(v[o + 32:o + 32 + 17])
    chk('%s z 与重算一致' % tag, z == list(b61.z_chain(x, y)))
try:
    b61.check_block(v, 'L62 V')
    chk('check_block 通过', True)
except SystemExit as e:
    chk('check_block 通过', False)

# 钩子 / 指针表 / tone 表不动
for name, addr, n in (('SU 桩', 0x605DECD4, 69), ('校验桩', 0x605DED1C, 38),
                      ('SD 桩', 0x605DED44, 56), ('矩阵指针表', b61.b54.MX_PTRTAB, 16),
                      ('色度指针表', b61.CH_PTRTAB, 16)):
    o = addr - BASE
    chk('%s 未变' % name, pa[o:o + n] == pb[o:o + n])
chk('尺寸与 L55 相同', len(pa) == len(pb))

bad = [n for n, c in ok if not c]
for n, c in ok:
    print(('[OK ] ' if c else '[ERR] ') + n)
import hashlib
sha = hashlib.sha256(open(B, 'rb').read()).hexdigest()
json.dump({'sha256': sha, 'closed': not bad, 'changed': len(diff), 'range': [hex(lo + BASE), hex(hi + BASE)],
           'scope': '静态：仅 V 色度块 y/z；无 ISP 仿真'},
          open('build/L62_chromaV.BIN.sim.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('closed =', not bad)
sys.exit(1 if bad else 0)
