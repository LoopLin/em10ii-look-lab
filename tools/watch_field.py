#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watch_field.py — 用 libsim 的寄存器轨迹反推"这段代码碰了哪块内存"。

为什么不是直接 watchpoint
--------------------------
libsim（.tmp/olya 的 penf-mn103 harness）的 PFMN002 协议里没有逐次访问的
watchpoint。它只给三样东西：

  * 每条指令执行前的 32 个寄存器快照（最多 100000 条，可保留头或尾）；
  * 运行结束后各映射区的最终字节；
  * PC 断点。

本工具因此这样重建访问：

  指令地址  = 轨迹第 i 条的 PC
  有效地址  = 该指令的寻址方式（tools/mn10300_dis.py 从镜像解码）
              + 执行前寄存器
  写入值    = 执行前源寄存器的值
  读出值    = 执行后目标寄存器的值（最后一条用引擎返回的最终寄存器）

能证明：在本次模拟的这段执行里，PC=... 的指令读/写了这个地址。
不能证明：真机也这样（没有 ISP、没有真实调度；--patch 会绕过装载器）。
详见 docs/map/watch-recipes.md。

用法示例（完整示例见文档）：

  python tools/watch_field.py --pc 0x604AA296 --stop 0x604AA31D --limit 2000 \
      --code 0x604AA280:0x100 --code 0x604B4B00:0x200 \
      --rw 0x605D4000:0x2000 \
      --reg A3=0x605D4C00 --reg A2=0x605D5400 --reg SP=0x605D5280 \
      --poke 0x605D4FC0=00000000 --poke 0x605D502F=02 --poke 0x605D4C0C=00a4 \
      --watch 0x605D4829:0x64 --watch 0x605D541D:0x64
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASE = 0x60021020
IMAGE = ROOT / "analysis" / "00_0100_40300020.decoded.bin"
ENGINE = ROOT / ".tmp" / "olya" / "toolchains" / "mn103-gnu-build" / ".libs" / "penf-mn103.exe"
REGISTERS = ['D0', 'D1', 'D2', 'D3', 'A0', 'A1', 'A2', 'A3', 'SP', 'PC', 'PSW',
             'MDR', 'LIR', 'LAR', 'FAULT', 'Z', 'XY', 'R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7',
             'RAMB', 'RAR', 'U', 'VC', 'EXB', 'CRT', 'UNK']

sys.path.insert(0, str(HERE))
from mn10300_dis import Decoder, Operands  # noqa: E402

REGS_ORDER = [(0x80, (2,)), (0x40, (3,)), (0x20, (6,)), (0x10, (7,)),
              (0x08, (0, 1, 4, 5, 10, 12, 13)),
              (0x04, (17, 18)), (0x02, (19, 20, 21, 22)), (0x01, (15, 16))]

# movm 的存取顺序照 igen 的 store/load 顺序抄；地址是相对的，顺序只影响
# 地址与寄存器值的配对。
PUSH_BLOCKS = [(0x04, [17, 18]), (0x02, [19, 20, 21, 22]), (0x01, [15, 16]),
               (0x80, [2]), (0x40, [3]), (0x20, [6]), (0x10, [7]),
               (0x08, [0, 1, 4, 5, 10, 12, 13])]
POP_BLOCKS = [(0x08, [13, 12, 10, 5, 4, 1, 0]), (0x10, [7]), (0x20, [6]),
              (0x40, [3]), (0x80, [2]), (0x01, [16, 15]),
              (0x02, [22, 21, 20, 19]), (0x04, [18, 17])]

COMMENT_RE = re.compile(r"\s+\([^()]*\s+[^()]*\)\s*$")
FORM_RE = re.compile(r"\(([A-Za-z0-9_,+]+)\)")
MEM_MNEMS = ("mov", "movbu", "movhu")


def parse_span(text):
    """ADDR:LEN -> (start, length)。与 tools/em10_run.py 一致，第二段是长度。"""
    addr, _, ln = text.partition(":")
    start = int(addr, 0)
    if not ln:
        raise SystemExit(f"区间缺少长度: {text!r}（要 ADDR:LEN）")
    length = int(ln, 16) if ln.lower().startswith("0x") else int(ln, 0)
    return start, length


def sign_extend(v, bits):
    if v is None:
        return None
    if v >> (bits - 1):
        v -= 1 << bits
    return v


