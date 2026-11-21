# -*- coding: utf-8 -*-
"""Probe 1: dump the 8 colour matrices at 0x605ECECA and hunt for readers."""
import struct, sys, os

BIN = r"G:\em10fw\analysis\00_0100_40300020.decoded.bin"
BASE = 0x60021020

data = open(BIN, "rb").read()
def off(a): return a - BASE
def rd(a, n): return data[off(a):off(a)+n]

M0 = 0x605ECECA
N = 8
L = 18

print("=== region dump 0x605ECE80..0x605ED080 ===")
for row in range(0, 0x200, 16):
    a = 0x605ECE80 + row
    b = rd(a, 16)
    hx = " ".join("%02X" % c for c in b)
    print("%08X  %-47s  %s" % (a, hx, "".join(chr(c) if 32 <= c < 127 else "." for c in b)))

print("\n=== 8 matrices (s16/256) ===")
for i in range(N):
    a = M0 + i*L
    v = struct.unpack("<9h", rd(a, L))
    sh = [x/256.0 for x in v]
    print("m%d @ %08X raw=%s" % (i, a, v))
    for r in range(3):
        print("      [%8.4f %8.4f %8.4f]  sum=%.4f" % tuple(sh[r*3:r*3+3] + [sum(sh[r*3:r*3+3])]))

print("\n=== six-axis records after matrices ===")
for i in range(12):
    a = M0 + N*L + i*24
    v = struct.unpack("<12h", rd(a, 24))
    print("rec%2d @ %08X  %s" % (i, a, v))

# ---- searches -------------------------------------------------------------
print("\n=== u32 LE values pointing into 0x605EC000..0x605ED400 (whole file) ===")
lo, hi = 0x605EC000, 0x605ED400
hits = []
for o in range(0, len(data)-4, 2):
    v = struct.unpack_from("<I", data, o)[0]
    if lo <= v < hi:
        hits.append((BASE+o, v))
print("count", len(hits))
for a, v in hits[:80]:
    print("  %08X -> %08X" % (a, v))

print("\n=== FC imm32 (mov imm32,An) with target in 0x605ECE80..0x605ED400 ===")
for o in range(0, len(data)-5):
    if data[o] != 0xFC:
        continue
    v = struct.unpack_from("<I", data, o+2)[0]
    if 0x605ECE80 <= v < 0x605ED400:
        print("  %08X  regbyte=%02X  -> %08X" % (BASE+o, data[o+1], v))
