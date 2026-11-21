"""Scan whole image for 5-term geometric ramps with ratio ~1.065 (contrast +/-6.5%/step).

Tries u16 and u32 element widths, both monotone directions.
"""
import struct

BASE = 0x60021020
d = open(r'G:\em10fw\analysis\00_0100_40300020.decoded.bin', 'rb').read()
N = len(d)

TARGET = 1.065
TOL = 0.008


def ok(vals):
    a, b, c, e, f = vals
    if min(vals) <= 0:
        return False
    rs = [b / a, c / b, e / c, f / e]
    up = all(r > 1 for r in rs)
    dn = all(r < 1 for r in rs)
    if not (up or dn):
        return False
    return all(abs(r - TARGET) < TOL for r in rs)


for width, fmt in ((2, '<5H'), (4, '<5I')):
    print('=== width %d' % width)
    step = width
    hits = 0
    for o in range(0, N - 5 * width, step):
        vals = struct.unpack_from(fmt, d, o)
        if ok(vals):
            print('  %08X  %s' % (o + BASE, vals))
            hits += 1
            if hits > 60:
                break
    print('  hits=%d' % hits)
