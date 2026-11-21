# -*- coding: utf-8 -*-
"""B01（beta 第 1 包）：21 目录签名曲线探针。

目的：一次刷机、一次拍照，拿到「四个标准画面模式各自落进哪个 tone 目录」。

做法：
- 21 个 tone 目录（0x60600920 + dir*0x20，每目录 8 槽 = 4 对）全部重指向同一张表，
  该表属于本目录专用的签名曲线 k = dir；
- 21 张签名曲线各不相同，只差一个亮度增益 s（0.55 .. 1.45，步长 0.045），
  gamma 固定 0.62。灰卡上读中间调亮度即可反查是哪个目录；
- 色度块 k=0 写 -40% 饱和（y = 128 + (x-128)*0.6），用来同时确认色度是否全档生效。

不碰代码、不碰启动路径、不碰 0x605DEF44 之后的 bank2。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'stock', 'OLY_E_120_1400_0000_0000.BIN')
OUT = os.path.join(ROOT, 'build', 'B01_dirprobe.BIN')
PATCH_FILE = os.path.join(ROOT, '.tmp', 'B01_dirprobe.patch')
META = os.path.join(ROOT, '.tmp', 'B01_dirprobe.meta.json')

TONE_BASE = 0x605D8344
TONE_STEP = 0x200
DIR_TABLE = 0x60600920
DIRS = 21
SLOTS_PER_DIR = 8
CHROMA_BASE = 0x605D4829
BASE = 0x60021020

GAMMA = 0.62


def signature(s: float) -> np.ndarray:
    """签名曲线：table[i] = 1023 * (i/255)^gamma * s，夹到 0..1023，保单调。"""
    v = np.arange(256, dtype=np.float64) / 255.0
    t = 1023.0 * np.power(v, GAMMA) * s
    t = np.clip(np.round(t), 0, 1023).astype(np.int64)
    return np.maximum.accumulate(t)


def gains() -> list[float]:
    return [round(0.55 + 0.045 * i, 3) for i in range(DIRS)]


def build_chroma(desat: float = 0.6) -> bytes:
    """只改 y：y = 128 + (x-128)*desat；x 沿用库存，z 按公式重算（dx=0 处沿用库存的 128 填充）。"""
    stock_off = CHROMA_BASE - BASE
    with open(os.path.join(ROOT, 'analysis', '00_0100_40300020.decoded.bin'), 'rb') as f:
        f.seek(stock_off)
        blk = bytearray(f.read(100))

    xb = np.frombuffer(bytes(blk[0x00:0x10]), dtype=np.uint8).astype(np.int64)
    yb = np.frombuffer(bytes(blk[0x10:0x20]), dtype=np.uint8).astype(np.int64)
    zb = np.frombuffer(bytes(blk[0x20:0x31]), dtype=np.uint8).astype(np.int64)
    assert list(xb) == list(yb), 'stock 色度块不是恒等，停止（需要人工确认）'
    assert list(blk[0x31:0x32]) == [0] and list(blk[0x63:0x64]) == [0], 'pad 非 0'

    y = np.round(128.0 + (xb.astype(np.float64) - 128.0) * desat).astype(np.int64)
    y = np.clip(y, 0, 255)
    z = zb.copy()
    z[0] = 0
    for i in range(1, 16):
        dx = xb[i] - xb[i - 1]
        dy = y[i] - y[i - 1]
        z[i] = int(round(dy * 128.0 / dx)) if dx > 0 else 128
        z[i] = max(0, min(255, z[i]))
    z[16] = zb[16]          # 多余的第 17 项：库存是 128，保持不变

    new = bytearray(blk)
    new[0x10:0x20] = y.astype(np.uint8).tobytes()
    new[0x20:0x31] = z.astype(np.uint8).tobytes()
    assert len(new) == 100
    return bytes(new)


def main() -> int:
    if os.path.exists(OUT):
        print('⚠ 已存在，重新生成：%s' % OUT)
        os.remove(OUT)

    lines, meta = [], {'name': 'B01_dirprobe', 'phase': 'beta', 'purpose':
                       '21 目录签名曲线探针：四档→目录映射', 'gamma': GAMMA, 'dirs': []}

    gs = gains()
    tables = {}
    for d, s in enumerate(gs):
        t = signature(s)
        tables[d] = t
        ram = TONE_BASE + d * TONE_STEP
        hexs = t.astype('<u2').tobytes().hex().upper()
        lines.append('ram 0x%08X=%s' % (ram, hexs))
        meta['dirs'].append({
            'dir': d, 'touch': 't%d' % d, 'gain': s, 'tone_ram': '0x%08X' % ram,
            'd8_v50': int(round(t[128] / 1023.0 * 255.0)),
            'd8_v18': int(round(t[22] / 1023.0 * 255.0)),
        })

    # 指针表：每个目录 8 个槽全指向本目录的签名表
    ptr = np.zeros(DIRS * SLOTS_PER_DIR, dtype=np.uint32)
    for d in range(DIRS):
        ptr[d * SLOTS_PER_DIR:(d + 1) * SLOTS_PER_DIR] = TONE_BASE + d * TONE_STEP
    lines.append('ram 0x%08X=%s' % (DIR_TABLE, ptr.astype('<u4').tobytes().hex().upper()))
    meta['pointer_table'] = {'ram': '0x%08X' % DIR_TABLE, 'entries': int(ptr.size),
                             'distinct': len(set(ptr.tolist()))}

    blk = build_chroma()
    lines.append('ram 0x%08X=%s' % (CHROMA_BASE, blk.hex().upper()))
    meta['chroma'] = {'ram': '0x%08X' % CHROMA_BASE, 'desat': 0.6,
                      'y': list(blk[0x10:0x20]), 'z': list(blk[0x20:0x31])}

    os.makedirs(os.path.dirname(PATCH_FILE), exist_ok=True)
    with open(PATCH_FILE, 'w', encoding='utf-8', newline='') as f:
        f.write('# B01 21-dir signature probe\n')
        f.write('\n'.join(lines) + '\n')

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cmd = [sys.executable, os.path.join(ROOT, 'tools', 'mkpatch.py'), SRC,
           '--out', OUT, '--patch-file', PATCH_FILE]
    print('$', ' '.join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print('mkpatch 失败，rc=%d' % r.returncode)
        return 3

    # ---- 独立复核：重新解析容器，逐字节回读
    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    import oly_container  # type: ignore

    with open(OUT, 'rb') as f:
        blocks = oly_container.parse(f.read())
    pay = bytes(blocks[0].payload)

    bad = []
    for d, t in tables.items():
        got = np.frombuffer(pay[TONE_BASE - BASE + d * TONE_STEP:
                                TONE_BASE - BASE + d * TONE_STEP + 512], dtype='<u2')
        if not np.array_equal(got, t.astype(np.uint16)):
            bad.append('tone t%d' % d)
    gotp = np.frombuffer(pay[DIR_TABLE - BASE:DIR_TABLE - BASE + 672], dtype='<u4')
    if not np.array_equal(gotp, ptr):
        bad.append('pointer table')
    if pay[CHROMA_BASE - BASE:CHROMA_BASE - BASE + 100] != blk:
        bad.append('chroma block')

    anchors = {
        'dir_table_first_u32': '0x%08X' % int(gotp[0]),
        'dir_table_ok': int(gotp[0]) == TONE_BASE,
        'matrix_head': [int(x) for x in np.frombuffer(pay[0x605ECECA - BASE:0x605ECECA - BASE + 18], dtype='<i2')],
        'hook_bytes': pay[0x604B6A98 - BASE:0x604B6A98 - BASE + 8].hex().upper(),
    }
    meta['verify'] = {'failures': bad, 'anchors': anchors, 'size': os.path.getsize(OUT)}
    with open(META, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1,
                  default=lambda o: o.item() if hasattr(o, 'item') else str(o))

    print('复核:', '全部通过' if not bad else ('失败 %s' % bad))
    print('输出: %s  %d B' % (OUT, os.path.getsize(OUT)))
    print('锚点: 指针首项=%s (期望 0x%08X) 矩阵m0=%s hook=%s'
          % (anchors['dir_table_first_u32'], TONE_BASE, anchors['matrix_head'], anchors['hook_bytes']))
    return 0 if not bad else 4


if __name__ == '__main__':
    sys.exit(main())
