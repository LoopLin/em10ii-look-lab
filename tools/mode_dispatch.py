#!/usr/bin/env python3
"""Extract the mode -> handler table that ends at 0x60601cc4 (8-byte entries)."""
import struct
IMG='analysis/00_0100_40300020.decoded.bin'; BASE=0x60021020
d=open(IMG,'rb').read()
def dw(a): return struct.unpack_from('<I', d, a-BASE)[0]
END=0x60601CC4
CODE=(0x60400000,0x605C0000)
rows=[]
a=END-8
while a>END-0x200:
    key,tgt=dw(a),dw(a+4)
    if CODE[0]<=tgt<CODE[1] and key<0x1000:
        rows.append((a,key,tgt))
    a-=8
rows.reverse()
print('entries: %d  (array 0x%08x .. 0x%08x)' % (len(rows), rows[0][0], END))
for a,k,t in rows:
    print('  [0x%08x] key=%d -> 0x%08x' % (a,k,t))