def reg_index(name):
    """d0..d3/a0..a3/sp/e0..e7 -> 引擎寄存器下标。"""
    name = name.strip().lower().rstrip("+")
    if name == "sp":
        return 8
    if name == "pc":
        return 9
    if len(name) == 2 and name[0] in "da" and name[1].isdigit():
        return (4 if name[0] == "a" else 0) + int(name[1])
    if len(name) >= 2 and name[0] == "e" and name[1:].isdigit():
        return 15 + int(name[1:])
    return None


def split_ops(text):
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
    out.append(text[start:].strip())
    return [x for x in out if x != ""]


def top_comma(text):
    depth = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            return i
    return None


class Inst(object):
    """一条已解码指令的静态信息（按 PC 缓存）。"""
    __slots__ = ("pc", "nb", "mnem", "ops", "syntax", "rule", "op", "kind",
                 "size", "form", "value_reg", "bit", "movm")

    def __init__(self, pc, nb, mnem, ops, rule, op):
        self.pc = pc
        self.nb = nb
        self.mnem = mnem
        self.rule = rule
        self.op = op
        self.ops = COMMENT_RE.sub("", ops or "").strip()
        self.kind = None
        self.size = 0
        self.form = None
        self.value_reg = None
        self.bit = None
        self.movm = None
        syn = rule.syntax or ""
        body = syn[len(mnem):].strip() if syn.lower().startswith(mnem.lower()) else syn
        self.syntax = COMMENT_RE.sub("", body).strip()
        if mnem in MEM_MNEMS:
            m = FORM_RE.search(self.syntax)
            if m is None:
                return
            comma = top_comma(self.syntax)
            if comma is None:
                return
            self.form = m.group(1)
            self.size = {"mov": 4, "movbu": 1, "movhu": 2}[mnem]
            self.kind = "R" if m.start() < comma else "W"
            others = [p for p in split_ops(self.ops) if "(" not in p]
            if others:
                self.value_reg = others[0]
        elif mnem in ("bset", "bclr", "btst"):
            m = FORM_RE.search(self.syntax)
            if m is None:
                return
            self.form = m.group(1)
            self.size = 1
            self.kind = "R" if mnem == "btst" else "W"
            parts = split_ops(self.ops)
            if parts:
                self.bit = parts[0]
        elif mnem == "movm":
            m = FORM_RE.search(self.syntax)
            if m is None:
                return
            comma = top_comma(self.syntax)
            self.kind = "R" if (comma is None or m.start() < comma) else "W"
            self.size = 4
            self.movm = op.raw.get("REGS") or 0


