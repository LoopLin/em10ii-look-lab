#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""callers.py <target...>  --  find relative 'call' (CD disp16) sites whose target matches.
Writes analysis/callers.txt
"""
import struct
import sys

BASE = 0x60021020
IMG = r"analysis\00_0100_40300020.decoded.bin"
OUT = r"analysis\callers.txt"

d = open(IMG, "rb").read()
targets = [int(a, 0) for a in sys.argv[1:]]

lines = []
for t in targets:
    out = []
    i = d.find(b"\xCD")
    while i >= 0:
        if i + 2 < len(d):
            disp = struct.unpack_from("<h", d, i + 1)[0]
            pc = BASE + i
            if pc + disp == t:
                out.append(pc)
        i = d.find(b"\xCD", i + 1)
    lines.append("0x%08X <- %d : %s" % (t, len(out), " ".join("0x%08X" % c for c in out[:24])))

txt = "\n".join(lines) + "\n"
open(OUT, "w", encoding="utf-8").write(txt)
print("wrote", OUT)
