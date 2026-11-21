"""Dump hex + ascii for RAM ranges."""
import sys

BASE = 0x60021020
d = open(r'G:\em10fw\analysis\00_0100_40300020.decoded.bin', 'rb').read()

for spec in sys.argv[1:]:
    lo, hi = [int(x, 16) for x in spec.split('-')]
    o = lo - BASE
    print('=== %08X-%08X' % (lo, hi))
    for i in range(0, hi - lo, 16):
        chunk = d[o + i:o + i + 16]
        asc = ''.join(chr(c) if 32 <= c < 127 else '.' for c in chunk)
        print('%08X  %s  %s' % (lo + i, ' '.join('%02x' % c for c in chunk), asc))
