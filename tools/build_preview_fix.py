#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_preview_fix.py -- 预览分派函数表指针补丁生成器（离线，只新建文件）

背景
----
预览取表分派函数在 RAM `0x604B9CC3`（payload 偏移 = 地址 - 0x60021020）。
函数里每条分支都是一条 `mov imm32,a0`（编码 `FC DC` + 4 字节小端 imm32，
所以立即数字段在指令起始 +2 处），把 d0 的取值映射到"开机现算的 bank2 表"：

    函数偏移  分支                                    立即数（RELEASE_v1）
    0x1d      d0 == -2 (cmp 0xfe)                      0x605E034E
    0x25      d0 == -1 (cmp 0xff)                      0x605DF74E
    0x2d      d0 ==  1                                 0x605DF54E
    0x3b      d0 == -3 (cmp 0xfd) 与 d0 < 0 兜底        0x605E014E
    0x43      d0 ==  2 与 d0 > 2 兜底                   0x605E054E
    0x55      d0 ==  0（那个子调用返回非 0 时）         0x605DB144  <- 已是我们

我们的四张 tone 表（同样在 RAM 里、开机由别处填好）：

    V 0x605DAF44   N 0x605DB144   M 0x605DB344   P 0x605DB544

RELEASE_v1 只改了 d0==0 那一条，所以 M/P 的**预览**仍吃原厂表（偏亮发灰）。
本工具按 `--map` 给出的"分支=表"映射，把这些 imm32 改成我们的表地址；
**其余字节一个不动**。容器重建不在这里重写：本工具只生成 `.patch` 文本，
再调用现成的 tools/mkpatch.py（内部走 tools/oly_container.py 的
replace_payload 修 block 校验和，并做落点自检）。

用法
----
    python tools/build_preview_fix.py \
        --base build/RELEASE_v1.BIN --out build/PREVIEW_FIX.BIN \
        --map "-2=V,-1=N,1=M,2=P,neg=P,0=M"

    --map 可只给部分分支；键见 SITES（-2 -1 0 1 2 neg，neg 也接受 -3/<0），
    值只能是 V/N/M/P。没给的分支保持 base 原值。

产物
----
    <out>                 新固件（base 的副本 + 这些立即数）
    <out>.spec.json       改了哪几个地址、旧->新、对应哪张表（UTF-8）

注意
----
* 哪个分支上是 V/N/M/P 由分析任务（preview-dispatch）给出，本工具不猜；
* mkpatch 不允许输出到输入同目录，所以 base 会先复制到 .tmp/preview_fix/；
* 不刷机、不碰存储卡、不改 base 与任何已有文件。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import oly_container as oc  # noqa: E402

CODE_BASE = 0x60021020
PREVIEW_FN = 0x604B9CC3          # 预览取表分派函数（RAM）
MOV_A0_OPCODE = b"\xfc\xdc"      # mov imm32,a0；imm32 紧跟其后

TABLES = {
    "V": 0x605DAF44,
    "N": 0x605DB144,
    "M": 0x605DB344,
    "P": 0x605DB544,
}

# 分支键 -> (函数内指令偏移, base 里的旧立即数, 说明)
SITES = {
    "-2": (0x1D, 0x605E034E, "d0 == -2 (cmp 0xfe)"),
    "-1": (0x25, 0x605DF74E, "d0 == -1 (cmp 0xff)"),
    "1": (0x2D, 0x605DF54E, "d0 == 1"),
    "neg": (0x3B, 0x605E014E, "d0 == -3 (cmp 0xfd) 与 d0 < 0 兜底"),
    "2": (0x43, 0x605E054E, "d0 == 2 与 d0 > 2 兜底"),
    "0": (0x55, 0x605DB144, "d0 == 0（子调用返回非 0 时）；base 里已指向 N 表"),
}

# 同一站点可以有多个叫法：-3 走的就是 neg 那条（cmp 0xfd 显式跳过去）
ALIASES = {
    "-3": "neg",
    "neg": "neg",
    "lt0": "neg",
    "<0": "neg",
    "negative": "neg",
}

BRANCH_ORDER = ["-2", "-1", "1", "neg", "2", "0"]


def imm_ram(branch):
    """分支键 -> 立即数字段的 RAM 地址（指令起始 +2）。"""
    return PREVIEW_FN + SITES[branch][0] + 2


