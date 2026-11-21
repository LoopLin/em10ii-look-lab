#!/usr/bin/env python3
"""opench helper: scan call/jmp/imm32/u32 refs in the decoded block0 image.

Usage:
  python tools/oc_xref.py call 0x604B7AFF [more...]
  python tools/oc_xref.py jmp  0x604B7AFF ...
  python tools/oc_xref.py imm  0x605ECECA ...
  python tools/oc_xref.py u32  0x605ECECA ...
  python tools/oc_xref.py range-u32 0x605ECEB0 0x605ED200

call/jmp: MN10300 `CD d16` / `CC d16`, target = cia + int16(disp) (verified
against disr output). imm: FC xx <u32> (mov imm32,An). u32: little-endian u32
anywhere in the image, printed with the containing RAM range.
"""
import struct
import sys

BASE = 0x60021020
IMG = "analysis/00_0100_40300020.decoded.bin"


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    mode = argv[0]
    data = open(IMG, "rb").read()
    n = len(data)
    if mode in ("call", "jmp"):
        op = 0xCD if mode == "call" else 0xCC
        targets = {int(a, 0) for a in argv[1:]}
        hits = {t: [] for t in targets}
        for i in range(n - 3):
            if data[i] != op:
                continue
            d = struct.unpack_from("<h", data, i + 1)[0]
            cia = BASE + i
            tgt = (cia + d) & 0xFFFFFFFF
            if tgt in hits:
                hits[tgt].append(cia)
        for t in sorted(hits):
            print(f"{mode} {t:#010x}: {len(hits[t])}")
            for c in hits[t]:
                print(f"  {c:#010x}")
    elif mode == "imm":
        targets = {int(a, 0) for a in argv[1:]}
        hits = {t: [] for t in targets}
        for i in range(n - 6):
            if data[i] != 0xFC:
                continue
            v = struct.unpack_from("<I", data, i + 2)[0]
            if v in hits:
                hits[v].append((BASE + i, data[i + 1]))
        for t in sorted(hits):
            print(f"imm {t:#010x}: {len(hits[t])}")
            for c, reg in hits[t]:
                print(f"  {c:#010x} reg={reg:#04x}")
    elif mode == "u32":
        targets = {int(a, 0) for a in argv[1:]}
        hits = {t: [] for t in targets}
        for i in range(n - 4):
            v = struct.unpack_from("<I", data, i)[0]
            if v in hits:
                hits[v].append(BASE + i)
        for t in sorted(hits):
            print(f"u32 {t:#010x}: {len(hits[t])}")
            for c in hits[t][:60]:
                print(f"  {c:#010x}")
    elif mode == "range-u32":
        lo, hi = int(argv[1], 0), int(argv[2], 0)
        for i in range(n - 4):
            v = struct.unpack_from("<I", data, i)[0]
            if lo <= v < hi:
                print(f"  {BASE + i:#010x} -> {v:#010x}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
