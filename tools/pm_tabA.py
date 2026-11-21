"""Dump settings registry table A (93 x 20B @ file 0x36F000) with name strings."""
import struct

BASE = 0x60021020
d = open(r'G:\em10fw\analysis\00_0100_40300020.decoded.bin', 'rb').read()


def rd(ram, n):
    o = ram - BASE
    return d[o:o + n] if 0 <= o < len(d) else b''


def cstr(ram):
    s = rd(ram, 80).split(b'\x00')[0]
    try:
        return s.decode('ascii')
    except Exception:
        return repr(s)


for i in range(93):
    o = 0x36F000 + i * 20
    m, w, sid, np_, lp = struct.unpack('<5I', d[o:o + 20])
    print('%2d mask=%06X w=%03X sid=%06X name=%08X %-40r list=%08X'
          % (i, m, w, sid, np_, cstr(np_), lp))
