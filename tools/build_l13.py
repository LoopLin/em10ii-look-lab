#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_l13.py -- L13: 六轴探针（换入口：派发器 0x604789CF）

L12 的教训：钩 0x604790A2（通用拷贝入口）时机太早、对象含义不固定，开机即死。
L13 换到派发器入口 0x604789CF：
  - 这里 a0 = O（对象），*(O+8) = 标志字，*(O+0x0C) = W（图像上下文）
  - 桩：先给 *(O+8) 置 bit3（0x8，让同一帧走 six-axis 拷贝分支），
        再把 W+0x6C..0x84 六个 dword 写成 0x40004000（满刻度），
        然后重放被覆盖的三条原指令，跳回 0x604789D4
  - 守卫：W==0 时只重放、不写
桩放在 0x605DEDF4（槽 53 空闲区，336 B）。
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
OUT = 'build/L13.BIN'
PATCH = '.tmp/l13.patch'

HOOK = 0x604789CF          # mov a0,a3 / mov (8,a3),a2 / mov (a2),d3
HOOK_LEN = 5
RET = 0x604789D4
STUB = 0x605DEDF4

FILL = 0x40004000

TEXT = '''
\tmov\t(8,a0),a2
\tmov\t(a2),d0
\tor\t8,d0
\tmov\td0,(a2)
\tmov\t(12,a0),a1
\tcmp\t0,a1
\tbeq\tLgo
\tmov\t0x40004000,d0
\tadd\t0x6C,a1
\tmov\td0,(a1)
\tmov\td0,(4,a1)
\tmov\td0,(8,a1)
\tmov\td0,(12,a1)
\tmov\td0,(16,a1)
\tmov\td0,(20,a1)
Lgo:
\tmov\ta0,a3
\tmov\t(8,a3),a2
\tmov\t(a2),d3
'''


def payload_of(path):
    with open(path, 'rb') as f:
        return bytearray(oc.parse(f.read())[0].payload)


def main():
    img = payload_of(SRC)
    stub = b46.assemble(TEXT, 'l13_sixaxis', STUB)
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
