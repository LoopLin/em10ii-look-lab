#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l9b.py -- L9b = L1 + 4 bytes: ART2 cell slot0  n0 -> n6.

This is the "repoint an existing cell" experiment:
  cell 0x606012DC is referenced by the ART2 handler (0x604B90D0) and by the
  ten-bank installer (0x604A73E7); slot0 is the dataset pointer.  Nothing in
  the key table changes, no new key is inserted, no stub is added.
"""
from __future__ import annotations

import hashlib
import json
import sys

sys.path.insert(0, 'tools')
import oly_container as oc          # noqa: E402
import verify_b32_chain as V        # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
PKG = 'build/L9b.BIN'
L1 = 'build/L1.BIN'
CELL = 0x606012DC
N6 = 0x605F1F40
CONST = 0x605EC96C
SEL_ENTRY, SEL_STOP = 0x604B8F1A, 0x604B9228
KEY_ART2 = 0x0101006D
KEY_ART10 = 0x01010074

checks = []


def add(name, ok, detail=''):
    checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    print(('PASS ' if ok else 'FAIL ') + name + (('  ' + detail) if detail else ''))


def payload(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def at(img, a, n):
    return bytes(img[a - BASE:a - BASE + n])


def u32(img, a):
    import struct
    return struct.unpack('<I', at(img, a, 4))[0]


def run_key(img, key):
    ctx = 0x61E10000
    kb = bytes([(key >> 24) & 0xFF, (key >> 16) & 0xFF, key & 0xFF, (key >> 8) & 0xFF])
    pokes = [(ctx + 8 + i, kb[i:i + 1]) for i in range(4)]
    pokes += [(ctx + 0x430, b'\x00'), (ctx + 0x427, b'\x00'), (ctx + 0x3C0, b'\x00\x00\x00\x00')]
    d = V.sim(img, SEL_ENTRY, SEL_STOP, limit=20000, pokes=pokes,
              regs={'PC': SEL_ENTRY, 'A0': ctx})
    r = V.regs_of(d)
    return r.get('A0', 0) & 0xFFFFFFFF


def main():
    img = payload(PKG)
    stock = payload(SRC)
    l1 = payload(L1)

    import os
    add('size', os.path.getsize(PKG) == 69599232, str(os.path.getsize(PKG)))
    add('cell slot0 == n6', u32(img, CELL) == N6, hex(u32(img, CELL)))
    add('cell slots 1..12 == const',
        at(img, CELL + 4, 48) == at(stock, CELL + 4, 48),
        at(img, CELL + 4, 48).hex())
    add('handler unchanged (0x604B90CE at slot46)',
        u32(img, 0x60601EC4 + 8 * 46 + 4) == 0x604B90CE,
        hex(u32(img, 0x60601EC4 + 8 * 46 + 4)))
    add('key table unchanged vs stock',
        at(img, 0x60601EC4, 512) == at(stock, 0x60601EC4, 512))

    diff = [i for i in range(0, len(img), 1)
            if img[i] != l1[i]]
    # diff vs L1: only the four bytes at CELL
    lo = CELL - BASE
    only_cell = all(i in range(lo, lo + 4) for i in diff)
    add('diff vs L1 == 4 bytes at cell', len(diff) <= 4 and only_cell,
        'n_diff=%d first=%s' % (len(diff), [hex(BASE + i) for i in diff[:8]]))

    a_art2 = run_key(img, KEY_ART2)
    add('dispatch ART2 key -> cell', a_art2 == CELL, hex(a_art2))
    a_art10 = run_key(img, KEY_ART10)
    add('dispatch Dramatic key -> 0x60601310', a_art10 == 0x60601310, hex(a_art10))
    add('cell deref == n6', u32(img, CELL) == N6, hex(u32(img, CELL)))

    closed = all(c['ok'] for c in checks)
    out = {
        'pkg': PKG,
        'sha256': hashlib.sha256(open(PKG, 'rb').read()).hexdigest(),
        'size': len(img),
        'checks': checks,
        'closed': closed,
        'note': 'data-only change: repoint ART2 cell slot0 to n6; no key/handler/stub touched',
    }
    with open(PKG + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('closed=%s' % closed)
    return 0 if closed else 1


if __name__ == '__main__':
    sys.exit(main())
