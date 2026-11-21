#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一份 look 规格（tone 256 + 色度块 k=0）打成 SD 卡固件包。

用法：
    python tools/build_look_pkg.py .tmp/looks_final/reala_ace.json --out build/S77_reala_ace.BIN
    python tools/build_look_pkg.py <spec.json> --dry-run

规格 JSON（tools/look_spec4.py 的输出）：
    { "look": "...", "tone": [256 个 0..1023 整数],
      "chroma": {"cb_x":[16],"cb_y":[16],"cb_z":[17],
                 "cr_x":[16],"cr_y":[16],"cr_z":[17]}
      或 "chroma_block_hex": "100 字节 hex" }

写什么：
    tone  0x605D8344 + k*0x200, k=0..53，各 256 x u16 LE      （54 张全量覆盖）
    chroma 0x605D4829，100 B（k=0，标准拍照档唯一读点）

纪律：
    - 只用 tools/mkpatch.py 落盘（地址换算、校验和、落点自检都由它做）。
    - 产出后**独立**重新解码容器，逐字节回读两张表，不一致就报错。
    - 不碰 stock/、不覆盖已有 build 包。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

BASE = 0x60021020
TONE_BASE, TONE_STEP, TONE_COUNT = 0x605D8344, 0x200, 54
CHROMA_BASE, CHROMA_LEN = 0x605D4829, 100
IMAGE = ROOT / "analysis" / "00_0100_40300020.decoded.bin"
STOCK = ROOT / "stock" / "OLY_E_120_1400_0000_0000.BIN"
MKPATCH = ROOT / "tools" / "mkpatch.py"


def u16_le(values):
    return b"".join(struct.pack("<H", v) for v in values)


def load_spec(path):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    tone = spec["tone"]
    if len(tone) != 256:
        raise SystemExit(f"tone 必须是 256 项，实际 {len(tone)}")
    tone = [int(v) for v in tone]
    if any(v < 0 or v > 1023 for v in tone):
        raise SystemExit("tone 取值越界（0..1023）")
    if any(tone[i] > tone[i + 1] for i in range(255)):
        raise SystemExit("tone 非单调")
    if tone[0] != 0 or tone[-1] != 1023:
        print(f"  [warn] tone 端点 {tone[0]}..{tone[-1]}（预期 0..1023）")

    if "chroma_block_hex" in spec:
        block = bytes.fromhex(spec["chroma_block_hex"].replace(" ", ""))
    else:
        c = spec["chroma"]
        block = (bytes(c["cb_x"]) + bytes(c["cb_y"]) + bytes(c["cb_z"]) + b"\x00"
                 + bytes(c["cr_x"]) + bytes(c["cr_y"]) + bytes(c["cr_z"]) + b"\x00")
    if len(block) != CHROMA_LEN:
        raise SystemExit(f"色度块必须是 100 B，实际 {len(block)}")
    return spec.get("look", Path(path).stem), tone, block


def check_chroma(block):
    """结构体检：x 单调、y 值域、z 与 y/x 一致、pad 为 0。"""
    problems = []
    for name, off in (("Cb", 0), ("Cr", 50)):
        x = list(block[off:off + 16])
        y = list(block[off + 16:off + 32])
        z = list(block[off + 32:off + 49])
        if any(x[i] > x[i + 1] for i in range(15)):
            problems.append(f"{name}: x 非单调 {x}")
        for i in range(15):
            dx = x[i + 1] - x[i]
            if dx == 0:
                continue
            want = round((y[i + 1] - y[i]) * 128 / dx)
            if abs(want - z[i + 1]) > 1:
                problems.append(f"{name}: z[{i + 1}]={z[i + 1]} 与 (dy*128/dx)={want} 不符")
    if block[49] != 0 or block[99] != 0:
        problems.append(f"pad 非 0: {block[49]}, {block[99]}")
    return problems


