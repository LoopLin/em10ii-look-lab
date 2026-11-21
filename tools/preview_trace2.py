#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preview_trace2.py — 只读静态追踪：预览链的 tone 表到底由谁写、写进哪个字段。

用法（只读，不写任何镜像）：
  python tools/preview_trace2.py dis   0x604AA228 0x1f8      # objdump 反汇编切片
  python tools/preview_trace2.py hex   0x604B9C1D 0x20        # 字节
  python tools/preview_trace2.py xref  0x604AA228 0x604AA336  # 谁 call/jmp 到这些地址
  python tools/preview_trace2.py fan   0x604AA228            # 该函数体里所有 call/jmp 目标
  python tools/preview_trace2.py head  0x604B4E29            # 向前找函数头（mov sp,a3 / add -N,sp）
  python tools/preview_trace2.py dref  0x605DF34E            # 立即数/字面量引用（LE u32）

镜像：stock `analysis/00_0100_40300020.decoded.bin`（payload 已 XOR 0xFF），
地址 = 0x60021020 + offset。可选 `--img build/PREVIEW_PERMODE.BIN` 先解容器。
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile

BASE = 0x60021020
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK = os.path.join(ROOT, "analysis", "00_0100_40300020.decoded.bin")
OBJDUMP = os.path.join(ROOT, ".tmp", "olya", "toolchains", "mn10300-elf", "bin",
                       "mn10300-elf-objdump.exe")


def load(path=None):
    path = path or STOCK
    if path.endswith(".BIN") or path.endswith(".bin") and "decoded" not in path:
        # 容器 → block0 payload（复用项目里的 oly_container）
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import oly_container
        blocks = oly_container.parse(open(path, "rb").read())
        return blocks[0].payload
    return open(path, "rb").read()


def objdump_slice(data, addr, length):
    off = addr - BASE
    chunk = data[off:off + length]
    fd, tmp = tempfile.mkstemp(suffix=".bin")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(chunk)
        out = subprocess.run(
            [OBJDUMP, "-D", "-b", "binary", "-m", "mn10300",
             "--adjust-vma=0x%X" % addr, tmp],
            capture_output=True, text=True)
        lines = out.stdout.splitlines()
        keep = []
        for ln in lines:
            if ln.startswith(" ") or "file format" in ln or "Disassembly" in ln or not ln.strip():
                continue
            keep.append(ln)
        return "\n".join(keep), out.stderr
    finally:
        os.unlink(tmp)


def scan_refs(data, targets):
    """扫 call16(CD)/call32(DD)/jmp16(CC)/jmp32(DC)。target = cia + disp（项目校准口径）。"""
    hits = {t: [] for t in targets}
    n = len(data)
    for i in range(n - 3):
        op = data[i]
        if op not in (0xCD, 0xCC, 0xDD, 0xDC):
            continue
        cia = BASE + i
        if op in (0xCD, 0xCC):
            if i + 3 > n:
                continue
            disp = struct.unpack_from("<h", data, i + 1)[0]
            size = 3
        else:
            if i + 5 > n:
                continue
            disp = struct.unpack_from("<i", data, i + 1)[0]
            size = 5
        for label, tgt in (("cia", cia + disp), ("next", cia + size + disp)):
            tgt &= 0xFFFFFFFF
            if tgt in hits:
                hits[tgt].append((cia, label, data[i:i + size].hex(" ")))
    return hits


def scan_dref(data, values):
    hits = {v: [] for v in values}
    for v in values:
        pat = struct.pack("<I", v)
        start = 0
        while True:
            i = data.find(pat, start)
            if i < 0:
                break
            hits[v].append((BASE + i, i))
            start = i + 1
    return hits


def find_head(data, addr, back=0x400):
    """向前找函数头：`mov sp,a3`(0x3F) 或 `add -N,sp`(0x28 xx)。返回候选列表。"""
    cands = []
    start = max(0, addr - BASE - back)
    end = addr - BASE
    for i in range(start, end):
        b = data[i]
        if b == 0x3F:
            # 3F 也可能是别的指令首字节；标出，人工复核
            cands.append((BASE + i, "mov sp,a3", data[i:i + 8].hex(" ")))
        elif b == 0x28 and data[i + 1] & 0x80:
            cands.append((BASE + i, "add -N,sp", data[i:i + 8].hex(" ")))
    return cands


def fan(data, addr, length=0x400):
    """列出区间内所有 call/jmp 的目标。"""
    out = []
    off = addr - BASE
    chunk = data[off:off + length]
    i = 0
    while i < len(chunk) - 3:
        op = chunk[i]
        cia = addr + i
        if op in (0xCD, 0xCC):
            disp = struct.unpack_from("<h", chunk, i + 1)[0]
            out.append((cia, "call" if op == 0xCD else "jmp",
                        (cia + disp) & 0xFFFFFFFF, chunk[i:i + 3].hex(" ")))
            i += 3
        elif op in (0xDD, 0xDC):
            if i + 5 > len(chunk):
                break
            disp = struct.unpack_from("<i", chunk, i + 1)[0]
            out.append((cia, "call32" if op == 0xDD else "jmp32",
                        (cia + disp) & 0xFFFFFFFF, chunk[i:i + 5].hex(" ")))
            i += 5
        else:
            i += 1
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    img = None
    if "--img" in argv:
        k = argv.index("--img")
        img = argv[k + 1]
        argv = argv[:k] + argv[k + 2:]
    data = load(img)
    if cmd == "dis":
        addr, ln = int(argv[1], 0), int(argv[2], 0)
        text, err = objdump_slice(data, addr, ln)
        print(text)
        if err.strip():
            print("STDERR:", err.strip())
    elif cmd == "hex":
        addr, ln = int(argv[1], 0), int(argv[2], 0)
        off = addr - BASE
        b = data[off:off + ln]
        for i in range(0, len(b), 16):
            print("0x%08X  %s" % (addr + i, b[i:i + 16].hex(" ")))
    elif cmd == "xref":
        targets = [int(a, 0) for a in argv[1:]]
        hits = scan_refs(data, targets)
        for t in targets:
            print("0x%08X: %d hit(s)" % (t, len(hits[t])))
            for cia, label, raw in hits[t]:
                print("    %-4s at 0x%08X   %s" % (label, cia, raw))
    elif cmd == "fan":
        addr, ln = int(argv[1], 0), (int(argv[2], 0) if len(argv) > 2 else 0x400)
        for cia, kind, tgt, raw in fan(data, addr, ln):
            print("0x%08X  %-6s -> 0x%08X   %s" % (cia, kind, tgt, raw))
    elif cmd == "head":
        addr = int(argv[1], 0)
        back = int(argv[2], 0) if len(argv) > 2 else 0x400
        for a, why, raw in find_head(data, addr, back):
            print("0x%08X  %-10s %s" % (a, why, raw))
    elif cmd == "dref":
        vals = [int(a, 0) for a in argv[1:]]
        hits = scan_dref(data, vals)
        for v in vals:
            print("0x%08X: %d ref(s)" % (v, len(hits[v])))
            for ram, off in hits[v][:40]:
                print("    off 0x%06X  ram 0x%08X  ctx %s" %
                      (off, ram, data[max(0, off - 6):off + 6].hex(" ")))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
