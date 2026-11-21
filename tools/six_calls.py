import struct, sys

BASE = 0x60021020
d = open(r"analysis\00_0100_40300020.decoded.bin", "rb").read()
TEXT_HI = 0x605C0000

def calls_to(targets, span=0x20000):
    """scan whole image for `call disp16 [imm8]` whose target is in [t, t+span) for any t"""
    out = {}
    n = len(d)
    for i in range(n - 2):
        if d[i] != 0xCD:
            continue
        r = BASE + i
        if r >= TEXT_HI:
            break
        disp = struct.unpack_from("<h", d, i + 1)[0]
        tgt = r + disp
        for t in targets:
            if t <= tgt < t + span:
                out.setdefault(t, []).append((r, tgt))
    return out

targets = [int(x, 16) for x in sys.argv[1:]] or [0x604B7AFF]
res = calls_to(targets)
for t in targets:
    hits = res.get(t, [])
    print("target %08x: %d call sites" % (t, len(hits)))
    for r, tgt in hits:
        print("   %08x -> %08x" % (r, tgt))
