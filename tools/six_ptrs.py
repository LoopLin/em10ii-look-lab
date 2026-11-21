import struct

BASE = 0x60021020
d = open(r"analysis\00_0100_40300020.decoded.bin", "rb").read()
TEXT_LO, TEXT_HI = 0x60021020, 0x605C0000

def ram(i):
    return BASE + i

targets = [(0x60622000, 0x60624000), (0x6062C000, 0x6062D000)]
for lo, hi in targets:
    hits = {}
    for i in range(0, len(d) - 4, 2):
        v = struct.unpack_from("<I", d, i)[0]
        if lo <= v < hi:
            r = ram(i)
            if TEXT_LO <= r < TEXT_HI:
                hits.setdefault(v, []).append(r)
    print("== pointers into %08x..%08x : %d distinct" % (lo, hi, len(hits)))
    for v in sorted(hits):
        print("  %08x <- %s" % (v, " ".join("%08x" % x for x in hits[v][:6])))

# also: which code refs any address in the 0x605ECF00..0x605ED240 param preset table?
lo, hi = 0x605ECF00, 0x605ED300
hits = {}
for i in range(0, len(d) - 4, 2):
    v = struct.unpack_from("<I", d, i)[0]
    if lo <= v < hi:
        r = ram(i)
        if TEXT_LO <= r < TEXT_HI:
            hits.setdefault(v, []).append(r)
print("== pointers into preset table 605ECF00..605ED300: %d distinct" % len(hits))
print("   distinct addrs: " + " ".join("%08x" % v for v in sorted(hits)))
callers = sorted({r for v in hits for r in hits[v]})
print("   ref sites: " + " ".join("%08x" % r for r in callers))
