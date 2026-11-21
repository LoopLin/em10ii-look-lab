#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_l12.py -- L12: 六轴探针（stock + 一个钩 + 39 B 桩）

只做一件事：在 staging 拷贝入口 0x604790A2 处，把 image 结构的六个
六轴 dword（image+0x6C..0x84）全部写成 0x40004000（S4 交接里的满刻度），
然后原样执行被覆盖的三条指令并跳回 0x604790A8。

判据：标准档（Vivid）直出是否出现明显色相/饱和变化。
  - 变了  -> 六轴通道在标准帧上活着，可以继续做"四档分区色相"
  - 没变  -> 该通道对标准帧是死的，六轴这条线到此为止（或只能走 ART）

不写 EXIF、不改任何 look 数据、不碰 tone/矩阵/色度块。
"""
from __future__ import annotations

import hashlib
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
OUT = 'build/L12.BIN'
PATCH = '.tmp/l12.patch'

HOOK = 0x604790A2          # copy: a1 = image, a0 = staging window
HOOK_LEN = 6               # mov a1,d1 / mov a0,d3 / mov a0,(0,SP)
STUB = 0x605DECD4
RET = 0x604790A8

FILL = 0x40004000          # 六轴满刻度（S4: 中性 0x3F00 / 满 0x4000）

TEXT = '''
\tmov\ta1,a2
\tadd\t0x6C,a2
\tmov\t0x40004000,d0
\tmov\td0,(a2)
\tmov\td0,(4,a2)
\tmov\td0,(8,a2)
\tmov\td0,(12,a2)
\tmov\td0,(16,a2)
\tmov\td0,(20,a2)
\tmov\ta1,d1
\tmov\ta0,d3
\tmov\ta0,(0,SP)
\tjmp\t0x0
'''


def payload_of(path):
    with open(path, 'rb') as f:
        return bytearray(oc.parse(f.read())[0].payload)


def main():
    img = payload_of(SRC)
    stub = b46.assemble(TEXT, 'l12_sixaxis', STUB)
    site = STUB + len(stub) - 5
    stub = stub[:-5] + b46.jmp_bytes(site, RET)
    if len(stub) > 0x70:
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
