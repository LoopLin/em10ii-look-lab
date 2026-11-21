#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l4.py -- read build/L4.BIN back: L2 looks + runtime HSL dataset probe."""
import hashlib, io, json, sys
sys.path.insert(0, 'tools')
import oly_container as oc
import verify_b32_chain as V
import build_b46 as b46
import build_b95 as B95

BASE = 0x60021020
PKG = 'build/L4.BIN'
HOOK, STUB, RET, CELL = 0x604B9228, 0x6066159E, 0x604B922A, 0x61894CC0
checks = []

def add(n, ok, d=''):
    checks.append({'check': n, 'ok': bool(ok), 'detail': d})
    print(('PASS ' if ok else 'FAIL ') + n + (('  ' + d) if d else ''))

def payload(p):
    with open(p, 'rb') as f:
        return oc.parse(f.read())[0].payload

def at(img, a, n):
    return bytes(img[a-BASE:a-BASE+n])

def main():
    img = payload(PKG)
    stock = payload(B95.SRC)
    add('hook == jmp stub', at(img, HOOK, 5) == b46.jmp_bytes(HOOK, STUB), at(img, HOOK, 5).hex(' '))
    stub = at(img, STUB, 23)
    add('stub ends ret [],0x4', stub[-3:] == bytes.fromhex('df0004'), stub.hex(' '))
    add('stub writes EXIF cell', stub[:6] == bytes.fromhex('fcddc04c8961'))
    # look data unchanged vs stock-built B95
    ref = payload('build/B95_looks.BIN')
    same = True
    for a, n in [(x, 0x200) for x in B95.b54.TONE_ADDRS] + \
                [(B95.b54.MX_DATA+18*i, 18) for i in range(4)] + \
                [(x, 100) for x in B95.b61.CH_ADDRS]:
        if at(img, a, n) != at(ref, a, n):
            same = False; break
    add('look data == B95', same)
    # dynamic
    ok_all = True
    for key, exp_cell, exp_ds in ((0x00000001, 0x60600CC4, 0x605EE41C),
                                  (0x00000003, 0x60600D2C, 0x605EFBC4)):
        ctx = 0x61E00000
        d = V.sim(img, STUB, RET, limit=20000, regs={'A0': exp_cell},
                  extra_rwz=('0x61890000:0x10000',))
        cell = int.from_bytes(V.mem_bytes(d, CELL, 4), 'little')
        ds = int.from_bytes(V.mem_bytes(d, CELL+4, 4), 'little')
        mask = int.from_bytes(V.mem_bytes(d, CELL+8, 4), 'little')
        ok = (cell == exp_cell and ds == exp_ds)
        ok_all &= ok
        add('key 0x%08X -> cell 0x%08X ds 0x%08X mask 0x%04X' % (key, cell, ds, mask), ok,
            'status=%s fault=%s' % (d.get('status'), d.get('fault')))
    add('dynamic all', ok_all)
    obj = {'pkg': PKG, 'sha256': hashlib.sha256(open(PKG,'rb').read()).hexdigest(),
           'checks': checks, 'closed': all(c['ok'] for c in checks)}
    io.open('build/L4.BIN.sim.json', 'w', encoding='utf-8').write(json.dumps(obj, indent=1, ensure_ascii=False))
    print('closed=%s' % obj['closed'])
    return 0 if obj['closed'] else 1

if __name__ == '__main__':
    sys.exit(main())
