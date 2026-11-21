#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ptrfind.py <addr...>  -- u32 little-endian references, written to analysis/ptrfind.txt"""
import sys

BASE = 0x60021020
IMG = r"analysis\00_0100_40300020.decoded.bin"
OUT = r"analysis\ptrfind.txt"

d = open(IMG, "rb").read()
lines = []
for a in sys.argv[1:]:
    t = int(a, 0)
    b = t.to_bytes(4, "little")
    hits = []
    i = d.find(b)
    while i >= 0:
        hits.append(i + BASE)
        i = d.find(b, i + 1)
    lines.append("0x%08X <- %d : %s" % (t, len(hits), " ".join("0x%08X" % h for h in hits[:20])))
open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("wrote", OUT)
