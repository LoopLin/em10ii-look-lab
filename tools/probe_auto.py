#!/usr/bin/env python3
"""自动补边界的探针：跑到未映射地址 → 把该地址当普通 RAM 补上 → 重跑，直到跑通。

用法:
  python tools/probe_auto.py --pc 0x60479960 --reg A0=0x9E00A400 --reg A1=0x61E10000 \
      --reg SP=0x61E0F000 --dump 0x9E00A400:0x2000 --max-stubs 25

每次补的边界都打印出来（地址、访问宽度），最后打印 dump。
代码区默认把整个主块映射为 rx；所有 --rw 会一直保留。
"""
import argparse, json, subprocess, sys, tempfile, os

IMAGE = "analysis/00_0100_40300020.decoded.bin"
BASE = 0x60021020
CODE_END = 0x60021020 + 0x10E6FE0


def run_once(regs, rw, dump, limit, path):
    cmd = [sys.executable, "tools/em10_run.py", "--pc", hex(regs.pop("_pc")),
           "--code", f"0x{BASE:x}:0x{CODE_END - BASE:x}", "--limit", str(limit),
           "--out", path]
    for k, v in regs.items():
        cmd += ["--reg", f"{k}={hex(v)}"]
    for lo, hi in rw:
        cmd += ["--rw", f"0x{lo:x}:0x{hi - lo:x}"]
    if dump:
        cmd += ["--dump", dump]
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = p.stdout
    i = out.find("{")
    if i < 0:
        print(out, p.stderr)
        sys.exit(1)
    j = json.loads(out[i:out.rfind("}") + 1])
    return j, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pc", required=True)
    ap.add_argument("--reg", action="append", default=[])
    ap.add_argument("--rw", action="append", default=[])
    ap.add_argument("--dump", default=None)
    ap.add_argument("--limit", type=int, default=200000)
    ap.add_argument("--max-stubs", type=int, default=25)
    a = ap.parse_args()

    regs = {"_pc": int(a.pc, 16)}
    for spec in a.reg:
        k, v = spec.split("=")
        regs[k.strip()] = int(v, 16)
    rw = []
    for spec in a.rw:
        lo, ln = spec.split(":")
        lo = int(lo, 16)
        rw.append((lo, lo + int(ln, 16)))

    stubs = []
    for step in range(a.max_stubs + 1):
        j, out = run_once(dict(regs), rw, a.dump, a.limit, ".tmp/probe_auto.json")
        st = j.get("status")
        print(f"[{step}] status={st} insns={j.get('completed')}", flush=True)
        if st != "memory_fault":
            print(out[out.find("PC="):][:2000])
            break
        f = j["fault"]
        addr = f["address"]
        size = f.get("size", 4) or 4
        page = addr & ~0xFFF
        if any(lo <= addr < hi for lo, hi in rw):
            print(f"  已在映射区内仍 fault: {addr:#x} size={size}")
            break
        rw.append((page, page + 0x1000))
        stubs.append((addr, size))
        print(f"  补边界 {addr:#x} (size={size}) -> 映射 {page:#x}", flush=True)
    else:
        print("达到 max-stubs 上限")
    print("补过的边界:", ", ".join(f"{x:#x}" for x, _ in stubs))


if __name__ == "__main__":
    main()
