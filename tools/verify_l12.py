#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l12.py -- static verification for the L12 six-axis probe.

L12 does not run libsim: the probe changes only the staging copy function and
writes six dwords into the image struct; the emulator does not model the ISP
side that consumes them.  What can be proven offline is the patch shape:

  1. hook at 0x604790A2 is a relative jmp to the stub, tail padded;
  2. the stub bytes are exactly what build_l12 assembles, and its last five
     bytes are a relative jmp back to 0x604790A8;
  3. the whole package differs from stock in exactly two spans (hook + stub);
  4. the rest of tone slot 52 (0x605DEB44..0x605DED44 minus the stub window)
     is byte-identical to stock.

Emits build/L12.BIN.sim.json (closed=true) in the same shape as the other
packages so flash_gate accepts it.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.chdir(ROOT)

import oly_container as oc          # noqa: E402
import build_b46 as b46             # noqa: E402
import build_l12 as b12             # noqa: E402

TEXT = b12.TEXT
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
PKG = 'build/L12.BIN'
HOOK = 0x604790A2
HOOK_LEN = 6
STUB = 0x605DECD4
RET = 0x604790A8

TEXT = b12.TEXT

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
PKG = 'build/L12.BIN'
HOOK = 0x604790A2
HOOK_LEN = 6
STUB = 0x605DECD4
RET = 0x604790A8


def payload(path):
    with open(path, 'rb') as f:
        return bytes(oc.parse(f.read())[0].payload)


def main():
    stock = payload(SRC)
    pkg = payload(PKG)
    checks = []

    stub = b46.assemble(TEXT, 'l12_sixaxis', STUB)
    site = STUB + len(stub) - 5
    stub = stub[:-5] + b46.jmp_bytes(site, RET)
    want_hook = b46.jmp_bytes(HOOK, STUB) + b'\x00' * (HOOK_LEN - 5)

    got_hook = pkg[HOOK - BASE:HOOK - BASE + HOOK_LEN]
    checks.append({'name': 'hook_is_relative_jmp_to_stub', 'ok': got_hook == want_hook,
                   'want': want_hook.hex(), 'got': got_hook.hex()})

    got_stub = pkg[STUB - BASE:STUB - BASE + len(stub)]
    checks.append({'name': 'stub_bytes_match_build', 'ok': got_stub == stub, 'size': len(stub)})
    checks.append({'name': 'stub_tail_jmp_back', 'ok': got_stub[-5:] == b46.jmp_bytes(site, RET)})

    diff = [i for i in range(len(stock)) if stock[i] != pkg[i]]
    spans = []
    for i in diff:
        if spans and i == spans[-1][1]:
            spans[-1][1] = i + 1
        else:
            spans.append([i, i + 1])
    want_spans = [[HOOK - BASE, HOOK - BASE + 5], [STUB - BASE, STUB - BASE + len(stub)]]
    checks.append({'name': 'diff_spans_are_hook_and_stub', 'ok': spans == want_spans,
                   'spans': spans, 'bytes': len(diff)})

    # rest of tone slot 52 untouched
    s52 = (0x605DEB44, 0x605DED44)
    seg_ok = True
    for a, b in ((s52[0], STUB), (STUB + len(stub), s52[1])):
        seg_ok &= stock[a - BASE:b - BASE] == pkg[a - BASE:b - BASE]
    checks.append({'name': 'rest_of_slot52_untouched', 'ok': seg_ok})

    sha = hashlib.sha256(open(PKG, 'rb').read()).hexdigest()
    rec = {'pkg': PKG, 'sha256': sha, 'verifier': 'verify_l12.py',
           'cases': len(checks), 'closed': all(c['ok'] for c in checks),
           'checks': checks,
           'note': 'static only; libsim does not model the ISP consumer of the six-axis coefficients'}
    out = PKG + '.sim.json'
    io.open(out, 'w', encoding='utf-8', newline='\n').write(json.dumps(rec, ensure_ascii=False, indent=2))
    for c in checks:
        print(('PASS' if c['ok'] else 'FAIL'), c['name'])
    print('closed =', rec['closed'], '->', out)


if __name__ == '__main__':
    main()
