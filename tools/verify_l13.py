#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l13.py -- static verification for the L13 six-axis probe.

Checks (no libsim: ISP consumption is not modelled):
  1. hook at 0x604789CF is a relative jmp to the stub (5 B, tail zero);
  2. stub bytes equal build_l13 assembly and end with jmp back to 0x604789D4;
  3. package differs from stock in exactly two spans (hook + stub);
  4. everything else, including slot52 chroma area and the rest of slot53,
     is byte-identical to stock.
Emits build/L13.BIN.sim.json (closed=true).
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
import build_l13 as b13             # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
PKG = b13.OUT
HOOK = b13.HOOK
HOOK_LEN = b13.HOOK_LEN
STUB = b13.STUB
RET = b13.RET


def payload(path):
    with open(path, 'rb') as f:
        return bytes(oc.parse(f.read())[0].payload)


def main():
    stock = payload(SRC)
    pkg = payload(PKG)
    problems = []

    # expected stub
    exp_stub = b46.assemble(b13.TEXT, 'l13_sixaxis', STUB)
    site = STUB + len(exp_stub)
    exp_stub = exp_stub + b46.jmp_bytes(site, RET)

    hook = pkg[HOOK - BASE:HOOK - BASE + HOOK_LEN]
    exp_hook = b46.jmp_bytes(HOOK, STUB) + b'\x00' * (HOOK_LEN - 5)
    if hook != exp_hook:
        problems.append('hook bytes mismatch: %s != %s' % (hook.hex(), exp_hook.hex()))
    if pkg[STUB - BASE:STUB - BASE + len(exp_stub)] != exp_stub:
        problems.append('stub bytes mismatch')

    # diff spans
    spans = []
    i = 0
    n = len(stock)
    while i < n:
        if stock[i] != pkg[i]:
            j = i
            while j < n and stock[j] != pkg[j]:
                j += 1
            spans.append((i + BASE, j + BASE))
            i = j
        else:
            i += 1
    want = sorted([(HOOK, HOOK + HOOK_LEN), (STUB, STUB + len(exp_stub))])
    if spans != want:
        problems.append('diff spans %r != %r' % (spans, want))

    # slot52 chroma region unchanged
    if stock[0x605DEB44 - BASE:0x605DECD4 - BASE] != pkg[0x605DEB44 - BASE:0x605DECD4 - BASE]:
        problems.append('slot52 chroma region changed')
    # rest of slot53 (before stub window) unchanged
    if stock[0x605DED44 - BASE:STUB - BASE] != pkg[0x605DED44 - BASE:STUB - BASE]:
        problems.append('slot53 prefix changed')

    sha = hashlib.sha256(open(PKG,'rb').read()).hexdigest()
    out = {
        'pkg': PKG,
        'sha256': sha,
        'verifier': 'tools/verify_l13.py',
        'closed': not problems,
        'problems': problems,
        'checks': [
            'hook_jmp', 'stub_bytes', 'diff_spans', 'slot52_untouched', 'slot53_prefix_untouched',
        ],
        'static': True,
        'note': 'libsim does not model ISP six-axis consumption; static shape only',
    }
    io.open(PKG + '.sim.json', 'w', encoding='utf-8', newline='\n').write(
        json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == '__main__':
    sys.exit(main())