class Analyzer(object):
    def __init__(self, image, writes_only, max_hits, detail):
        self.image = image
        self.dec = Decoder()
        self.cache = {}
        self.writes_only = writes_only
        self.max_hits = max_hits
        self.detail = detail
        self.hits = []
        self.hit_count = 0
        self.truncated_hits = False
        self.undecoded = 0
        self.undecoded_pcs = []
        self.unhandled = 0
        self.unhandled_pcs = []
        self.shadow = {}
        self.mismatch = 0
        self.mismatch_samples = []
        self.warnings = []
        self.captures = {}

    # -- decode ---------------------------------------------------------
    def inst(self, pc):
        if pc in self.cache:
            return self.cache[pc]
        off = pc - BASE
        info = None
        if 0 <= off < len(self.image):
            res = self.dec.match(self.image, off, pc)
            if res is not None:
                nb, mnem, ops, rule = res
                op = Operands(rule, self.image, off, pc)
                info = Inst(pc, nb, mnem, ops, rule, op)
        self.cache[pc] = info
        return info

    # -- registers ------------------------------------------------------
    @staticmethod
    def pick(op, stem):
        """规则里字段名可能是 DI / DI0 / RM0 —— 按前缀找第一个。"""
        if stem in op.raw:
            return stem
        for k in op.raw:
            if k.startswith(stem) and (k[len(stem):] == "" or k[len(stem):].isdigit()):
                return k
        return None

    @staticmethod
    def reg_val(op, field):
        raw = op.raw.get(field)
        if raw is None:
            return None
        width = op.rule.fields[field][2]
        if width == 2:
            return (4 + raw) if field[0] == "A" else raw
        if 8 <= raw <= 11:
            return 4 + raw - 8
        if 12 <= raw <= 15:
            return raw - 12
        if 0 <= raw <= 7:
            return 15 + raw
        return None

    def addr_of(self, info, regs):
        form, op = info.form, info.op
        parts = form.split(",")
        if len(parts) == 1:
            t = parts[0].rstrip("+")
            tl = t.lower()
            if tl == "sp":
                return regs[8]
            if tl == "usp":
                return regs[25]
            if tl.startswith("abs") or tl.startswith("imm"):
                bits = int(re.sub(r"[^0-9]", "", t) or "32")
                if bits == 8:
                    return op.raw.get("IMM8")
                return op.value("IMM%d" % bits)
            fld = self.pick(op, t.upper())
            idx = self.reg_val(op, fld) if fld else None
            return None if idx is None else regs[idx]
        disp, base = parts[0], parts[1].rstrip("+")
        base_l = base.lower()
        if base_l == "sp":
            basev = regs[8]
        elif base_l == "usp":
            basev = regs[25]
        else:
            fld = self.pick(op, base.upper())
            idx = self.reg_val(op, fld) if fld else None
            basev = None if idx is None else regs[idx]
        if basev is None:
            return None
        dl = disp.lower()
        if dl in ("di", "ri"):
            fld = self.pick(op, disp.upper())
            idx = self.reg_val(op, fld) if fld else None
            return None if idx is None else (basev + regs[idx]) & 0xFFFFFFFF
        if dl.startswith("d"):
            bits = int(dl[1:])
            if base_l in ("sp", "usp"):
                if bits == 8:
                    v = op.value("D8")
                else:
                    v = op.value("IMM%d" % bits)
            elif base_l[0] == "r":
                raw = op.raw.get("IMM%d" % bits)
                if raw is None:
                    raw = op.raw.get("D8")
                v = sign_extend(raw, bits)
            else:
                v = op.signed("D%d" % bits)
            return None if v is None else (basev + v) & 0xFFFFFFFF
        return None

    def movm_accesses(self, info, before, after):
        mask, out = info.movm or 0, []
        if info.kind == "W":
            sp = before[8]
            for bit, regslist in PUSH_BLOCKS:
                if mask & bit:
                    for r in regslist:
                        sp = (sp - 4) & 0xFFFFFFFF
                        out.append((sp, 4, before[r], "push"))
                    if bit == 0x01:
                        sp = (sp - 16) & 0xFFFFFFFF
        else:
            sp = (before[8] + 4) & 0xFFFFFFFF
            for bit, regslist in POP_BLOCKS:
                if mask & bit:
                    if bit == 0x01:
                        sp = (sp + 16) & 0xFFFFFFFF
                    for r in regslist:
                        out.append((sp, 4, after[r], "pop"))
                        sp = (sp + 4) & 0xFFFFFFFF
        return out

    # -- shadow ---------------------------------------------------------
    def shadow_byte(self, addr, initial):
        if addr in self.shadow:
            return self.shadow[addr]
        v = initial(addr)
        if v is not None:
            self.shadow[addr] = v
        return v

    def set_shadow(self, addr, size, value):
        if value is None:
            return
        for k in range(size):
            self.shadow[addr + k] = (value >> (8 * k)) & 0xFF


def parse_watch(text):
    parts = text.split(":")
    if len(parts) < 2:
        raise SystemExit(f"--watch 格式是 ADDR:LEN[:r|w|rw]，收到 {text!r}")
    addr, length = int(parts[0], 0), int(parts[1], 0)
    perm = parts[2].lower() if len(parts) > 2 else "rw"
    if perm not in ("r", "w", "rw"):
        raise SystemExit(f"--watch 权限只能是 r/w/rw: {text!r}")
    return addr, length, perm


def parse_follow(text):
    """FIELD:LEN[:PERM]

    FIELD 可以是：
      0x60123456            绝对地址的 u32 指针字段
      0x60123456+0x1d       绝对字段 + 数据起点偏移
      a3+0x1230             寄存器相对字段
      a3+0x1230+0x1d        寄存器相对字段 + 数据起点偏移
    """
    parts = text.split(":")
    if len(parts) < 2:
        raise SystemExit(f"--follow 格式是 FIELD:LEN[:r|w|rw]，收到 {text!r}")
    field, length = parts[0], int(parts[1], 0)
    perm = parts[2].lower() if len(parts) > 2 else "rw"
    if perm not in ("r", "w", "rw"):
        raise SystemExit(f"--follow 权限只能是 r/w/rw: {text!r}")
    m = re.fullmatch(r"0x([0-9a-fA-F]+)(?:\+0x([0-9a-fA-F]+))?", field)
    if m:
        return {"kind": "abs", "addr": int(m.group(1), 16),
                "delta": int(m.group(2), 16) if m.group(2) else 0,
                "len": length, "perm": perm, "field_text": field}
    m = re.fullmatch(r"([A-Za-z][A-Za-z0-9]*)(?:\+(-?0x[0-9a-fA-F]+|-?\d+))?"
                     r"(?:\+(0x[0-9a-fA-F]+|\d+))?", field)
    if not m:
        raise SystemExit(f"--follow 字段看不懂: {field!r}")
    reg = m.group(1)
    disp = int(m.group(2), 0) if m.group(2) else 0
    delta = int(m.group(3), 0) if m.group(3) else 0
    if reg_index(reg) is None:
        raise SystemExit(f"--follow 寄存器不认识: {reg!r}")
    return {"kind": "reg", "reg": reg_index(reg), "disp": disp, "delta": delta,
            "len": length, "perm": perm, "field_text": field}


