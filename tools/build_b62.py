#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b62.py —— B62：B61 + 1 字节闸门翻转（0x604803AF 的 bne -> bra）

## 一句话

`0x60480047` 在 `u8[O+0x38EC]==1` 时把 slot1 的 100 B 色度块原样抄进新鲜记录
（`0x604803B1..0x604803C5`）并 `jmp 0x60480B1A` 跳过整段重算（`0x604803C9`）。
B62 把这条 `bne` 的无条件化：`0x604803AF` 的 `C9 1A` -> `CA 1A`，
于是**所有档**都掉进 `0x604803C9` 那条"按参照记录 P 重标定色度曲线"的路。

## 与 B61 的差别（只有 1 字节）

- `0x604803AF`: `C9`(bne) -> `CA`(bra)，位移字节 `1A` 不动。
- MN10300 的 8 位分支位移基准是**指令起始地址**（实测三条：`0x604803AF+0x1A=0x604803C9`、
  `0x604805A0+0x3E=0x604805DE`、`0x604803C6+0x754=0x60480B1A`），所以 `CA 1A`
  正好落到 `0x604803C9`，和原 bne 的目标一致。
- 其余全部沿用 B61（四档 tone / 四套矩阵 / EXIF 写 k / 四张极端色度块 / 双写桩）。

## 硬约束（build 时逐条断言）

- 只从 stock 起包（经 B61 的 patch 体，追加 1 行）
- 与 B61 的差异必须**只有 1 个字节**，且落在 0x604803AF
- `0x604803AD..0x604803AE`（`A0 01` cmp）与 `0x604803C6..0x604803C8`（`jmp 0x60480B1A`）不动
- 容器大小 / 校验和由 mkpatch 重算

只离线：不刷机、不写卡、不碰任何盘符。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.chdir(ROOT)

import build_b61 as B61                      # noqa: E402

BASE = B61.BASE
SRC = B61.SRC
B61BIN = B61.OUT
OUT = 'build/B62_gateflip.BIN'

GATE = 0x604803AF          # bne (0x604803c9,PC) 的 opcode 字节
GATE_OLD = b'\xC9\x1A'
GATE_NEW = b'\xCA\x1A'
CMP_SITE = 0x604803AD      # A0 01  cmp 0x1,d0 —— 不能动
CMP_BYTES = b'\xA0\x01'
JMP_SITE = 0x604803C6      # CC 54 07  jmp 0x60480b1a —— 不能动
JMP_BYTES = b'\xCC\x54\x07'
GATE_TARGET = 0x604803C9


def payload_of(path):
    import oly_container as oc
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def main():
    info = B61.build()                       # B61 全套断言照跑
    body = io.open(info['patch'], encoding='utf-8').read()
    if not body.endswith('\n'):
        body += '\n'
    body += ('ram 0x%x=%s  # B62 gate flip: bne -> bra @0x%x (target 0x%x)\n'
             % (GATE, GATE_NEW.hex(), GATE, GATE_TARGET))
    pf = '.tmp/b62.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)

    if os.path.exists(OUT):
        os.remove(OUT)
    B61.b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT, '--patch-file', pf])

    stock = payload_of(SRC)
    b61 = payload_of(B61BIN)
    built = payload_of(OUT)

    def at(img, addr, n):
        return bytes(img[addr - BASE:addr - BASE + n])

    ok = True

    print('B62 = B61 + 1 字节（0x%X: %s -> %s）' % (GATE, GATE_OLD.hex(), GATE_NEW.hex()))
    # 1) 相对 B61：只有 1 个字节
    d61 = [i for i in range(len(built)) if built[i] != b61[i]]
    exact = d61 == [GATE - BASE]
    print('  vs B61 差异 %d 字节 @%s（必须是 1 字节 @0x%X）-> %s'
          % (len(d61), ['0x%X' % (BASE + i) for i in d61[:8]], GATE,
             'OK' if exact else 'FAIL'))
    ok &= exact

    # 2) 闸门字节 / 邻居指令
    gate_now = at(built, GATE, 2)
    gate_stock = at(stock, GATE, 2)
    gate_b61 = at(b61, GATE, 2)
    g_ok = gate_now == GATE_NEW and gate_stock == GATE_OLD and gate_b61 == GATE_OLD
    print('  闸门 0x%X = %s（stock=%s B61=%s，期望 %s）-> %s'
          % (GATE, gate_now.hex(), gate_stock.hex(), gate_b61.hex(), GATE_NEW.hex(),
             'OK' if g_ok else 'FAIL'))
    ok &= g_ok

    cmp_ok = at(built, CMP_SITE, 2) == CMP_BYTES == at(stock, CMP_SITE, 2)
    print('  cmp 0x%X..0x%X = %s 未动 -> %s'
          % (CMP_SITE, CMP_SITE + 1, at(built, CMP_SITE, 2).hex(), 'OK' if cmp_ok else 'FAIL'))
    ok &= cmp_ok

    jmp_ok = at(built, JMP_SITE, 3) == JMP_BYTES == at(stock, JMP_SITE, 3)
    print('  jmp 0x%X..0x%X = %s（->0x60480B1A）未动 -> %s'
          % (JMP_SITE, JMP_SITE + 2, at(built, JMP_SITE, 3).hex(), 'OK' if jmp_ok else 'FAIL'))
    ok &= jmp_ok

    # 3) 位移自检（起始地址基准）
    disp = struct.unpack('b', GATE_NEW[1:2])[0]
    tgt = GATE + disp
    print('  bra 位移基准=指令起始：0x%X + (%+d) = 0x%X（期望 0x%X）-> %s'
          % (GATE, disp, tgt, GATE_TARGET, 'OK' if tgt == GATE_TARGET else 'FAIL'))
    ok &= (tgt == GATE_TARGET)

    # 4) 其余字节仍 == stock 的 B61 改动集合：用 B61 的区间表复核
    outside = [i for i in d61 if i != GATE - BASE]
    print('  除闸门外无其它差异 -> %s' % ('OK' if not outside else 'FAIL'))
    ok &= not outside

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'base_pkgs': [B61BIN, SRC], 'gate_site': GATE, 'gate_old': GATE_OLD.hex(),
           'gate_new': GATE_NEW.hex(), 'gate_target': GATE_TARGET,
           'gate_disp_base': 'instruction start address', 'diff_vs_b61': 1,
           'cmp_site': CMP_SITE, 'jmp_site': JMP_SITE,
           'chroma': {'hook': B61.CH_INSERT, 'stub': B61.CH_STUB,
                      'stub_len': len(info['stub']), 'ptrtab': B61.CH_PTRTAB,
                      'blocks': B61.CH_ADDRS, 'looks': B61.LOOKS},
           'ranges': info['ranges'], 'new_spans': info['new_spans']}
    with io.open('.tmp/b62_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
