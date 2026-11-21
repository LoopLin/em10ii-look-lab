#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b70.py -- B70: read-only probe of the ten HSL slot pointers.

Hook the ISP coefficient uploader right before it copies the ten HSL slots
(0x60479A9E, three instructions, 6 B) and dump ctx+0x1434..ctx+0x145F (11 dwords)
into the EXIF user-comment text area (+0x30..+0x5B).  Nothing is changed in the
image path: the stub replays the three instructions and jumps back.

Reading it: for each of the ten pointers, 0 => slot not installed (flat table),
anything else => the installer wrote that table into dst+0x1400+0x200*i.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.chdir(ROOT)

import oly_container as oc                      # noqa: E402
import build_b46 as b46                         # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/B70.BIN'
PATCH = '.tmp/b70.patch'
LAYOUT = '.tmp/b70_layout.json'

HOOK = 0x60479A77        # mov (4,SP),a0 / mov (964,a0),d0
HOOK_LEN = 6
RET = 0x60479A7D         # original `cmp 0,d0`
STUB = 0x6066159E        # 66 B stock hole (all 0xFF)
EXIF = 0x61894CC0        # user-comment text area
CELL = EXIF + 0x30
N = 11                   # ctx+0x1434 .. ctx+0x145F


def payload_of(path):
    data = io.open(path, 'rb').read()
    return bytearray(oc.parse(data)[0].payload)


def main() -> int:
    stock = payload_of(SRC)

    body = ('\n mov (4,SP),a0\n mov (964,a0),d0\n mov d0,d2\n'
            ' mov a0,a2\n add 0x1434,a2\n'
            ' mov 0x%08x,a1\n add 0x30,a1\n'
            ' mov %d,d0\n'
            'L:\n mov (a2),a3\n mov a3,(a1)\n add 4,a2\n add 4,a1\n'
            ' add -1,d0\n cmp 0,d0\n bne L\n'
            ' mov d2,d0\n mov (4,SP),a0\n'
            % (EXIF, N))
    head = b46.assemble(body, 'b70_body', STUB)
    stub = b46.assemble(body + ' jmp 0x%08x-(0x%08x+%d)\n' % (RET, STUB, len(head)),
                        'b70_stub', STUB)
    # b46.assemble does not know the final length before assembly; re-assemble
    # once to fix the jmp displacement (it is relative to the jmp itself, so a
    # two-pass pass with the measured length is enough).
    n = len(stub)
    body = ('\n mov (4,SP),a0\n mov (964,a0),d0\n mov d0,d2\n'
            ' mov a0,a2\n add 0x1434,a2\n'
            ' mov 0x%08x,a1\n add 0x30,a1\n'
            ' mov %d,d0\n'
            'L:\n mov (a2),a3\n mov a3,(a1)\n add 4,a2\n add 4,a1\n'
            ' add -1,d0\n cmp 0,d0\n bne L\n'
            ' mov d2,d0\n mov (4,SP),a0\n'
            % (EXIF, N))
    head = b46.assemble(body, 'b70_body', STUB)
    stub = b46.assemble(body + ' jmp 0x%08x-(0x%08x+%d)\n' % (RET, STUB, len(head)),
                        'b70_stub', STUB)
    if len(stub) != n:
        raise SystemExit('stub length changed between passes: %d -> %d' % (n, len(stub)))
    if n > 66:
        raise SystemExit('stub is %d B, hole is 66 B' % n)
    if stock[STUB - BASE:STUB - BASE + 66] != b'\xff' * 66:
        raise SystemExit('stub hole is not free in stock')

    hook = b46.jmp_bytes(HOOK, STUB) + b'\xcb'
    if len(hook) != HOOK_LEN:
        raise SystemExit('hook is %d B, site is %d B' % (len(hook), HOOK_LEN))
    orig = bytes(stock[HOOK - BASE:HOOK - BASE + HOOK_LEN])
    if orig != bytes.fromhex('5c04fa00c403'):
        raise SystemExit('hook site bytes changed: %s' % orig.hex())

    io.open(PATCH, 'w', encoding='utf-8', newline='\n').write(
        'ram 0x%x=%s  # B70 probe hook -> 0x%X\n'
        'ram 0x%x=%s  # B70 stub: 11 dwords ctx+0x1434 -> EXIF+0x30\n'
        % (HOOK, hook.hex(), STUB, STUB, stub.hex()))

    if os.path.exists(OUT):
        os.remove(OUT)
    b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT,
             '--patch-file', PATCH, '--omemu-patch', LAYOUT])

    built = payload_of(OUT)
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    ranges = [(HOOK, HOOK_LEN), (STUB, n)]
    stray = [i for i in diff if not any(a - BASE <= i < a - BASE + l for a, l in ranges)]
    if stray:
        raise SystemExit('diff outside patch ranges: %d B, first 0x%X'
                         % (len(stray), BASE + stray[0]))

    print('hook 0x%X -> 0x%X : %s' % (HOOK, STUB, hook.hex()))
    print('stub %d B: %s' % (n, stub.hex()))
    print('diff %d B: hook %d + stub %d' % (len(diff), HOOK_LEN, n))
    print('BIN %d B sha256=%s' % (len(io.open(OUT, 'rb').read()),
                                  hashlib.sha256(io.open(OUT, 'rb').read()).hexdigest()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
