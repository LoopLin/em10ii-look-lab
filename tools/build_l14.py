#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_l14.py -- L14: 六轴只读探针（计数门 + 指针门，零写入）

与 L13 的区别：
  * 不置 bit3、不写 W+0x6C（零写入，只记录）；
  * 计数门：自己维护一个帧计数器，前 900 帧完全不动（只重放原指令）；
  * 指针门：W = *(O+0x0C) 必须非 0、4 字节对齐、高字节落在 0x60..0x6F；
  * 通过后把 W+0x6C 起的 72 B（3 条假设的 24 B 记录）抄进 EXIF 文本区 +0x10，
    拍照后从 JPG 的 UserComment 读出。

EXIF 文本区 0x61894C90（117 B）：+0x00 由 SU 桩写 "k=N\\0"，+0x10..+0x57 给本探针，
+0x60 起 4 B 作帧计数器。
"""
from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.chdir(ROOT)

import oly_container as oc          # noqa: E402
import build_b46 as b46             # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/L14.BIN'
PATCH = '.tmp/l14.patch'

HOOK = 0x604789CF          # mov a0,a3 / mov (8,a3),a2 / mov (a2),d3
HOOK_LEN = 5
RET = 0x604789D4
STUB = 0x605DEDF4

COUNTER = 0x61894CF0       # EXIF text +0x60
DUMP = 0x61894CA0          # EXIF text +0x10

TEXT = '''
\tmov\t0x61894CF0,a1
\tmov\t(a1),d0
\tadd\t1,d0
\tmov\td0,(a1)
\tmov\t900,d1
\tcmp\td1,d0
\tblt\tLskip
\tmov\t(12,a0),a1
\tcmp\t0,a1
\tbeq\tLskip
\tmov\ta1,d0
\tand\t3,d0
\tbne\tLskip
\tmov\ta1,d0
\tasr\t24,d0
\tcmp\t0x60,d0
\tblt\tLskip
\tcmp\t0x6F,d0
\tbhi\tLskip
\tmov\t0x61894CA0,a2
\tadd\t0x6C,a1
\tmov\t18,d1
Lcp:
\tmov\t(a1),d0
\tmov\td0,(a2)
\tadd\t4,a1
\tadd\t4,a2
\tadd\t-1,d1
\tcmp\t0,d1
\tbne\tLcp
Lskip:
\tmov\ta0,a3
\tmov\t(8,a3),a2
\tmov\t(a2),d3
'''


def payload_of(path):
    with open(path, 'rb') as f:
        return bytearray(oc.parse(f.read())[0].payload)


def main():
    img = payload_of(SRC)
    stub = b46.assemble(TEXT, 'l14_sixaxis_ro', STUB)
    site = STUB + len(stub)
    stub = stub + b46.jmp_bytes(site, RET)
    if len(stub) > 0x150:
        raise SystemExit('stub too big: %d B' % len(stub))

    hook = b46.jmp_bytes(HOOK, STUB) + b'\x00' * (HOOK_LEN - 5)
    lines = ['ram 0x%x=%s' % (HOOK, hook.hex()),
             'ram 0x%x=%s' % (STUB, stub.hex())]
    io.open(PATCH, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')

    orig = bytes(img[HOOK - BASE:HOOK - BASE + HOOK_LEN])
    print('hook orig', orig.hex())
    print('hook new ', hook.hex())
    print('stub %d B at 0x%X -> 0x%X' % (len(stub), STUB, RET))
    print('patch written to', PATCH)


if __name__ == '__main__':
    main()
