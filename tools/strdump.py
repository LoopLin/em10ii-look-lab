#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""strdump.py <RAMstart> <RAMend> [outfile]
Print printable ASCII runs (len>=4) with their RAM addresses.
"""
import sys

BASE = 0x60021020
IMG = r"analysis\00_0100_40300020.decoded.bin"


def main():
    start = int(sys.argv[1], 0)
    end = int(sys.argv[2], 0)
    out = sys.argv[3] if len(sys.argv) > 3 else None
    data = open(IMG, "rb").read()
    a = start - BASE
    b = min(end - BASE, len(data))
    lines = []
    i = a
    cur = bytearray()
    cstart = a
    while i < b:
        c = data[i]
        if 0x20 <= c < 0x7F:
            if not cur:
                cstart = i
            cur.append(c)
        else:
            if len(cur) >= 4:
                lines.append("0x%08X  %s" % (cstart + BASE, cur.decode("ascii")))
            cur = bytearray()
        i += 1
    if len(cur) >= 4:
        lines.append("0x%08X  %s" % (cstart + BASE, cur.decode("ascii")))
    txt = "\n".join(lines) + "\n"
    if out:
        open(out, "w", encoding="utf-8").write(txt)
        print("wrote %s (%d strings)" % (out, len(lines)))
    else:
        print(txt)


main()