def build_regions(args, image):
    regions = []

    def slice_image(start, length):
        end = start + length
        if not (BASE <= start < end <= BASE + len(image)):
            sys.exit(f"区间超出镜像: {start:#x}:{length:#x}（镜像 {BASE:#x}..{BASE + len(image):#x}）")
        return image[start - BASE:end - BASE]

    for spec in args.code:
        start, length = parse_span(spec)
        regions.append([f"code_{start:x}", start, bytearray(slice_image(start, length)), "rx"])
    for spec in args.rw:
        start, length = parse_span(spec)
        regions.append([f"rw_{start:x}", start, bytearray(slice_image(start, length)), "rw"])
    for spec in args.rwx:
        start, length = parse_span(spec)
        regions.append([f"rwx_{start:x}", start, bytearray(slice_image(start, length)), "rwx"])
    for spec in args.rw_any:
        start, length = parse_span(spec)
        regions.append([f"rwany_{start:x}", start, bytearray(length), "rw"])

    regions.sort(key=lambda r: r[1])
    for a, b in zip(regions, regions[1:]):
        if b[1] < a[1] + len(a[2]):
            sys.exit(f"区间重叠: {a[0]} 与 {b[0]}（libsim 不允许重叠）")

    pokes = []
    for spec in args.poke:
        addr, _, hx = spec.partition("=")
        pokes.append((int(addr, 0), bytes.fromhex(hx)))
    for pa, pb in pokes:
        hit = False
        for _name, start, data, _perm in regions:
            if start <= pa and pa + len(pb) <= start + len(data):
                data[pa - start:pa - start + len(pb)] = pb
                hit = True
        if not hit:
            print(f"warning: --poke {pa:#x} 不在任何映射区里，引擎会忽略它", file=sys.stderr)
    return regions, pokes


def run_engine(args, regions, pokes):
    regs = {name: 0 for name in REGISTERS}
    regs["PC"] = int(args.pc, 0)
    regs["SP"] = 0x61E00800
    for item in args.reg:
        name, _, value = item.partition("=")
        name = name.upper()
        if name not in REGISTERS:
            sys.exit(f"未知寄存器 {name}")
        regs[name] = int(value, 0)
    stop = int(args.stop, 0)
    trace_limit = args.trace_limit
    if trace_limit is None:
        trace_limit = min(args.limit, 100000)
    trace_limit = min(trace_limit, 100000)

    payload = bytearray(b"PFMN002\0" + struct.pack(
        "<6I", args.limit, stop, trace_limit, len(regions), 0, 0))
    payload.extend(struct.pack("<32I", *[regs[n] for n in REGISTERS]))
    for _name, start, data, perm in regions:
        mask = sum(bit for ch, bit in (("r", 1), ("w", 2), ("x", 4)) if ch in perm)
        payload.extend(struct.pack("<5I", start, len(data), mask, 1, 0xFFFFFFFF))
        payload.extend(bytes(data))

    try:
        proc = subprocess.run([str(ENGINE)], input=payload, capture_output=True,
                              timeout=args.timeout)
    except subprocess.TimeoutExpired:
        sys.exit(f"引擎超过 {args.timeout:g}s 未结束：请减小 --limit 或 --trace-limit "
                 f"（本次没有可用结果）")
    if proc.returncode:
        sys.exit(f"引擎失败 {proc.returncode}: {proc.stderr.decode(errors='replace')[:2000]}")
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        sys.exit(f"引擎输出无法解析: {proc.stdout[:400]!r} {proc.stderr[:400]!r}")
    return result, regs, stop, trace_limit


