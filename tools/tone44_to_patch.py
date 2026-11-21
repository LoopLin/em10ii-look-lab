#!/usr/bin/env python3
"""把一条 256 项 10 位 LUT 原位写进 44 张 tone 表（0x605D8344 + k*0x200）。

用法：python tools/tone44_to_patch.py <lut.txt> <out.patch>
lut.txt 是 256 个十进制或十六进制整数（0..1023），空白分隔。
"""
import sys

BASE = 0x605D8344
STEP = 0x200
COUNT = 44


def main():
    src, dst = sys.argv[1], sys.argv[2]
    txt = open(src).read().split()
    words = [int(w, 0) if w.lower().startswith("0x") else int(w) for w in txt]
    assert len(words) == 256, len(words)
    words = [min(1023, max(0, w)) for w in words]
    data = b"".join(w.to_bytes(2, "little") for w in words)

    lines, changed = [], 0
    for k in range(COUNT):
        addr = BASE + k * STEP
        for off in range(0, len(data), 32):
            lines.append(f"ram 0x{addr + off:08x}={data[off:off + 32].hex()}")
        changed += len(data)

    open(dst, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"{COUNT} tables, {len(lines)} lines, {changed} bytes -> {dst}")
    print("first:", words[0], words[64], words[128], words[192], words[255])


if __name__ == "__main__":
    main()
