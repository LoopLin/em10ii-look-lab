#!/usr/bin/env python3
"""扫描 block0 里所有 `call (d32,PC)`（操作码 0xDD）的目标，列出命中指定地址的调用点。

用法：
    python tools/xref_call.py 0x604DDE64 0x604FF057 0x604FF073

判定与 tools/mn10300_dis.py 一致：target = cia + signed32(LE)。
（另有少量工具用 next + d32，本脚本把两种都算出来，标在输出里。）
只做字节级筛选，不保证每个 0xDD 都是指令——命中行会给出原始字节，必要时再用
`mn10300_dis.py --off` 复核。
"""
import struct
import sys

BASE = 0x60021020
BLOCK0 = "analysis/00_0100_40300020.decoded.bin"


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    targets = {int(a, 0) for a in argv}
    data = open(BLOCK0, "rb").read()
    n = len(data)
    hits = {t: [] for t in targets}
    i = 0
    while i < n - 6:
        if data[i] != 0xDD:
            i += 1
            continue
        d32 = struct.unpack_from("<i", data, i + 1)[0]
        cia = BASE + i
        nxt = cia + 7
        for label, tgt in (("cia", cia + d32), ("next", nxt + d32)):
            tgt &= 0xFFFFFFFF
            if tgt in targets:
                hits[tgt].append((cia, label, d32, data[i:i + 7].hex()))
        i += 1
    total = 0
    for t in sorted(hits):
        print(f"target {t:08x}: {len(hits[t])} hit(s)")
        for cia, label, d32, raw in hits[t][:40]:
            print(f"  call at {cia:08x} ({label}+d32={d32:+#x})  {raw}")
        total += len(hits[t])
    print(f"total {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
