#!/usr/bin/env python3
"""Read-only structural scanning of decoded Olympus firmware blocks.

Deliberately offline: operates only on files already extracted by
oly_container.py.  Nothing here validates or guarantees flashability.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import oly_container as oc  # noqa: E402

ANALYSIS = os.path.join(os.path.dirname(HERE), "analysis")


def blocks():
    with open(os.path.join(os.path.dirname(HERE), "stock",
                           "OLY_E_120_1400_0000_0000.BIN"), "rb") as f:
        data = f.read()
    return oc.parse(data)


def block_files():
    out = []
    for b in blocks():
        p = os.path.join(ANALYSIS, b.filename)
        out.append((b, p, open(p, "rb").read()))
    return out


def shannon(buf: bytes) -> float:
    if not buf:
        return 0.0
    counts = [0] * 256
    for byte in buf:
        counts[byte] += 1
    n = len(buf)
    return -sum((c / n) * math.log2(c / n) for c in counts if c)


def cmd_stats(args):
    for b, path, buf in block_files():
        n = len(buf)
        chunk = 0x10000
        chunks = [buf[i:i + chunk] for i in range(0, n, chunk)]
        ents = [shannon(c) for c in chunks]
        zeros = buf.count(0)
        printable = sum(1 for c in buf if 0x20 <= c < 0x7F or c in (9, 10, 13))
        print(f"== block {b.index} kind=0x{b.kind:04x} {path}")
        print(f"   size={n} (0x{n:x}) addr=0x{b.address:08x} payload_addr=0x{b.address + 32:08x}")
        print(f"   chunks={len(chunks)} entropy min={min(ents):.3f} max={max(ents):.3f} "
              f"mean={sum(ents) / len(ents):.3f}")
        hist = {}
        for e in ents:
            hist[round(e * 2) / 2] = hist.get(round(e * 2) / 2, 0) + 1
        print("   entropy histogram:", dict(sorted(hist.items())))
        print(f"   zero bytes={zeros} ({zeros / n:.1%})  printable={printable / n:.1%}")
        # long zero runs
        runs = []
        start = None
        for i, byte in enumerate(buf):
            if byte == 0:
                if start is None:
                    start = i
            else:
                if start is not None and i - start >= 4096:
                    runs.append((start, i - start))
                start = None
        if start is not None and n - start >= 4096:
            runs.append((start, n - start))
        big = sorted(runs, key=lambda r: -r[1])[:8]
        print(f"   zero runs >=4K: {len(runs)}; largest: " +
              ", ".join(f"0x{o:x}+0x{l:x}" for o, l in big))
        head = buf[:64].hex()
        print(f"   head: {head}")


def cmd_entropy(args):
    """Per-64K entropy line, compact; for eyeballing big transitions."""
    for b, path, buf in block_files():
        if args.block is not None and b.index != args.block:
            continue
        print(f"== block {b.index} 0x{b.address:08x} {len(buf)}")
        chunk = 0x10000
        line = []
        for i in range(0, len(buf), chunk):
            e = shannon(buf[i:i + chunk])
            line.append(f"{e:4.1f}")
            if len(line) == 16:
                print(f"  {i // chunk:4d}K  " + " ".join(line))
                line = []
        if line:
            print(f"  {len(buf) // chunk // 16 * 16:4d}K  " + " ".join(line))


def cmd_ptr(args):
    """Count 32-bit LE values pointing into each candidate address window.

    Confirms where a block believes it is loaded.
    """
    lo_lim, hi_lim = 0x30000000, 0x50000000
    for b, path, buf in block_files():
        if args.block is not None and b.index != args.block:
            continue
        n = len(buf)
        vals = struct.unpack_from(f"<{n // 4}I", buf, 0)
        total = 0
        inwin = 0
        base_guess = b.address + 32
        buckets = {}
        for v in vals:
            total += 1
            if lo_lim <= v < hi_lim:
                inwin += 1
                buckets[v >> 16] = buckets.get(v >> 16, 0) + 1
        print(f"== block {b.index} words={total} in 0x3000_0000..0x4fff_ffff: {inwin} "
              f"({inwin / total:.2%})")
        top = sorted(buckets.items(), key=lambda kv: -kv[1])[:12]
        print("   top 64K pages: " + ", ".join(f"0x{k << 16:08x}:{v}" for k, v in top))
        # how many point inside this block's own [base, base+size)
        own = sum(1 for v in vals if base_guess <= v < base_guess + n)
        print(f"   inside own payload window 0x{base_guess:08x}..0x{base_guess + n:08x}: {own} "
              f"({own / total:.2%})")
        for delta in (-0x20, 0, 0x20, -0x1000, 0x1000):
            base = b.address + delta
            c = sum(1 for v in vals if base <= v < base + n)
            print(f"     base 0x{base:08x}: {c} ({c / total:.2%})")


def cmd_strings(args):
    """ASCII string census for one block: counts, lengths, sample."""
    minlen = args.min
    pat = re.compile(rb"[\x20-\x7e]{%d,}" % minlen)
    for b, path, buf in block_files():
        if args.block is not None and b.index != b.index:
            pass
        if args.block is not None and b.index != args.block:
            continue
        hits = [(m.start(), m.group()) for m in pat.finditer(buf)]
        print(f"== block {b.index}: {len(hits)} strings >= {minlen}")
        if args.dump:
            for off, s in hits:
                print(f"  0x{off:08x} 0x{b.address + 32 + off:08x} {s[:100]!r}")
        else:
            for off, s in hits[:args.limit]:
                print(f"  0x{off:08x} {s[:110]!r}")


def cmd_find(args):
    """Literal / regex byte search across blocks with address annotation."""
    needle = args.hex
    data = bytes.fromhex(needle) if needle else args.text.encode()
    for b, path, buf in block_files():
        pos = 0
        while True:
            i = buf.find(data, pos)
            if i < 0:
                break
            ctx = buf[max(0, i - 16):i + len(data) + 48]
            print(f"block {b.index} off=0x{i:08x} addr=0x{b.address + 32 + i:08x} "
                  f"ctx={ctx.hex()}")
            pos = i + 1
            if args.limit and pos > 0 and False:
                break


def cmd_hexdump(args):
    b, path, buf = [(x, y, z) for x, y, z in block_files() if x.index == args.block][0]
    off = args.offset
    data = buf[off:off + args.length]
    for row in range(0, len(data), 16):
        chunk = data[row:row + 16]
        hexs = " ".join(f"{c:02x}" for c in chunk)
        text = "".join(chr(c) if 0x20 <= c < 0x7F else "." for c in chunk)
        print(f"{off + row:08x}  {hexs:<47}  {text}")


def cmd_embed(args):
    """Look for well-known embedded format signatures."""
    sigs = {
        "JPEG": b"\xff\xd8\xff",
        "JFIF": b"JFIF",
        "EXIF": b"Exif\x00\x00",
        "PNG": b"\x89PNG\r\n\x1a\n",
        "GIF": b"GIF8",
        "RIFF": b"RIFF",
        "WAVE": b"WAVE",
        "OggS": b"OggS",
        "ID3": b"ID3",
        "ZIP": b"PK\x03\x04",
        "GZIP": b"\x1f\x8b\x08",
        "BMP": b"BM",
        "TIFF": b"II*\x00",
        "ELF": b"\x7fELF",
        "OE": b"OE",
    }
    for b, path, buf in block_files():
        print(f"== block {b.index} 0x{b.address:08x}")
        for name, sig in sigs.items():
            hits = []
            pos = 0
            while True:
                i = buf.find(sig, pos)
                if i < 0:
                    break
                hits.append(i)
                pos = i + 1
                if len(hits) >= 2000:
                    break
            if hits:
                print(f"   {name:5s} {len(hits):5d}  first: " +
                      ", ".join(f"0x{h:08x}" for h in hits[:6]))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("stats")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("entropy")
    p.add_argument("--block", type=int)
    p.set_defaults(func=cmd_entropy)

    p = sub.add_parser("ptr")
    p.add_argument("--block", type=int)
    p.set_defaults(func=cmd_ptr)

    p = sub.add_parser("strings")
    p.add_argument("--block", type=int)
    p.add_argument("--min", type=int, default=8)
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--dump", action="store_true")
    p.set_defaults(func=cmd_strings)

    p = sub.add_parser("find")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--hex")
    g.add_argument("--text")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("hexdump")
    p.add_argument("block", type=int)
    p.add_argument("offset", type=lambda s: int(s, 0))
    p.add_argument("length", type=lambda s: int(s, 0), nargs="?", default=256)
    p.set_defaults(func=cmd_hexdump)

    p = sub.add_parser("embed")
    p.set_defaults(func=cmd_embed)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
