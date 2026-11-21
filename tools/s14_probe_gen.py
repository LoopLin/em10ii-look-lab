#!/usr/bin/env python3
"""S14: 组探针 —— 给 8 个组各一个独特的、一眼能认出来的色度签名。

组 0/1：不动（identity）
组 2：Cb 反相        -> 红↔青 翻转
组 3：Cr 反相        -> 蓝↔黄 翻转
组 4：Cb+Cr 都反相   -> 近似负片
组 5：Cb 压平        -> 只剩红绿轴
组 6：Cr 压平        -> 只剩蓝黄轴
组 7：两边都压平     -> 灰

改的是每个 100B 曲线块的 y（+0x10 Cb / +0x42 Cr）与 z（+0x20 Cb / +0x52 Cr），x 不动。
从 stock 生成，不叠在 S13 上。
"""
import struct
import sys

BASE = 0x60021020
STOCK = r"analysis/00_0100_40300020.decoded.bin"
PTR_TABLE = 0x605FFDC8
BLOCKS = 0x605D4829
STRIDE = 0x64
NGROUP = 8
NENT = 12

Cb_Y, Cb_Z = 0x10, 0x20
Cr_Y, Cr_Z = 0x42, 0x52


def main():
    d = open(STOCK, "rb").read()

    def off(ram):
        return ram - BASE

    # 指针表前 96 项 -> 块地址 -> 组号（首次出现）
    group_of = {}
    for g in range(NGROUP):
        for k in range(NENT):
            p = struct.unpack_from("<I", d, off(PTR_TABLE) + (g * NENT + k) * 4)[0]
            if p in group_of:
                continue
            group_of[p] = g

    # 只处理落在曲线块区的指针
    blocks = {}
    for p, g in group_of.items():
        if BLOCKS <= p < BLOCKS + 64 * STRIDE:
            blocks[p] = g

    print("referenced blocks in curve area: %d" % len(blocks))
    by_group = {}
    for p, g in sorted(blocks.items()):
        by_group.setdefault(g, []).append(p)
    for g in range(NGROUP):
        print("  g%d: %d blocks" % (g, len(by_group.get(g, []))))

    patch = {}  # ram -> byte

    def put(addr, data):
        for i, b in enumerate(data):
            patch[addr + i] = b

    for p, g in sorted(blocks.items()):
        o = off(p)
        blk = bytearray(d[o:o + STRIDE])

        def invert(yoff, zoff):
            for i in range(16):
                blk[yoff + i] = 255 - blk[yoff + i]
            for i in range(17):
                blk[zoff + i] = 255 - blk[zoff + i]

        def flat(yoff, zoff):
            for i in range(16):
                blk[yoff + i] = 128
            for i in range(17):
                blk[zoff + i] = 0

        if g in (0, 1):
            continue
        elif g == 2:
            invert(Cb_Y, Cb_Z)
        elif g == 3:
            invert(Cr_Y, Cr_Z)
        elif g == 4:
            invert(Cb_Y, Cb_Z)
            invert(Cr_Y, Cr_Z)
        elif g == 5:
            flat(Cb_Y, Cb_Z)
        elif g == 6:
            flat(Cr_Y, Cr_Z)
        elif g == 7:
            flat(Cb_Y, Cb_Z)
            flat(Cr_Y, Cr_Z)

        new = bytes(blk)
        old = d[o:o + STRIDE]
        if new == old:
            continue
        # 只提交有变化的连续段
        i = 0
        while i < STRIDE:
            if new[i] == old[i]:
                i += 1
                continue
            j = i
            while j < STRIDE and new[j] != old[j]:
                j += 1
            put(p + i, new[i:j])
            i = j

    # 合并成补丁行
    lines = []
    addrs = sorted(patch)
    i = 0
    while i < len(addrs):
        start = addrs[i]
        j = i
        while j + 1 < len(addrs) and addrs[j + 1] == addrs[j] + 1:
            j += 1
        data = bytes(patch[a] for a in addrs[i:j + 1])
        lines.append("ram 0x%08x=%s" % (start, data.hex()))
        i = j + 1

    out = r".tmp/s14_groupprobe.patch"
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    total = len(patch)
    print("\nwrote %s: %d lines, %d bytes" % (out, len(lines), total))
    for g in range(NGROUP):
        n = 0
        for p, gg in blocks.items():
            if gg != g:
                continue
            for a in patch:
                if p <= a < p + STRIDE:
                    n += 1
        print("  g%d changed bytes: %d" % (g, n))


if __name__ == "__main__":
    sys.exit(main())
