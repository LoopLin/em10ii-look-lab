import struct

BASE = 0x60021020
d = open(r"analysis\00_0100_40300020.decoded.bin", "rb").read()

def callers(target):
    out = []
    for i in range(2, len(d) - 4):
        if d[i] != 0xCD:
            continue
        disp = struct.unpack_from("<h", d, i + 1)[0]
        pc = BASE + i
        if pc + disp == target:
            out.append(pc)
    return out

for t in (0x604B7AFF, 0x604B7FE4, 0x604B7A45, 0x604B7A20, 0x604B7A8D, 0x604B7AE5,
          0x604B8049, 0x604B843D, 0x604B7C5B):
    cs = callers(t)
    print("%08x <- %d callers: %s" % (t, len(cs), " ".join("%08x" % c for c in cs[:20])))
