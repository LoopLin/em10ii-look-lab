#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_l15.py -- L15: 六轴写入探针（同一派发入口，置 bit3 + 写 24 B 记录）

L14 已证明：入口 0x604789CF 安全；W+0x6C..0x84 是 6 个色相带 x (hue,sat) 的
24 B 运行时记录；写在这条记录上的值会在同一帧被 staging 拷走。

L15 在 L14 的门之后多做两件事：
  * 给 *(O+8) 置 bit3，让 six-axis 拷贝/上传分支在同一帧执行；
  * 把 24 B 模板（放在 0x605DEF00）拷到 W+0x6C。
仍把写入后的记录 dump 到 EXIF +0x10，留证。
"""
from __future__ import annotations

import io
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.chdir(ROOT)

import oly_container as oc          # noqa: E402
import build_b46 as b46             # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/L15.BIN'
PATCH = '.tmp/l15.patch'

HOOK = 0x604789CF
HOOK_LEN = 5
RET = 0x604789D4
STUB = 0x605DEDF4
TEMPLATE = 0x605DEF00

# 6 个色相带 x (hue, sat)；先只动第 1 带，值取 96（默认约 18~24）
TPL = struct.pack('<12h', 96, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

TEXT = '''
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
\tmov\t(8,a0),a2
\tmov\t(a2),d0
\tor\t8,d0
\tmov\td0,(a2)
\tmov\t0x605DEF00,a3
\tadd\t0x6C,a1
\tmov\t6,d1
Lcp:
\tmov\t(a3),d0
\tmov\td0,(a1)
\tadd\t4,a3
\tadd\t4,a1
\tadd\t-1,d1
\tcmp\t0,d1
\tbne\tLcp
\tmov\t(12,a0),a1
\tadd\t0x6C,a1
\tmov\t0x61894CA0,a2
\tmov\t6,d1
Ldump:
\tmov\t(a1),d0
\tmov\td0,(a2)
\tadd\t4,a1
\tadd\t4,a2
\tadd\t-1,d1
\tcmp\t0,d1
\tbne\tLdump
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
    body = b46.assemble(TEXT, 'l15_sixaxis_w', STUB)
    site = STUB + len(body)
    stub = body + b46.jmp_bytes(site, RET)
    if len(stub) > 0x100:
        raise SystemExit('stub too big: %d B' % len(stub))
    if STUB + len(stub) > TEMPLATE:
        raise SystemExit('stub overlaps template')

    hook = b46.jmp_bytes(HOOK, STUB) + b'\x00' * (HOOK_LEN - 5)
    lines = ['ram 0x%x=%s' % (HOOK, hook.hex()),
             'ram 0x%x=%s' % (STUB, stub.hex()),
             'ram 0x%x=%s' % (TEMPLATE, TPL.hex())]
    io.open(PATCH, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')

    print('hook new ', hook.hex())
    print('stub %d B at 0x%X -> 0x%X' % (len(stub), STUB, RET))
    print('template', TPL.hex(), 'at 0x%X' % TEMPLATE)


if __name__ == '__main__':
    main()
