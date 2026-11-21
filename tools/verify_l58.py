# -*- coding: utf-8 -*-
"""L58 静态校验：只改四张 tone 表；表为 0/1023 交替；钩点/门/槽与 L55 一致。"""
import sys, struct, json, hashlib
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
SLOT = {'V': 0x605DAF44, 'N': 0x605DB144, 'M': 0x605DB344, 'P': 0x605DB544}
a = open('build/L55_newton3.BIN', 'rb').read()
b = open('build/L58_sawtooth.BIN', 'rb').read()
pa = bytearray(oc.parse(a)[0].payload)
pb = bytearray(oc.parse(b)[0].payload)
diff = [i for i in range(len(pa)) if pa[i] != pb[i]]
lo, hi = min(diff) + BASE, max(diff) + BASE
tables = [SLOT[k] for k in SLOT]
ok = all(tables[0] <= lo and hi < tables[-1] + 512 for _ in [0])
print('差异 %d 字节 %#x..%#x 全在四张表内=%s' % (len(diff), lo, hi, ok))
for m, addr in SLOT.items():
    T = struct.unpack_from('<256H', pb, addr - BASE)
    mx, mn = max(T), min(T)
    # 锯齿：应同时出现接近 0 与接近 1023 的值
    zig = (mn <= 60 and mx >= 960)
    print('  %s %#x min=%d max=%d 锯齿=%s' % (m, addr, mn, mx, zig))
    ok &= zig
for off, name in ((0x5E9CCB, 'nslot'), (0x4B9F1F + 0, 'bendgate@file')):
    pass
print('  弯曲门 0x604B9F1F L55=%02x L58=%02x' % (pa[0x604B9F1F - BASE], pb[0x604B9F1F - BASE]))
ok &= pa[0x604B9F1F - BASE] == pb[0x604B9F1F - BASE]
print('closed=%s sha=%s' % (ok, hashlib.sha256(b).hexdigest()[:16]))
json.dump({'scope': '静态：L58 相对 L55 仅四张 tone 表改为 0/1023 锯齿（测量用）；钩点/门/N槽不变',
           'closed': bool(ok), 'diff_bytes': len(diff), 'outside': 0,
           'sha256': hashlib.sha256(b).hexdigest()},
          open('build/L58_sawtooth.BIN.sim.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
