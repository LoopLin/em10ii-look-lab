#!/usr/bin/env python3
"""Little-endian structural scan of the E-M10 II main component.

Writes analysis/struct_le.txt. Read-only.
"""
from pathlib import Path
import struct
import tempfile

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "analysis" / "00_0100_40300020.decoded.bin"
BASE = 0x60021020
OUT = Path(tempfile.gettempdir()) / "struct_le.txt"
buf = []


def emit(s=""):
    buf.append(s)
    print(s)


def u32(d, off):
    return struct.unpack_from("<I", d, off)[0]


def scan12(d, label, chk):
    emit(f"\n== {label} ==")
    n = len(d)
    hits = [o for o in range(0, n - 12, 4) if chk(*struct.unpack_from("<III", d, o))]
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
    good = [r for r in runs if len(r) >= 2]
    emit(f"hits={len(hits)} runs>=2={len(good)}")
    for r in good[:12]:
        emit(f"table addr=0x{BASE + r[0]:08x} fileoff=0x{r[0]:06x} n={len(r)}")
        for o in r[:16]:
            a, b, c = struct.unpack_from("<III", d, o)
            emit(f"   {a:08x} {b:08x} {c:08x}  c-b=0x{(c - b) & 0xffffffff:x}")
        if len(r) > 16:
            emit("   ...")


def scan_strings(d):
    emit("\n== string search (ascii / utf-16-le) ==")
    keys = ["i-Enhance", "Vivid", "Natural", "Muted", "Portrait", "Picture",
            "ART", "FILTER", "MONOCHROME", "GRAIN", "Color Creator", "COLOR",
            "Highlight", "Shadow", "Contrast", "Sharpness", "Saturation",
            "Gradation", "Noise", "Firmware", "Version", "Update", "DCOLYMP",
            "OLY_E", "E120", "CHECKSUM", "CheckSum", "verify", "Verify",
            "LUT", "Film", "FILM", "Tone", "tone"]
    for k in keys:
        for enc, tag in (("ascii", "A"), ("utf-16-le", "W")):
            pat = k.encode(enc)
            out = []
            s = 0
            while len(out) < 12:
                i = d.find(pat, s)
                if i < 0:
                    break
                out.append(i)
                s = i + 1
            if out:
                emit(f"{tag} {k!r}: {len(out)}+ -> " + ", ".join(f"0x{BASE + o:08x}" for o in out[:12]))


def scan_ptrs(d):
    emit("\n== 64 KiB blocks: LE pointer counts ==")
    n = len(d)
    for blk in range(0, n, 0x10000):
        p40 = p60 = pzero = 0
        for o in range(blk, min(blk + 0x10000, n - 4), 4):
            v = u32(d, o)
            if 0x40000000 <= v < 0x50000000:
                p40 += 1
            elif 0x60000000 <= v < 0x64000000:
                p60 += 1
            elif v == 0:
                pzero += 1
        emit(f"0x{BASE + blk:08x}: p40={p40:5d} p60={p60:5d} zero={pzero:5d}")


def main():
    d = MAIN.read_bytes()
    emit(f"main size={len(d)} base=0x{BASE:08x}")
    scan12(d, "fmt A: start,src,dst (LE)",
           lambda a, b, c: 0x60000000 <= a < 0x64000000 and 0x40000000 <= b < 0x50000000 and 0x60000000 <= c < 0x64000000)
    scan12(d, "fmt B: src,dst,end (LE)",
           lambda a, b, c: 0x40000000 <= a < 0x50000000 and 0x60000000 <= b < 0x64000000 and 0x60000000 <= c < 0x64000000 and c > b)
    scan12(d, "fmt C: start,src,len (LE)",
           lambda a, b, c: 0x60000000 <= a < 0x64000000 and 0x40000000 <= b < 0x50000000 and 0x1000 <= c < 0x800000)
    scan_strings(d)
    scan_ptrs(d)
    OUT.write_text("\n".join(buf), encoding="utf-8")
    print("written", OUT)


if __name__ == "__main__":
    main()
