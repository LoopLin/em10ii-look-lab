#!/usr/bin/env python3
"""Find every reference to settings+0x4788 / neighbouring offsets.

Two kinds of hits:
  * raw little-endian 16-bit constants 0x4770..0x47A0 inside the image
  * 32-bit little-endian immediates in the same range
Each hit is shown with the surrounding bytes and, where possible, decoded as
`add imm,An` / `mov imm,An` / `movhu (d16,An)`.
"""
import struct
import sys

BASE = 0x60021020
PATH = 'analysis/00_0100_40300020.decoded.bin'


def main():
    data = open(PATH, 'rb').read()
    lo, hi = 0x4700, 0x4800
    # gather 2-byte constants in range
    hits = []
    for i in range(len(data) - 1):
        v = data[i] | (data[i + 1] << 8)
        if lo <= v <= hi:
            hits.append((i, v))
    # dedupe by address, prefer longer context
    print('raw 16-bit constants in [%#x,%#x]: %d' % (lo, hi, len(hits)))
    for off, v in hits:
        addr = BASE + off
        pre = data[max(0, off - 3):off]
        post = data[off + 2:off + 6]
        print('%08x  v=%04x  pre=%s  post=%s' % (addr, v, pre.hex(' '), post.hex(' ')))


if __name__ == '__main__':
    main()
