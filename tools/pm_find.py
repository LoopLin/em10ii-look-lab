"""Byte-pattern / dword-sequence search over the decoded image."""
import sys
import struct

BASE = 0x60021020
d = open(r'G:\em10fw\analysis\00_0100_40300020.decoded.bin', 'rb').read()


def find(pat, limit=40):
    out = []
    i = d.find(pat)
    while i >= 0 and len(out) < limit:
        out.append(i + BASE)
        i = d.find(pat, i + 1)
    return out


def seq32(vals, limit=40):
    pat = b''.join(struct.pack('<I', v) for v in vals)
    return find(pat, limit)


def seq16(vals, limit=40):
    pat = b''.join(struct.pack('<H', v) for v in vals)
    return find(pat, limit)


mode = sys.argv[1]
if mode == 'b':
    pat = bytes.fromhex(sys.argv[2])
    hits = find(pat)
    print('bytes %s -> %d' % (sys.argv[2], len(hits)))
    for h in hits:
        print('  %08X' % h)
elif mode == 'd':
    vals = [int(x, 0) for x in sys.argv[2:]]
    hits = seq32(vals)
    print('dwords %s -> %d' % (vals, len(hits)))
    for h in hits:
        print('  %08X' % h)
elif mode == 'w':
    vals = [int(x, 0) for x in sys.argv[2:]]
    hits = seq16(vals)
    print('words %s -> %d' % (vals, len(hits)))
    for h in hits:
        print('  %08X' % h)
