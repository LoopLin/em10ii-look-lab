"""Find code that reads the six-axis param block (+108..+128)."""
import struct, re

BASE = 0x60021020
d = open(r"analysis\00_0100_40300020.decoded.bin", "rb").read()
TEXT_LO, TEXT_HI = 0x60021020, 0x605C0000   # code region heuristic

pats = {
    "mov (108,aN)": [b"\xf8\x00\x6c", b"\xf8\x04\x6c", b"\xf8\x08\x6c", b"\xf8\x0c\x6c"],
    "movhu (108,aN)": [b"\xf8\x60\x6c", b"\xf8\x64\x6c", b"\xf8\x68\x6c", b"\xf8\x6c\x6c"],
    "mov (120,aN)": [b"\xf8\x00\x78", b"\xf8\x04\x78", b"\xf8\x08\x78", b"\xf8\x0c\x78"],
    "movhu (120,aN)": [b"\xf8\x60\x78", b"\xf8\x64\x78", b"\xf8\x68\x78", b"\xf8\x6c\x78"],
    "mov (128,aN)": [b"\xfa\x00\x80\x00", b"\xfa\x04\x80\x00", b"\xfa\x08\x80\x00", b"\xfa\x0c\x80\x00"],
    "movhu (128,aN)": [b"\xfa\x60\x80\x00", b"\xfa\x64\x80\x00", b"\xfa\x68\x80\x00", b"\xfa\x6c\x80\x00"],
}
for name, ps in pats.items():
    hits = []
    for p in ps:
        i = d.find(p)
        while i >= 0:
            r = BASE + i
            if TEXT_LO <= r < TEXT_HI:
                hits.append(r)
            i = d.find(p, i + 1)
    hits.sort()
    print("%-18s %d hits: %s" % (name, len(hits), " ".join("%08x" % h for h in hits[:40])))
