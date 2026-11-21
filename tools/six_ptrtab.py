import struct

BASE = 0x60021020
d = open(r"analysis\00_0100_40300020.decoded.bin", "rb").read()

def find_ptr(addr):
    pat = struct.pack("<I", addr)
    out = []
    i = d.find(pat)
    while i >= 0:
        out.append(BASE + i)
        i = d.find(pat, i + 1)
    return out

targets = [0x604B7AFF, 0x604B7FE4, 0x604B7A8D, 0x604B7AE5, 0x604B7A20, 0x604B7A45,
           0x604B8049, 0x604B843D, 0x60479960]
for t in targets:
    hits = find_ptr(t)
    kinds = []
    for h in hits:
        kinds.append("%08x%s" % (h, " [data]" if h < 0x605C0000 and not (0x60021020 <= h < 0x605C0000) else ""))
    print("%08x: %d ptr refs -> %s" % (t, len(hits), " ".join(kinds)))
