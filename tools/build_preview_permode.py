#!/usr/bin/env python3
"""build_preview_permode.py — 生成 build/PREVIEW_PERMODE.BIN：预览「分档取表」桩。

背景（已核实，见 docs/analysis/preview-permode.md）
-----------------------------------------------
预览走 0x604B986B（唯一 caller 0x604B9E67，由模式应用 0x604AA228 调起）。它内部
有 9 道门，G8 在 0x604B9C1D 判 ``u8(ctx+8) ∈ {1,2}`` 就把 key（0x0101xxxx /
0x02xxxxxx）截住，直接返回 k=2 常量处的立即数（0x604B9D1A，RELEASE_v1 已改成我们
的 N 表 0x605DB144）。于是预览四档全吃 N 的 tone：V≈成片，M/P 偏亮发灰。

本补丁只做两件事（其余字节一个不动）：
  1) 0x604B9C1D 起 5 字节（``mov d3,a1; movbu (a1),d0; <cmp 首字节>``）改成
     ``jmp 0x6066159E``；0x604B9C22 那半个 cmp 成为死字节。
  2) 0x6066159E（66 B 原厂 FF 口袋，落在 second_verify 的 KNOWN_ZONES
     ``0x60661590..0x606615E0`` 内，0 个对齐 u32 引用）写入 49 字节桩：
     ``k = u8[ctx+8] & 7``，1..4 → ``tone_ptrtab[k-1]``（V/N/M/P），其余回 stock
     路径（G9 现算 / gate→k2 或恒等 0x605D8144）。

桩位为什么不是 0x60DA4E08：那一带在 tools/second_verify.py 的 DEAD_ZONES 里
（``0x60DA4E08..0x60DA5000 栈区（B14b/B15/B45 姿态相关重启）``）。本脚本现在只允许
在 SAFE_POCKETS 里放桩，并在生成前拒绝任何落在 DEAD_ZONES 的地址。

桩入口约定（与 stock G8 一致）：a0 = ctx，d1 = 32-bit key（不能破坏，G9 要用），
d2/d3/a2/a3 可随便用（0x604B9D26 的 ``ret [d2,d3,a2,a3],0x18`` 会从 caller 帧
恢复它们），a1 已被钩子盖掉故不可依赖。栈不动。

容器重建走 tools/mkpatch.py（它内部用 tools/oly_container.py），本脚本只负责
「汇编桩 → 校验 → 生成 patch 文件 → 调 mkpatch → 事后逐字节自检 → 写 .spec.json」。

用法
----
    python tools/build_preview_permode.py                     # 生成 build/PREVIEW_PERMODE.BIN
    python tools/build_preview_permode.py --dry-run           # 只算不写（mkpatch --dry-run）
    python tools/build_preview_permode.py --force             # 覆盖已存在的输出
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oly_container as oc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

CODE_BASE = 0x60021020
PREVIEW_FN = 0x604B986B          # 预览取表函数真头
CALLER = 0x604B9E67              # 唯一 caller（模式应用 0x604AA228 调起）

HOOK_ADDR = 0x604B9C1D           # G8 判定起 5 字节
HOOK_LEN = 5
HOOK_ORIG = bytes.fromhex("f1edf041a0")   # mov d3,a1; movbu (a1),d0; cmp <imm8> 首字节
DEAD_BYTE = 0x604B9C22           # 被盖掉那半个 cmp 的第 2 字节

STUB_ADDR = 0x6066159E           # 桩（66 B 原厂 FF 口袋的起点；在 second_verify KNOWN_ZONES 内）
STUB_POCKET_START = STUB_ADDR
STUB_POCKET_END = 0x606615E0
STUB_LIMIT = STUB_POCKET_END - STUB_POCKET_START

# 允许放桩的口袋。硬要求：都在 tools/second_verify.py 的 KNOWN_ZONES 里（否则二次核验报
# 「未知区域 warn」），且不与 DEAD_ZONES 相交。桩本身还要通过"整段原厂 FF + 0 引用"检查。
SAFE_POCKETS = [
    (0x60661590, 0x606615E0,
     "小块 FF 口袋（second_verify KNOWN_ZONES；实测 0x6066159E 起 66 B 全 FF、0 个对齐 u32 引用）"),
    (0x601ADE00, 0x601ADE50,
     "ISP 系数区旁口袋（second_verify KNOWN_ZONES；0x601ADE04 起已被 matrix 桩占用，通常会因非 FF 被拒）"),
]

# second_verify.py 的判死区：落进这些区间它会直接报 block，本脚本生成前先拒。
DEAD_ZONES = [
    (0x60DA4E08, 0x60DA5000, "栈区（B14b/B15/B45 姿态相关重启）"),
    (0x60DE0000, 0x60E00000, "不进内存（B13 炸机）"),
    (0x605DEF44, 0x605DFF44, "bank2（开机整片重写，B53 花屏）"),
]

PTRTAB = 0x605DEDD4              # tone 指针表：V N M P 四个 u32
TABLES = {"V": 0x605DAF44, "N": 0x605DB144, "M": 0x605DB344, "P": 0x605DB544}

RET_ADDR = 0x604B9D26            # 函数收尾 ret [d2,d3,a2,a3],0x18
G9_ADDR = 0x604B9C3B             # G9：key 不在 {1,2} 时的 stock 现算
GATE_ADDR = 0x604B9D0E           # stock G8 的 gate 段（call 0x604bdd42 → k2 / identity）

K_NAMES = {1: "Vivid", 2: "Natural", 3: "Muted", 4: "Portrait"}
K_TABLE = {1: "V", 2: "N", 3: "M", 4: "P"}

# 汇编器自检的金标（49 B）：如果汇编器换了版本/编码变了，这里会当场报错。
GOLDEN_STUB_HEX = (
    "f84008f8e00728ffa003c51254fcded4ed5d60f162f002dc7187e5ff"
    "f84008a002c80ba001c807dc7686e5ffdc4487e5ff"
)

STUB_ASM = """\
\t.am33
\t.text
\t.globl\tpreview_permode_stub
preview_permode_stub:
\tmovbu\t(8,a0),d0
\tand\t7,d0
\tadd\t-1,d0
\tcmp\t3,d0
\tbhi\tLfall_back
\tasl2\td0
\tmov\t0x{tone_ptrtab:08X},a2
\tadd\td0,a2
\tmov\t(a2),a0
\tjmp\tP_RET
Lfall_back:
\tmovbu\t(8,a0),d0
\tcmp\t2,d0
\tbeq\tLgate_back
\tcmp\t1,d0
\tbeq\tLgate_back
\tjmp\tP_G9
Lgate_back:
\tjmp\tP_GATE
"""

DEFAULT_TOOLCHAIN = ROOT / ".tmp" / "olya" / "toolchains" / "mn10300-elf" / "bin"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def u32(data: bytes, addr: int) -> int:
    """读 block0 里某个 RAM 地址的 u32。"""
    off = addr - CODE_BASE
    return struct.unpack_from("<I", data, off)[0]


def ram_to_payload(addr: int) -> int:
    return addr - CODE_BASE


def resolve_pocket(addr: int):
    """桩地址必须落在 SAFE_POCKETS 之一里，返回 (start, end, name)。"""
    for start, end, name in SAFE_POCKETS:
        if start <= addr < end:
            return start, end, name
    return None


def dead_zone_of(addr: int):
    for start, end, name in DEAD_ZONES:
        if start <= addr <= end:
            return f"{start:#x}..{end:#x} {name}"
    return None


def tool(name: str, toolchain: Path) -> Path:
    for cand in (toolchain / f"mn10300-elf-{name}.exe", toolchain / f"mn10300-elf-{name}"):
        if cand.exists():
            return cand
    raise SystemExit(f"error: {name} 不在 {toolchain}")


def assemble_stub(stub_addr: int, workdir: Path, toolchain: Path) -> tuple[bytes, str]:
    """把 STUB_ASM 汇编成二进制。远跳用 -defsym 让链接器算位移，汇编器只发重定位。"""
    workdir.mkdir(parents=True, exist_ok=True)
    asm = STUB_ASM.format(tone_ptrtab=PTRTAB)
    src = workdir / "preview_permode_stub.s"
    src.write_text(asm, encoding="utf-8")
    obj = workdir / "preview_permode_stub.o"
    elf = workdir / "preview_permode_stub.elf"
    bin_ = workdir / "preview_permode_stub.bin"

    def run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(
                f"error: {what} 失败 (exit {proc.returncode})\n"
                f"  cmd: {' '.join(cmd)}\n  stderr:\n{proc.stderr.strip()}"
            )
        return proc

    run([str(tool("as", toolchain)), "-o", str(obj), str(src)], "汇编桩")
    run(
        [
            str(tool("ld", toolchain)),
            "-e", "preview_permode_stub",
            f"-Ttext={stub_addr:#x}",
            f"-defsym=P_RET={RET_ADDR:#x}",
            f"-defsym=P_G9={G9_ADDR:#x}",
            f"-defsym=P_GATE={GATE_ADDR:#x}",
            "-o", str(elf), str(obj),
        ],
        "链接桩",
    )
    run([str(tool("objcopy", toolchain)), "-O", "binary", str(elf), str(bin_)], "导出桩二进制")
    return bin_.read_bytes(), asm


def objdump_jumps(elf: Path, toolchain: Path) -> dict[str, int]:
    """用 objdump 反汇编桩，取三条远跳的目标地址（objdump 自己解析 disp32）。"""
    proc = subprocess.run(
        [str(tool("objdump", toolchain)), "-d", str(elf)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"error: objdump 失败\n{proc.stderr.strip()}")
    want = {"P_RET": RET_ADDR, "P_G9": G9_ADDR, "P_GATE": GATE_ADDR}
    got: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        if "jmp" not in line or "<" not in line:
            continue
        import re
        m = re.search(r"jmp\s+([0-9a-f]{8})\s+<(P_[A-Z0-9]+)>", line)
        if m:
            got[m.group(2)] = int(m.group(1), 16)
    missing = set(want) - set(got)
    if missing:
        raise SystemExit(f"error: objdump 里找不到远跳 {sorted(missing)}")
    bad = {sym: hex(got[sym]) for sym in want if got[sym] != want[sym]}
    if bad:
        raise SystemExit(f"error: 桩的远跳目标不对: {bad}（期望 {[hex(v) for v in want.values()]}）")
    return got


def check_jmp_encoding(blob: bytes, stub_addr: int, offset: int, target: int) -> None:
    """独立核对 raw 字节：``DC disp32``，disp 相对指令自身地址。"""
    raw = blob[offset:offset + 5]
    if len(raw) != 5 or raw[0] != 0xDC:
        raise SystemExit(f"error: stub+{offset:#x} 不是 5 字节远跳: {raw.hex()}")
    disp = struct.unpack("<i", raw[1:5])[0]
    if stub_addr + offset + disp != target:
        raise SystemExit(
            f"error: stub+{offset:#x} 位移 {disp:+#x} 指向 {stub_addr + offset + disp:#x}，"
            f"期望 {target:#x}"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="生成 PREVIEW_PERMODE.BIN（预览分档取表桩；只改 G8 钩 + 一个新桩）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "映射（与 SU 桩同款）：k = u8[ctx+8] & 7；1=Vivid→V 0x605DAF44、"
            "2=Natural→N 0x605DB144、3=Muted→M 0x605DB344、4=Portrait→P 0x605DB544。\n"
            "其余（k=0/5../ctx+8 不是 1..4）走回 stock：key 高字节∈{1,2} → gate（k2 或恒等 "
            "0x605D8144），否则 G9 现算。"
        ),
    )
    ap.add_argument("--base", type=Path, default=ROOT / "build" / "RELEASE_v1.BIN",
                    help="底座镜像（默认 build/RELEASE_v1.BIN）")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "PREVIEW_PERMODE.BIN",
                    help="输出镜像（默认 build/PREVIEW_PERMODE.BIN）")
    ap.add_argument("--stub-addr", type=lambda s: int(s, 0), default=STUB_ADDR,
                    help="桩地址（默认 0x6066159e；只允许 SAFE_POCKETS 内：[0x60661590,0x606615e0) / [0x601ade00,0x601ade50)")
    ap.add_argument("--toolchain", type=Path, default=DEFAULT_TOOLCHAIN,
                    help="mn10300-elf 工具链 bin 目录")
    ap.add_argument("--workdir", type=Path, default=ROOT / ".tmp" / "permode",
                    help="临时目录（默认 .tmp/permode）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的输出")
    ap.add_argument("--dry-run", action="store_true", help="只算不写（mkpatch --dry-run，不产文件）")
    ap.add_argument("--keep-work", action="store_true", help="保留中间文件")
    args = ap.parse_args(argv)

    stub_addr = args.stub_addr
    dz = dead_zone_of(stub_addr)
    if dz:
        ap.error(f"--stub-addr {stub_addr:#x} 落在判死区（second_verify DEAD_ZONES）：{dz}")
    pocket = resolve_pocket(stub_addr)
    if pocket is None:
        ap.error("--stub-addr 必须落在安全口袋里：" +
                 " / ".join(f"[{a:#x},{b:#x}) {n}" for a, b, n in SAFE_POCKETS))
    pocket_start, pocket_end, pocket_name = pocket
    if not args.base.exists():
        raise SystemExit(f"error: 底座不存在: {args.base}")

    base_data = args.base.read_bytes()
    base_sha = sha256(base_data)
    blocks = oc.parse(base_data)
    if len(blocks) != 3:
        raise SystemExit(f"error: 期望 3 个 block，实际 {len(blocks)}")
    payload = blocks[0].payload

    # ---- 1. 底座自检：钩子字节、桩区全 FF、ptrtab、四张表
    hook_off = ram_to_payload(HOOK_ADDR)
    got_hook = payload[hook_off:hook_off + HOOK_LEN]
    if got_hook != HOOK_ORIG:
        raise SystemExit(
            f"error: {HOOK_ADDR:#x} 处不是预期的 G8 判定字节 "
            f"（旧 {got_hook.hex()}，期望 {HOOK_ORIG.hex()}）——底座已被改过？"
        )
    stub_off = ram_to_payload(stub_addr)
    entries = [u32(payload, PTRTAB + 4 * i) for i in range(4)]
    expect_entries = [TABLES[v] for v in ("V", "N", "M", "P")]
    if entries != expect_entries:
        raise SystemExit(
            f"error: tone_ptrtab {PTRTAB:#x} = {[hex(e) for e in entries]}，"
            f"期望 {[hex(e) for e in expect_entries]}"
        )

    # ---- 2. 汇编桩
    blob, asm = assemble_stub(stub_addr, args.workdir, args.toolchain)
    if stub_addr + len(blob) > pocket_end:
        raise SystemExit(
            f"error: 桩 {len(blob)} B @ {stub_addr:#x} 越出口袋上限 {pocket_end:#x}"
        )
    jumps = objdump_jumps(args.workdir / "preview_permode_stub.elf", args.toolchain)
    for off, target in ((0x17, RET_ADDR), (0x27, G9_ADDR), (0x2C, GATE_ADDR)):
        check_jmp_encoding(blob, stub_addr, off, target)

    src_region = payload[stub_off:stub_off + len(blob)]
    if any(b != 0xFF for b in src_region):
        bad = next(i for i, b in enumerate(src_region) if b != 0xFF)
        raise SystemExit(f"error: 桩区 {stub_addr + bad:#x} 不是 0xFF（这个口袋已被占用）")
    # 桩所在那一段连续 FF 的范围（给 spec / 校验器用）
    ff_start = stub_off
    while ff_start > 0 and payload[ff_start - 1] == 0xFF:
        ff_start -= 1
    ff_end = stub_off + len(blob)
    while ff_end < len(payload) and payload[ff_end] == 0xFF:
        ff_end += 1
    ff_run = (CODE_BASE + ff_start, CODE_BASE + ff_end)
    if blob.hex() != GOLDEN_STUB_HEX:
        raise SystemExit(
            f"error: 桩编码与金标不一致（汇编器版本变了？）\n  实际 {blob.hex()}\n  期望 {GOLDEN_STUB_HEX}"
        )

    # ---- 3. 钩子字节
    disp = stub_addr - HOOK_ADDR
    hook_new = bytes([0xDC]) + struct.pack("<i", disp)
    assert len(hook_new) == HOOK_LEN

    # ---- 4. patch 文件 + mkpatch
    work = args.workdir
    work.mkdir(parents=True, exist_ok=True)
    base_copy = work / "base.bin"
    if base_copy.exists():
        base_copy.unlink()
    shutil.copyfile(args.base, base_copy)
    patch_file = work / "preview_permode.patch"
    # 注：mkpatch.py 用 Path.read_text()（locale 编码，本机是 GBK）读 patch 文件，
    # 所以这里的注释必须全 ASCII，否则 GBK 解不了 UTF-8 字节。
    patch_file.write_text(
        "# PREVIEW_PERMODE: G8 hook -> per-mode tone stub (nothing else changes)\n"
        f"ram {HOOK_ADDR:#010x}={hook_new.hex()}   # G8: jmp {stub_addr:#x} (was {HOOK_ORIG.hex()})\n"
        f"ram {stub_addr:#010x}={blob.hex()}   # stub {len(blob)} B: u8[ctx+8]&7 -> ptrtab[k-1]\n",
        encoding="utf-8",
    )

    if args.out.exists() and not args.dry_run:
        if not args.force:
            raise SystemExit(f"error: {args.out} 已存在（--force 覆盖，或先删掉）")
        args.out.unlink()

    mkpatch = ROOT / "tools" / "mkpatch.py"
    cmd = [
        sys.executable, str(mkpatch), str(base_copy),
        "--out", str(args.out),
        "--patch-file", str(patch_file),
        "--code-base", hex(CODE_BASE),
        "--expect-sha256", base_sha,
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"error: mkpatch 失败 (exit {proc.returncode})\n{proc.stdout}\n{proc.stderr}")
    try:
        mk_report = json.loads(proc.stdout[proc.stdout.index("{"):])
    except (ValueError, IndexError):
        mk_report = {"raw": proc.stdout.strip()}

    # ---- 5. 事后自检（对输出文件，不是对内存）
    if args.dry_run:
        print(f"dry-run: 不会写出 {args.out}")
        print(f"  钩子 {HOOK_ADDR:#x}: {HOOK_ORIG.hex()} → {hook_new.hex()}（jmp {stub_addr:#x}）")
        print(f"  桩   {stub_addr:#x}: {len(blob)} B {blob.hex()}")
        return 0

    out_data = args.out.read_bytes()
    out_sha = sha256(out_data)
    out_blocks = oc.parse(out_data)
    if len(out_blocks) != 3:
        raise SystemExit(f"error: 输出解析出 {len(out_blocks)} 个 block")
    out_payload = out_blocks[0].payload

    allowed = set(range(hook_off, hook_off + HOOK_LEN)) | set(range(stub_off, stub_off + len(blob)))
    for i, (before, after) in enumerate(zip(payload, out_payload)):
        if i not in allowed and before != after:
            raise SystemExit(f"error: 意外改动 {CODE_BASE + i:#x}: {before:02x} → {after:02x}")
    if out_payload[hook_off:hook_off + HOOK_LEN] != hook_new:
        raise SystemExit("error: 输出里的钩子字节不对")
    if out_payload[stub_off:stub_off + len(blob)] != blob:
        raise SystemExit("error: 输出里的桩字节不对")
    for i in range(1, 3):
        if blocks[i].payload != out_blocks[i].payload:
            raise SystemExit(f"error: block{i} 的 payload 变了")
        if blocks[i].header != out_blocks[i].header or blocks[i].trailer != out_blocks[i].trailer:
            raise SystemExit(f"error: block{i} 的 header/trailer 变了")

    # ---- 6. spec.json
    spec_path = Path(str(args.out) + ".spec.json")
    spec = {
        "generator": "tools/build_preview_permode.py",
        "base": str(args.base),
        "base_sha256": base_sha,
        "out": str(args.out),
        "out_sha256": out_sha,
        "design": {
            "problem": (
                "预览 0x604B986B 的 G8（0x604B9C1D）把 u8(ctx+8)∈{1,2} 的 key 截住，"
                "直接返回 0x604B9D1A 处的立即数（RELEASE_v1 = N 表 0x605DB144），"
                "于是预览四档全吃 N 的 tone：V 接近成片，M/P 偏亮发灰。"
            ),
            "fix": f"G8 改成无条件跳到 {stub_addr:#x} 的桩；桩按 k=u8[ctx+8]&7 查 tone_ptrtab。",
            "hook_covers": f"{HOOK_ADDR:#x}..{HOOK_ADDR + HOOK_LEN - 1:#x}（mov d3,a1 / movbu (a1),d0 / cmp 首字节）",
            "dead_bytes": [f"{DEAD_BYTE:#x}"],
            "stock_g8_kept": (
                f"{HOOK_ADDR + HOOK_LEN:#x} 起的 stock G8 指令原样保留（beq/call/gate/jmp 0x604b9d18|d20），"
                "但桩不再落到那里，故永不执行（作为回滚的天然参照）。"
            ),
            "a1_unusable": "a1 原是 ctx+8（mov d3,a1 被钩子盖掉），桩改从 a0=ctx 现算。",
            "registers": {
                "entry": "a0=ctx, d1=32-bit key（不可破坏）, d3=ctx+8（本桩不碰）",
                "clobbered": "d0（key 字节/下标）, a2（ptrtab 指针）—— 二者都由 0x604B9D26 的 ret [d2,d3,a2,a3],0x18 从 caller 帧恢复",
                "stack": "不动（桩里没有 push/pop / add sp）",
            },
        },
        "hook": {
            "ram": f"{HOOK_ADDR:#x}",
            "len": HOOK_LEN,
            "old": HOOK_ORIG.hex(),
            "new": hook_new.hex(),
            "jmp_target": f"{stub_addr:#x}",
            "disp": disp,
            "decoded": f"jmp ({disp:+#x},PC)  ; {HOOK_ADDR:#x} + {disp:+#x} = {stub_addr:#x}",
        },
        "stub": {
            "ram": f"{stub_addr:#x}",
            "len": len(blob),
            "end": f"{stub_addr + len(blob) - 1:#x}",
            "hex": blob.hex(),
            "sha256": sha256(blob),
            "region": (
                f"{pocket_start:#x}..{pocket_end:#x}：{pocket_name}；"
                f"桩所在连续 FF 段 {ff_run[0]:#x}..{ff_run[1]:#x}（{ff_run[1] - ff_run[0]} B）"
            ),
            "pocket": {"start": f"{pocket_start:#x}", "end": f"{pocket_end:#x}", "name": pocket_name},
            "ff_run": [f"{ff_run[0]:#x}", f"{ff_run[1]:#x}"],
            "dead_zone_check": (
                "桩位不在 second_verify DEAD_ZONES 内；0x60DA4E08..0x60DA5000 是判死区"
                "（B14b/B15/B45 姿态相关重启），本包已从那搬走"
            ),
            "source_all_ff": True,
            "objdump_jumps": {k: f"{v:#x}" for k, v in jumps.items()},
            "asm": asm,
            "fallback": {
                "d0_out_of_range": f"u8[ctx+8]&7 ∈ {{0,5,6,7}} → 回 stock",
                "stock_key_1_2": f"jmp {GATE_ADDR:#x}（stock G8 gate：call 0x604bdd42 → 0 → 恒等表 0x605D8144；≠0 → k2 立即数 0x605DB144）",
                "stock_other": f"jmp {G9_ADDR:#x}（G9 现算 d0，覆盖 d0>=3 与 key 高字节 ∉{{1,2}} 的全部行为）",
            },
            "aliasing": (
                "与 SU 桩同款 &7 语义：只取 ctx+8 的低 3 位。正常 key 是 0x01/0x02/0x03/0x04"
                "（及 0x0101xxxx 之类），低 3 位即档位；0x09..0x0C、0x11.. 之类同余值也会被当 1..4"
                "（现实中不出现；这是刻意沿用 SU 桩的写法）。"
            ),
        },
        "tone_ptrtab": {
            "ram": f"{PTRTAB:#x}",
            "entries": [
                {"k": k + 1, "name": K_NAMES[k + 1], "table": K_TABLE[k + 1], "u32": hex(entries[k])}
                for k in range(4)
            ],
            "unchanged": True,
        },
        "branch_map": [
            {"k": k, "name": K_NAMES[k], "table": K_TABLE[k], "addr": f"{TABLES[K_TABLE[k]]:#x}"}
            for k in sorted(K_TABLE)
        ],
        "allowed_diff_windows": [
            {"ram": f"{HOOK_ADDR:#x}", "len": HOOK_LEN, "what": "G8 → jmp 桩"},
            {"ram": f"{stub_addr:#x}", "len": len(blob), "what": "分档取表桩"},
        ],
        "untouched": {
            "tables": {k: f"{v:#x}" for k, v in TABLES.items()},
            "identity_table": "0x605D8144",
            "matrix": ["0x605DEEE0", "0x605DEEF2", "0x605DEF04", "0x605DEF16"],
            "chroma": ["0x605DEB44", "0x605DEBA8", "0x605DEC0C", "0x605DEC70"],
            "existing_stubs": ["0x605DECD4", "0x605DED1C", "0x605DED44", "0x605DED70",
                               "0x605DEDD4", "0x605DEDE4", "0x605DEE3C", "0x601ADE04"],
            "k2_immediate": "0x604B9D1A（仍是 N 表，没有动）",
        },
        "mkpatch": {
            "patches": mk_report.get("patches"),
            "container_checks": mk_report.get("container_checks"),
            "output_sha256": mk_report.get("output_sha256"),
        },
        "scope": (
            "静态：容器重建 + 逐字节差异审计 + 桩机器码反汇编自检。"
            "未上机验证（预览四档观感需真机确认）。"
        ),
    }
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---- 6b. 二次核验的落点声明（second_verify.py --allow）
    allow_path = args.out.parent / (args.out.stem + ".allow.json")
    allow = [
        [HOOK_ADDR, HOOK_ADDR + HOOK_LEN - 1,
         "预览分派 G8 门钩点（mov d3,a1/movbu (a1),d0/cmp → jmp 桩）"],
        [stub_addr, stub_addr + len(blob) - 1,
         f"预览分档取表桩 {len(blob)} B（k=u8[ctx+8]&7 → tone_ptrtab[k-1]）"],
    ]
    allow_path.write_text(json.dumps(allow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    spec["allow_file"] = str(allow_path)
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---- 7. 打印人类可读摘要
    print(f"base     {args.base}  sha256 {base_sha}")
    print(f"out      {args.out}  sha256 {out_sha}  ({len(out_data):,} B)")
    print()
    print(f"钩子  {HOOK_ADDR:#010x}  {HOOK_ORIG.hex()} → {hook_new.hex()}")
    print(f"      {spec['hook']['decoded']}")
    print(f"      {DEAD_BYTE:#x} 留 0x{payload[ram_to_payload(DEAD_BYTE)]:02x}（死字节）")
    print()
    print(f"桩    {stub_addr:#010x}..{stub_addr + len(blob) - 1:#010x}  {len(blob)} B")
    print(f"      k = u8[ctx+8] & 7 → tone_ptrtab[{PTRTAB:#x}][k-1]")
    for k in sorted(K_TABLE):
        print(f"        k={k} {K_NAMES[k]:<9} → {K_TABLE[k]} {TABLES[K_TABLE[k]]:#010x}")
    print("      其余 → stock（key∈{1,2} 走 gate；否则 G9）")
    print()
    print(f"spec     {spec_path}")
    print(f"allow    {allow_path}  （second_verify.py --allow 用；区间端点为闭区间）")
    print("块校验   block1/block2 payload+header+trailer 逐字节相同；block0 trailer 已重算校验和")
    return 0


if __name__ == "__main__":
    sys.exit(main())
