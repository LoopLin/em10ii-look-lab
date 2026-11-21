#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_preview_fix.py -- 预览表指针补丁的静态审计（只读，不改任何东西）

断言（任何一条不过就 closed=false，退出码 1）：

  1. 容器：两包都能被 oly_container 解析，尺寸相同、block 数相同；
  2. 变更范围：解码后的差异**只**落在预览分派函数那 6 条 `mov imm32,a0`
     的 4 字节立即数字段里（base 里 5 条是原厂表、1 条已是我们的 N 表）；
     其余 payload 字节——tone 表、矩阵、色度、钩子、桩、N 槽、弯曲门、
     UI 名——逐字节相同；block 1/2 连 header/trailer 都不许动；
  3. 语义：每个被改的立即数必须正好等于 V/N/M/P 四张表地址之一，
     且指向的地址落在 block 0 payload 内；
  4. 结构：那 6 个位置仍是 `FC DC` 开头的 `mov imm32,a0`（代码没被改写）；
  5. 规格：若存在 <out>.spec.json，则实际改动与 spec 记录一一对应
     （spec 里 changed 的点必须真的变了、unchanged 的点必须真的没变）。

产物：`<out>.sim.json`（UTF-8，`sha256` + `closed` + `scope`，flash_gate.py 认它）。

用法：
    python tools/verify_preview_fix.py --base build/RELEASE_v1.BIN --out build/PREVIEW_FIX.BIN
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import oly_container as oc  # noqa: E402
import build_preview_fix as bpf  # noqa: E402  定义只读，导入不会执行 main()

CODE_BASE = bpf.CODE_BASE
PREVIEW_FN = bpf.PREVIEW_FN
TABLES = bpf.TABLES
SITES = bpf.SITES
MOV_A0_OPCODE = bpf.MOV_A0_OPCODE

# ---- 独立抄一份"金标"，防止 build 工具的定义被人改歪（交叉校验用） ----
GOLDEN_SITES = {
    "-2": (0x1D, 0x605E034E),
    "-1": (0x25, 0x605DF74E),
    "1": (0x2D, 0x605DF54E),
    "neg": (0x3B, 0x605E014E),
    "2": (0x43, 0x605E054E),
    "0": (0x55, 0x605DB144),
}
GOLDEN_TABLES = {"V": 0x605DAF44, "N": 0x605DB144, "M": 0x605DB344, "P": 0x605DB544}

# ---- RELEASE_v1 里"绝不能动"的区域（RAM 地址, 长度） ----
TONE = [0x605DAF44, 0x605DB144, 0x605DB344, 0x605DB544]        # 4 x 512 B
MATRIX = [0x605DEEE0, 0x605DEEF2, 0x605DEF04, 0x605DEF16]      # 4 x 18 B
CHROMA = [0x605DEB44, 0x605DEBA8, 0x605DEC0C, 0x605DEC70]      # 4 x 100 B
HOOKS = [(0x604B811D, 7), (0x60479A2B, 5), (0x604B6AD6, 5), (0x604B4CAD, 6)]
STUBS = [(0x605DECD4, 69), (0x605DED1C, 38), (0x605DED44, 56),
         (0x605DEDD4, 16), (0x605DEDE4, 24), (0x605DEE3C, 16),
         (0x601ADE04, 50)]
SINGLE = [0x605E9CCC, 0x604B9F1F]                              # N 槽、弯曲门
# 注意：0x604B9D1A/B 是 d0==0 那条的立即数（本补丁允许改），不算不变量。
UI_NAMES = ('Ektachrome 100 VS', 'Elite Color 200', 'Classic Chrome', 'Pro Neg Hi')


