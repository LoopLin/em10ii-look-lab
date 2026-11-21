#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Find 32-bit immediate references to given RAM addresses inside block0."""
import struct
import sys

BASE = 0x60021020
IMG = 'analysis/00_0100_40300020.decoded.bin'
data = open(IMG, 'rb').read()

addrs = [int(a, 0) for a in sys.argv[1:]]
for a in addrs:
    pat = struct.pack('<I', a)
    hits = []
    start = 0
    while True:
        i = data.find(pat, start)
        if i < 0:
            break
        hits.append(i)
        start = i + 1
    print('0x%08X: %d hits' % (a, len(hits)))
    for i in hits:
        ctx = data[max(0, i - 12):i + 8].hex(' ')
        print('   off 0x%06X  ref-at-ram 0x%08X  ctx: %s' % (i, BASE + i, ctx))
