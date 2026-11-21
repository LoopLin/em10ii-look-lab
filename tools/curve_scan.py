#!/usr/bin/env python3
"""在解码镜像里找「曲线表」候选：16 点单调递增的 x 数组 + 同样单调的 y 数组，
或 256 字节单调 LUT；并统计每个候选被代码以 u32 立即数引用的次数。

用法: python tools/curve_scan.py START END [--min-span 200] [--top 40]
地址都是 RAM（0x60021020 基址）。
"""
import sys

BASE = 0x60021020
IMG = r"analysis/00_0100_40300020.decoded.bin"


def u32refs(data, addr):
    pat = addr.to_bytes(4, "little")
    n = 0
    i = data.find(pat)
    while i != -1:
        n += 1
        i = data.find(pat, i + 1)
    return n


def mono(b):
    return all(b[i] <= b[i + 1] for i in range(len(b) - 1))


def main(args):
    start = int(args[0], 0)
    end = int(args[1], 0)
    min_span = 200
    top = 40
    for k, a in enumerate(args[2:]):
        if a == "--min-span":
            min_span = int(args[2 + k + 1], 0)
        if a == "--top":
            top = int(args[2 + k + 1], 0)

    data = open(IMG, "rb").read()
    o0 = start - BASE
    o1 = end - BASE

    print(f"scan 0x{start:08x}-0x{end:08x} ({o1-o0} bytes)")
    hits16 = []
    hits256 = []
    for off in range(o0, o1 - 32):
        b = data[off:off + 32]
        if not mono(b[:16]) or not mono(b[16:]):
            continue
        span_x = b[15] - b[0]
        span_y = b[31] - b[16]
        if span_x < min_span or span_y < min_span:
            continue
        if b[0] > 10 or b[15] < 240:
            continue
        hits16.append((BASE + off, list(b[:16]), list(b[16:32])))

    for off in range(o0, o1 - 256):
        b = data[off:off + 256]
        if not mono(b):
            continue
        if b[255] - b[0] < 200:
            continue
        hits256.append((BASE + off, b[0], b[128], b[255]))

    print(f"\n16+16 candidates: {len(hits16)}")
    rows = []
    for addr, x, y in hits16:
        r = u32refs(data, addr) + u32refs(data, addr + 16)
        ident = x == y
        rows.append((r, addr, x, y, ident))
    rows.sort(reverse=True)
    for r, addr, x, y, ident in rows[:top]:
        tag = "identity" if ident else ""
        print(f"0x{addr:08x} refs={r:3d} {tag:9s} x={x[:8]}... y={y[:8]}...")

    print(f"\n256-byte LUT candidates: {len(hits256)}")
    lrows = [(u32refs(data, a), a, v0, v128, v255) for a, v0, v128, v255 in hits256]
    lrows.sort(reverse=True)
    for r, addr, v0, v128, v255 in lrows[:top]:
        print(f"0x{addr:08x} refs={r:3d}  v0={v0:3d} v128={v128:3d} v255={v255:3d}")


if __name__ == "__main__":
    main(sys.argv[1:])