def analyze(args, image, regions, pokes, result):
    an = Analyzer(image, args.writes_only, args.max_hits, args.detail)
    watches = []
    for i, spec in enumerate(args.watch, 1):
        start, length, perm = parse_watch(spec)
        watches.append({"id": i, "start": start, "end": start + length,
                        "perm": perm, "label": "watch#%d" % i})
    follows = []
    for i, spec in enumerate(args.follow, 1):
        f = parse_follow(spec)
        f["id"] = i
        f["label"] = "follow#%d" % i
        f["values"] = {}
        follows.append(f)

    # 初始字节：poke > rw-any 零 > 镜像
    def initial(addr):
        for pa, pb in pokes:
            if pa <= addr < pa + len(pb):
                return pb[addr - pa]
        for _name, start, data, perm in regions:
            if "rw" in perm and "x" not in perm and start <= addr < start + len(data):
                return data[addr - start]
        off = addr - BASE
        if 0 <= off < len(image):
            return image[off]
        return None

    def initial_u32(addr):
        bs = [initial(addr + k) for k in range(4)]
        if any(b is None for b in bs):
            return None
        return int.from_bytes(bytes(bs), "little")

    # 监听区必须在已映射区里，否则一次访问就会 fault
    def mapped(a, n=1):
        for _name, start, data, _perm in regions:
            if start <= a and a + n <= start + len(data):
                return True
        return False

    for w in watches:
        if not mapped(w["start"], w["end"] - w["start"]):
            an.warnings.append("watch#%d 不在已映射区里（%#x..%#x），除非它从不被访问，否则会 fault"
                               % (w["id"], w["start"], w["end"]))
    for f in follows:
        if f["kind"] == "abs" and not mapped(f["addr"], 4):
            an.warnings.append("follow#%d 的字段 %#x 不在已映射区里" % (f["id"], f["addr"]))

    trace = result.get("trace") or []
    final = result["registers"]
    status = result.get("status")
    fault = result.get("fault") or {}
    fault_pc = fault.get("pc") if fault else None
    attempted = result.get("attempted", 0)
    truncated = result.get("trace_truncated", False)

    for cap in result.get("memory", []):
        raw = cap.get("hex")
        if raw is None:
            raw = cap.get("data")
        data = bytes.fromhex(raw) if isinstance(raw, str) else bytes(raw or [])
        an.captures[cap.get("address", 0)] = data

    counts = {"R": 0, "W": 0}
    per_watch = {}
    agg = {}

    def bump(key, hit):
        cur = agg.get(key)
        if cur is None:
            cur = agg[key] = {"pc": hit["pc"], "kind": hit["kind"], "count": 0,
                              "amin": hit["addr"], "amax": hit["addr"],
                              "labels": set(), "sample": hit["insn"]}
        cur["count"] += 1
        cur["amin"] = min(cur["amin"], hit["addr"])
        cur["amax"] = max(cur["amax"], hit["addr"] + hit["size"] - 1)
        cur["labels"].update(hit["regions"])
        return cur

    for i, before in enumerate(trace):
        pc = before[9]
        after = trace[i + 1] if i + 1 < len(trace) else final
        incomplete = (status in ("memory_fault", "instruction_fault")
                      and fault_pc == pc)
        info = an.inst(pc)
        if info is None:
            an.undecoded += 1
            if len(an.undecoded_pcs) < 5:
                an.undecoded_pcs.append(pc)
            continue
        if info.kind is None:
            if (info.mnem or "").startswith("mov") and "(" in info.syntax:
                an.unhandled += 1
                if len(an.unhandled_pcs) < 5:
                    an.unhandled_pcs.append(pc)
            continue

        # 本条指令生效的 follow 窗口（先用指令执行前的指针值）
        windows = [(w["start"], w["end"], w["perm"], w["label"]) for w in watches]
        for f in follows:
            if f["kind"] == "abs":
                field_addr = f["addr"]
            else:
                field_addr = None
                basev = before[f["reg"]]
                if basev:
                    field_addr = (basev + f["disp"]) & 0xFFFFFFFF
            if field_addr is None:
                continue
            val = f["values"].get(field_addr)
            if val is None:
                val = initial_u32(field_addr)
                if val is not None:
                    f["values"][field_addr] = val
            if val:
                start = (val + f["delta"]) & 0xFFFFFFFF
                windows.append((start, start + f["len"], f["perm"], f["label"]))
                f["last_window"] = (start, start + f["len"])

        accesses = []
        if info.mnem == "movm":
            for addr, size, value, extra in an.movm_accesses(info, before, after):
                accesses.append((info.kind, addr, size, value, extra))
        else:
            addr = an.addr_of(info, before)
            if addr is None:
                an.unhandled += 1
                if len(an.unhandled_pcs) < 5:
                    an.unhandled_pcs.append(pc)
                continue
            size = info.size
            if info.mnem in ("bset", "bclr", "btst"):
                bit = None
                b = info.bit or ""
                if b.lower().startswith("0x"):
                    bit = int(b, 16)
                else:
                    idx = reg_index(b)
                    if idx is not None:
                        bit = before[idx] & 0x1F
                prev = an.shadow_byte(addr, initial)
                if bit is None or prev is None:
                    value = None
                    note = "bit=?"
                elif info.mnem == "btst":
                    value = prev
                    note = "bit=%d" % bit
                elif info.mnem == "bset":
                    value = prev | (1 << bit)
                    note = "bit=%d" % bit
                else:
                    value = prev & ~(1 << bit) & 0xFF
                    note = "bit=%d" % bit
                accesses.append((info.kind, addr, 1, value & 0xFF if value is not None else None, note))
            else:
                mask = (1 << (8 * size)) - 1
                idx = reg_index(info.value_reg) if info.value_reg else None
                value = None
                if not incomplete and idx is not None:
                    value = (before[idx] if info.kind == "W" else after[idx]) & mask
                accesses.append((info.kind, addr, size, value, ""))

        for kind, addr, size, value, note in accesses:
            # 写到的若是指针字段，更新 follow 的值（只对后续指令生效）。
            # 这一步必须在命中过滤之前做：字段本身不一定在 --watch 里。
            if kind == "W":
                for f in follows:
                    if f["kind"] == "abs":
                        candidates = [f["addr"]]
                    else:
                        basev = before[f["reg"]]
                        candidates = [(basev + f["disp"]) & 0xFFFFFFFF] if basev else []
                    for fa in candidates:
                        if not (addr < fa + 4 and addr + size > fa):
                            continue
                        cur = f["values"].get(fa)
                        if cur is None:
                            cur = initial_u32(fa) or 0
                        bs = bytearray(cur.to_bytes(4, "little"))
                        for k in range(4):
                            a = fa + k
                            if addr <= a < addr + size and value is not None:
                                bs[k] = (value >> (8 * (a - addr))) & 0xFF
                        f["values"][fa] = int.from_bytes(bytes(bs), "little")

            matched = []
            for start, end, perm, label in windows:
                if kind.lower() in perm and addr < end and addr + size > start:
                    matched.append(label)
            if not matched:
                continue
            # 位操作按读改写处理；--writes-only 只报写
            if args.writes_only and kind != "W":
                continue
            rec = {"i": i, "pc": pc, "kind": kind, "addr": addr, "size": size,
                   "value": value, "note": note, "regions": matched,
                   "insn": ("%s %s" % (info.mnem, info.ops)).strip()}
            counts[kind] += 1
            for lab in matched:
                per_watch.setdefault(lab, {"R": 0, "W": 0})[kind] += 1
            bump((pc, kind), rec)
            if len(an.hits) < args.max_hits:
                an.hits.append(rec)
            else:
                an.truncated_hits = True

            # 读值对阴影做一致性检查（阴影来自镜像/poke + 已观测到的写）
            if kind == "R" and value is not None:
                for k in range(size):
                    a = addr + k
                    if not any(s <= a < e for s, e, _p, _l in windows):
                        continue
                    actual = (value >> (8 * k)) & 0xFF
                    sb = an.shadow_byte(a, initial)
                    if sb is None:
                        an.shadow[a] = actual
                    elif sb != actual:
                        an.mismatch += 1
                        if len(an.mismatch_samples) < 8:
                            an.mismatch_samples.append((pc, a, sb, actual))
                        an.shadow[a] = actual
            else:
                an.set_shadow(addr, size, value)

    an.counts = counts
    an.per_watch = per_watch
    an.agg = agg
    an.watches = watches
    an.follows = follows
    an.status = status
    an.attempted = attempted
    an.completed = result.get("completed", 0)
    an.truncated = truncated
    an.final = final
    an.fault = fault
    an.trace_len = len(trace)
    an.initial = initial
    return an


