#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l6.py -- read build/L6.BIN back: L2 looks + selector-entry key probe."""
import hashlib, io, json, sys
sys.path.insert(0, 'tools')
import oly_container as oc
import verify_b32_chain as V
import build_b46 as b46
import build_b95 as B95

BASE = 0x60021020
PKG = 'build/L6.BIN'
HOOK, STUB, RET, CELL = 0x604B8F1A, 0x6066159E, 0x604B8F1F, 0x61894CC0
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
    add('stock hook was entry', at(stock, HOOK, 5) == bytes.fromhex('939a08f17e'))
    stub = at(img, STUB, 40)
    add('stub replays entry + writes ctx', (stub[:5]==bytes.fromhex('939a08f17e') and stub[5:11]==bytes.fromhex('fcddc04c8961')), stub.hex(' '))
    ref = payload('build/B95_looks.BIN')
    same = all(at(img, a, n) == at(ref, a, n) for a, n in
               [(x, 0x200) for x in B95.b54.TONE_ADDRS] +
               [(B95.b54.MX_DATA+18*i, 18) for i in range(4)] +
               [(x, 100) for x in B95.b61.CH_ADDRS])
    add('look data == B95', same)
    ok_all = True
    for key_bytes, exp in ((bytes.fromhex('00000100'), 0x00000001),
                           (bytes.fromhex('01037A00'), 0x0103007A),
                           (bytes.fromhex('0101006D'), 0x0101006D)):
        ctx = 0x61890500
        d = V.sim(img, STUB, RET, limit=4000, regs={'A0': ctx},
                  pokes=[(ctx + 8, key_bytes)],
                  extra_rwz=('0x61890000:0x10000',))
        cell = int.from_bytes(V.mem_bytes(d, CELL, 4), 'little')
        got = int.from_bytes(V.mem_bytes(d, CELL+4, 4), 'little')
        ok = (cell == ctx and got == exp)
        ok_all &= ok
        add('key bytes %s -> 0x%08X' % (key_bytes.hex(), got), ok, 'status=%s' % d.get('status'))
    add('dynamic all', ok_all)
    obj = {'pkg': PKG, 'sha256': hashlib.sha256(open(PKG, 'rb').read()).hexdigest(),
           'checks': checks, 'closed': all(c['ok'] for c in checks)}
    io.open('build/L6.BIN.sim.json', 'w', encoding='utf-8').write(json.dumps(obj, indent=1, ensure_ascii=False))
    print('closed=%s' % obj['closed'])
    return 0 if obj['closed'] else 1

if __name__ == '__main__':
    sys.exit(main())
