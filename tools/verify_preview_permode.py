#!/usr/bin/env python3
"""verify_preview_permode.py — 校验 build/PREVIEW_PERMODE.BIN（预览分档取表桩）。

只读：不写镜像、不改底座；唯一的写动作是把结论落到 ``<out>.sim.json``（UTF-8）。
flash_gate.py 读的就是这个文件（要求 ``closed`` 为真、``sha256`` 与文件一致）。

校验清单
--------
A 容器层：3 个 block、总大小一致；block1/block2 的 payload+header+trailer 逐字节相同；
  block0 的 header 相同、trailer 只有校验和（trailer[28:32]）变了。
B 差异面：block0 与底座的差异**只**落在 0x604B9C1D..0x604B9C21（钩子 5 B）和
  0x6066159E..0x606615CE（桩 49 B）两个窗口内，别处一个字节都不许动。
  桩位必须在 second_verify 的 KNOWN_ZONES 内、且不在 DEAD_ZONES 内（0x60DA4E08 那带是判死区）。
C 钩子：字节 = DC + disp32，disp 相对指令自身地址，目标必须正好是桩地址；
  底座原位必须是 stock 的 ``mov d3,a1; movbu (a1),d0; cmp`` 首字节。
D 桩：底座该处 49 B 全是 0xFF（真口袋）；输出里与金标 49 B 完全一致。
E 反汇编自检：用 mn10300-elf-objdump 重新反汇编**镜像里的字节**（不是汇编源码），
  逐条核对助记符/操作数/分支目标；钩子必须解成 ``jmp <桩>``。
F 不许动的东西：tone 四表、tone_ptrtab、恒等表、矩阵四组、色度四组、已有的钩与桩
  （含 SU/SD 桩）、k2 立即数 0x604B9D1A、stock G8 残段（0x604B9C23..0x604B9C2A）、UI 名字。
G 死字节安全：整个 0x604B986B 函数里没有任何分支目标落进被盖掉的 [0x604B9C1D,0x604B9C23)，
  且 0x604B9C1D 不是任何 caller 的直接入口（函数唯一入口 0x604B986B、唯一 caller 0x604B9E67）。
H spec 对账：.spec.json 存在、out_sha256/base_sha256 与文件一致、钩子/桩/窗口描述与实测一致。

用法
----
    python tools/verify_preview_permode.py
    python tools/verify_preview_permode.py --out build/PREVIEW_PERMODE.BIN --json build/x.sim.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oly_container as oc  # noqa: E402
from mn10300_dis import Decoder  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# ---- 独立金标（刻意与 build_preview_permode.py 分开抄一份：任一文件漂移都会被抓到）
CODE_BASE = 0x60021020
PREVIEW_FN = 0x604B986B
CALLER = 0x604B9E67
HOOK_ADDR = 0x604B9C1D
HOOK_LEN = 5
HOOK_ORIG = bytes.fromhex("f1edf041a0")
STUB_ADDR = 0x6066159E
STUB_POCKET_START = 0x60661590
STUB_POCKET_END = 0x606615E0
STUB_LIMIT_END = STUB_POCKET_END
STUB_LEN = 49
STUB_HEX = (
    "f84008f8e00728ffa003c51254fcded4ed5d60f162f002dc7187e5ff"
    "f84008a002c80ba001c807dc7686e5ffdc4487e5ff"
)

# 与 tools/second_verify.py 的表同源（硬抄，不 import）：
#  - KNOWN_ZONES 决定"已知落点"（桩必须在里面，否则二次核验报未知区域）
#  - DEAD_ZONES 是判死区（桩落进去直接 block）
SAFE_POCKETS = [
    (0x60661590, 0x606615E0, "小块 FF 口袋"),
    (0x601ADE00, 0x601ADE50, "ISP 系数区旁口袋"),
]
DEAD_ZONES = [
    (0x60DA4E08, 0x60DA5000, "栈区（B14b/B15/B45 姿态相关重置）"),
    (0x60DE0000, 0x60E00000, "不进内存（B13 炸机）"),
    (0x605DEF44, 0x605DFF44, "bank2（开机整片重写，B53 花屏）"),
]
TABLE = {"V": 0x605DAF44, "N": 0x605DB144, "M": 0x605DB344, "P": 0x605DB544}
PTRTAB = 0x605DEDD4
PTRTAB_ORDER = ["V", "N", "M", "P"]
IDENTITY = 0x605D8144
K2_IMM = 0x604B9D1A
RET_ADDR = 0x604B9D26
G9_ADDR = 0x604B9C3B
GATE_ADDR = 0x604B9D0E

# 桩逐条指令金标：(偏移, 长度, objdump 归一化后的文本)。地址/符号注解会被剥掉。
STUB_GOLDEN = [
    (0x00, 3, "movbu (8,a0),d0"),
    (0x03, 3, "and 7,d0"),
    (0x06, 2, "add -1,d0"),
    (0x08, 2, "cmp 3,d0"),
    (0x0A, 2, f"bhi {STUB_ADDR + 0x1C:08x}"),
    (0x0C, 1, "asl2 d0"),
    (0x0D, 6, f"mov {PTRTAB},a2"),
    (0x13, 2, "add d0,a2"),
    (0x15, 2, "mov (a2),a0"),
    (0x17, 5, f"jmp {RET_ADDR:08x}"),
    (0x1C, 3, "movbu (8,a0),d0"),
    (0x1F, 2, "cmp 2,d0"),
    (0x21, 2, f"beq {STUB_ADDR + 0x2C:08x}"),
    (0x23, 2, "cmp 1,d0"),
    (0x25, 2, f"beq {STUB_ADDR + 0x2C:08x}"),
    (0x27, 5, f"jmp {G9_ADDR:08x}"),
    (0x2C, 5, f"jmp {GATE_ADDR:08x}"),
]

# F 段：必须逐字节不变的区域（与 verify_release.py 的清单同源）
TONE = [TABLE["V"], TABLE["N"], TABLE["M"], TABLE["P"]]
TONE_LEN = 512
MATRIX = [0x605DEEE0, 0x605DEEF2, 0x605DEF04, 0x605DEF16]
MATRIX_LEN = 18
CHROMA = [0x605DEB44, 0x605DEBA8, 0x605DEC0C, 0x605DEC70]
CHROMA_LEN = 100
HOOKS = [(0x604B811D, 7), (0x60479A2B, 5), (0x604B6AD6, 5), (0x604B4CAD, 6)]
STUBS = [(0x605DECD4, 69), (0x605DED1C, 38), (0x605DED44, 56), (0x605DEDD4, 16),
         (0x605DEDE4, 16), (0x605DEE3C, 16), (0x601ADE04, 50)]
STOCK_G8_TAIL = (0x604B9C23, 0x604B9C2B - 0x604B9C23)   # 钩子后面到 gate 之前的 stock 残段
DEAD_WINDOW = (HOOK_ADDR, HOOK_ADDR + HOOK_LEN + 1)      # 含被盖掉的 0x604B9C22
UI_NAMES = ["Ektachrome 100 VS", "Elite Color 200", "Classic Chrome", "Pro Neg Hi"]

DEFAULT_TOOLCHAIN = ROOT / ".tmp" / "olya" / "toolchains" / "mn10300-elf" / "bin"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def chk(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("  ok   " if ok else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))
        return bool(ok)

    @property
    def closed(self) -> bool:
        return all(c["ok"] for c in self.checks)

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.checks if not c["ok"]]


def payload_u32(payload: bytes, addr: int) -> int:
    return struct.unpack_from("<I", payload, addr - CODE_BASE)[0]


def slice_at(payload: bytes, addr: int, n: int) -> bytes:
    off = addr - CODE_BASE
    if off < 0 or off + n > len(payload):
        raise ValueError(f"{addr:#x}+{n} 超出 block0 payload")
    return payload[off:off + n]


# ---------------------------------------------------------------- objdump 自检
def tool(name: str, toolchain: Path) -> Path:
    for cand in (toolchain / f"mn10300-elf-{name}.exe", toolchain / f"mn10300-elf-{name}"):
        if cand.exists():
            return cand
    raise SystemExit(f"error: {name} 不在 {toolchain}（反汇编自检需要 mn10300-elf 工具链）")


def disassemble_bytes(blob: bytes, addr: int, toolchain: Path, workdir: Path) -> list[tuple[int, int, str]]:
    """把**镜像里的机器码字节**交给 objdump 反汇编，返回 [(addr, len, text)]。

    用 .byte 伪指令重建输入，避免"反汇编的是我自己的汇编源码"这种自证循环。
    """
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "image_bytes.s"
    lines = ["\t.am33", "\t.text", "\t.globl\tblob", "blob:"]
    lines += [f"\t.byte\t0x{b:02x}" for b in blob]
    src.write_text("\n".join(lines) + "\n", encoding="ascii")
    obj, elf = workdir / "image_bytes.o", workdir / "image_bytes.elf"

    def run(cmd: list[str], what: str) -> None:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"error: {what} 失败\n{' '.join(cmd)}\n{proc.stderr}")

    run([str(tool("as", toolchain)), "-o", str(obj), str(src)], "重建字节块")
    run([str(tool("ld", toolchain)), "-e", "blob", f"-Ttext={addr:#x}", "-o", str(elf), str(obj)],
        "链接字节块")
    proc = subprocess.run([str(tool("objdump", toolchain)), "-d", str(elf)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"error: objdump 失败\n{proc.stderr}")

    out: list[tuple[int, int, str]] = []
    for line in proc.stdout.splitlines():
        m = re.match(r"^\s*([0-9a-f]{8}):\t((?:[0-9a-f]{2} )+)\s*(\S.*)?$", line)
        if not m:
            continue
        start = int(m.group(1), 16)
        raw = bytes.fromhex(m.group(2).replace(" ", " "))
        text = (m.group(3) or "").strip()
        text = re.sub(r"\s*<[^>]*>", "", text)          # 去掉 <sym>
        text = re.sub(r"\s+", " ", text)
        # objdump 把超过一行的指令拆成若干行，续行没有助记符：并回上一条。
        if not text and out:
            a, n, t = out[-1]
            out[-1] = (a, n + len(raw), t)
            continue
        out.append((start, len(raw), text))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验 PREVIEW_PERMODE.BIN（预览分档取表桩）")
    ap.add_argument("--base", type=Path, default=ROOT / "build" / "RELEASE_v1.BIN")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "PREVIEW_PERMODE.BIN")
    ap.add_argument("--spec", type=Path, default=None, help="默认 <out>.spec.json")
    ap.add_argument("--json", type=Path, default=None, help="默认 <out>.sim.json")
    ap.add_argument("--toolchain", type=Path, default=DEFAULT_TOOLCHAIN)
    ap.add_argument("--workdir", type=Path, default=ROOT / ".tmp" / "permode",
                    help="反汇编自检的中间目录（默认 .tmp/permode）")
    ap.add_argument("--no-objdump", action="store_true", help="跳过反汇编自检（仅排查用，正式校验不要加）")
    args = ap.parse_args(argv)

    out_path = args.out
    spec_path = args.spec or Path(str(out_path) + ".spec.json")
    sim_path = args.json or Path(str(out_path) + ".sim.json")
    rep = Report()

    print(f"base  {args.base}")
    print(f"out   {out_path}")
    print()
    print("[A] 容器层")
    base_data = args.base.read_bytes()
    out_data = out_path.read_bytes()
    base_sha, out_sha = sha256(base_data), sha256(out_data)
    base_blocks, out_blocks = oc.parse(base_data), oc.parse(out_data)
    rep.chk("底座是 3 块的 RELEASE_v1 容器", len(base_blocks) == 3, f"{len(base_blocks)} 块")
    rep.chk("输出也是 3 块", len(out_blocks) == 3, f"{len(out_blocks)} 块")
    rep.chk("文件大小一致", len(base_data) == len(out_data),
            f"{len(base_data):,} B")
    rep.chk("sha256 与底座不同", base_sha != out_sha, f"{out_sha[:16]}…")
    if len(base_blocks) != 3 or len(out_blocks) != 3:
        # 容器都不是 3 块，后面每条断言都没意义：直接给结论，别抛 traceback
        sim_path.write_text(json.dumps({
            "verifier": "tools/verify_preview_permode.py",
            "sha256": out_sha,
            "closed": False,
            "cases": len(rep.checks),
            "passed": sum(1 for c in rep.checks if c["ok"]),
            "checks": rep.checks,
            "failed": rep.failed,
            "scope": "静态校验中止：容器块数不是 3",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("OPEN  容器块数不对，校验中止")
        print(f"sim  {sim_path}")
        return 1
    b1 = [(blocks[1].payload, blocks[1].header, blocks[1].trailer) for blocks in (base_blocks, out_blocks)]
    rep.chk("block1 payload+header+trailer 逐字节相同", b1[0] == b1[1])
    b2 = [(blocks[2].payload, blocks[2].header, blocks[2].trailer) for blocks in (base_blocks, out_blocks)]
    rep.chk("block2 payload+header+trailer 逐字节相同", b2[0] == b2[1])
    rep.chk("block0 header 相同", base_blocks[0].header == out_blocks[0].header)
    rep.chk("block0 trailer 只重算了校验和（trailer[28:32] 变、其余同）",
            base_blocks[0].trailer[:28] == out_blocks[0].trailer[:28]
            and base_blocks[0].trailer[28:32] != out_blocks[0].trailer[28:32],
            f"{base_blocks[0].trailer[28:32].hex()} → {out_blocks[0].trailer[28:32].hex()}")

    bp, op = base_blocks[0].payload, out_blocks[0].payload
    hook_off = HOOK_ADDR - CODE_BASE
    stub_off = STUB_ADDR - CODE_BASE

    print()
    print("[B] 差异面（只许这两个窗口）")
    allowed = set(range(hook_off, hook_off + HOOK_LEN)) | set(range(stub_off, stub_off + STUB_LEN))
    stray = [(CODE_BASE + i, bp[i], op[i]) for i in range(len(bp))
             if bp[i] != op[i] and i not in allowed]
    rep.chk(f"窗口外 0 处改动（block0 共 {len(bp):,} B）", not stray,
            f"第一处 {stray[0][0]:#x}: {stray[0][1]:02x}→{stray[0][2]:02x}" if stray else "")
    changed = sum(1 for i in range(len(bp)) if bp[i] != op[i])
    changed_in_hook = sum(1 for i in range(hook_off, hook_off + HOOK_LEN) if bp[i] != op[i])
    changed_in_stub = sum(1 for i in range(stub_off, stub_off + STUB_LEN) if bp[i] != op[i])
    rep.chk("改动字节数 = 钩子内 + 桩内", changed == changed_in_hook + changed_in_stub,
            f"{changed} = {changed_in_hook} + {changed_in_stub}")

    print()
    print("[C] 钩子")
    base_hook = slice_at(bp, HOOK_ADDR, HOOK_LEN)
    out_hook = slice_at(op, HOOK_ADDR, HOOK_LEN)
    rep.chk("底座原位是 stock G8 判定字节", base_hook == HOOK_ORIG, base_hook.hex())
    ok_len = out_hook[0] == 0xDC and len(out_hook) == 5
    disp = struct.unpack("<i", out_hook[1:5])[0] if ok_len else 0
    hook_target = HOOK_ADDR + disp
    rep.chk("钩子是 5 字节远跳 DC disp32", ok_len, out_hook.hex())
    rep.chk("远跳目标正好是桩地址", hook_target == STUB_ADDR, f"{hook_target:#x}")
    rep.chk("被盖掉的第 6 字节（cmp 的立即数 0x02）原样留着",
            slice_at(op, HOOK_ADDR + 5, 1) == slice_at(bp, HOOK_ADDR + 5, 1) == b"\x02")

    print()
    print("[D] 桩区")
    src_region = slice_at(bp, STUB_ADDR, STUB_LEN)
    rep.chk("底座该处 49 B 全是 0xFF（原厂口袋，没踩到东西）",
            src_region == b"\xff" * STUB_LEN)
    out_stub = slice_at(op, STUB_ADDR, STUB_LEN)
    rep.chk("输出里的桩等于金标 49 B", out_stub.hex() == STUB_HEX,
            f"sha {sha256(out_stub)[:16]}…")
    rep.chk("桩落在 pocket 上限之内", STUB_ADDR + STUB_LEN <= STUB_LIMIT_END,
            f"{STUB_ADDR:#x}..{STUB_ADDR + STUB_LEN - 1:#x} < {STUB_LIMIT_END:#x}")
    rest = STUB_POCKET_END - (STUB_ADDR + STUB_LEN)
    rep.chk("桩所在口袋的剩余字节仍全 FF",
            slice_at(op, STUB_ADDR + STUB_LEN, rest) == b"\xff" * rest,
            f"{rest} B")
    pocket = [name for a, b, name in SAFE_POCKETS if a <= STUB_ADDR < b]
    rep.chk("桩位在 second_verify 的已知安全口袋内（KNOWN_ZONES）",
            bool(pocket) and STUB_ADDR + STUB_LEN <= max(
                b for a, b, _ in SAFE_POCKETS if a <= STUB_ADDR < b),
            f"{STUB_ADDR:#x} ∈ {pocket[0] if pocket else '无'}")
    dead = [f"{a:#x}..{b:#x} {n}" for a, b, n in DEAD_ZONES
            if not (STUB_ADDR + STUB_LEN <= a or STUB_ADDR > b)]
    rep.chk("桩不在 second_verify 的判死区（DEAD_ZONES）内", not dead, str(dead))

    print()
    print("[E] 反汇编自检（objdump 反汇编镜像里的字节）")
    if args.no_objdump:
        rep.chk("反汇编自检", False, "被 --no-objdump 跳过")
    else:
        # 中间文件放工作区里（%TEMP% 下 GNU as 建不了 .o：Permission denied）
        work = args.workdir / "objdump"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        stub_dis = disassemble_bytes(out_stub, STUB_ADDR, args.toolchain, work)
        got = [(a - STUB_ADDR, n, t) for a, n, t in stub_dis]
        golden = [(o, n, t) for o, n, t in STUB_GOLDEN]
        rep.chk("桩的指令条数与金标一致", len(got) == len(golden), f"{len(got)} / {len(golden)}")
        if len(got) == len(golden):
            diffs = [f"+{g[0]:#04x} 期望 {g[2]!r} 实得 {h[2]!r}"
                     for g, h in zip(golden, got) if g != h]
            rep.chk("桩逐条指令（偏移/长度/助记符/操作数/分支目标）与金标一致",
                    not diffs, diffs[0] if diffs else f"{len(got)} 条")
            jmps = {t: a for a, _, t in got if t.startswith("jmp ")}
            rep.chk("桩里三条远跳指向 RET / G9 / GATE",
                    jmps.get(f"jmp {RET_ADDR:08x}") == 0x17
                    and jmps.get(f"jmp {G9_ADDR:08x}") == 0x27
                    and jmps.get(f"jmp {GATE_ADDR:08x}") == 0x2C,
                    str({k: hex(v) for k, v in jmps.items()}))
            rep.chk("桩里没有 call（不做远调用，避免 5/6 字节编码分歧）",
                    not any(t.startswith("call") for _, _, t in got))
        # 桩里的三个位移：自己再按 raw 字节算一遍（不依赖 objdump）
        raw_ok = True
        for off, target in ((0x17, RET_ADDR), (0x27, G9_ADDR), (0x2C, GATE_ADDR)):
            d = struct.unpack("<i", out_stub[off + 1:off + 5])[0]
            raw_ok &= (STUB_ADDR + off + d) == target
        rep.chk("三条远跳的 disp32 按 raw 字节自算也指向目标", raw_ok)

        hook_dis = disassemble_bytes(out_hook + slice_at(op, HOOK_ADDR + HOOK_LEN, 3),
                                     HOOK_ADDR, args.toolchain, work)
        first = hook_dis[0] if hook_dis else (0, 0, "")
        rep.chk("钩子被反汇编成 jmp <桩>（5 字节）",
                first[0] == HOOK_ADDR and first[1] == 5
                and re.fullmatch(rf"jmp {STUB_ADDR:08x}", first[2] or "") is not None,
                f"{first[2]!r} len={first[1]}")

    print()
    print("[F] 不许动的东西")
    for name, addr in (("V", TONE[0]), ("N", TONE[1]), ("M", TONE[2]), ("P", TONE[3])):
        rep.chk(f"tone 表 {name} {addr:#x}（512 B）逐字节相同",
                slice_at(bp, addr, TONE_LEN) == slice_at(op, addr, TONE_LEN))
    rep.chk("tone_ptrtab 16 B 逐字节相同",
            slice_at(bp, PTRTAB, 16) == slice_at(op, PTRTAB, 16),
            slice_at(op, PTRTAB, 16).hex())
    entries = [payload_u32(op, PTRTAB + 4 * i) for i in range(4)]
    rep.chk("ptrtab 仍是 V/N/M/P 顺序",
            entries == [TABLE[v] for v in PTRTAB_ORDER], str([hex(e) for e in entries]))
    rep.chk(f"恒等表 {IDENTITY:#x} 的立即数没动（0x604B9D20 那条）",
            slice_at(op, 0x604B9D20, 6) == slice_at(bp, 0x604B9D20, 6),
            slice_at(op, 0x604B9D20, 6).hex())
    rep.chk("k2 立即数 0x604B9D1A 仍是 N 表",
            payload_u32(op, K2_IMM) == TABLE["N"], hex(payload_u32(op, K2_IMM)))
    for i, addr in enumerate(MATRIX):
        rep.chk(f"矩阵 {i+1} {addr:#x}（{MATRIX_LEN} B）逐字节相同",
                slice_at(bp, addr, MATRIX_LEN) == slice_at(op, addr, MATRIX_LEN))
    for i, addr in enumerate(CHROMA):
        rep.chk(f"色度 {i+1} {addr:#x}（{CHROMA_LEN} B）逐字节相同",
                slice_at(bp, addr, CHROMA_LEN) == slice_at(op, addr, CHROMA_LEN))
    for addr, n in HOOKS:
        rep.chk(f"已有钩子 {addr:#x}（{n} B）逐字节相同",
                slice_at(bp, addr, n) == slice_at(op, addr, n))
    for addr, n in STUBS:
        rep.chk(f"已有桩/表 {addr:#x}（{n} B）逐字节相同",
                slice_at(bp, addr, n) == slice_at(op, addr, n))
    tail_addr, tail_len = STOCK_G8_TAIL
    rep.chk(f"stock G8 残段 {tail_addr:#x}..{tail_addr + tail_len - 1:#x} 原样保留",
            slice_at(bp, tail_addr, tail_len) == slice_at(op, tail_addr, tail_len))
    ui_hits = [n for n in UI_NAMES
               if n.encode("utf-16-le") in base_blocks[1].payload
               and n.encode("utf-16-le") in out_blocks[1].payload]
    rep.chk("block1 里 4 个 UI 名字都还在", len(ui_hits) == 4, f"{len(ui_hits)}/4")
    rep.chk("block1 内容整体未变", base_blocks[1].payload == out_blocks[1].payload)

    print()
    print("[G] 死字节安全")
    dec = Decoder()
    fn_lines = list(dec.disasm(op, PREVIEW_FN - CODE_BASE, RET_ADDR + 6 - PREVIEW_FN, PREVIEW_FN))
    targets: dict[int, int] = {}
    for item in fn_lines:
        addr, length, raw, mn, ops, _rule = item
        if mn in ("jmp", "bra", "beq", "bne", "bhi", "bls", "bcc", "bcs", "bge", "blt",
                  "bgt", "ble", "bvc", "bvs", "bnc", "bns", "call"):
            m = re.search(r"0x([0-9a-f]{8})", ops or "")
            if m:
                targets[int(m.group(1), 16)] = addr
    rep.chk("函数内没有任何分支/调用目标落进被盖掉的 5+1 字节",
            not [t for t in targets if DEAD_WINDOW[0] <= t < DEAD_WINDOW[1]],
            f"{len(targets)} 个目标扫过")
    rep.chk("钩子处不是别的分支的重入点（只有 fall-through）",
            not [a for t, a in targets.items() if t == HOOK_ADDR],
            f"引用者 {[hex(a) for t, a in targets.items() if t == HOOK_ADDR]}")
    rep.chk(f"函数入口 {PREVIEW_FN:#x} 与 caller {CALLER:#x} 未变",
            slice_at(bp, PREVIEW_FN, 6) == slice_at(op, PREVIEW_FN, 6)
            and slice_at(bp, CALLER, 26) == slice_at(op, CALLER, 26))

    print()
    print("[H] spec 对账")
    if not spec_path.exists():
        rep.chk(f"spec 存在：{spec_path}", False, "缺文件（先跑 build_preview_permode.py）")
        spec = {}
    else:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        rep.chk("spec 的 base_sha256 与底座一致", spec.get("base_sha256") == base_sha)
        rep.chk("spec 的 out_sha256 与输出一致", spec.get("out_sha256") == out_sha,
                spec.get("out_sha256", "")[:16])
        rep.chk("spec 的钩子 new 字节与实测一致",
                (spec.get("hook") or {}).get("new") == out_hook.hex())
        rep.chk("spec 的钩子目标与实测一致",
                (spec.get("hook") or {}).get("jmp_target") == f"{hook_target:#x}")
        rep.chk("spec 的桩地址/长度/hex 与实测一致",
                (spec.get("stub") or {}).get("ram") == f"{STUB_ADDR:#x}"
                and (spec.get("stub") or {}).get("len") == STUB_LEN
                and (spec.get("stub") or {}).get("hex") == out_stub.hex())
        wins = spec.get("allowed_diff_windows") or []
        rep.chk("spec 声明的差异窗口 = 实测的两个窗口",
                {(w.get("ram"), w.get("len")) for w in wins}
                == {(f"{HOOK_ADDR:#x}", HOOK_LEN), (f"{STUB_ADDR:#x}", STUB_LEN)},
                str([(w.get("ram"), w.get("len")) for w in wins]))
        rep.chk("spec 的目录/映射与金标一致",
                (spec.get("design") or {}).get("fix") is not None
                and [b.get("table") for b in (spec.get("branch_map") or [])] == ["V", "N", "M", "P"]
                and [int(b.get("addr"), 16) for b in (spec.get("branch_map") or [])]
                == [TABLE[v] for v in PTRTAB_ORDER])

    print()
    print("[I] 二次核验落点声明（allow.json）")
    allow_path = Path(str(out_path)[:-4] + ".allow.json") if str(out_path).endswith(".BIN") else \
        out_path.parent / (out_path.stem + ".allow.json")
    if not allow_path.exists():
        rep.chk("allow.json 存在", False, str(allow_path))
    else:
        allow = json.loads(allow_path.read_text(encoding="utf-8"))
        rep.chk("allow.json 是 [[start,end,name],...] 三条内", isinstance(allow, list) and len(allow) >= 2,
                str(allow_path.name))
        hook_win = [a for a in allow if a and a[0] == HOOK_ADDR and a[1] == HOOK_ADDR + HOOK_LEN - 1]
        stub_win = [a for a in allow if a and a[0] == STUB_ADDR
                    and a[1] == STUB_ADDR + STUB_LEN - 1]
        rep.chk("allow.json 声明了钩子 5 字节（闭区间）", bool(hook_win), str(hook_win[:1]))
        rep.chk("allow.json 声明了桩 49 字节（闭区间）", bool(stub_win), str(stub_win[:1]))

    print()
    closed = rep.closed
    cases = len(rep.checks)
    passed = sum(1 for c in rep.checks if c["ok"])
    print(f"{'CLOSED' if closed else 'OPEN'}  {passed}/{cases} 项通过")

    sim = {
        "verifier": "tools/verify_preview_permode.py",
        "sha256": out_sha,
        "closed": closed,
        "cases": cases,
        "passed": passed,
        "base": str(args.base),
        "base_sha256": base_sha,
        "out": str(out_path),
        "spec": str(spec_path),
        "patch": {
            "hook": {"ram": f"{HOOK_ADDR:#x}", "len": HOOK_LEN,
                     "old": base_hook.hex(), "new": out_hook.hex(),
                     "jmp_target": f"{hook_target:#x}", "disp": disp},
            "stub": {"ram": f"{STUB_ADDR:#x}", "len": STUB_LEN,
                     "hex": out_stub.hex(), "sha256": sha256(out_stub)},
            "branch_map": [{"k": i + 1, "table": v, "addr": f"{TABLE[v]:#x}"}
                           for i, v in enumerate(PTRTAB_ORDER)],
            "k2_immediate_left_as_is": f"{K2_IMM:#x} = N",
            "stub_pocket": f"{STUB_POCKET_START:#x}..{STUB_POCKET_END:#x}",
            "allow_file": str(allow_path),
        },
        "scope": (
            "静态：容器重建 + 逐字节差异审计 + objdump 反汇编自检。"
            "未上机验证（四档预览观感、以及 0x6066159E 口袋运行期不被写，需真机确认）。"
        ),
        "checks": rep.checks,
        "failed": rep.failed,
    }
    sim_path.write_text(json.dumps(sim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"sim  {sim_path}  (closed={closed})")
    return 0 if closed else 1


if __name__ == "__main__":
    sys.exit(main())
