import struct

BASE = 0x60021020
d = open(r"analysis\00_0100_40300020.decoded.bin", "rb").read()

def dump(a, n=24, stride=8, label=""):
    o = a - BASE
    print("== %s %08x (stride %d)" % (label, a, stride))
    for k in range(n):
        b = d[o + k * stride: o + (k + 1) * stride]
        if len(b) < stride:
            break
        w = struct.unpack_from("<I", b, 0)[0]
        w2 = struct.unpack_from("<I", b, 4)[0] if stride >= 8 else 0
        h = struct.unpack_from("<H", b, 4)[0] if stride >= 8 else 0
        print("  [%2d] %08x | key=%08x | +4 raw=%08x (hi16=%04x) bytes=%s"
              % (k, a + k * stride, w, w2, h, " ".join("%02x" % x for x in b[4:8])))

dump(0x60601844, 24, 8, "filter->handler (inside sixaxis dispatcher)")
dump(0x60600144, 20, 8, "chroma filter table")
dump(0x60610B3C, 24, 4, "23-way mode jump table")
