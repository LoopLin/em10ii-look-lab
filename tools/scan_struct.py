#!/usr/bin/env python3
"""Offline structural scan of the decoded E-M10 II main component.

Writes analysis/struct_scan.txt. Read-only over the .decoded.bin files.
"""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "analysis" / "00_0100_40300020.decoded.bin"
ASSET1 = ROOT / "analysis" / "01_0101_41760020.decoded.bin"
ASSET2 = ROOT / "analysis" / "02_0106_43160020.decoded.bin"
BASE = 0x60021020
OUT = ROOT / "analysis" / "struct_scan.txt"

lines = []


def emit(s=""):
    lines.append(s)
    print(s)


def u32be(d, off):
    return struct.unpack_from(">I", d, off)[0]


def scan_descriptors(d, base):
    emit("== section-descriptor candidate scan (12-byte, big-endian) ==")
    n = len(d)
    formats = [
        ("start(0x6..),src(0x4..),dst(0x6..)", lambda a, b, c: 0x60000000 <= a < 0x64000000 and 0x40000000 <= b < 0x50000000 and 0x60000000 <= c < 0x64000000),
        ("src(0x4..),dst(0x6..),end(0x6..)", lambda a, b, c: 0x40000000 <= a < 0x50000000 and 0x60000000 <= b < 0x64000000 and 0x60000000 <= c < 0x64000000 and c > b),
        ("start(0x6..),end(0x6..),src(0x4..)", lambda a, b, c: 0x60000000 <= a < 0x64000000 and 0x60000000 <= b < 0x64000000 and 0x40000000 <= c < 0x50000000 and b > a),
        ("start(0x6..),dst(0x6..),src(0x4..)", lambda a, b, c: 0x60000000 <= a < 0x64000000 and 0x60000000 <= b < 0x64000000 and 0x40000000 <= c < 0x50000000),
    ]
    for name, chk in formats:
        hits = []
        for off in range(0, n - 12, 4):
            a, b, c = struct.unpack_from(">III", d, off)
            if chk(a, b, c):
                hits.append(off)
        runs = []
        i = 0
        while i < len(hits):
            run = [hits[i]]
            j = i + 1
            while j < len(hits) and hits[j] == run[-1] + 12:
                run.append(hits[j])
                j += 1
            runs.append(run)
            i = j
        good = [r for r in runs if len(r) >= 3]
        emit(f"-- {name}: {len(hits)} hits, {len(good)} runs>=3")
        for r in good[:15]:
            emit(f"   table addr=0x{base + r[0]:08x} fileoff=0x{r[0]:06x} n={len(r)}")
            for off in r[:14]:
                a, b, c = struct.unpack_from(">III", d, off)
                emit(f"      0x{a:08x} 0x{b:08x} 0x{c:08x}  delta=0x{c - b:x}")
            if len(r) > 14:
                emit("      ...")


def scan_strings(d, base):
    emit("\n== key strings (ASCII + UTF-16LE/BE) ==")
    keys = [
        "i-Enhance", "iEnhance", "Vivid", "Natural", "Muted", "Portrait",
        "Picture Mode", "PICTURE MODE", "ART FILTER", "Art Filter",
        "MONOCHROME", "Monochrome", "GRAIN", "Grain", "COLOR CREATOR",
        "Color Creator", "Highlight", "Shadow", "Contrast", "Sharpness",
        "Saturation", "White Balance", "Gradation", "Noise Reduction",
        "Firmware", "firmware", "Version", "version", "Update", "update",
        "DCOLYMP", "OLY_E", "E120", "CHECKSUM", "checksum", "Verify", "verify",
        "LCD", "LUT", "look", "Look", "FILM", "Film", "Tone", "tone curve",
    ]
    for k in keys:
        found = []
        for enc in ("ascii", "utf-16-le", "utf-16-be"):
            try:
                pat = k.encode(enc)
            except Exception:
                continue
            start = 0
            if enc == "ascii":
                while True:
                    i = d.find(pat, start)
                    if i < 0:
                        break
                    found.append((i, enc))
                    start = i + 1
                    if len(found) > 40:
                        break
            else:
                # UTF-16: search for both zero-byte alignments
                start = 0
                while True:
                    i = d.find(pat, start)
                    if i < 0:
                        break
                    found.append((i, enc))
                    start = i + 1
                    if len(found) > 40:
                        break
        if found:
            emit(f"-- {k!r}: {len(found)} hits")
            for off, enc in found[:8]:
                emit(f"      0x{BASE + off:08x} (fileoff 0x{off:06x}) {enc}")


def scan_pointer_density(d, base, step=0x1000):
    emit("\n== pointer density per 64 KiB (0x40xxxxxx / 0x60xxxxxx BE words) ==")
    n = len(d)
    for blk in range(0, n, 0x10000):
        hi40 = hi60 = 0
        end = min(blk + 0x10000, n - 4)
        for off in range(blk, end, 4):
            if off % 4:
                continue
            v = u32be(d, off)
            if 0x40000000 <= v < 0x50000000:
                hi40 += 1
            elif 0x60000000 <= v < 0x64000000:
                hi60 += 1
        if hi40 or hi60:
            emit(f"0x{base + blk:08x}: p40={hi40:5d} p60={hi60:5d}")


def zero_map(d, base, minrun=0x1000):
    emit("\n== zero runs >= 4 KiB ==")
    n = len(d)
    run = 0
    start = 0
    total = 0
    for i in range(n):
        if d[i] == 0:
            if run == 0:
                start = i
            run += 1
        else:
            if run >= minrun:
                emit(f"0x{base + start:08x}..0x{base + i:08x} len=0x{run:x} ({run})")
                total += run
            run = 0
    if run >= minrun:
        emit(f"0x{base + start:08x}..0x{base + n:08x} len=0x{run:x} ({run})")
        total += run
    emit(f"total zero in runs>=4K: {total} / {n} = {total / n:.1%}")


def main():
    d = MAIN.read_bytes()
    emit(f"main: {len(d)} bytes, base 0x{BASE:08x}")
    scan_descriptors(d, BASE)
    scan_strings(d, BASE)
    scan_pointer_density(d, BASE)
    zero_map(d, BASE)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwritten {OUT}")


if __name__ == "__main__":
    main()
