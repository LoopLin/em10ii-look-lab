#!/usr/bin/env python3
"""Landing preflight: static checks only, and it refuses to certify.

Rules learned the hard way (2026-11-19):
  * "FF run + zero imm32 refs" is NOT enough. L10a v1 put a stub at 0x601F8840
    inside an 18 KB FF hole; the boot code clears that region and the camera
    rebooted at power-on (same family as B13/B14b: landing outside the loaded
    segments / in runtime-initialised memory).
  * Always run a dynamic sentinel (write DEADBEEF, boot in emu, re-read) before
    flashing. This tool only does the static half and prints a hard warning.

Usage:
  python tools/preflight_landing.py ADDR LEN [--payload analysis/00_0100_40300020.decoded.bin]
"""
import argparse
import struct
import sys

BASE = 0x60021020

# Ranges we already know are dangerous, with the reason.
BANNED = [
    (0x60DE0000, 0x61000000, "B13: not inside any load segment -> self-test loop"),
    (0x605DEF44, 0x605DF000, "B53: bank2, rewritten at boot -> garbled screen / crash"),
    (0x60DA4E08, 0x60DA5000, "B14b/B15: runtime stack/data area -> self-test loop on reboot"),
    (0x60DA5000, 0x60E00000, "load hole: not backed by RAM image"),
]


def load_payload(path):
    with open(path, "rb") as f:
        data = f.read()
    # decoded payload: RAM image, no 32-byte header
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("addr", type=lambda s: int(s, 0))
    ap.add_argument("len", type=lambda s: int(s, 0))
    ap.add_argument("--payload", default="analysis/00_0100_40300020.decoded.bin")
    a = ap.parse_args()

    img = load_payload(a.payload)
    off = a.addr - BASE
    if off < 0 or off + a.len > len(img):
        print("FAIL: out of image range")
        return 2

    ok = True
    for lo, hi, why in BANNED:
        if a.addr < hi and lo < a.addr + a.len:
            print("FAIL: overlaps banned range %#x-%#x (%s)" % (lo, hi, why))
            ok = False

    run = img[off:off + a.len]
    if run == b"\xff" * a.len:
        print("PASS: %d bytes of 0xFF" % a.len)
    else:
        nz = sum(1 for b in run if b != 0xFF)
        print("WARN: %d non-FF byte(s) in span" % nz)

    # imm32 references anywhere in the image pointing into the span
    refs = []
    for i in range(0, len(img) - 4, 1):
        if struct.unpack_from("<I", img, i)[0] == a.addr:
            refs.append(BASE + i)
    if refs:
        print("FAIL: %d literal reference(s), first at %#x" % (len(refs), refs[0]))
        ok = False
    else:
        print("PASS: no literal imm32 reference")

    print()
    print("*** STATIC ONLY — NOT A CLEARANCE ***")
    print("*** dynamic sentinel is still REQUIRED: write DEADBEEF, boot in emu,")
    print("*** re-read; if the bytes changed, the region is runtime-initialised.")
    print("*** (This is the check that was skipped for L10a v1.)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
