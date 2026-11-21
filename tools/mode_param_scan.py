#!/usr/bin/env python3
"""找「画质模式的参数表」：每个模式一组 (contrast, saturation, sharpness) 小整数。

EXIF 实测已知：i-Enhance (0,0,0) / Vivid (2,2,2) / Natural (0,2,2) /
Muted (1,0,1) / Portrait (0,2,1)

以 Vivid 的 (2,2,2) 为锚做向量化搜索，再检查同 stride 上是不是还排着其它已知三元组。
"""
import sys

import numpy as np

BASE = 0x60021020
IMG = r"analysis/00_0100_40300020.decoded.bin"

KNOWN = {(0, 0, 0), (2, 2, 2), (0, 2, 2), (1, 0, 1), (0, 2, 1)}


def main():
    data = np.frombuffer(open(IMG, "rb").read(), dtype=np.uint8)
    n = len(data)
    assert data[0] == 2 and data[1] == 2 and data[2] == 2 or True
    m = (data[:-2] == 2) & (data[1:-1] == 2) & (data[2:] == 2)
    anchors = np.nonzero(m)[0]
    print("anchor (2,2,2) count:", len(anchors))

    hits = 0
    for stride in (3, 4, 6, 8):
        for a in anchors:
            for back in range(0, 23):
                off = int(a) - back * stride
                if off < 0 or off + 23 * stride > n:
                    continue
                seen = set()
                rows = []
                good = True
                for k in range(23):
                    p = off + k * stride
                    t = (int(data[p]), int(data[p + 1]), int(data[p + 2]))
                    rows.append(t)
                    if t in KNOWN:
                        seen.add(t)
                    elif any(v > 8 for v in t):
                        good = False
                        break
                if not good or len(seen) < 4:
                    continue
                hits += 1
                print(f"\nstride={stride} start=0x{BASE+off:08x} matched={sorted(seen)}")
                for k, t in enumerate(rows):
                    mark = "  <<<" if t in KNOWN else ""
                    print(f"  [{k:2d}] {t}{mark}")
                if hits >= 8:
                    print("\nstopping after 8 hits")
                    return
    print("\ndone, hits:", hits)


if __name__ == "__main__":
    sys.exit(main())
