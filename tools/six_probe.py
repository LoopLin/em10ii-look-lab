"""Probe EM10-II firmware for six-axis hue/sat signatures (read-only)."""
import sys, struct, os

BASE = 0x60021020
p = sys.argv[1] if len(sys.argv) > 1 else r"analysis\00_0100_40300020.decoded.bin"
d = open(p, "rb").read()
print("image", p, len(d), "bytes; RAM %08x..%08x" % (BASE, BASE + len(d)))


def find(pat, label, limit=20):
    out = []
    i = d.find(pat)
    while i >= 0 and len(out) < limit:
        out.append(i)
        i = d.find(pat, i + 1)
    print("%-34s %d hit(s)" % (label, len(out)))
    for o in out:
        print("    file %#08x  RAM %#010x" % (o, BASE + o))
    return out


def i16(*vals):
    return b"".join(struct.pack("<h", v) for v in vals)


def i8(*vals):
    return bytes((v & 0xFF) for v in vals)


vivid_sat = [18, 4, 24, 0, 8, 8]
vivid_hue = [0, 2, 4, -1, -6, 0]
muted_sat = [-22, 4, -16, 0, 4, -8]
port_sat = [14, 0, 0, 5, 0, 0]

find(i16(*vivid_sat), "Vivid sat i16")
find(i16(*vivid_hue), "Vivid hue i16")
find(i16(*muted_sat), "Muted sat i16")
find(i16(*port_sat), "Portrait sat i16")
find(i8(*vivid_sat), "Vivid sat i8")
find(i8(*muted_sat), "Muted sat i8 (signed byte)")

# 6-sector angle quantization: 0,60,120,180,240,300 in various scales
for sc, name in ((1, "deg"), (10, "x10"), (100, "x100"), (256, "x256/1.5deg"), (256*6, "x1536")):
    vals = [int(round(v * sc)) for v in (0, 60, 120, 180, 240, 300)]
    if max(vals) < 32768:
        find(i16(*vals), "sector angles %s i16 %s" % (name, vals))
