#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imm16scan.py <imm16 hex>  -- find any 2-byte little-endian occurrence that is likely an
imm16 operand (preceded by 0x26/0x2C/0x2E/0xFA/0xFC/0xFB/0xFD/0x2D), plus raw counts.
Writes analysis/imm16scan.txt
"""
import sys

BASE = 0x60021020
IMG = r"analysis\00_0100_40300020.decoded.bin"
OUT = r"analysis\imm16scan.txt"

d = open(IMG, "rb").read()
lines = []
for a in sys.argv[1:]:
    imm = int(a, 0)
    pat = bytes([imm & 0xFF, (imm >> 8) & 0xFF])
    i = d.find(pat)
    hits = []
    while i >= 0:
        prev = d[i - 1] if i >= 1 else 0
        hits.append("%s(prev=%02X)" % ("0x%08X" % (i + BASE), prev))
        i = d.find(pat, i + 1)
    lines.append("imm16 %#06x : %d raw hits" % (imm, len(hits)))
    lines.append("   " + " ".join(hits[:40]))
open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("wrote", OUT)
