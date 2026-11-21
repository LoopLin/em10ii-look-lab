#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l8.py -- read build/L9a.BIN back and prove the ART1/ART2 routing probe.

L9a = L1 (four standard looks + UI names) + routing test only:
  * key table slot 38 : key 0x01020064 (Pop Art / ART1) -> stub 0x6066159E
  * key table slot 46 : handler 0x604B90CE -> stub 0x606615A9 (Soft Focus / ART2)
  * two 13 x u32 blocks in the 982 B hole at 0x60672C20 / 0x60672C54,
    slot0 = n1 (0x605EE41C = default flat dataset), slots 1..12 = 0x605EC96C
No dataset bytes, no predicate, no ISP code touched.

Expected on the real camera: ART1 and ART2 both render the Partial Color look
(large grey areas), proving per-mode routing; everything else unchanged.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys

sys.path.insert(0, 'tools')
import oly_container as oc          # noqa: E402
import verify_b32_chain as V        # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
PKG = 'build/L9a.BIN'
KEYTAB = 0x60601EC4
SLOT38 = KEYTAB + 8 * 38
SLOT46 = KEYTAB + 8 * 46
STUB1, STUB2 = 0x6066159E, 0x606615A9
CELL1, CELL2 = 0x60672C20, 0x60672C54
N8 = 0x605EE41C
CONST = 0x605EC96C
FILLER = 0x605EC96C
SEL_ENTRY, SEL_STOP = 0x604B8F1A, 0x604B9228

checks = []


def add(name, ok, detail=''):
    checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    print(('PASS ' if ok else 'FAIL ') + name + (('  ' + detail) if detail else ''))


def payload(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def at(img, a, n):
    return bytes(img[a - BASE:a - BASE + n])


def run_key(img, key):
    ctx = 0x61E10000
    # selector builds key = b0<<24 | b1<<16 | u16LE(+2); invert that
    kb = bytes([(key >> 24) & 0xFF, (key >> 16) & 0xFF, key & 0xFF, (key >> 8) & 0xFF])
    pokes = [(ctx + 8 + i, kb[i:i+1]) for i in range(4)]
    pokes += [(ctx + 0x430, b'\x00'), (ctx + 0x427, b'\x00'), (ctx + 0x3C0, b'\x00\x00\x00\x00')]
    d = V.sim(img, SEL_ENTRY, SEL_STOP, limit=20000, pokes=pokes,
              regs={'PC': SEL_ENTRY, 'A0': ctx})
    r = V.regs_of(d)
    return d, r.get('A0', 0) & 0xFFFFFFFF


def main():
    img = payload(PKG)
    stock = payload(SRC)

    add('slot38 key == 0x01020064', at(img, SLOT38, 4) == bytes.fromhex('64000201'), at(img, SLOT38, 4).hex())
    add('slot38 handler -> stub1', at(img, SLOT38 + 4, 4) == STUB1.to_bytes(4, 'little'))
    add('slot46 key still 0x0101006D', at(img, SLOT46, 4) == bytes.fromhex('6d000101'))
    add('slot46 handler -> stub2', at(img, SLOT46 + 4, 4) == STUB2.to_bytes(4, 'little'), at(img, SLOT46 + 4, 4).hex())

    s1 = at(img, STUB1, 11)
    s2 = at(img, STUB2, 11)
    add('stub1 = mov cell1,a0 ; jmp 0x604B91B6',
        s1[:6] == b'\xfc\xdc' + CELL1.to_bytes(4, 'little') and s1[6] == 0xdc, s1.hex())
    add('stub2 = mov cell2,a0 ; jmp 0x604B91B6',
        s2[:6] == b'\xfc\xdc' + CELL2.to_bytes(4, 'little') and s2[6] == 0xdc, s2.hex())
    for tag, c in (('cell1', CELL1), ('cell2', CELL2)):
        body = at(img, c, 52)
        want = N8.to_bytes(4, 'little') + FILLER.to_bytes(4, 'little') * 12
        add('%s = n8 + 12 x 0x%X' % (tag, FILLER), body == want, body[:8].hex())

    add('hole 0x60672C20 was 0xFF in stock', at(stock, CELL1, 104) == b'\xff' * 104)
    add('slot46 old handler was 0x604B90CE', at(stock, SLOT46 + 4, 4) == (0x604B90CE).to_bytes(4, 'little'))
    add('slot38 was empty in stock', at(stock, SLOT38, 8) == b'\x00' * 4 + (0x604B9186).to_bytes(4, 'little'))

    # ---- dynamic: three keys through the real selector ----
    for key, want, tag in ((0x01020064, CELL1, 'ART1 Pop Art'),
                           (0x0101006D, CELL2, 'ART2 Soft Focus'),
                           (0x01020066, 0x60600BC0, 'ART3 Pale&Light (control)')):
        d, a0 = run_key(img, key)
        add('key 0x%08X (%s) -> a0=0x%X' % (key, tag, want), a0 == want,
            'a0=0x%X fault=%s' % (a0, d.get('fault')))

    # the routed cell must dereference to n8
    for tag, c in (('cell1', CELL1), ('cell2', CELL2)):
        add('%s deref == n1' % tag, at(img, c, 4) == N8.to_bytes(4, 'little'))

    closed = all(c['ok'] for c in checks)
    with io.open(PKG + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump({'pkg': PKG, 'sha256': hashlib.sha256(io.open(PKG, 'rb').read()).hexdigest(),
                   'closed': closed, 'checks': checks}, f, ensure_ascii=False, indent=1)
    print('closed=%s' % closed)
    return 0 if closed else 1


if __name__ == '__main__':
    raise SystemExit(main())