def build_patch_lines(tone, block):
    blob = u16_le(tone)
    lines = []
    for k in range(TONE_COUNT):
        base = TONE_BASE + k * TONE_STEP
        for off in range(0, len(blob), 32):
            lines.append(f"ram 0x{base + off:08X}={blob[off:off + 32].hex()}")
    for off in range(0, len(block), 32):
        chunk = block[off:off + 32]
        lines.append(f"ram 0x{CHROMA_BASE + off:08X}={chunk.hex()}")
    return lines


def verify_image(path, tone, block):
    """独立回读：重新解码容器，逐字节核对 54 张表与色度块。"""
    import oly_container as oc

    blocks = oc.parse(Path(path).read_bytes())
    payload = blocks[0].payload
    want_tone = u16_le(tone)
    bad = 0
    for k in range(TONE_COUNT):
        off = TONE_BASE + k * TONE_STEP - BASE
        got = payload[off:off + len(want_tone)]
        if got != want_tone:
            bad += 1
            print(f"  [FAIL] tone[{k}] 不一致")
    coff = CHROMA_BASE - BASE
    got_block = bytes(payload[coff:coff + CHROMA_LEN])
    if got_block != block:
        bad += 1
        print("  [FAIL] 色度块不一致")
    if bad:
        raise SystemExit(f"回读校验失败：{bad} 处")
    return len(payload)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path, help="输出 BIN（缺省时只做 dry-run 报告）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--image", type=Path, default=STOCK, help="打补丁的源包（默认官方包）")
    ap.add_argument("--omemu", type=Path, help="同时写出 om-emu overlay JSON")
    args = ap.parse_args()

    look, tone, block = load_spec(args.spec)
    print(f"look = {look}")
    print(f"  tone  : 256 项，端点 {tone[0]}..{tone[-1]}，"
          f"v=.18→{tone[46]}，v=.50→{tone[128]}，v=.90→{tone[230]}")
    print(f"  chroma: 100 B  Cb gain@x128={block[16 + 8]}/128  Cr gain@x128={block[66 + 8]}/128")
    problems = check_chroma(block)
    for p in problems:
        print(f"  [chroma] {p}")
    if problems:
        raise SystemExit("色度块结构体检不通过")

    patch_lines = build_patch_lines(tone, block)
    print(f"  补丁行：{len(patch_lines)}，覆盖 {54 * 512 + CHROMA_LEN} B")

    if args.dry_run or not args.out:
        print("dry-run：不写文件")
        return

    if args.out.exists():
        raise SystemExit(f"输出已存在，拒绝覆盖：{args.out}")
    tmp_patch = ROOT / ".tmp" / f"s77_{args.out.stem}.patch"
    tmp_patch.write_text("\n".join(patch_lines) + "\n", encoding="utf-8")

    cmd = [sys.executable, str(MKPATCH), str(args.image), "--out", str(args.out),
           "--patch-file", str(tmp_patch)]
    if args.omemu:
        cmd += ["--omemu-patch", str(args.omemu)]
    print("  -> " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode:
        print(proc.stdout[-2000:])
        print(proc.stderr[-2000:])
        raise SystemExit(f"mkpatch 失败（{proc.returncode}）")
    report = json.loads(proc.stdout)
    print(f"  container_checks = {report['container_checks']}")
    print(f"  sha256 = {report['output_sha256']}  size = {report['output_size']}")

    payload_len = verify_image(args.out, tone, block)
    print(f"  回读校验通过：54 张 tone + 色度块 k=0 全部一致（payload {payload_len} B）")

    meta = {
        "look": look,
        "bin": str(args.out),
        "sha256": report["output_sha256"],
        "size": report["output_size"],
        "source": report["source"],
        "source_sha256": report["source_sha256"],
        "patches": report["patches"],
        "tone_tables": TONE_COUNT,
        "chroma_block": CHROMA_BASE,
        "note": "sd name E1209999.BIN; keep official E1200001.BIN on the card",
    }
    meta_path = args.out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  元数据 -> {meta_path}")


if __name__ == "__main__":
    main()
