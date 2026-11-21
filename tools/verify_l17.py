#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l16.py -- L16（标准四档六轴探针）静态核验。

L16 = L1 + 5 B 钩 + 138 B 桩。检查：
  1. 钩字节 = 相对 jmp 0x605DEDF4 + 尾部零；
  2. 桩字节等于 build_l16.TEXT 的汇编结果（含尾 jmp 0x604789D4）；
  3. 与 L1 的差异只有两段（钩、桩）；
  4. 槽 52 色度块与 L1 逐字节相同。
写出 build/L16.BIN.sim.json（closed=true），供 flash_gate 使用。
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
import build_l17 as b17             # noqa: E402

BASE = 0x60021020
PKG = b17.OUT
L1 = 'build/L1.BIN'
HOOK, HOOK_LEN, STUB, RET = b17.HOOK, b17.HOOK_LEN, b17.STUB, b17.RET
CHROMA = (0x605DEB44, 0x605DECD3)


def payload(path):
    with open(path, 'rb') as f:
        return bytes(oc.parse(f.read())[0].payload)


def spans(a, b):
    out, i, n = [], 0, len(a)
    while i < n:
        if a[i] == b[i]:
            i += 1
            continue
        j = i
        while j < n and a[j] != b[j]:
            j += 1
        out.append((i, j - i))
        i = j
    return out


def main():
    pkg, base = payload(PKG), payload(L1)
    problems = []

    body = b46.assemble(b17.TEXT, 'l17_band0', STUB)
    site = STUB + len(body)
    exp_stub = body + b46.jmp_bytes(site, RET)
    exp_hook = b46.jmp_bytes(HOOK, STUB) + b'\x00' * (HOOK_LEN - 5)

    if pkg[HOOK - BASE:HOOK - BASE + HOOK_LEN] != exp_hook:
        problems.append('hook bytes mismatch')
    if pkg[STUB - BASE:STUB - BASE + len(exp_stub)] != exp_stub:
        problems.append('stub bytes mismatch')

    df = spans(pkg, base)
    def inside(o, l):
        h = HOOK - BASE <= o and o + l <= HOOK - BASE + HOOK_LEN
        st = STUB - BASE <= o and o + l <= STUB - BASE + len(exp_stub)
        return h or st
    bad = [s for s in df if not inside(*s)]
    if bad:
        problems.append('diff outside hook/stub: %r' % (bad[:6],))

    if pkg[CHROMA[0] - BASE:CHROMA[1] - BASE] != base[CHROMA[0] - BASE:CHROMA[1] - BASE]:
        problems.append('chroma blocks differ from L1')

    out = {
        'pkg': PKG,
        'sha256': hashlib.sha256(open(PKG, 'rb').read()).hexdigest(),
        'base': L1,
        'closed': not problems,
        'checks': [
            {'name': 'hook_bytes', 'ok': not any('hook' in p for p in problems)},
            {'name': 'stub_bytes', 'ok': not any('stub' in p for p in problems)},
            {'name': 'diff_only_hook_stub', 'ok': not any('diff outside' in p for p in problems)},
            {'name': 'chroma_blocks_equal_L1', 'ok': not any('chroma' in p for p in problems)},
        ],
        'diff_spans': [{'off': o, 'len': l, 'ram': hex(BASE + o)} for o, l in df],
        'note': 'libsim does not model ISP six-axis consumption; static closed only',
        'problems': problems,
    }
    io.open(PKG + '.sim.json', 'w', encoding='utf-8', newline='\n').write(
        json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if problems:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
