"""Precise layout of the preset parameter table around 0x605ECF00."""
import struct

BASE = 0x60021020
d = open(r"analysis\00_0100_40300020.decoded.bin", "rb").read()


def off(r):
    return r - BASE


def i16at(r):
    return struct.unpack_from("<h", d, off(r))[0]


print("=== i16 words 0x605ECF40..0x605ED080 ===")
for r in range(0x605ECF40, 0x605ED080, 2):
    v = i16at(r)
    print("%#010x: %6d (0x%04x)" % (r, v, v & 0xFFFF))

print()
print("=== search candidate 12-word records ===")
cands = {
    "Vivid sat+hue": [18, 4, 24, 0, 8, 8, 0, 2, 4, -1, -6, 0],
    "Muted sat+hue": [-22, 4, -16, 0, 4, -8, 9, 8, 10, -22, 4, -16],
}
for name, vals in cands.items():
    pat = b"".join(struct.pack("<h", v) for v in vals)
    i = d.find(pat)
    print("%-16s %s" % (name, ("file %#08x RAM %#010x" % (i, BASE + i)) if i >= 0 else "none"))
