#!/usr/bin/env python3
"""verify_beta_modeprobe.py - 用 libsim 离线验证 B24 各变体的桩与探针表选择。

变体（--variant b|c|d，默认 c）：
  b  桩先 a2=(12,SP) 再 a0=*(O+4)，k=(u16(ctx+0x0A)&7)|((u8(ctx+8)&3)<<3)
  c  桩 a0=(12,SP)，      k=(u16(ctx+0x0A)&7)|((u8(ctx+8)&3)<<3)
  d  桩 a0=(12,SP)，      k=(u8(ctx+0x0A)&7)|((u8(ctx+9)&3)<<3)

场景（真实代码，不是玩具循环）：
  从 uploader 入口 0x60479960 起跑，A0=DEST(0x9E00A400)、A1=O(0x61E10000)、
  SP=0x61E0F800；O+0x0C = S、O+0x64 的 bit2 打开第二段拷贝、
  S+0x41C=0（跳过第一段）、S+0x420 = 非 0（走 tone 段）、S+0x1230 = 原厂 slot 44
  （故意指向一张没被改过的表 —— 桩若没生效，DEST 就会是 slot 44 的内容）。
  跑到 0x60479A77（两段拷贝之后的汇合点）停下，读 DEST 与寄存器。

b 变体的 ctx = *(O+4)；c/d 变体直接读 O。为了证明读的是"对的那一层"，b/c/d
都往另一个候选基址里塞了一个**诱导值**（各给出 k=31 / k=15 这种明显不同的槽），
若桩读错基址，命中判定就会失败。

用法：python tools/verify_beta_modeprobe.py --variant c
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / '.tmp'))
sys.path.insert(0, str(ROOT / 'tools'))
import simrun  # noqa: E402

BASE = 0x60021020
TONE = 0x605D8344
STUB_RAM = 0x605DED44            # 桩（tone 池 slot 53）
ENTRY = 0x60479960               # uploader 入口
STOP = 0x60479A77                # tone 段拷贝之后的汇合点
CODE = f'0x{BASE:08X}:0x1460000'  # 整个 main 块
DEST_RAM = 0x9E00A400
STOCK_LUT_SLOT = 44              # 一张没被 B24 改过的原厂表
STOCK_LUT = TONE + STOCK_LUT_SLOT * 0x200
O = 0x61E10000                   # A1，(12,SP)
S = 0x61E18000                   # O+0x0C
CTX = 0x61E20000                 # O+0x04（b 变体的 ctx）
T2 = 0x61E19000                  # *(S+0x420)：1024 B 来源（内容无关）
SP = 0x61E0F800


def p32(addr: int, val: int):
    return (addr, val.to_bytes(4, 'little'))


def tone_bytes(k: int) -> bytes:
    # 与出包器共用同一份表定义，避免两处口径漂移
    from build_beta_modeprobe import probe_table
    return probe_table(k)


def expected_k(group: int, mode: int) -> int:
    return (mode & 7) | ((group & 3) << 3)


def decoy_k(variant: str) -> int:
    """诱导值放进去以后，错误基址会算出的 k。"""
    return 31 if variant in ('b', 'c', 'e') else 15


def case(name: str, group: int, mode: int, *, variant: str,
         second_stage: bool = True) -> dict:
    image = str(ROOT / '.tmp' / f'B24{variant}.decoded.bin')
    pokes = [
        p32(O + 0x0C, S),
        p32(O + 0x04, CTX),
        p32(O + 0x64, 4),            # btst 2 -> 走 tone 段
        p32(O + 0x34, 0),
        (O + 0xBD, b'\x01'),
        p32(S + 0x41C, 0),           # 跳过第一段（1024 B）
        p32(S + 0x420, T2 if second_stage else 0),
        p32(S + 0x1230, STOCK_LUT),  # 原厂表：桩不生效就会看到它
        # 诱导值：若桩仍去 *(O+4) 取 ctx，会算出 decoy_k
        (CTX + 8, b'\x03\x01'),
        (CTX + 0x0A, b'\x07\x00'),
    ]
    if variant == 'b':
        pokes += [(CTX + 8, bytes([group & 0xFF, 0x01])),
                  (CTX + 0x0A, (mode & 0xFFFF).to_bytes(2, 'little'))]
    elif variant == 'e':
        pokes += [(S + 8, bytes([group & 0xFF, 0x01])),
                  (S + 0x0A, (mode & 0xFFFF).to_bytes(2, 'little'))]
    elif variant == 'c':
        pokes += [(O + 8, bytes([group & 0xFF, 0x01])),
                  (O + 0x0A, (mode & 0xFFFF).to_bytes(2, 'little'))]
    else:  # d：+8 塞 0xAA 当诱饵，group 放 +9，mode 只放低字节
        pokes += [(O + 8, b'\xaa'),
                  (O + 9, bytes([group & 0xFF])),
                  (O + 0x0A, bytes([mode & 0xFF]))]

    d = simrun.run(pc=ENTRY, stop=STOP, limit=400000, codes=[CODE],
                   rwzs=['0x61e00000:0x40000', '0x9e00a000:0x3000',
                         '0x8fe00000:0x1000', '0x8ff00000:0x8000',
                         '0xa1c21000:0x1000', '0xa1c2a000:0x1000'],
                   pokes=pokes, regs={'A0': DEST_RAM, 'A1': O, 'SP': SP},
                   image=image)
    regs = dict(zip(simrun.REGS, d['registers']))
    mem = {}
    for r in d['memory']:
        mem[r['address']] = bytes.fromhex(r['hex'])
    dest = mem.get(0x9E00A000, b'')[DEST_RAM - 0x9E00A000:]
    got = list(struct.unpack_from('<256I', dest, 0)) if len(dest) >= 1024 else []
    k = expected_k(group, mode)
    want = list(struct.unpack('<256H', tone_bytes(k)))
    hit = bool(got) and got == want
    stock_want = list(struct.unpack('<256H', open(image, 'rb').read()[
        STOCK_LUT - BASE:STOCK_LUT - BASE + 512]))
    is_stock = bool(got) and got == stock_want
    print(f'[{name}] group={group} mode=0x{mode:04X} -> k={k} (tone@{TONE + k * 0x200:#x})'
          f'   [诱导基址会给出 k={decoy_k(variant)}]')
    print(f'    stop PC=0x{regs["PC"]:08x}  A2=0x{regs["A2"]:08x}  D0=0x{regs["D0"]:08x}  '
          f'status={d["status"]}')
    print(f'    DEST[0:8]={got[:8]}')
    print(f'    期望 tone_{k}[0:8]={want[:8]}  -> {"命中" if hit else "未命中"}'
          + (f'  (== 原厂 slot{STOCK_LUT_SLOT} 表)' if is_stock else ''))
    return {'name': name, 'group': group, 'mode': mode, 'k': k, 'hit': hit,
            'stock_path': is_stock, 'pc': regs['PC'], 'a2': regs['A2'],
            'dest0': got[:8], 'want0': want[:8]}


def jump_probe(variant: str) -> dict:
    """在桩入口下断点，证明 0x60479A27 的 DC 远跳确实落到 0x605DED44。"""
    image = str(ROOT / '.tmp' / f'B24{variant}.decoded.bin')
    pokes = [
        p32(O + 0x0C, S), p32(O + 0x04, CTX), p32(O + 0x64, 4), p32(O + 0x34, 0),
        (O + 0xBD, b'\x01'), p32(S + 0x41C, 0), p32(S + 0x420, T2),
        p32(S + 0x1230, STOCK_LUT),
        (O + 8, b'\x01\x01'), (O + 9, b'\x01'),
        (O + 0x0A, (0x79).to_bytes(2, 'little')),
    ]
    d = simrun.run(pc=ENTRY, stop=STOP, limit=400000, codes=[CODE],
                   rwzs=['0x61e00000:0x40000', '0x9e00a000:0x3000',
                         '0x8fe00000:0x1000', '0x8ff00000:0x8000',
                         '0xa1c21000:0x1000', '0xa1c2a000:0x1000'],
                   bps=[STUB_RAM], pokes=pokes,
                   regs={'A0': DEST_RAM, 'A1': O, 'SP': SP}, image=image)
    regs = dict(zip(simrun.REGS, d['registers']))
    hit = d['status'] == 'breakpoint' and regs['PC'] == STUB_RAM
    print(f'[G-jump] 断点 @ 0x{STUB_RAM:08X}: status={d["status"]}  PC=0x{regs["PC"]:08x}'
          f'  SP=0x{regs["SP"]:08x}  -> {"落点正确" if hit else "落点不对"}')
    return {'name': 'G-jump', 'hit': hit, 'pc': regs['PC'], 'status': d['status']}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--variant', default='c', choices=['b', 'c', 'd', 'e'])
    args = ap.parse_args()
    v = args.variant
    image = ROOT / '.tmp' / f'B24{v}.decoded.bin'
    img = image.read_bytes()
    print(f'variant {v}  image {image}  {len(img):,} B  (B24{v} decoded payload)\n')

    rows = [
        case('A', 1, 0x0079, variant=v),
        case('B', 2, 0x0079, variant=v),
        case('C', 0, 0x0066, variant=v),
        case('D', 1, 0x0096, variant=v),
        case('E 对照 第二段关', 1, 0x0079, variant=v, second_stage=False),
        case('F 基址对照', 1, 0x0079, variant=v),
    ]
    g = jump_probe(v)

    print('\n---- 汇总 ----')
    ok = True
    for r in rows[:4]:
        print(f'{r["name"]:2s} k={r["k"]:2d}  {"PASS" if r["hit"] else "FAIL"}')
        ok &= r['hit']
    e = rows[4]
    print(f'E  {"PASS" if e["stock_path"] else "FAIL"}（第二段关掉时用的是原厂 slot44 整表：{e["stock_path"]}）')
    ok &= e['stock_path']
    f = rows[5]
    print(f'F  {"PASS" if f["hit"] else "FAIL"}（诱导基址 k={decoy_k(v)} 没有命中，说明读的是对的基址）')
    ok &= f['hit']
    ok &= g['hit']
    print(f'G  {"PASS" if g["hit"] else "FAIL"}（0x60479A27 的 DC 跳到 0x{STUB_RAM:08X}）')
    print(f'\n{"ALL PASS" if ok else "有 FAIL"}  (variant {v})')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
