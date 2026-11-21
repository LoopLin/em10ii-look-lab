#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan block0 for debug strings related to colorTone / picture mode."""
import sys

BASE = 0x60021020
IMG = 'analysis/00_0100_40300020.decoded.bin'

data = open(IMG, 'rb').read()

KWS = [b'ColorTone', b'Colortone', b'colorTone', b'SaturationLevel',
       b'ContrastLevel', b'SharpnessLevel', b'GradationLevel',
       b'PictureMode', b'PictureGroup', b'PictureType', b'ArtEffect',
       b'Contrast', b'Sharpness', b'Saturation', b'Gradation',
       b'ColorToneLevel', b'ToneLevel']

for kw in KWS:
    print('==== %s ====' % kw.decode())
    start = 0
    n = 0
    while True:
        i = data.find(kw, start)
        if i < 0:
            break
        n += 1
        s = i
        while s > 0 and 0x20 <= data[s - 1] < 0x7f:
            s -= 1
        e = i
        while e < len(data) and 0x20 <= data[e] < 0x7f:
            e += 1
        print('  off 0x%06X ram 0x%08X : %r' % (i, BASE + i, data[s:e][:140]))
        start = i + 1
        if n > 60:
            print('  ...more')
            break
