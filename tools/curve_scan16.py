#!/usr/bin/env python3
"""找 u16 曲线表：16/17/32 点、单调递增、跨度大（0..4095 或 0..1023）。

用法: python tools/curve_scan16.py [--top 30]
"""
import sys

import numpy as np

BASE = 0x60021020
IMG = r"analysis/00_0100_40300020.decoded.bin"


def main(args):
    top = 30
    for k, a in enumerate(args):
        if a == "--top":
            top = int(args[k + 1])
    data = open(IMG, "rb").read()
    u16 = np.frombuffer(data, dtype="<u2")[:(len(data) // 2)]
    n = len(u16)

    results = []
    for npts in (16, 17, 32):
        arr = np.lib.stride_tricks.sliding_window_view(u16, npts)
        d = np.diff(arr, axis=1)
        mono = (d >= 0).all(axis=1)
        span = arr[:, -1].astype(np.int32) - arr[:, 0].astype(np.int32)
        ok = mono & (span > 3000) & (arr[:, 0] < 64)
        idx = np.nonzero(ok)[0]
        print(f"u16 x{npts}: {len(idx)} candidates")
        for i in idx[:2000]:
            addr = BASE + int(i) * 2
            results.append((npts, addr, int(arr[i, 0]), int(arr[i, npts // 2]), int(arr[i, -1])))

    def refs(addr):
        pat = addr.to_bytes(4, "little")
        c = 0
        i = data.find(pat)
        while i != -1:
            c += 1
            i = data.find(pat, i + 1)
        return c

    scored = [(refs(a), npts, a, v0, vm, v1) for npts, a, v0, vm, v1 in results]
    scored.sort(reverse=True)
    print(f"\ntop {top} by u32 refs (total {len(scored)}):")
    for r, npts, a, v0, vm, v1 in scored[:top]:
        print(f"  0x{a:08x} n={npts:2d} refs={r:3d} v0={v0:5d} vmid={vm:5d} v1={v1:5d}")


if __name__ == "__main__":
    main(sys.argv[1:])
