#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l7.py -- read build/L7.BIN back: L2 + HSL dataset swap at n0.

L2 = L1 (B95 look data, built from stock) + one read-only probe:
  * 5 B hook at 0x604B8F1A (the parameter-group selector entry) -> 0x6066159E
  * 18 B stub at 0x6066159E: replays the three original instructions, then
    writes u32[ctx+8..B] (the mode key) into EXIF text area 0x61894CC0, then
    jumps back to 0x604B8F1F.

Static: hook/stub bytes, free pocket, diff set vs B95 == (5 B hook + 18 B stub),
        and every look byte (tone/matrix/chroma) identical to B95.
Dynamic: run the stub with a crafted ctx, key = 0x0102006B (Pop Art) and
        0x01010074 (Dramatic Tone); the EXIF cell must receive the key and the
        v5 stub must still be reachable (jmp target checked statically).

Writes build/L7.BIN.sim.json; exit 0 only when every check closes.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys

sys.path.insert(0, 'tools')
import oly_container as oc          # noqa: E402
import verify_b32_chain as V        # noqa: E402
import build_b46 as b46             # noqa: E402
import build_b95 as B95             # noqa: E402

BASE = 0x60021020
PKG = 'build/L7.BIN'
REF = 'build/L2.BIN'
HOOK, STUB, RET, CELL = 0x604B8F1A, 0x6066159E, 0x604B8F1F, 0x61894CC0
EXIF_REGION = (0x61890000, 0x618A0000)

checks = []


def add(name, ok, detail=''):
    checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    print(('PASS ' if ok else 'FAIL ') + name + (('  ' + detail) if detail else ''))


def payload(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def at(img, a, n):
    return bytes(img[a - BASE:a - BASE + n])


def main():
    img = payload(PKG)
    ref = payload(REF)
    stock = payload(B95.SRC)

    add('hook == jmp 0x%X' % STUB, at(img, HOOK, 5) == b46.jmp_bytes(HOOK, STUB),
        at(img, HOOK, 5).hex(' '))
    stub = at(img, STUB, 18)
    add('stub 18 B, tail jmp back', stub[-5:] == b46.jmp_bytes(STUB + 13, RET),
        stub.hex(' '))
    add('stub replays original 3 insns', stub[:5] == bytes.fromhex('939a08f17e'))
    add('stub writes EXIF cell', b'\xfc\xdd' in stub and b'\x89\x61' in stub)

    # diff vs B95 == hook + stub only
    diff = [i for i in range(len(img)) if img[i] != ref[i]]
    spans = set()
    for i in diff:
        a = BASE + i
        if HOOK <= a < HOOK + 5:
            spans.add('hook')
        elif STUB <= a < STUB + 18:
            spans.add('stub')
        elif 0x605ED848 <= a < 0x605ED848 + 0xBD4:
            spans.add('hsl')
        else:
            spans.add('other:0x%X' % a)
    add('diff vs L2 == hsl only', spans == {'hsl'}, repr(sorted(spans)))

    # look data identical to B95
    same = True
    for a, n in [(x, 0x200) for x in B95.b54.TONE_ADDRS] + \
                [(B95.b54.MX_DATA + 18 * i, 18) for i in range(4)] + \
                [(x, 100) for x in B95.b61.CH_ADDRS]:
        if at(img, a, n) != at(ref, a, n):
            same = False
            break
    add('n0 dataset == PartialColor n8 (stock 0x605F36E8)', at(img,0x605ED848,0xBD4)==at(stock,0x605F36E8,0xBD4))
    add('look data identical to L2', same)

    # dynamic: run the stub, EXIF cell must receive the key
    ok_all = True
    for key in (0x0102006B, 0x01010074, 0x00000002):
        ctx = 0x61E00000
        pokes = [(ctx + 8, key.to_bytes(4, 'little'))]
        d = V.sim(img, STUB, RET, limit=2000, pokes=pokes, regs={'A0': ctx},
                  extra_rwz=('0x61890000:0x10000',))
        got = V.mem_bytes(d, CELL, 4)
        ok = got == key.to_bytes(4, 'little')
        ok_all &= ok
        add('stub key=0x%08X -> EXIF' % key, ok,
            'status=%s got=%s fault=%s' % (d.get('status'), got.hex(' '),
                                           d.get('fault')))
    add('dynamic all', ok_all)

    obj = {'pkg': PKG, 'sha256': hashlib.sha256(open(PKG, 'rb').read()).hexdigest(),
           'checks': checks, 'closed': all(c['ok'] for c in checks)}
    io.open('build/L7.BIN.sim.json', 'w', encoding='utf-8').write(
        json.dumps(obj, indent=1, ensure_ascii=False))
    print('closed=%s' % obj['closed'])
    return 0 if obj['closed'] else 1


if __name__ == '__main__':
    sys.exit(main())
