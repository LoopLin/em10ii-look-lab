#!/usr/bin/env python3
"""S16: 第一个「真 look」—— 只改 0x605D4829 这一块，全局生效。

设计（不是一刀切系数）：
  d = x - 128
  |d| <= 32   : k = 0.82        （中低饱和基本保留，只轻收）
  32 -> 128   : k 线性降到 0.62 （高饱和压住，避免红/蓝顶死）
  y = 128 + k*d
sRGB 色度轴两端因此被抬起/压下：x=0 -> y≈41，x=255 -> y≈207，
暗部保留一点颜色、高饱和不再撞墙 —— 这是「淡彩」而不是「发灰」。

z 由 y 重算（z[i]=round((y[i]-y[i-1])*128/(x[i]-x[i-1]))，z[0]=0，z[16]=z[15]）。
"""
import sys

BASE = 0x60021020
STOCK = r"analysis/00_0100_40300020.decoded.bin"
BLOCK = 0x605D4829
Cb_Y, Cb_Z = 0x10, 0x20
Cr_Y, Cr_Z = 0x42, 0x52
K_MID = 0.75
K_EDGE = 0.58
KNEE = 32


def f(x):
    d = x - 128
    a = abs(d)
    if a <= KNEE:
        k = K_MID
    else:
        t = min(1.0, (a - KNEE) / (128.0 - KNEE))
        k = K_MID + (K_EDGE - K_MID) * t
    v = 128.0 + k * d
    return max(0, min(255, int(round(v))))


def slopes(x, y):
    z = [0] * 17
    z[0] = 0
    prev = None
    for i in range(1, 16):
        dx = x[i] - x[i - 1]
        if dx == 0:
            if prev is not None:
                z[i] = prev
            continue
        v = int(round((y[i] - y[i - 1]) * 128.0 / dx))
        v = max(0, min(255, v))
        z[i] = v
        prev = v
    z[16] = z[15]
    return z


def main():
    d = open(STOCK, "rb").read()
    o = BLOCK - BASE
    blk = bytearray(d[o:o + 100])
    x_cb = list(blk[0x00:0x10])
    x_cr = list(blk[0x32:0x42])

    y_cb = [f(v) for v in x_cb]
    y_cr = [f(v) for v in x_cr]
    z_cb = slopes(x_cb, y_cb)
    z_cr = slopes(x_cr, y_cr)

    print("x  =", x_cb)
    print("y  =", y_cb)
    print("z  =", z_cb)
    print("x2 =", x_cr)
    print("y2 =", y_cr)
    print("z2 =", z_cr)

    new = bytearray(blk)
    new[0x10:0x20] = bytes(y_cb)
    new[0x20:0x31] = bytes(z_cb)
    new[0x42:0x52] = bytes(y_cr)
    new[0x52:0x63] = bytes(z_cr)

    old = bytes(blk)
    new = bytes(new)
    patch = {}
    i = 0
    while i < 100:
        if new[i] == old[i]:
            i += 1
            continue
        j = i
        while j < 100 and new[j] != old[j]:
            j += 1
        for k in range(i, j):
            patch[BLOCK + k] = new[k]
        i = j

    addrs = sorted(patch)
    lines = []
    i = 0
    while i < len(addrs):
        s = addrs[i]
        j = i
        while j + 1 < len(addrs) and addrs[j + 1] == addrs[j] + 1:
            j += 1
        lines.append("ram 0x%08x=%s" % (s, bytes(patch[a] for a in addrs[i:j + 1]).hex()))
        i = j + 1

    out = r".tmp/s16_look.patch"
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\nwrote %s: %d lines, %d bytes" % (out, len(lines), len(patch)))


if __name__ == "__main__":
    sys.exit(main())
