#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""addscan.py <imm16 hex ...>  -- find 'add imm16,aN' (FA D0..D3 imm16) sites, plus
'mov imm16,dN' (2C xx yy) sites, and report them grouped, into analysis/addscan.txt
"""
import sys

BASE = 0x60021020
IMG = r"analysis\00_0100_40300020.decoded.bin"
OUT = r"analysis\addscan.txt"

d = open(IMG, "rb").read()
lines = []
for a in sys.argv[1:]:
    imm = int(a, 0)
    lo, hi = imm & 0xFF, (imm >> 8) & 0xFF
    sites = []
    for n in range(4):
        pat = bytes([0xFA, 0xD0 + n, lo, hi])
        i = d.find(pat)
        while i >= 0:
            sites.append("%s(a%d)" % ("0x%08X" % (i + BASE), n))
            i = d.find(pat, i + 1)
    lines.append("add %#06x : %d sites  %s" % (imm, len(sites), " ".join(sites[:30])))

open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("wrote", OUT)
