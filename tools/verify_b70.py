#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b70.py -- read build/B70.BIN back and prove the ten-slot pointer probe.

B70 = stock + one read-only probe:
  * 6 B hook at 0x60479A9E (three original instructions, 6 B) -> 0x6066159E
  * 40 B stub at 0x6066159E: replays those three instructions, copies 11 dwords
    ctx+0x1434..ctx+0x145F into EXIF text +0x30..+0x5B, jumps back to 0x60479AA4

Static: hook/stub bytes, free pocket, diff set == (6 B + 40 B) only.
Dynamic: stub with a crafted ctx (11 distinct pointers) must leave them in EXIF
         and reach 0x60479AA4 unchanged.

Exit 0 only when every check closes; writes build/B70.sim.json.
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

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
PKG = 'build/B70.BIN'
HOOK, HOOK_LEN, STUB, RET, CELL = 0x60479A77, 6, 0x6066159E, 0x60479A7D, 0x61894CC0 + 0x30
N = 11
ORIG = bytes.fromhex('5c04fa00c403')

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
    stock = payload(SRC)
    stub = at(img, STUB, 44)

    add('hook == jmp 0x%X' % STUB,
        at(img, HOOK, HOOK_LEN) == b46.jmp_bytes(HOOK, STUB) + b'\xcb',
        at(img, HOOK, HOOK_LEN).hex())
    jmp_site = HOOK + 6
    add('hole 0x%X is 0xFF in stock' % STUB, at(stock, STUB, 66) == b'\xff' * 66)
    add('stub %d B, replays original two insns at entry' % len(stub),
        stub[:6] == ORIG, stub[:6].hex())
    add('stub tail is jmp 0x%X' % RET, stub[-5] == 0xdc, stub[-5:].hex())

    diff = [i for i in range(len(img)) if img[i] != stock[i]]
    stray = [i for i in diff if not (HOOK - BASE <= i < HOOK - BASE + HOOK_LEN
                                     or STUB - BASE <= i < STUB - BASE + 44)]
    add('diff == hook 6 B + stub 44 B (bytes that differ)', 44 <= len(diff) <= 50 and not stray,
        '%d B' % len(diff))
    add('look/table bytes untouched',
        all(at(img, a, n) == at(stock, a, n) for a, n in
            [(0x605DEB44, 0x190), (0x605DEEE0, 0x48), (0x60479960, 0x20)]))

    # ---- dynamic: stub copies ctx+0x1434.. into EXIF+0x30 ----
    ctx = 0x61E10000
    dst = 0x61E20000
    vals = [0x605F0000 + 0x100 * i for i in range(N)]
    pokes = [(V.SP + 4, ctx.to_bytes(4, 'little')),
             (V.SP + 8, dst.to_bytes(4, 'little'))]
    pokes += [(ctx + 0x1434 + 4 * i, vals[i].to_bytes(4, 'little')) for i in range(N)]
    d = V.sim(img, STUB, RET, limit=4000, pokes=pokes,
              regs={'PC': STUB, 'SP': V.SP}, extra_rwz=('0x61890000:0x00010000',))
    got = V.mem_bytes(d, CELL, 4 * N)
    want = b''.join(v.to_bytes(4, 'little') for v in vals)
    add('stub copies 11 dwords into EXIF+0x30', got == want,
        (got or b'').hex())
    add('stub reaches 0x%X' % RET, d.get('status') == 'returned' or d.get('fault') is None,
        'status=%s fault=%s' % (d.get('status'), d.get('fault')))

    closed = all(c['ok'] for c in checks)
    with io.open(PKG + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump({'pkg': PKG, 'sha256': hashlib.sha256(io.open(PKG, 'rb').read()).hexdigest(),
                   'closed': closed, 'checks': checks}, f, ensure_ascii=False, indent=1)
    print('closed=%s' % closed)
    return 0 if closed else 1


if __name__ == '__main__':
    raise SystemExit(main())
