import struct

BASE = 0x60021020
d = open(r"analysis\00_0100_40300020.decoded.bin", "rb").read()

lo, hi = 0x605ECF00, 0x605ED300
rows = []
for i in range(0, len(d) - 4, 2):
    v = struct.unpack_from("<I", d, i)[0]
    if lo <= v < hi:
        r = BASE + i
        if 0x60021020 <= r < 0x605C0000:
            rows.append((r, v))
# print site->target only for interesting code zones
zones = [(0x604A0000, 0x604B0000), (0x604B5000, 0x604BC000)]
for zlo, zhi in zones:
    print("=== zone %08x..%08x" % (zlo, zhi))
    for r, v in rows:
        if zlo <= r < zhi:
            print("  %08x -> %08x" % (r, v))
print("=== data-zone sites (<0x604A0000 or >=0x605C0000) count %d" % len([1 for r, v in rows if not (0x604A0000 <= r < 0x604BC000)]))
for r, v in rows:
    if not (0x604A0000 <= r < 0x604BC000):
        print("  %08x -> %08x" % (r, v))
