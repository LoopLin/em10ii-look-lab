#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""six_order.py
(a) find u32 little-endian references to given addresses
(b) scan for code that computes struct+0x6C/0x78/0x80 (six-axis param offsets)
Output is UTF-8 written to analysis/six_order.txt
"""
BASE = 0x60021020
IMG = r"analysis\00_0100_40300020.decoded.bin"
OUT = r"analysis\six_order.txt"

data = open(IMG, "rb").read()
lines = []


def hx(a):
    return "0x%08X" % a


# (a) u32 refs
targets = [0x604BAB14, 0x604BA774, 0x604BA8D2, 0x604BA83F, 0x604BAD0C,
           0x604BADD1, 0x604B5036, 0x604BAE00, 0x604BA8CA, 0x604BAB27]
lines.append("=== u32 refs ===")
for t in targets:
    b = t.to_bytes(4, "little")
    addrs = []
    i = data.find(b)
    while i >= 0:
        addrs.append(i + BASE)
        i = data.find(b, i + 1)
    lines.append("%s -> %s" % (hx(t), ", ".join(hx(a) for a in addrs) if addrs else "none"))

# (b) immediate 0x6c/0x78/0x80 used as offset base
lines.append("")
lines.append("=== add imm 0x6C/0x78/0x80,aN  (FA Dn imm16) ===")
for n in range(4):
    for imm in (0x6C, 0x78, 0x80):
        pat = bytes([0xFA, 0xD0 + n, imm, 0x00])
        i = data.find(pat)
        while i >= 0:
            lines.append("add %#x,a%d   at %s" % (imm, n, hx(i + BASE)))
            i = data.find(pat, i + 1)

lines.append("")
lines.append("=== mov imm 0x6C/0x78/0x80 to aN (imm8 forms 90..9C) ===")
# observed: 90 xx = mov imm8,a0 ; 95/9A/9C variants for a1/a2/a3
for op, an in ((0x90, 0), (0x91, 1), (0x92, 2), (0x93, 3)):
    for imm in (0x6C, 0x78, 0x80):
        pat = bytes([op, imm])
        i = data.find(pat)
        while i >= 0:
            lines.append("mov %#.2x,a%d   at %s" % (imm, an, hx(i + BASE)))
            i = data.find(pat, i + 1)

open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("wrote %s (%d lines)" % (OUT, len(lines)))
