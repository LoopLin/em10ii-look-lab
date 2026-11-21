#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_l16.py -- L16: 标准四档六轴探针（每档一个可见色相偏移）

同 L15 的入口 0x604789CF / 桩区 0x605DEDF4，不额外占表：
  * 用 SD 桩写在 0x605DED70 的 32 位 key 算 k（与 SU 桩同一公式）；
  * k=1..4 分别写 hue = -45 / 0 / +45 / +90 度，sat=0；
  * 6 个色相带都写同一个值（整幅旋转），便于肉眼与统计判读。
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

SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/L16.BIN'
PATCH = '.tmp/l16.patch'

HOOK = 0x604789CF
HOOK_LEN = 5
RET = 0x604789D4
STUB = 0x605DEE4C
STASH = 0x605DED70
EXIF6 = 0x61894CA0

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
\tmov\t0x605DED70,a2
\tmov\t(a2),d0
\tmov\td0,d2
\tand\t7,d0
\tasr\t21,d2
\tand\t0x18,d2
\tor\td2,d0
\tadd\t-1,d0
\tcmp\t3,d0
\tbhi\tLskip
\tcmp\t0,d0
\tbeq\tH0
\tcmp\t1,d0
\tbeq\tH1
\tcmp\t2,d0
\tbeq\tH2
\tmov\t90,d0
\tbra\tHw
H0:
\tmov\t-45,d0
\tbra\tHw
H1:
\tmov\t0,d0
\tbra\tHw
H2:
\tmov\t45,d0
Hw:
\tadd\t0x6C,a1
\tmov\t6,d1
Lw:
\tmov\td0,(a1)
\tclr\td2
\tmov\td2,(2,a1)
\tadd\t4,a1
\tadd\t-1,d1
\tcmp\t0,d1
\tbne\tLw
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
    body = b46.assemble(TEXT, 'l16_sixaxis4', STUB)
    site = STUB + len(body)
    stub = body + b46.jmp_bytes(site, RET)
    if len(stub) > 0x150:
        raise SystemExit('stub too big: %d B' % len(stub))
    hook = b46.jmp_bytes(HOOK, STUB) + b'\x00' * (HOOK_LEN - 5)
    lines = ['ram 0x%x=%s' % (HOOK, hook.hex()),
             'ram 0x%x=%s' % (STUB, stub.hex())]
    io.open(PATCH, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')
    print('hook new ', hook.hex())
    print('stub %d B at 0x%X -> 0x%X' % (len(stub), STUB, RET))


if __name__ == '__main__':
    main()
