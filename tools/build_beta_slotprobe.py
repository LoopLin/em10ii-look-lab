# -*- coding: utf-8 -*-
"""B18：槽位探针——一次刷机拿到「四个画面模式各自读哪个 (dir, slot)」。

与 B01 的区别：B01 只让"目录"不同（同目录 8 个槽写同一张表），所以只能判到 dir。
本包让**每个槽**都不同：dir0..dir5 共 6 目录 × 8 槽 = 48 个槽，每槽一条独立增益曲线
（s_j = 0.40 + 0.02*j，j = dir*8 + slot），其余目录统一指回一张占位表。

判读：灰块 8bit 值 → 反解 s → 唯一 j → (dir, slot)。
为了让相邻档差得开，请在比正常亮约 2 EV 的条件下拍（索引 ≈ 70，此时 display ≈ 255*(i/255)^0.62*s，
档间差 ≈ 2.4 级）。

不碰代码、不碰启动路径、不碰 bank2；只写 tone 池 48 张表 + 168 个池指针。
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
OUT = os.path.join(ROOT, 'build', 'B18_slotprobe.BIN')
PATCH_FILE = os.path.join(ROOT, '.tmp', 'B18_slotprobe.patch')
META = os.path.join(ROOT, '.tmp', 'B18_slotprobe.meta.json')

TONE_BASE = 0x605D8344
TONE_STEP = 0x200
DIR_TABLE = 0x60600920
BASE = 0x60021020
GAMMA = 0.62

PROBE_DIRS = 6                      # dir0..dir5
SLOTS = 8
N_PROBE = PROBE_DIRS * SLOTS        # 48
FILLER_TABLE = 48                   # 其余目录指向这张占位表
S0 = 0.40
DS = 0.02


def signature(s: float) -> np.ndarray:
    v = np.arange(256, dtype=np.float64) / 255.0
    t = 1023.0 * np.power(v, GAMMA) * s
    t = np.clip(np.round(t), 0, 1023).astype(np.int64)
    return np.maximum.accumulate(t)


def build() -> dict:
    patches = []
    table_of_slot = {}

    # 48 张探针表 + 1 张占位表
    for j in range(N_PROBE):
        s = round(S0 + DS * j, 4)
        tab = signature(s)
        ram = TONE_BASE + j * TONE_STEP
        patches.append((ram, tab.astype('<u2').tobytes()))
        table_of_slot[j] = {'s': s, 'table_ram': ram}

    filler = signature(1.0)
    filler_ram = TONE_BASE + FILLER_TABLE * TONE_STEP
    patches.append((filler_ram, filler.astype('<u2').tobytes()))

    # 168 个池指针：dir d 的 8 个槽 → dir d 自己的 8 张表（d<6），否则 → 占位表
    for d in range(21):
        dir_ram = DIR_TABLE + d * 0x20
        vals = []
        for slot in range(SLOTS):
            if d < PROBE_DIRS:
                t = TONE_BASE + (d * SLOTS + slot) * TONE_STEP
            else:
                t = filler_ram
            vals.append(t)
        patches.append((dir_ram, np.array(vals, dtype='<u4').tobytes()))

    return {'patches': patches, 'slot_table': table_of_slot,
            'filler_ram': filler_ram}


def main() -> int:
    if os.path.exists(OUT):
        print(f'拒绝覆盖已存在的 {OUT}')
        return 1
    info = build()

    lines = ['# B18 slot probe (auto-generated, ASCII only)']
    for ram, data in info['patches']:
        lines.append(f'ram 0x{ram:08X}={data.hex()}')
    with open(PATCH_FILE, 'w', encoding='ascii') as f:
        f.write('\n'.join(lines) + '\n')

    cmd = [sys.executable, os.path.join(ROOT, 'tools', 'mkpatch.py'), SRC,
           '--out', OUT, '--patch-file', PATCH_FILE]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout[-3000:])
    if r.returncode != 0:
        print(r.stderr[-3000:])
        return r.returncode

    h = hashlib.sha256(open(OUT, 'rb').read()).hexdigest()
    meta = {'output': OUT, 'sha256': h, 'size': os.path.getsize(OUT),
            'probe_dirs': PROBE_DIRS, 'slots': SLOTS,
            's0': S0, 'ds': DS, 'gamma': GAMMA,
            'slot_table': info['slot_table'], 'filler_ram': info['filler_ram']}
    with open(META, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print('sha256', h)
    print('meta', META)
    return 0


if __name__ == '__main__':
    sys.exit(main())
