import sys, struct
BASE = 0x60021020
IMG = r"analysis\00_0100_40300020.decoded.bin"
data = open(IMG, "rb").read()
lo, hi = sys.argv[1], sys.argv[2]
lo = int(lo, 16); hi = int(hi, 16)
step = 8
for a in range(lo, hi, step*2):
    off = a - BASE
    chunk = data[off:off+step*2]
    if not chunk:
        break
    vals = []
    for i in range(0, len(chunk)-1, 2):
        v = struct.unpack_from("<H", chunk, i)[0]
        vals.append("%04X/%d" % (v, v))
    asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    print("%08X  %s  |%s|" % (a, " ".join(vals), asc))
