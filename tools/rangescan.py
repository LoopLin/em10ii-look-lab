#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rangescan.py <lo> <hi>  -- list all u32 values in [lo,hi] found in the image, grouped by location."""
import sys

BASE = 0x60021020
IMG = r"analysis\00_0100_40300020.decoded.bin"
OUT = r"analysis\rangescan.txt"

lo = int(sys.argv[1], 0)
hi = int(sys.argv[2], 0)
d = open(IMG, "rb").read()
lines = []
n = len(d) - 3
for i in range(0, n, 2):
    v = int.from_bytes(d[i:i + 4], "little")
    if lo <= v <= hi:
        lines.append("0x%08X : 0x%08X" % (i + BASE, v))
open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("wrote %s (%d)" % (OUT, len(lines)))
