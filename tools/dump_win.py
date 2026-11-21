#!/usr/bin/env python3
"""Dump printable ASCII / UTF-16LE runs (and raw hex) from windows of the
decoded main component, addressed in analysis space (base 0x60021020)."""
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "analysis" / "00_0100_40300020.decoded.bin"
BASE = 0x60021020
data = MAIN.read_bytes()

ASCII_RE = re.compile(rb"[\x20-\x7e]{4,}")
UTF16_RE = re.compile(rb"(?:[\x20-\x7e]\x00){3,}")


def dump(start, end, mode="both"):
    s = start - BASE
    e = end - BASE
    if mode in ("ascii", "both"):
        for m in ASCII_RE.finditer(data, s, e):
            print(f"A 0x{BASE + m.start():08x}  {m.group().decode('latin-1')}")
    if mode in ("utf16", "both"):
        for m in UTF16_RE.finditer(data, s, e):
            print(f"W 0x{BASE + m.start():08x}  {m.group().decode('utf-16-le', 'replace')}")


def hexdump(start, end):
    s = start - BASE
    for o in range(s, end - BASE, 16):
        chunk = data[o:o + 16]
        txt = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        print(f"0x{BASE + o:08x}  {chunk.hex(' '):<47}  {txt}")


if __name__ == "__main__":
    if sys.argv[1] == "hex":
        hexdump(int(sys.argv[2], 16), int(sys.argv[3], 16))
    else:
        mode = sys.argv[3] if len(sys.argv) > 3 else "both"
        dump(int(sys.argv[1], 16), int(sys.argv[2], 16), mode)