def changed_bytes(an, watch, image):
    for caddr, data in an.captures.items():
        if not (caddr <= watch["start"] and watch["end"] <= caddr + len(data)):
            continue

        def init(addr):
            return an.initial(addr)

        changed = 0
        known = True
        for k in range(watch["end"] - watch["start"]):
            v = init(watch["start"] + k)
            if v is None:
                known = False
                break
            if data[watch["start"] + k - caddr] != v:
                changed += 1
        return (changed, watch["end"] - watch["start"]) if known else None
    return None


def print_report(an, args, image, trace_limit):
    print("== watch_field ==")
    if an.status == "returned":
        print("执行: 到达 stop（%s）" % args.stop)
    else:
        print("执行: 停止原因 %s" % an.status)
    print("引擎: attempted=%d completed=%d trace=%d%s"
          % (an.attempted, an.completed, an.trace_len,
             "（已截断，只分析了前 %d 条）" % an.trace_len if an.truncated else ""))
    if an.fault:
        print("fault: kind=%s pc=%#x address=%#x size=%s"
              % (an.fault.get("kind"), an.fault.get("pc", 0),
                 an.fault.get("address", 0), an.fault.get("size", "?")))
    if args.writes_only:
        print("模式: 只报写（--writes-only）")
    print("监听:")
    for w in an.watches:
        print("  [%s] %#x..%#x (%d B, %s)"
              % (w["label"], w["start"], w["end"], w["end"] - w["start"], w["perm"]))
    for f in an.follows:
        lw = f.get("last_window")
        where = ("-> %#x..%#x" % lw) if lw else "-> 未解析出指针"
        print("  [%s] *( %s ) %s (%d B, %s)" % (f["label"], f["field_text"], where, f["len"], f["perm"]))

    total = an.counts["R"] + an.counts["W"]
    print("命中: %d（R=%d W=%d）" % (total, an.counts["R"], an.counts["W"]))
    if an.truncated_hits:
        print("注意: 明细只保留前 %d 条，后面的只计数（--max-hits 可调）" % args.max_hits)

    if an.agg:
        print("按 PC 汇总:")
        for (pc, kind), cur in sorted(an.agg.items()):
            span = "%#x" % cur["amin"]
            if cur["amax"] != cur["amin"]:
                span += "..%#x" % cur["amax"]
            print("  %08X %s %4d 次  %-18s %-6s %s"
                  % (pc, kind, cur["count"], span, ",".join(sorted(cur["labels"])),
                     cur["sample"]))

    if an.hits:
        print("明细（前 %d 条）:" % min(len(an.hits), args.detail))
        for h in an.hits[:args.detail]:
            val = "-" if h["value"] is None else "0x%0*x" % (h["size"] * 2, h["value"])
            extra = (" " + h["note"]) if h["note"] else ""
            print("  %08X %s %#010x %dB %-10s %-12s %s"
                  % (h["pc"], h["kind"], h["addr"], h["size"], val,
                     ",".join(h["regions"]), h["insn"] + extra))

    if an.follows:
        for f in an.follows:
            print("follow#%d: 指针字段解析值 %s"
                  % (f["id"], ", ".join("%#x=%#x" % (a, v) for a, v in f["values"].items()) or "无"))

    changed = []
    for w in an.watches:
        r = changed_bytes(an, w, image)
        if r is not None:
            changed.append((w["label"], r[0], r[1]))
    if changed:
        print("运行后内存（capture 对照镜像）:")
        for label, c, n in changed:
            print("  %s: %d/%d 字节变化" % (label, c, n))

    # 一致性/覆盖警告
    if an.mismatch:
        print("警告: %d 个读值与阴影不一致（可能漏识别了写或初值过期）" % an.mismatch)
        for pc, a, sb, actual in an.mismatch_samples[:5]:
            print("  %08X @%#x 阴影=%02x 实测=%02x" % (pc, a, sb, actual))
    if an.undecoded:
        print("警告: %d 条指令无法解码（%s）"
              % (an.undecoded, ", ".join("%#x" % p for p in an.undecoded_pcs)))
    if an.unhandled:
        print("警告: %d 条内存指令未归类（%s）——已列出的命中不受影响，但可能漏报"
              % (an.unhandled, ", ".join("%#x" % p for p in an.unhandled_pcs)))
    if an.truncated:
        print("警告: trace 截断，分析只覆盖前 %d 条指令；要覆盖更长区间请减小 --limit "
              "或提高 --trace-limit（上限 100000）" % an.trace_len)
    if total == 0:
        print("说明: 0 命中 ≠ 没执行。先看上面的执行状态/PC 覆盖，再判断是没执行到、"
              "还是执行到但没有访问这块内存。")
    for w in an.warnings:
        print("警告: " + w)


