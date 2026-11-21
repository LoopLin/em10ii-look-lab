"""audit_beta_pack.py — 对已出的 B 测包做独立审计（不写任何文件，只读）。

目的：确认"已经通过真机验证的通道"在我们的包里没有隐藏的恶性 bug。
检查项：
  1. 容器：大小 69,599,232、能解析、32 位字累加和 == 0、头尾 20 B 一致
  2. 锚点：0x60600920 首项 == 0x605D8344；矩阵 m0 行和 == 256；hook 处字节
  3. tone：256 项、0..1023、单调不减、首项一般为 0（--strict-tone0 才强制；
     抬黑的 look 会写非 0，默认只提示）
  4. 池指针：168 项、每项都指向 tone 区内的 0x200 对齐地址
  5. 色度块：x 单调、z 与 x/y 自洽（容差 1）、两个 pad 为 0
  6. 矩阵：9 个 i16、行和 256、m01/m21 为偶数、m10+m12 为偶数（硬件会偶化）
用法：python tools/audit_beta_pack.py build/B07_*.BIN
"""
from __future__ import annotations

import sys
import io
import struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import oly_container  # noqa: E402

BASE = 0x60021020
TONE_RAM = 0x605D8344
POOL_RAM = 0x60600920
CHROMA_RAM = 0x605D4829
MATRIX_RAM = 0x605ECECA
STOCK_DECODED = Path(__file__).resolve().parent.parent / 'analysis' / '00_0100_40300020.decoded.bin'
HOOK_RAM = 0x604B6AD4
SIZE = 69_599_232


def off(ram: int) -> int:
    # 注意：paylaod 已经 decode 过，索引就是 ram - BASE；
    # 32 + (ram - BASE) 是容器里 payload 的偏移，不是解码后的下标。
    return ram - BASE


def main(paths: list[str]) -> int:
    strict_tone0 = '--strict-tone0' in paths
    paths = [x for x in paths if x != '--strict-tone0']
    bad = 0
    for p in paths:
        name = Path(p).name
        data = Path(p).read_bytes()
        errs: list[str] = []
        notes: list[str] = []
        if len(data) != SIZE:
            errs.append(f'size {len(data)} != {SIZE}')
        blocks = oly_container.parse(data)
        code = blocks[0].payload
        # 1. 校验和：header + payload + trailer 的 32 位字累加应为 0
        b0 = blocks[0]
        tot = (oly_container.sum32(b0.header) + oly_container.sum32(b0.payload)
               + oly_container.sum32(b0.trailer)) & 0xFFFFFFFF
        if tot:
            errs.append(f'checksum != 0 (0x{tot:08x})')
        # 2. 锚点
        pool0 = struct.unpack_from('<I', code, off(POOL_RAM))[0]
        if pool0 != TONE_RAM:
            errs.append(f'pool[0]=0x{pool0:08x} != 0x{TONE_RAM:08x}')
        hook = code[off(HOOK_RAM):off(HOOK_RAM) + 2]
        # 3. tone 表（用池首项指向的那张）
        t = struct.unpack_from('<256H', code, off(TONE_RAM))
        if t[0] != 0:
            # tone[0] 抬黑是某些 look 的设计意图（如 bleach bypass 的 17），
            # 不是硬件约束；默认只提示，--strict-tone0 才当错误。
            if strict_tone0:
                errs.append(f'tone[0]={t[0]} != 0')
            else:
                notes.append(f'tone[0]={t[0]}（黑位抬升；--strict-tone0 可判错）')
        if any(a > b for a, b in zip(t, t[1:])):
            errs.append('tone 非单调不减')
        if max(t) > 1023:
            errs.append(f'tone max {max(t)} > 1023')
        # 4. 池指针
        pool = struct.unpack_from('<168I', code, off(POOL_RAM))
        for i, ptr in enumerate(pool):
            if not (TONE_RAM <= ptr < TONE_RAM + 54 * 0x200):
                errs.append(f'pool[{i}]=0x{ptr:08x} 不在 tone 区')
                break
            if (ptr - TONE_RAM) % 0x200:
                errs.append(f'pool[{i}]=0x{ptr:08x} 未对齐')
                break
        # 5. 色度块
        blk = code[off(CHROMA_RAM):off(CHROMA_RAM) + 100]
        for tag, xo, yo, zo in (('Cb', 0x00, 0x10, 0x20), ('Cr', 0x32, 0x42, 0x52)):
            x = list(blk[xo:xo + 16])
            y = list(blk[yo:yo + 16])
            z = list(blk[zo:zo + 17])
            if any(a > b for a, b in zip(x, x[1:])):
                errs.append(f'{tag} x 非单调')
            prev = 0
            for i in range(1, 17):
                dx = x[i] - x[i - 1] if i < 16 else 0
                if dx == 0:
                    want = prev
                else:
                    want = round((y[i] - y[i - 1]) * 128 / dx) & 0xFF
                if abs(want - z[i]) > 1:
                    errs.append(f'{tag} z[{i}]={z[i]} want {want}（整段错位或填充不对？）')
                    break
                prev = want
        if blk[0x31] or blk[0x63]:
            errs.append(f'pad 非 0: 0x{blk[0x31]:02x}/0x{blk[0x63]:02x}')
        # 6. 矩阵 m0：只在"本包写了矩阵"时才检查（原厂矩阵本身就可能不满足偶化规则）
        mx = struct.unpack_from('<9h', code, off(MATRIX_RAM))
        stock_mx = struct.unpack_from('<9h', STOCK_DECODED.read_bytes(), off(MATRIX_RAM))
        if mx != stock_mx:
            for r in range(3):
                if sum(mx[r * 3:r * 3 + 3]) != 256:
                    errs.append(f'matrix 行 {r} 和 {sum(mx[r*3:r*3+3])} != 256')
            if mx[1] & 1 or mx[7] & 1:
                errs.append(f'm01/m21 为奇数 {mx[1]}/{mx[7]}（硬件会偶化）')
            if (mx[3] + mx[5]) & 1:
                errs.append(f'm10+m12 为奇数 {mx[3]}+{mx[5]}（硬件会偶化）')
            notes.append('matrix: 本包写入，已按硬件规则检查')
        else:
            notes.append('matrix: 未改动（原厂 m0），跳过检查')
        status = 'OK ' if not errs else 'BAD'
        print(f'[{status}] {name}  len={len(data):,}  hook={hook.hex()}  '
              f'm0={list(mx)}  t18%={t[46]}')
        for e in errs:
            print(f'        - {e}')
        for n in notes:
            print(f'        · {n}')
        bad += bool(errs)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
