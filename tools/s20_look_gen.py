#!/usr/bin/env python3
"""S20：把全局色度块 0x605D4829 改成一套「暖调淡彩」look。
Cb 与 Cr 各自一条 y=f(x) 折线（结点按 |x-128| 分段），z 斜率表同步重算。
输出 mkpatch 可吃的 ram 行。
"""
import struct, sys

IMG = "analysis/00_0100_40300020.decoded.bin"
BASE = 0x60021020
BLK  = 0x605D4829

def load_block():
    off = BLK - BASE
    return bytearray(open(IMG, "rb").read()[off:off+100])

def build_y(x, k_lo, k_hi, shift):
    """|d|<=16 用 k_lo，>=112 用 k_hi，中间线性过渡；shift 是整体偏移。"""
    out = []
    for v in x:
        d = v - 128
        t = min(max((abs(d) - 16) / 96.0, 0.0), 1.0)
        k = k_lo + (k_hi - k_lo) * t
        y = 128 + shift + k * d
        out.append(int(round(min(max(y, 0), 255))))
    return out

def build_z(x, y):
    z = [0] * 17
    last = 0
    for i in range(1, 16):
        dx = x[i] - x[i-1]
        if dx == 0:
            z[i] = last
        else:
            z[i] = int(round((y[i] - y[i-1]) * 128.0 / dx))
            last = z[i]
    z[16] = z[15]
    return z

def main():
    b = load_block()
    xcb, ycb = b[0:16], b[16:32]
    xcr, ycr = b[0x32:0x32+16], b[0x42:0x42+16]
    # 暖彩：蓝绿压 20%、红黄压 15%，整体 +1/+4 暖偏
    ncb = build_y(xcb, 0.80, 0.92, +1)
    ncr = build_y(xcr, 0.70, 0.85, +4)
    zcb = build_z(xcb, ncb); zcr = build_z(xcr, ncr)
    print(f"# Cb y: {ncb}")
    print(f"# Cb z: {zcb}")
    print(f"# Cr y: {ncr}")
    print(f"# Cr z: {zcr}")
    lines = [
        ("ram 0x%08x=%s" % (BLK + 0x10, bytes(ncb).hex())),
        ("ram 0x%08x=%s" % (BLK + 0x20, bytes(zcb[:17]).hex())),
        ("ram 0x%08x=%s" % (BLK + 0x42, bytes(ncr).hex())),
        ("ram 0x%08x=%s" % (BLK + 0x52, bytes(zcr[:17]).hex())),
    ]
    open(".tmp/s20_look.patch", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))

main()
