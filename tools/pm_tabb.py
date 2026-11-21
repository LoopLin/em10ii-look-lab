"""Dump settings table B rows and their value arrays, focusing on small element counts."""
import struct
import sys

IMG = r'G:\em10fw\analysis\00_0100_40300020.decoded.bin'
BASE = 0x60021020
TABB = 0x60390770  # file 0x36F750


def ram(addr, n):
    off = addr - BASE
    if off < 0 or off + n > len(data):
        return None
    return data[off:off + n]


with open(IMG, 'rb') as f:
    data = f.read()

want = set(int(x) for x in sys.argv[1:]) if len(sys.argv) > 1 else set(range(1, 9))

maxelem = 0
rows = 0
for i in range(700):
    raw = ram(TABB + i * 20, 20)
    if raw is None:
        break
    vp, alt, eb, flags, sid = struct.unpack('<5I', raw)
    if sid == 0 or sid > 0x0FFFFFFF:
        break
    rows += 1
    elem = eb >> 16
    blen = eb & 0xFFFF
    maxelem = max(maxelem, elem)
    if elem not in want:
        continue
    size = blen // elem if elem else 0
    vs = ram(vp, blen)
    if vs is None or size not in (1, 2, 4):
        print('sid %08x elem %d blen %d vp %08x  (values unreadable)' % (sid, elem, blen, vp))
        continue
    if size == 1:
        vals = list(vs)
    elif size == 2:
        vals = list(struct.unpack('<%dH' % elem, vs))
    else:
        vals = list(struct.unpack('<%dI' % elem, vs))
    print('sid %08x elem %d size %d vp %08x : %s' % (sid, elem, size, vp, vals))
print('rows=%d maxelem=%d' % (rows, maxelem))
