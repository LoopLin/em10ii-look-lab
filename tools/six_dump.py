"""Dump the 0x30-stride table at 0x605ECFxx and find its referrers."""
import struct, sys

BASE = 0x60021020
d = open(r"analysis\00_0100_40300020.decoded.bin", "rb").read()


def ram(o):
    return BASE + o


def off(r):
    return r - BASE

print("=== i16 dump 0x605ECF00..0x605ED400 (0x30 stride guess) ===")
start = 0x605ECF00
for rec in range(0x605ECF00, 0x605ED080, 0x30):
    o = off(rec)
    vals = struct.unpack_from("<24h", d, o)
    sat = vals[0:6]
    hue = vals[6:12]
    rest = vals[12:24]
    print("%#010x  sat=%-28s hue=%-28s +24:%s" % (rec, sat, hue, rest))

print()
print("=== 32-bit pointers into 0x605EC000..0x605EE000 ===")
lo, hi = 0x605EC000, 0x605EE000
hits = []
for i in range(0, len(d) - 3, 4):
    v = struct.unpack_from("<I", d, i)[0]
    if lo <= v < hi:
        hits.append((i, v))
print("count", len(hits))
for i, v in hits[:200]:
    print("  at file %#08x (RAM %#010x) -> %#010x" % (i, ram(i), v))
