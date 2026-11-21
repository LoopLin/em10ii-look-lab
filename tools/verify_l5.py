#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l5.py -- read build/L5.BIN back: L4 probe + menu order change."""
import hashlib, io, json, struct, sys
sys.path.insert(0, 'tools')
import oly_container as oc
import verify_b32_chain as V
import build_b46 as b46
import build_b95 as B95

BASE = 0x60021020
PKG = 'build/L5.BIN'
HOOK, STUB, RET, CELL = 0x604B9228, 0x6066159E, 0x604B922A, 0x61894CC0
ORDER = 0x6037F7C0
EXP = [7, 0, 1, 2, 5, 15, 3, 6, 20, 21, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 26, 27]
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
    add('probe stub ends ret [],0x4', stub[-3:] == bytes.fromhex('df0004'))
    got = list(struct.unpack_from('<23I', img, ORDER-BASE))
    add('order array == expected', got == EXP, str(got))
    stock_order = list(struct.unpack_from('<23I', stock, ORDER-BASE))
    add('stock order was original', stock_order[5] == 3 and stock_order[16] == 15, str(stock_order))
    ref = payload('build/B95_looks.BIN')
    same = all(at(img, a, n) == at(ref, a, n) for a, n in
               [(x, 0x200) for x in B95.b54.TONE_ADDRS] +
               [(B95.b54.MX_DATA+18*i, 18) for i in range(4)] +
               [(x, 100) for x in B95.b61.CH_ADDRS])
    add('look data == B95', same)
    obj = {'pkg': PKG, 'sha256': hashlib.sha256(open(PKG, 'rb').read()).hexdigest(),
           'checks': checks, 'closed': all(c['ok'] for c in checks)}
    io.open('build/L5.BIN.sim.json', 'w', encoding='utf-8').write(json.dumps(obj, indent=1, ensure_ascii=False))
    print('closed=%s' % obj['closed'])
    return 0 if obj['closed'] else 1

if __name__ == '__main__':
    sys.exit(main())