def imm_payload_offset(branch):
    return imm_ram(branch) - CODE_BASE


def window(branch):
    start = imm_payload_offset(branch)
    return start, start + 4


def sha256(data):
    return oc.sha256(data)


def normalize_branch(text):
    s = re.sub(r"[\s]", "", text.strip().lower())
    s = s.replace("d0", "")
    s = re.sub(r"^(==|=|:)", "", s)
    if s in ALIASES:
        return ALIASES[s]
    try:
        value = int(s, 0)
    except ValueError:
        raise ValueError(
            "unknown branch %r (accept %s, or -3 / <0 for the negative fallback)"
            % (text, ", ".join(BRANCH_ORDER))
        )
    if value == -3:
        return "neg"
    if str(value) in SITES:
        return str(value)
    raise ValueError("branch d0==%d has no immediates site here" % value)


def parse_map(text):
    """'-2=V,-1=N' -> {'-2': 'V', '-1': 'N'}；同名站点重复给不同表直接报错。"""
    mapping = {}
    if not text:
        raise ValueError("--map is required, e.g. \"-2=V,-1=N,1=M,2=P,neg=P,0=M\"")
    for piece in re.split(r"[,;]", text):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ValueError("map item must look like BRANCH=TABLE: %r" % piece)
        key, _, table = piece.partition("=")
        branch = normalize_branch(key)
        table = table.strip().upper()
        if table not in TABLES:
            raise ValueError("unknown table %r (accept %s)" % (table, "/".join(TABLES)))
        if branch in mapping and mapping[branch] != table:
            raise ValueError(
                "branch-%s site given twice with different tables (%s vs %s)"
                % (branch, mapping[branch], table)
            )
        mapping[branch] = table
    if not mapping:
        raise ValueError("--map is empty")
    return mapping


def read_payload(image, index=0):
    return oc.parse(image)[index].payload


def check_sites(image_bytes, mapping, allow_unknown_old=False):
    """落点体检：opcode 必须是 FC DC，旧立即数必须是已知旧值或四张表之一。"""
    payload = read_payload(image_bytes)
    rows = []
    for branch in BRANCH_ORDER:
        off = SITES[branch][0]
        at = PREVIEW_FN - CODE_BASE + off
        raw = payload[at:at + 6]
        if len(raw) != 6 or raw[:2] != MOV_A0_OPCODE:
            raise ValueError(
                "function offset %#x is not a `mov imm32,a0` (got %s); wrong base image?"
                % (off, raw[:2].hex())
            )
        old = struct.unpack("<I", raw[2:6])[0]
        known = {SITES[b][1] for b in SITES} | set(TABLES.values())
        if old not in known and not allow_unknown_old:
            raise ValueError(
                "function offset %#x holds unexpected immediate 0x%08x; "
                "refusing (pass --allow-unknown-old to override)" % (off, old)
            )
        target = TABLES[mapping[branch]] if branch in mapping else None
        rows.append({
            "branch": branch,
            "branch_desc": SITES[branch][2],
            "function_offset": off,
            "ram_address": imm_ram(branch),
            "decoded_offset": imm_payload_offset(branch),
            "old_u32": old,
            "table": mapping.get(branch),
            "new_u32": old if target is None else target,
            "mapped": branch in mapping,
        })
    return rows


