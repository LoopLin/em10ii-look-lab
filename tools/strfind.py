#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""strfind.py <pattern> [pattern2 ...]
Case-insensitive search over printable ASCII runs; prints addr + string."""
import sys

BASE = 0x60021020
IMG = r"analysis\00_0100_40300020.decoded.bin"


def runs(data):
    i = 0
    n = len(data)
    out = []
    cur = bytearray()
    cs = 0
    while i < n:
        c = data[i]
        if 0x20 <= c < 0x7F:
            if not cur:
                cs = i
            cur.append(c)
        else:
            if len(cur) >= 3:
                out.append((cs, cur.decode("ascii")))
            cur = bytearray()
        i += 1
    if len(cur) >= 3:
        out.append((cs, cur.decode("ascii")))
    return out


def main():
    args = sys.argv[1:]
    out = None
    if args and args[0].startswith("@"):
        out = args[0][1:]
        args = args[1:]
    pats = [p.lower() for p in args]
    data = open(IMG, "rb").read()
    lines = []
    for off, s in runs(data):
        low = s.lower()
        if any(p in low for p in pats):
            lines.append("0x%08X  %s" % (off + BASE, s))
    txt = "\n".join(lines) + "\n"
    if out:
        open(out, "w", encoding="utf-8").write(txt)
        print("wrote %s: %d hits" % (out, len(lines)))
    else:
        print(txt)


main()
