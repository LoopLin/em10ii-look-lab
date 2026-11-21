"""Dump settings table A (93 rows) with name_struct words and list arrays."""
import struct

IMG = r'G:\em10fw\analysis\00_0100_40300020.decoded.bin'
BASE = 0x60021020
TABA = 0x60390020  # file 0x36F000


def ram(addr, n):
    off = addr - BASE
    if off < 0 or off + n > len(data):
        return None
    return data[off:off + n]


with open(IMG, 'rb') as f:
    data = f.read()

for i in range(93):
    raw = ram(TABA + i * 20, 20)
    mask, magic, sid, name, lst = struct.unpack('<5I', raw)
    nm = ram(name, 16)
    nw = struct.unpack('<4I', nm) if nm else (0, 0, 0, 0)
    lv = ram(lst, 40)
    arr = list(struct.unpack('<10I', lv)) if lv else []
    # trim list at first absurd value
    out = []
    for v in arr:
        if v > 0x2000:
            break
        out.append(v)
    print('%2d sid %08x mask %08x name %08x %s list %08x %s' % (i, sid, mask, name, nw[:2], lst, out))
