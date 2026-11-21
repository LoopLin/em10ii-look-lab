#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""isp_map.py  -- find every write to the 0x9E00xxxx ISP window.
Encoding:  FC 81 <imm32 LE>  -> mov d0,(0x9E00xxxx)   e.g. FC 81 A4 24 00 9E
"""
from collections import Counter

BASE = 0x60021020
IMG = r"analysis\00_0100_40300020.decoded.bin"
OUT = r"analysis\isp_map.txt"

data = open(IMG, "rb").read()
rows = []
i = 0
pat = b"\xFC\x81"
while True:
    i = data.find(pat, i + 1)
    if i < 0:
        break
    if i + 5 >= len(data):
        break
    if data[i + 4] == 0x00 and data[i + 5] == 0x9E:
        disp = data[i + 2] | (data[i + 3] << 8)
        rows.append((i + BASE, 0x9E000000 + disp))

lines = ["=== ISP window writes (0x9E00xxxx) : %d ===" % len(rows)]
for a, t in rows:
    lines.append("0x%08X -> 0x%08X" % (a, t))

c = Counter((t >> 8) << 8 for a, t in rows)
lines.append("")
lines.append("=== target words (grouped) ===")
for p, n in sorted(c.items()):
    lines.append("0x%08X  %d writes" % (p, n))

open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("wrote %s : %d writes" % (OUT, len(rows)))
