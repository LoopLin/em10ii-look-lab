#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dump the ****_PARAM debug field-name region as C strings with addresses."""
import sys

BASE = 0x60021020
IMG = 'analysis/00_0100_40300020.decoded.bin'
data = open(IMG, 'rb').read()

lo = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x6061F700
hi = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x60622400

off = lo - BASE
end = hi - BASE
cur = []
start = off
i = off
while i < end:
    b = data[i]
    if 0x20 <= b < 0x7f:
        if not cur:
            start = i
        cur.append(b)
    else:
        if len(cur) >= 2:
            print('0x%08X (off 0x%06X) len=%3d  %s' %
                  (BASE + start, start, len(cur), bytes(cur).decode('latin1')))
        cur = []
    i += 1
if len(cur) >= 2:
    print('0x%08X (off 0x%06X) len=%3d  %s' % (BASE+start, start, len(cur), bytes(cur).decode('latin1')))