def main():
    ap = argparse.ArgumentParser(
        description="用 libsim 的寄存器轨迹重建内存访问（读/写），按 PC 打印命中",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pc", required=True, help="起点 PC（十六进制）")
    ap.add_argument("--stop", default="0x61E01000", help="停止 PC（执行前比较，不执行它）")
    ap.add_argument("--watch", action="append", default=[],
                    help="监听区间 ADDR:LEN[:r|w|rw]，可重复")
    ap.add_argument("--follow", action="append", default=[],
                    help="跟随指针 FIELD:LEN[:r|w|rw]，FIELD 形如 0x... 或 a3+0x1230(+0x1d)")
    ap.add_argument("--writes-only", action="store_true", help="只报写")
    ap.add_argument("--limit", type=int, default=20000, help="最多执行多少条指令（默认 20000）")
    ap.add_argument("--trace-limit", type=int, default=None,
                    help="轨迹条数（默认 min(limit,100000)；超过 100000 会被截断）")
    ap.add_argument("--max-hits", type=int, default=20000, help="最多保留多少条命中明细")
    ap.add_argument("--detail", type=int, default=40, help="终端打印多少条明细（默认 40）")
    ap.add_argument("--timeout", type=float, default=55.0, help="引擎超时秒数（默认 55）")
    ap.add_argument("--json", help="把完整结果写到这个 JSON 文件")
    ap.add_argument("--reg", action="append", default=[], help="NAME=VALUE，可重复")
    ap.add_argument("--code", action="append", default=[], help="ADDR:LEN，按镜像映射 rx")
    ap.add_argument("--rw", action="append", default=[], help="ADDR:LEN，按镜像映射 rw")
    ap.add_argument("--rwx", action="append", default=[], help="ADDR:LEN，按镜像映射 rwx")
    ap.add_argument("--rw-any", action="append", default=[], help="ADDR:LEN，映射零填充 rw")
    ap.add_argument("--poke", action="append", default=[], help="ADDR=HEXBYTES，运行前写进映射区")
    ap.add_argument("--image", default=str(IMAGE), help="解码后的 payload 镜像")
    args = ap.parse_args()

    image = Path(args.image).read_bytes()
    if args.limit <= 0:
        sys.exit("--limit 必须 > 0")
    if args.limit > 100000000:
        sys.exit("--limit 太大")
    regions, pokes = build_regions(args, image)
    result, _regs, _stop, trace_limit = run_engine(args, regions, pokes)
    an = analyze(args, image, regions, pokes, result)
    print_report(an, args, image, trace_limit)

    if args.json:
        out = Path(args.json)
        if out.parent and not out.parent.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "config": {
                "pc": int(args.pc, 0), "stop": int(args.stop, 0), "limit": args.limit,
                "trace_limit": trace_limit, "writes_only": args.writes_only,
                "watches": [{"start": w["start"], "end": w["end"], "perm": w["perm"]}
                            for w in an.watches],
                "follows": [{"field": f["field_text"], "len": f["len"], "perm": f["perm"],
                             "values": {("%#x" % k): v for k, v in f["values"].items()}}
                            for f in an.follows],
            },
            "engine": {"status": an.status, "attempted": an.attempted,
                       "completed": an.completed, "trace": an.trace_len,
                       "trace_truncated": an.truncated, "fault": an.fault},
            "counts": an.counts,
            "per_watch": an.per_watch,
            "aggregate": [
                {"pc": pc, "kind": kind, "count": cur["count"], "amin": cur["amin"],
                 "amax": cur["amax"], "regions": sorted(cur["labels"]),
                 "insn": cur["sample"]}
                for (pc, kind), cur in sorted(an.agg.items())],
            "hits": [{"i": h["i"], "pc": h["pc"], "kind": h["kind"], "addr": h["addr"],
                      "size": h["size"], "value": h["value"], "note": h["note"],
                      "regions": h["regions"], "insn": h["insn"]} for h in an.hits],
            "hits_truncated": an.truncated_hits,
            "shadow_mismatch": an.mismatch,
            "undecoded": an.undecoded,
            "unhandled": an.unhandled,
            "warnings": an.warnings,
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print("JSON: %s" % out)


if __name__ == "__main__":
    main()
