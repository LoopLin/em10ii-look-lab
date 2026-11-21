#!/usr/bin/env python3
"""Turn a 256-entry 10-bit LUT (text, hex words) into an mkpatch patch file.

Writes the table at TONE_ADDR and repoints all 168 entries of the
mode->tone-table pointer array at it.

usage: python tools/lut_to_patch.py .tmp/s24_lut.txt .tmp/s24_tone.patch
"""
import sys

TONE_ADDR = 0x60DB0000
PTR_TABLE = 0x60600920
PTR_COUNT = 168


def main():
    src, dst = sys.argv[1], sys.argv[2]
    words = [int(w, 16) for w in open(src).read().split()]
    assert len(words) == 256, len(words)
    assert all(0 <= w <= 1023 for w in words)

    lines = []
    data = b"".join(w.to_bytes(2, "little") for w in words)
    for off in range(0, len(data), 32):
        chunk = data[off:off + 32]
        lines.append(f"ram 0x{TONE_ADDR + off:08x}={chunk.hex()}")

    ptr = TONE_ADDR.to_bytes(4, "little")
    for off in range(0, PTR_COUNT * 4, 32):
        lines.append(f"ram 0x{PTR_TABLE + off:08x}={(ptr * 8).hex()}")

    open(dst, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"{len(lines)} lines, {len(data) + PTR_COUNT * 4} bytes -> {dst}")


if __name__ == "__main__":
    main()