def build_patch_file(rows, path):
    lines = [
        "# preview fix: point the preview-dispatch immediates at our tone tables",
        "# function 0x%08x, base 0x%08x" % (PREVIEW_FN, CODE_BASE),
    ]
    for row in rows:
        if not row["mapped"] or row["new_u32"] == row["old_u32"]:
            continue
        data = struct.pack("<I", row["new_u32"]).hex()
        lines.append(
            "ram 0x%x=%s  # %s -> %s (0x%08x), fn+0x%02x"
            % (row["ram_address"], data, row["branch"], row["table"],
               row["new_u32"], row["function_offset"])
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [line for line in lines if not line.startswith("#")]


def run_mkpatch(stage_image, patch_file, out_path, base_sha, dry_run):
    cmd = [
        sys.executable, str(TOOLS / "mkpatch.py"), str(stage_image),
        "--patch-file", str(patch_file),
        "--out", str(out_path),
        "--code-base", hex(CODE_BASE),
        "--expect-sha256", base_sha,
    ]
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode:
        tail = (proc.stderr or proc.stdout or "").strip()[-1200:]
        raise ValueError("mkpatch.py failed (exit %d):\n%s" % (proc.returncode, tail))
    text = proc.stdout or ""
    start = text.find("{")
    if start < 0:
        raise ValueError("mkpatch.py printed no JSON report:\n%s" % text[-1200:])
    return json.loads(text[start:])


def _glue_map_arg(argv):
    """`--map "-2=V,..."` 的值以 `-` 开头，argparse 会把它当成选项；这里先粘成 --map=..."""
    glued, i = [], 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--map" and i + 1 < len(argv):
            glued.append("--map=" + argv[i + 1])
            i += 2
            continue
        glued.append(tok)
        i += 1
    return glued


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="build/RELEASE_v1.BIN",
                        help="source image (default build/RELEASE_v1.BIN)")
    parser.add_argument("--out", default="build/PREVIEW_FIX.BIN",
                        help="output image (default build/PREVIEW_FIX.BIN)")
    parser.add_argument("--map", required=True, metavar="MAP",
                        help='branch=table list, e.g. "-2=V,-1=N,1=M,2=P,neg=P,0=M"')
    parser.add_argument("--force", action="store_true",
                        help="remove an existing output/spec first")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and report the plan without writing the image or spec")
    parser.add_argument("--allow-unknown-old", action="store_true",
                        help="accept immediates that are neither a known stock value nor one of our tables")
    args = parser.parse_args(_glue_map_arg(sys.argv[1:]))

    base_path = (ROOT / args.base).resolve() if not Path(args.base).is_absolute() else Path(args.base)
    out_path = (ROOT / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    if not base_path.is_file():
        raise ValueError("base image not found: %s" % base_path)
    if out_path == base_path:
        raise ValueError("refusing to overwrite the base image")

    mapping = parse_map(args.map)
    base_bytes = base_path.read_bytes()
    base_sha = sha256(base_bytes)
    rows = check_sites(base_bytes, mapping, args.allow_unknown_old)

    print("== 预览分派表指针补丁：%s" % base_path)
    print("   base sha256 = %s" % base_sha)
    print("   函数 0x%08x（payload 偏移 0x%x），code base 0x%08x"
          % (PREVIEW_FN, PREVIEW_FN - CODE_BASE, CODE_BASE))
    print("   映射 %s" % ", ".join("%s=%s" % (k, mapping[k]) for k in BRANCH_ORDER if k in mapping))
    print()
    print("   分支   函数内   立即数 RAM    旧值         新值         表")
    for row in rows:
        new = "0x%08x" % row["new_u32"]
        table = row["table"] or "-"
        note = ""
        if row["mapped"] and row["new_u32"] == row["old_u32"]:
            note = "  (已是该表，不动)"
        print("   %-5s  +0x%02x   0x%08x   0x%08x    %-11s  %-2s%s"
              % (row["branch"], row["function_offset"], row["ram_address"],
                 row["old_u32"], new, table, note))
    print()

    changes = [r for r in rows if r["mapped"] and r["new_u32"] != r["old_u32"]]
    if not changes:
        raise ValueError("映射与 base 现有值完全一致，没有任何字节要改（拒绝生成空补丁）")

    stage = ROOT / ".tmp" / "preview_fix"
    stage.mkdir(parents=True, exist_ok=True)
    stage_image = stage / "base.bin"
    patch_file = stage / "preview_fix.patch"
    shutil.copyfile(base_path, stage_image)
    patch_lines = build_patch_file(rows, patch_file)
    print("   补丁（%d 行）:" % len(patch_lines))
    for line in patch_lines:
        print("     " + line.split("  #")[0])
    print()

    if out_path.exists() and not args.dry_run:
        if not args.force:
            raise ValueError("output already exists: %s (pass --force to replace)" % out_path)
    if args.force:
        for stale in (out_path, Path(str(out_path) + ".spec.json")):
            if stale.exists():
                stale.unlink()

    report = run_mkpatch(stage_image, patch_file, out_path, base_sha, args.dry_run)
    if args.dry_run:
        print("   dry-run: 容器检查通过，未写盘。output_sha256 = %s"
              % report["output_sha256"])
        return

    out_bytes = out_path.read_bytes()
    out_sha = sha256(out_bytes)

    # 自检：输出里每个映射点都必须是目标表地址，且 payload 差异只落在这些窗口里。
    out_payload = read_payload(out_bytes)
    in_payload = read_payload(base_bytes)
    allowed = set()
    for branch in SITES:
        start, end = window(branch)
        allowed |= set(range(start, end))
    stray = [i for i in range(len(in_payload))
             if in_payload[i] != out_payload[i] and i not in allowed]
    if stray:
        raise ValueError("output changed %d byte(s) outside the six immediates, first at %#x"
                         % (len(stray), stray[0] + CODE_BASE))
    for row in rows:
        start = row["decoded_offset"]
        got = struct.unpack("<I", out_payload[start:start + 4])[0]
        if got != row["new_u32"]:
            raise ValueError("branch %s landed on 0x%08x, expected 0x%08x"
                             % (row["branch"], got, row["new_u32"]))
    # 容器级自检：只有 block 0 的 payload 允许变化，其余 block（含 header/trailer）逐字节相同。
    in_blocks = oc.parse(base_bytes)
    out_blocks = oc.parse(out_bytes)
    if len(in_blocks) != len(out_blocks):
        raise ValueError("block count changed: %d -> %d" % (len(in_blocks), len(out_blocks)))
    for b_in, b_out in zip(in_blocks, out_blocks):
        if b_in.index == 0:
            continue
        if (b_in.payload != b_out.payload or b_in.header != b_out.header
                or b_in.trailer != b_out.trailer):
            raise ValueError("block %d changed (only block 0 may change)" % b_in.index)

    edits, unchanged = [], []
    by_ram = {int(item["input_address"], 16): item for item in report["patches"]}
    for row in rows:
        item = by_ram.get(row["ram_address"])
        entry = {
            "branch": row["branch"],
            "branch_desc": row["branch_desc"],
            "table": row["table"],
            "function_offset": "0x%02x" % row["function_offset"],
            "ram_address": "0x%08x" % row["ram_address"],
            "decoded_offset": "0x%x" % row["decoded_offset"],
            "container_offset": None if item is None else item["container_offset"],
            "old_u32": row["old_u32"],
            "new_u32": row["new_u32"],
            "old": "0x%08x" % row["old_u32"],
            "new": "0x%08x" % row["new_u32"],
            "old_bytes": struct.pack("<I", row["old_u32"]).hex(),
            "new_bytes": struct.pack("<I", row["new_u32"]).hex(),
            "changed": row["old_u32"] != row["new_u32"],
        }
        (edits if row["mapped"] else unchanged).append(entry)

    spec = {
        "tool": "build_preview_fix.py",
        "base": str(args.base),
        "base_sha256": base_sha,
        "base_size": len(base_bytes),
        "out": str(args.out),
        "out_sha256": out_sha,
        "out_size": len(out_bytes),
        "code_base": "0x%08x" % CODE_BASE,
        "preview_dispatch_ram": "0x%08x" % PREVIEW_FN,
        "map_arg": args.map,
        "tables": {name: "0x%08x" % addr for name, addr in TABLES.items()},
        "edits": edits,
        "unchanged": unchanged,
        "allowed_diff_windows": [
            {
                "branch": branch,
                "decoded_start": "0x%x" % window(branch)[0],
                "decoded_end": "0x%x" % window(branch)[1],
                "ram_start": "0x%08x" % imm_ram(branch),
            }
            for branch in BRANCH_ORDER
        ],
        "container_checks": report["container_checks"],
        "scope": "离线：只改预览分派函数的表指针立即数；容器校验和由 mkpatch 重算。"
                 "不含 bootability/恢复/真机测试，也不代表预览观感已验收。",
    }
    spec_path = Path(str(out_path) + ".spec.json")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print("   changed %d site(s), untouched %d site(s)"
          % (len([e for e in edits if e["changed"]]), len(unchanged)))
    print("   output = %s" % out_path)
    print("   sha256 = %s" % out_sha)
    print("   spec   = %s" % spec_path)
    print("   下一步：python tools/verify_preview_fix.py --base %s --out %s"
          % (args.base, args.out))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        sys.exit("error: %s" % exc)
