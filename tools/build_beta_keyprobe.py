# -*- coding: utf-8 -*-
"""B20：filter-id 探针（hook 版，全部落在 tone 池内）。

目的：一次刷机确定「四个标准画面模式各自的 filter id（ctx+0x30）」。

做法：
- `0x60479A27`（tone 指针读取处，5 字节 `add 0x1230,a0 / mov (a0),d0`）改成远跳 `DC disp32`；
- 桩放在 tone 池 t16（`0x605DB344`），**直接复用 B16 里模拟器验证过的 66 B 桩体**，
  只重定位其中两个绝对地址（(key,table) 表首、表尾）；
- 8 张探针表放 t8..t15，增益 `s = 0.35 + 0.10*i`（EV+2 灰卡上相邻档差约 12 个 8bit 级）。

t8..t16 原厂只被 ART 目录（dir4–dir6）引用，标准档不读；代价是 ART 那几档颜色会坏。
整包只写 tone 池 + 一处 5 字节代码：不碰 bss、不碰段尾填充、不写 ctx。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'stock', 'OLY_E_120_1400_0000_0000.BIN')
OUT = os.path.join(ROOT, 'build', 'B20_keyprobe.BIN')
PATCH_FILE = os.path.join(ROOT, '.tmp', 'B20_keyprobe.patch')
META = os.path.join(ROOT, '.tmp', 'B20_keyprobe.meta.json')
B16_PATCH = os.path.join(ROOT, '.tmp', 'B16_hook_slot53.patch')

BASE = 0x60021020
TONE_BASE = 0x605D8344
TONE_STEP = 0x200
INSERT = 0x60479A27
STUB_SLOT = 16
STUB = TONE_BASE + STUB_SLOT * TONE_STEP
PAIRS = STUB + 0x80   # 桩占 66 B，留到 0x80 再放表
LOOK_SLOTS = [8, 9, 10, 11, 12, 13, 14, 15]
GAMMA = 0.62

KEYS = [0x01010066, 0x02000096, 0x01010067, 0x01010079,
        0x01020079, 0x0101006B, 0x0102006B, 0x01030079]


def b16_stub() -> bytes:
    for line in open(B16_PATCH, encoding='ascii'):
        m = re.match(r'ram 0x605ded44=([0-9a-f]+)', line.strip(), re.I)
        if m:
            return bytes.fromhex(m.group(1))
    raise SystemExit('B16 stub not found')


def relocate(stub: bytes) -> bytes:
    """把桩里两条 `mov <imm32>,aN` 的 disp32 换成新地址。"""
    b = bytearray(stub)
    # mov 0x605ded9c,a2  -> FC DE + disp32 at offset 9
    assert b[7:9] == b'\xfc\xde', b[7:9].hex()
    b[9:13] = PAIRS.to_bytes(4, 'little')
    # mov 0x605dedbc,a0  -> FC DC + disp32 at offset 15
    assert b[13:15] == b'\xfc\xdc', b[13:15].hex()
    b[15:19] = (PAIRS + 8 * len(KEYS)).to_bytes(4, 'little')
    # 两条 DC jmp 的 disp32 也依赖桩的落点，必须重算（基准＝指令起始地址）
    for off, target in ((43, 0x60479A32), (61, 0x60479A3A)):
        assert b[off] == 0xDC, (off, hex(b[off]))
        disp = target - (STUB + off)
        b[off + 1:off + 5] = int(disp).to_bytes(4, 'little', signed=True)
    return bytes(b)


def sig(s: float) -> np.ndarray:
    v = np.arange(256, dtype=np.float64) / 255.0
    t = np.clip(np.round(1023.0 * np.power(v, GAMMA) * s), 0, 1023).astype(np.int64)
    return np.maximum.accumulate(t)


def main() -> int:
    if os.path.exists(OUT):
        print(f'拒绝覆盖已存在的 {OUT}')
        return 1

    tables = []
    for i, slot in enumerate(LOOK_SLOTS):
        s = round(0.35 + 0.10 * i, 3)
        tables.append((TONE_BASE + slot * TONE_STEP, sig(s), s))

    pairs = b''.join(k.to_bytes(4, 'little') + t[0].to_bytes(4, 'little')
                     for k, t in zip(KEYS, tables))
    stub = relocate(b16_stub())
    insert = b'\xdc' + ((STUB - INSERT) & 0xFFFFFFFF).to_bytes(4, 'little')

    lines = ['# B20 key probe (ASCII only)',
             f'# stub {STUB:#010x} len {len(stub)}, pairs {PAIRS:#010x} len {len(pairs)}',
             f'# insert {INSERT:#010x} = {insert.hex()}']
    for (ram, _tab, s) in tables:
        lines.append(f'# table {ram:#010x} s={s}')
    for (ram, tab, _s) in tables:
        lines.append(f'ram 0x{ram:08X}={tab.astype("<u2").tobytes().hex()}')
    lines.append(f'ram 0x{PAIRS:08X}={pairs.hex()}')
    lines.append(f'ram 0x{STUB:08X}={stub.hex()}')
    lines.append(f'ram 0x{INSERT:08X}={insert.hex()}')
    with open(PATCH_FILE, 'w', encoding='ascii') as f:
        f.write('\n'.join(lines) + '\n')

    r = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'mkpatch.py'), SRC,
                        '--out', OUT, '--patch-file', PATCH_FILE],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print(r.stderr[-2000:])
        return r.returncode

    h = hashlib.sha256(open(OUT, 'rb').read()).hexdigest()
    meta = {'output': OUT, 'sha256': h, 'size': os.path.getsize(OUT),
            'stub': hex(STUB), 'stub_len': len(stub), 'pairs': hex(PAIRS),
            'keys': [hex(k) for k in KEYS],
            'tables': [{'slot_ram': hex(t[0]), 's': t[2]} for t in tables]}
    with open(META, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print('sha256', h)
    return 0


if __name__ == '__main__':
    sys.exit(main())