def load(path):
    return oc.parse(path.read_bytes())


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="build/RELEASE_v1.BIN")
    parser.add_argument("--out", default="build/PREVIEW_FIX.BIN")
    parser.add_argument("--spec", default=None,
                        help="spec path (default <out>.spec.json when it exists)")
    parser.add_argument("--sim", default=None, help="sim path (default <out>.sim.json)")
    parser.add_argument("--allow-empty-diff", action="store_true",
                        help="accept an output that equals the base (no-op mapping)")
    args = parser.parse_args()

    base_path = Path(args.base) if Path(args.base).is_absolute() else ROOT / args.base
    out_path = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    for path in (base_path, out_path):
        if not path.is_file():
            raise SystemExit("error: missing %s" % path)

    checks = []

    def chk(name, cond, detail=""):
        checks.append({"name": name, "pass": bool(cond), "detail": detail})
        print(("[OK ] " if cond else "[ERR] ") + name + (("  -> " + detail) if detail and not cond else ""))

    # 0. build 工具的定义必须还是金标（防止两处定义漂移）
    same_sites = {k: (v[0], v[1]) for k, v in SITES.items()} == GOLDEN_SITES
    same_tables = TABLES == GOLDEN_TABLES
    chk("工具定义与金标一致（6 个站点 / 4 张表）", same_sites and same_tables,
        "SITES=%r TABLES=%r" % (SITES, TABLES))
    if not (same_sites and same_tables):
        raise SystemExit("error: build_preview_fix.py 的站点/表定义与金标不符，先修工具再验包")

    base_bytes = base_path.read_bytes()
    out_bytes = out_path.read_bytes()
    base_sha, out_sha = oc.sha256(base_bytes), oc.sha256(out_bytes)
    base_blocks, out_blocks = load(base_path), load(out_path)

    chk("容器可解析（base %d 块 / out %d 块）" % (len(base_blocks), len(out_blocks)),
        len(base_blocks) == len(out_blocks) and len(out_blocks) == 3)
    chk("文件尺寸相同（%d B）" % len(out_bytes), len(base_bytes) == len(out_bytes))
    chk("输出确实变了（sha 不同）", base_sha != out_sha or args.allow_empty_diff,
        "输出与 base 完全相同：没有任何表指针被改")

    base_pay, out_pay = base_blocks[0].payload, out_blocks[0].payload

    # 1. 差异窗口：6 条 mov imm32,a0 的立即数字段
    allowed = set()
    windows = []
    for branch in bpf.BRANCH_ORDER:
        start, end = bpf.window(branch)
        allowed |= set(range(start, end))
        windows.append({"branch": branch, "start": start, "end": end,
                        "ram": bpf.imm_ram(branch)})
    diff = [i for i in range(len(base_pay)) if base_pay[i] != out_pay[i]]
    stray = [i for i in diff if i not in allowed]
    chk("block0 差异 %d 字节，全部落在 6 个立即数字段内" % len(diff), not stray,
        "窗口外 %d 字节，首个 %#x" % (len(stray), (stray[0] + CODE_BASE) if stray else 0))
    touched = sorted({w["branch"] for w in windows
                      if any((w["start"] + k) in diff for k in range(4))})
    print("   实际改到的分支: %s" % (", ".join(touched) if touched else "(无)"))

    # 2. 结构不变：6 处仍是 FC DC 开头的 mov imm32,a0
    struct_ok = True
    for branch in bpf.BRANCH_ORDER:
        at = bpf.SITES[branch][0] + (PREVIEW_FN - CODE_BASE)
        if out_pay[at:at + 2] != MOV_A0_OPCODE:
            struct_ok = False
            print("   %s 处 opcode 变成 %s" % (branch, out_pay[at:at + 2].hex()))
    chk("6 处仍是 mov imm32,a0（FC DC）", struct_ok)

    # 3. 语义：改过的立即数必须等于四张表地址之一；没给映射的点保持 base 原值
    table_addrs = set(TABLES.values())
    values, olds = {}, {}
    for branch in bpf.BRANCH_ORDER:
        start = bpf.imm_payload_offset(branch)
        values[branch] = struct.unpack("<I", out_pay[start:start + 4])[0]
        olds[branch] = struct.unpack("<I", base_pay[start:start + 4])[0]
    bad_target = {b: v for b, v in values.items()
                  if v not in table_addrs and not (v == olds[b] and b not in touched)}
    chk("改过的立即数都指向 V/N/M/P，未改的保持 base 原值（改后 %s）"
        % ", ".join("%s=0x%08x" % (b, values[b]) for b in bpf.BRANCH_ORDER),
        not bad_target, "非法目标: %r" % bad_target)
    legal = set(range(0, len(out_pay)))
    out_of_payload = {b: v for b, v in values.items() if (v - CODE_BASE) not in legal}
    chk("表地址都落在 block0 payload 内", not out_of_payload, "越界: %r" % out_of_payload)

    # 4. 不该动的区域逐字节相同
    regions = []
    for addr in TONE:
        regions.append(("tone 0x%08x" % addr, addr - CODE_BASE, 512))
    for addr in MATRIX:
        regions.append(("matrix 0x%08x" % addr, addr - CODE_BASE, 18))
    for addr in CHROMA:
        regions.append(("chroma 0x%08x" % addr, addr - CODE_BASE, 100))
    for addr, size in HOOKS:
        regions.append(("hook 0x%08x" % addr, addr - CODE_BASE, size))
    for addr, size in STUBS:
        regions.append(("stub 0x%08x" % addr, addr - CODE_BASE, size))
    for addr in SINGLE:
        regions.append(("single 0x%08x" % addr, addr - CODE_BASE, 1))
    changed_regions = [name for name, off, size in regions
                       if base_pay[off:off + size] != out_pay[off:off + size]]
    chk("tone/矩阵/色度/钩子/桩/N 槽/弯曲门 %d 处逐字节相同" % len(regions),
        not changed_regions, "被改动: %s" % ", ".join(changed_regions))

    ui_ok = all(name.encode("utf-16-le") in out_blocks[1].payload for name in UI_NAMES)
    other_blocks_ok = True
    for b_in, b_out in zip(base_blocks, out_blocks):
        if b_in.index == 0:
            continue
        if (b_in.payload != b_out.payload or b_in.header != b_out.header
                or b_in.trailer != b_out.trailer):
            other_blocks_ok = False
    chk("UI 名（block1）仍在且 block1/2 逐字节相同", ui_ok and other_blocks_ok)
    chk("block0 的 header 未变、trailer 只重算了校验和",
        base_blocks[0].header == out_blocks[0].header
        and base_blocks[0].trailer[:28] == out_blocks[0].trailer[:28]
        and base_blocks[0].trailer[28:32] != out_blocks[0].trailer[28:32])

    # 5. 与 spec 对账
    spec_path = Path(args.spec) if args.spec else Path(str(out_path) + ".spec.json")
    spec = None
    if spec_path.is_file():
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if spec is None:
        print("   注意：没有 spec（%s），只能做结构校验" % spec_path.name)
        chk("spec 存在", False, "缺 %s" % spec_path)
    else:
        chk("spec.out_sha256 与输出一致", spec.get("out_sha256") == out_sha,
            "spec=%s out=%s" % (spec.get("out_sha256"), out_sha))
        chk("spec.base_sha256 与 base 一致", spec.get("base_sha256") == base_sha)
        mismatch = []
        expected_changed = set()
        for edit in spec.get("edits", []):
            branch = edit["branch"]
            want = int(edit["new"], 16)
            got = values.get(branch)
            if got != want:
                mismatch.append("%s 期望 0x%08x 实得 0x%08x" % (branch, want, got))
            if edit.get("changed"):
                expected_changed.add(branch)
            elif branch in touched:
                mismatch.append("%s 在 spec 里标 unchanged 却变了" % branch)
        for edit in spec.get("unchanged", []):
            branch = edit["branch"]
            if values[branch] != int(edit["old"], 16):
                mismatch.append("%s 不该动却变成 0x%08x" % (branch, values[branch]))
        if set(touched) != expected_changed:
            mismatch.append("实际改到 %s，spec 说改 %s"
                            % (sorted(touched), sorted(expected_changed)))
        chk("spec 与输出逐点对齐（changed %d / untouched %d）"
            % (len(expected_changed), len(spec.get("unchanged", []))),
            not mismatch, "; ".join(mismatch))

    bad = [c["name"] for c in checks if not c["pass"]]
    closed = not bad
    sim_path = Path(args.sim) if args.sim else Path(str(out_path) + ".sim.json")
    sim = {
        "verifier": "verify_preview_fix.py",
        "sha256": out_sha,
        "closed": closed,
        "cases": len(checks),
        "base": str(args.base),
        "base_sha256": base_sha,
        "out": str(args.out),
        "spec": str(spec_path) if spec is not None else None,
        "scope": "静态：差异只允许落在预览分派函数 6 条 mov imm32,a0 的立即数字段；"
                 "tone/矩阵/色度/钩子/桩/N 槽/弯曲门与 UI 名逐字节相同；"
                 "改过的立即数必须是 V/N/M/P 之一、没给映射的保持 base 原值。"
                 "无 ISP 仿真、无 bootability 证明。",
        "edits": [
            {"branch": b, "ram_address": "0x%08x" % bpf.imm_ram(b),
             "value": "0x%08x" % values[b],
             "table": next((n for n, a in TABLES.items() if a == values[b]), None)}
            for b in bpf.BRANCH_ORDER if b in touched
        ],
        "checks": checks,
    }
    sim_path.write_text(json.dumps(sim, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")

    print()
    print("closed = %s   sha256 = %s" % (closed, out_sha))
    print("sim = %s" % sim_path)
    if bad:
        print("失败项: %s" % "; ".join(bad))
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        sys.exit("error: %s" % exc)
