#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan the decoded image for 9xu16 blocks whose three row sums are all 256
(candidate colour matrices: presets, identity, templates)."""
import numpy as np, sys, struct

PATH = "analysis/00_0100_40300020.decoded.bin"
BASE = 0x60021020
raw = open(PATH, "rb").read()
a = np.frombuffer(raw, dtype="<u2")
print("u16 count", len(a))

N = 9
hits = []
# pos = index of first u16 of the 9
limit = len(a) - N
mask = None
for i in range(limit):
    pass
# vectorised: build sums matrix by shifting
def rowsum(off):
    # sum of u16 at pos+off .. pos+off+2
    return a[off:off+ (len(a) - N)] + a[off+1:off+1 + (len(a) - N)] + a[off+2:off+2 + (len(a) - N)]

s0 = rowsum(0)
ok = (s0 == 256)
for r in (3, 6):
    s = rowsum(r)
    ok &= (s == 256)
idx = np.nonzero(ok)[0]
print("hits:", len(idx))
for i in idx:
    vals = a[i:i+9].tolist()
    # skip all-zero neighbours trivially
    print("file=0x%X ram=0x%X  %s" % (i*2, i*2 + BASE, vals))
