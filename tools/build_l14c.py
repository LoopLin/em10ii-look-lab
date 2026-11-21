#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_l14c.py -- L14c: 最小重放探针（隔离开关：入口 vs 落点 vs 代码）

只做一件事：在派发器入口 0x604789CF 处 jmp 到桩，桩里原样执行被覆盖的
三条指令，立刻跳回 0x604789D4。不读内存、不写内存、不用计数器。

用途：如果这一版能正常开机，说明“入口 0x604789CF 本身可用”，L14 的失败来自
计数器/导出那段代码或它的落点（槽 53）；如果这一版仍开机死，说明这个入口
根本不能碰（哪怕只重放），六轴判死。

桩放在 0x605DECD4（B63/B65/B66/L8 都用过的已验证代码落点），不用槽 53。
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
OUT = 'build/L14c.BIN'
PATCH = '.tmp/l14c.patch'

HOOK = 0x604789CF
HOOK_LEN = 5
RET = 0x604789D4
STUB = 0x605DECD4          # 已验证代码落点（非槽 53）

TEXT = '''
\tmov\ta0,a3
\tmov\t(8,a3),a2
\tmov\t(a2),d3
'''


def payload_of(path):
    with open(path, 'rb') as f:
        return bytearray(oc.parse(f.read())[0].payload)


def main():
    img = payload_of(SRC)
    stub = b46.assemble(TEXT, 'l14c', STUB)
    site = STUB + len(stub)
    stub = stub + b46.jmp_bytes(site, RET)
    if len(stub) > 0x150:
        raise SystemExit('stub too big')

    hook = b46.jmp_bytes(HOOK, STUB) + b'\x00' * (HOOK_LEN - 5)
    lines = ['ram 0x%x=%s' % (HOOK, hook.hex()),
             'ram 0x%x=%s' % (STUB, stub.hex())]
    io.open(PATCH, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')

    print('hook orig', bytes(img[HOOK - BASE:HOOK - BASE + HOOK_LEN]).hex())
    print('hook new ', hook.hex())
    print('stub %d B at 0x%X -> 0x%X' % (len(stub), STUB, RET))


if __name__ == '__main__':
    main()
