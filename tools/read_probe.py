#!/usr/bin/env python3
"""Read B75A/B75B trace records from a JPEG's EXIF UserComment.

Usage: python tools/read_probe.py <JPG> [...]
Record base R = UserComment text area +0x10 (0x61894CA0 in RAM).
"""
import struct, sys

def get_user_comment(path):
    b = open(path, 'rb').read()
    i = b.find(b'\xff\xe1')
    if i < 0:
        return None
    L = int.from_bytes(b[i+2:i+4], 'big')
    app1 = b[i+4:i+2+L]
    if app1[:6] != b'Exif\0\0':
        return None
    t = app1[6:]
    bo = '<' if t[:2] == b'II' else '>'
    r16 = lambda o: struct.unpack(bo + 'H', t[o:o+2])[0]
    r32 = lambda o: struct.unpack(bo + 'I', t[o:o+4])[0]
    def ents(off):
        for k in range(r16(off)):
            e = off + 2 + 12*k
            yield r16(e), r16(e+2), r32(e+4), r32(e+8), e
    exif = None
    for tag, ty, cnt, val, e in ents(r32(4)):
        if tag == 0x8769:
            exif = val
    if exif is None:
        return None
    for tag, ty, cnt, val, e in ents(exif):
        if tag == 0x9286:
            return t[val:val+cnt]
    return None

def show(path):
    uc = get_user_comment(path)
    print(path)
    if uc is None:
        print('  no UserComment'); return
    print('  k      :', uc[8:12])
    print('  key    :', uc[12:16].hex())
    for name, off in (('B75A', 0x10), ('B75B', 0x28)):
        rec = uc[off:off+16]
        if len(rec) < 12:
            continue
        magic = rec[:4]
        if magic not in (b'B75A', b'B75B'):
            print('  %s   : (none) %s' % (name, rec.hex())); continue
        if name == 'B75A':
            key = struct.unpack('<I', rec[4:8])[0]
            pb  = struct.unpack('<I', rec[8:12])[0]
            out = struct.unpack('<I', rec[12:16])[0]
            print('  B75A   : key=0x%08x PB=0x%08x out=0x%08x' % (key, pb, out))
        else:
            out = struct.unpack('<I', rec[4:8])[0]
            print('  B75B   : out=0x%08x mask=0x%02x PB64=0x%02x d1=%d banks=%d'
                  % (out, rec[8], rec[9], rec[10], rec[11]))

if __name__ == '__main__':
    for p in sys.argv[1:]:
        show(p)
