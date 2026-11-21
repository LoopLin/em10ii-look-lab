#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b71.py — B71 HSL 头部指针注入钩的离线验证

静态：钩字节/结构/mask 三项，其余与 B69 逐字节相同。
动态：从 0x604B974D 跑，a0=PB（PB+4=ctx、PB+12=out），在 call 0x604B922D
（0x604B9777）处应 a0=0x605F5A64、a1=out、d0=0x07DF；跑到 0x604B977C 后
out+0x1434..0x1458 应指向 ds 的十张表（按 out 偏移递增顺序）。
"""
from __future__ import annotations
import hashlib, json, struct, sys
sys.path.insert(0, 'tools')
import oly_container as oc
import verify_b32_chain as V

BASE = 0x60021020
B69_PKG = 'build/B69_slot1.BIN'
B71_PKG = 'build/B71_hsl_hook.BIN'
HOOK, HOOK_LEN = 0x604B9751, 21
STRUCT, STRUCT_LEN = 0x605DEDF4, 20
MASK, MASK_LEN = 0x605F6634, 2
DS = 0x605F5A64
FUNC = 0x604B974D
CALL = 0x604B9777
AFTER = 0x604B977C
PB, CTX, OUT = 0x61E10000, 0x61E11000, 0x61E28000
TAB_OFF = [0x150, 0x690, 0x2D0, 0x450, 0x810, 0x990, 0x510, 0xA50, 0x5D0, 0xB10]

def payload_of(p):
    with open(p, 'rb') as f:
        return oc.parse(f.read())[0].payload

def at(buf, addr, n):
    o = addr - BASE
    return bytes(buf[o:o+n])

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else B71_PKG
    img = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    b69 = payload_of(B69_PKG)
    res = {}

    res['hook_bytes'] = at(img, HOOK, HOOK_LEN) == bytes.fromhex('fcddf4ed5d60cc0c00' + 'cb'*12)
    res['struct_bytes'] = at(img, STRUCT, STRUCT_LEN) == bytes.fromhex('f4ed5d60' + '00'*12 + '645a5f60')
    res['mask_bytes'] = at(img, MASK, MASK_LEN) == bytes.fromhex('df07')
    res['after_hook_stock'] = at(img, HOOK+HOOK_LEN, 16) == at(b69, HOOK+HOOK_LEN, 16)
    diffs = [a for a in range(BASE, BASE+len(img)) if img[a-BASE] != b69[a-BASE]]
    # 差异必须只落在三个区间内
    def inside(a):
        return HOOK <= a < HOOK+HOOK_LEN or STRUCT <= a < STRUCT+STRUCT_LEN or MASK <= a < MASK+MASK_LEN
    res['diff_count'] = len(diffs)
    res['diff_only_sites'] = all(inside(a) for a in diffs)

    pokes = [
        (PB+4, CTX.to_bytes(4, 'little')),
        (PB+12, OUT.to_bytes(4, 'little')),
    ]
    d = V.sim(img, FUNC, CALL, limit=20000, pokes=pokes, regs={'A0': PB})
    regs = V.regs_of(d)
    d2 = V.sim(img, FUNC, AFTER, limit=20000, pokes=pokes, regs={'A0': PB})
    res['call_regs'] = [regs.get('A0'), regs.get('A1'), regs.get('D0')]
    res['call_a0_ds'] = regs.get('A0') == DS
    res['call_d0_mask'] = regs.get('D0') == 0x07DF
    ptrs = struct.unpack('<10I', V.mem_bytes(d2, OUT+0x1434, 40))
    res['table_ptrs'] = [hex(x) for x in ptrs]
    res['table_ptrs_ok'] = list(ptrs) == [DS+o for o in TAB_OFF]
    res['fault'] = d2.get('fault') or d.get('fault')

    allok = all(v is True for k, v in res.items() if k not in ('diff_count', 'call_regs', 'table_ptrs', 'fault'))
    rec = {'pkg': str(path), 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b71.py', 'closed': bool(allok), 'checks': res,
           'base': B69_PKG, 'evidence': 'libsim (静态 + 单帧调用)'}
    with open(str(path) + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    for k, v in res.items():
        if k in ('call_regs', 'table_ptrs'): continue
        print('  %-20s %s' % (k, v))
    print('  call a0/a1/d0 =', [hex(x) if isinstance(x, int) else x for x in res['call_regs']])
    print('  sim 记录: %s.sim.json  closed=%s' % (path, allok))
    return 0 if allok else 1

if __name__ == '__main__':
    sys.exit(main())
