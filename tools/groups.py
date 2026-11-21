#!/usr/bin/env python3
"""Enumerate tone group tables (each 0x20 = 8 pointers) from 0x60600920 upward."""
import struct
IMG='analysis/00_0100_40300020.decoded.bin'; BASE=0x60021020
d=open(IMG,'rb').read()
def dw(a): return struct.unpack_from('<I', d, a-BASE)[0]
TONE_LO, TONE_HI = 0x605D8344, 0x605D8344+0x200*64
def ok(v): return TONE_LO <= v < TONE_HI
groups=[]
a=0x60600920
while a < 0x60600C00:
    ptrs=[dw(a+4*i) for i in range(8)]
    if sum(ok(p) for p in ptrs) < 6: break
    groups.append((a,ptrs)); a+=0x20
print('groups found:', len(groups))
for i,(a,p) in enumerate(groups):
    print('g%-2d 0x%08x  ' % (i,a) + ' '.join('%08x'%x for x in p))
# map pointer -> tone table index
def tidx(v):
    if ok(v): return (v-TONE_LO)//0x200
    return None
print()
for i,(a,p) in enumerate(groups):
    print('g%-2d tables: %s' % (i, [tidx(x) for x in p]))
