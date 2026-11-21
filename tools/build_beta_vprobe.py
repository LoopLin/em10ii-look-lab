# -*- coding: utf-8 -*-
"""B21：把 ctx+0x30 的 u16 编进亮度（v & 7），一次刷机读出四个模式的值。

背景（B20 的教训）：`ctx+0x30` 是 **u16**——写者是
`0x60483852 movhu (322,a0),d1` → `0x60483856 movhu d1,(48,a2)`。
B20 用 32 位比较 0x010100xx，永远不可能命中，所以四个模式都还是原厂表。

B21 改为：`v = movhu(48,ctx)`，`idx = v & 7`，用 8 张亮度不同的表把低 3 位"显示"出来。
一切仍在 tone 池内：t8..t15 放表、t16 放桩与指针数组。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'stock', 'OLY_E_120_1400_0000_0000.BIN')
OUT = os.path.join(ROOT, 'build', 'B21_v09probe.BIN')
PATCH = os.path.join(ROOT, '.tmp', 'B21_v09probe.patch')
STUB_BIN = os.path.join(ROOT, '.tmp', 'b21.bin')

BASE = 0x60021020
TONE = 0x605D8344
STUB = 0x605DA344
ARRAY = 0x605DA3C0
INSERT = 0x60479A27
SLOTS = [8, 9, 10, 11, 12, 13, 14, 15]
S_VALUES = [0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1.05]
GAMMA = 0.62


def sig(s: float) -> np.ndarray:
    v = np.arange(256, dtype=np.float64) / 255.0
    t = np.clip(np.round(1023.0 * np.power(v, GAMMA) * s), 0, 1023).astype(np.int64)
    return np.maximum.accumulate(t)


def main() -> int:
    if os.path.exists(OUT):
        print(f'拒绝覆盖 {OUT}')
        return 1
    stub = open(STUB_BIN, 'rb').read()
    assert len(stub) == 34, len(stub)

    tables = [(TONE + s * 0x200, sig(v), v) for s, v in zip(SLOTS, S_VALUES)]
    array = b''.join(t[0].to_bytes(4, 'little') for t in tables)

    lines = ['# B21 v&7 probe (ASCII only)',
             f'# stub {STUB:#x} ({len(stub)} B), array {ARRAY:#x}, insert {INSERT:#x}']
    for (ram, tab, s) in tables:
        lines.append(f'# table {ram:#x} s={s}')
    for (ram, tab, _s) in tables:
        lines.append(f'ram 0x{ram:08X}={tab.astype("<u2").tobytes().hex()}')
    lines.append(f'ram 0x{ARRAY:08X}={array.hex()}')
    lines.append(f'ram 0x{STUB:08X}={stub.hex()}')
    lines.append(f'ram 0x{INSERT:08X}=' + (b'\xdc' + ((STUB - INSERT) & 0xFFFFFFFF).to_bytes(4, 'little')).hex())
    with open(PATCH, 'w', encoding='ascii') as f:
        f.write('\n'.join(lines) + '\n')

    r = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'mkpatch.py'), SRC,
                        '--out', OUT, '--patch-file', PATCH],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout[-1200:])
    if r.returncode != 0:
        print(r.stderr[-1200:])
        return r.returncode
    h = hashlib.sha256(open(OUT, 'rb').read()).hexdigest()
    print('sha256', h, os.path.getsize(OUT))
    with open(os.path.join(ROOT, '.tmp', 'B21_v09probe.meta.json'), 'w', encoding='utf-8') as f:
        json.dump({'sha256': h, 'stub': hex(STUB), 'array': hex(ARRAY),
                   's': S_VALUES}, f, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
