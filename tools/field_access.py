#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""field_access.py -- static field-access map for aN-relative structure offsets.

Scans the decoded E-M10 II payload for every memory access of the form
``(disp8,aN)`` / ``(disp16,aN)`` / (optionally) ``(disp32,aN)``, aggregates by
displacement and classifies each *writer* by where its stored value came from.

Scope / honesty notes (read docs/map/field-access.md too):

* The table aggregates by **disp value regardless of base register**.  A row
  "+0x30" means "some instruction accessed (0x30,aN)" -- it does NOT prove the
  base register held ctx.  `(disp,SP)` (the dedicated SP encoding) is excluded,
  but ``a3`` used as a general register is not, and in many functions a3 is ctx
  while in others it is a frame/scratch pointer.
* Only segments the project's segment atlas classifies as ``code`` count towards
  the table by default (the other 55% of the payload decodes "legally" forever:
  every one of the 256 first bytes starts an AM33-2 instruction, so data yields
  millions of bogus accesses).  ``--include-data`` shows the raw full-image
  numbers.  The full image IS scanned either way.
* Writer class:
    imm      the stored register was loaded with an immediate (or `clr`) in the
             same basic block, with no redefinition in between;
    copy     the stored value was loaded from memory (load -> store pair, or a
             setlb/lne hardware loop);
    indirect the source is a parameter (`a0-a2,d0-d3` at function entry), SP, or
             a pointer/CTX-derived register rather than a loaded datum;
    unknown  anything else (arithmetic on a loaded value, call return, ...).
* ``ctx`` tagging (the ``--ctx`` table) is a *lower bound*: a register is marked
  CTX when it is loaded from ``(0x0C,p)`` or ``(0x3940,p)`` (ctx = *(O+0x0C) /
  *(O+0x3940)), then propagated through register copies and ``add imm`` address
  arithmetic.  Functions that receive ctx as an argument without reloading it
  from O are NOT captured -- that is why the full aN table is the primary one.

Usage
-----
    python tools/field_access.py                      # markdown table to stdout
    python tools/field_access.py --offset 0x1230      # every site of one offset
    python tools/field_access.py --offset 0x30 --offset 0x427
    python tools/field_access.py --min 0x400 --max 0x460 --jobs 8
    python tools/field_access.py --format tsv --out .tmp/fields.tsv
    python tools/field_access.py --ctx                # only proven-ctx accesses
    python tools/field_access.py --include-data       # count non-code segments too

RAM address = 0x60021020 + payload_offset  (payload = analysis/00_0100_40300020.decoded.bin)
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import mn10300_dis as M  # noqa: E402

DEFAULT_PAYLOAD = os.path.join(ROOT, "analysis", "00_0100_40300020.decoded.bin")
BASE = 0x60021020
SEGMENTS = os.path.join(ROOT, "docs", "map", "segments.json")
FUNCS = os.path.join(ROOT, "docs", "map", "functions-index.json")

# ---------------------------------------------------------------------------
# operand parsing
# ---------------------------------------------------------------------------

#: (disp,aN) or (aN); the disp group must be a plain number, so the register
#: indexed forms `(d0,a0)` simply do not match (their offset is not static).
MEM_RE = re.compile(r"\(\s*(?:(?P<disp>-?(?:0x[0-9a-fA-F]+|\d+))\s*,\s*|)"
                    r"(?P<base>a[0-3]|sp)\s*\)")
REG_RE = re.compile(r"\b(?:d[0-3]|a[0-3]|sp)\b")
REGSET_RE = re.compile(r"\[([^\]]*)\]")

WIDTH = {"movbu": 1, "movhu": 2, "mov": 4, "bset": 1, "bclr": 1, "btst": 1}

REGS = ("d0", "d1", "d2", "d3", "a0", "a1", "a2", "a3")

#: mnemonics where the last operand is the destination (MN10300 syntax)
WRITE_LAST = set("""mov movbu movhu add addc sub subc and or xor
                    asl lsr asr rol ror mul mulu div divu mulq divq extb extbu
                    exth exthu not neg movm""".split())
WRITE_ONLY = set("clr inc inc4 dec dec4".split())
NO_WRITE = set("""cmp tst btst bset bclr bra beq bne bgt bge blt ble bhi bls
                  bcc bcs bnc bns bvc bvs setlb lcc lcs leq lge lgt lhi lle
                  lls llt lne lra call calls ret retf rets reti nop break
                  jmp""".split())
LOOP_CLOSE = set("""lcc lcs leq lge lgt lhi lle lls llt lne lra""".split())
BRANCH = set(M.BRANCH8)


def split_operands(ops):
    """Split rendered operand text at top-level commas."""
    out, depth, cur = [], 0, []
    for ch in ops:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return out


def mem_refs(ops):
    """-> list of (operand_index, disp_or_None, base) for parenthesised refs."""
    refs = []
    for oi, op in enumerate(split_operands(ops)):
        for m in MEM_RE.finditer(op):
            d = m.group("disp")
            refs.append((oi, None if d is None else int(d, 0), m.group("base")))
    return refs


def regs_in(text):
    return REG_RE.findall(text)


def written_regs(mnem, ops):
    """Registers an instruction writes (best effort, scalar forms only)."""
    if mnem in NO_WRITE or mnem is None:
        return []
    parts = split_operands(ops or "")
    if mnem == "movm":
        # movm (SP),[regs] loads; movm [regs],(SP) stores
        if parts and parts[-1].startswith("["):
            s = REGSET_RE.search(parts[-1])
            return s.group(1).replace(" ", "").split(",") if s else []
        return []
    if mnem in WRITE_ONLY:
        return [r for p in parts for r in regs_in(p)]
    if mnem in WRITE_LAST and parts:
        return regs_in(parts[-1])
    return []


# ---------------------------------------------------------------------------
# per-function register state
# ---------------------------------------------------------------------------
#
# prov[reg] = (tag, value, block)
#   ('const', v, blk)  register holds a known constant
#   ('param', None, _) register holds a function parameter / ABI input
#   ('ctx',   k, _)    register holds ctx+k (k from `add imm` arithmetic)
#   ('mem',   pc, blk) register holds a value loaded from memory at pc
#   ('addr',  None, _) pointer arithmetic result, not ctx
#   ('ret',   None, _) call return value

CTX_ANCHORS = (0x0C, 0x3940)     # ctx = *(O+0x0C) / *(O+0x3940)
UNK = ("unk", None, -1)
PARAM = ("param", None, -1)


def classify_writer(prov, src, pc, block, in_loop):
    """Return (class, imm_value, detail)."""
    if src is None:
        return "unknown", None, "no scalar source"
    if src == "sp":
        return "indirect", None, "source = SP"
    tag, val, blk = prov.get(src, PARAM)
    if tag == "const":
        if blk == block:
            return "imm", val, "same-block immediate"
        return "unknown", None, "constant defined in another block"
    if tag == "mem":
        if in_loop or (blk == block and pc - val <= 24):
            return "copy", None, "load->store %s" % ("loop" if in_loop else "pair")
        return "indirect", None, "value loaded outside the pair window"
    if tag == "param":
        return "indirect", None, "source is a parameter"
    if tag == "ctx":
        return "indirect", None, "source is ctx-derived"
    if tag == "addr":
        return "indirect", None, "source is pointer arithmetic"
    if tag == "ret":
        return "unknown", None, "call return value"
    return "unknown", None, "value provenance unknown"


def update_prov(prov, mnem, ops, pc, block):
    """Apply one instruction's effect on the tracked registers (a0-a3,d0-d3)."""
    if mnem is None:
        return
    if mnem in ("call", "calls"):
        # MN10300 ABI: d0,d1,a0,a1 are caller-saved; d2,d3,a2,a3 survive.
        for r in ("d0", "d1", "a0", "a1"):
            prov[r] = ("ret", None, block) if r == "d0" else UNK
        return
    if mnem in ("ret", "retf", "rets", "reti"):
        return
    parts = split_operands(ops or "")
    written = written_regs(mnem, ops)
    if not written:
        return
    refs = {oi: (d, b) for oi, d, b in mem_refs(ops or "")}

    if mnem in ("mov", "movbu", "movhu"):
        dst = parts[-1] if parts else ""
        dstregs = regs_in(dst)
        if len(parts) < 2 or len(dstregs) != 1:
            for r in written:
                prov[r] = UNK
            return
        reg = dstregs[0]
        src = parts[0]
        if 0 in refs:                            # load
            d, b = refs[0]
            bt, bv, _ = prov.get(b, UNK) if b != "sp" else ("param", None, -1)
            prov[reg] = _load_prov(mnem, d, b, bt, pc, block)
        else:
            toks = regs_in(src)
            if toks and toks[0] == "sp":
                prov[reg] = ("param", None, block)
            elif toks:
                t, v, blk = prov.get(toks[0], PARAM)
                prov[reg] = (t, v, blk)
            else:
                try:
                    v = _num(src)
                    prov[reg] = ("const", v, block)
                except ValueError:
                    prov[reg] = UNK
        return

    if mnem == "clr":
        for r in written:
            prov[r] = ("const", 0, block)
        return

    if mnem in ("inc", "inc4", "dec", "dec4"):
        step = 1 if mnem.startswith("inc") else -1
        for r in written:
            tag, v, blk = prov.get(r, UNK)
            if tag == "const":
                prov[r] = ("const", v + step, blk)
            elif tag == "ctx":
                prov[r] = ("ctx", v + step, blk)
            else:
                prov[r] = UNK
        return

    if mnem in ("add", "addc", "sub", "subc", "and", "or", "xor"):
        dst = parts[-1] if parts else ""
        dstregs = regs_in(dst)
        if len(dstregs) != 1:
            for r in written:
                prov[r] = UNK
            return
        reg = dstregs[0]
        src = parts[0]
        a_t, a_v, a_b = prov.get(reg, UNK)
        toks = regs_in(src)
        if toks:
            b_t, b_v, _ = prov.get(toks[0], UNK)
        else:
            try:
                b_v = _num(src)
                b_t = "const"
            except ValueError:
                b_t, b_v = "unk", None
        sign = -1 if mnem in ("sub", "subc") else 1
        if mnem in ("and", "or", "xor"):
            prov[reg] = ("const", 0, block) if (a_t == "const" and b_t == "const") else UNK
            return
        if a_t == "const" and b_t == "const":
            prov[reg] = ("const", (a_v + sign * b_v) & 0xFFFFFFFF, a_b)
        elif a_t == "ctx" and b_t == "const":
            prov[reg] = ("ctx", (a_v + sign * b_v) & 0xFFFFFFFF, a_b)
        elif a_t == "const" and b_t == "ctx":
            prov[reg] = ("ctx", (b_v + sign * a_v) & 0xFFFFFFFF, a_b)
        elif a_t == "ctx" or b_t == "ctx":
            prov[reg] = ("addr", None, block)
        else:
            prov[reg] = UNK
        return

    for r in written:
        prov[r] = UNK


def _num(s):
    s = s.strip()
    return int(s, 16) & 0xFFFFFFFF if s.lower().startswith("0x") else int(s, 0)


def _load_prov(mnem, disp, base, base_tag, pc, block):
    """Provenance of a value loaded from memory."""
    if mnem == "mov" and disp is not None and base != "sp" and disp in CTX_ANCHORS:
        # ctx = *(O+0x0C) or *(O+0x3940); also covers a reload through ctx itself
        return ("ctx", 0, block)
    return ("mem", pc, block)


# ---------------------------------------------------------------------------
# scan worker
# ---------------------------------------------------------------------------

G = {}


def _init(payload, funcs, keep_offsets, forms, include_data):
    G["data"] = open(payload, "rb").read()
    G["funcs"] = funcs
    G["starts"] = [f[0] for f in funcs]
    G["keep"] = set(keep_offsets)
    G["forms"] = set(forms)
    G["include_data"] = include_data


def _new_entry():
    return {"w": 0, "r": 0, "rw": 0, "wis": [], "ris": [],
            "cls": collections.Counter(), "forms": collections.Counter(),
            "widths": collections.Counter(), "bases": collections.Counter(),
            "loops": 0, "nfun": set(), "all": [], "rall": []}


def _add_site(entry, site, write):
    key = "wis" if write else "ris"
    lst = entry[key]
    if len(lst) < 5:
        lst.append(site)
        lst.sort()
    elif site < lst[-1]:
        lst[-1] = site
        lst.sort()


def scan_range(args):
    """Scan one address range; returns (agg, agg_ctx, stats)."""
    start, end, seg_kind = args
    data = G["data"]
    base = BASE
    dec = M.Decoder()
    forms = G["forms"]
    off0 = start - base
    off1 = end - base
    agg = collections.defaultdict(_new_entry)
    agg_ctx = collections.defaultdict(_new_entry)
    stats = collections.Counter()
    prov = {r: PARAM for r in REGS}
    block = 0
    loops = []                       # setlb stack
    fstarts = G["starts"]
    funcs = G["funcs"]
    pos = max(0, bisect.bisect_right(fstarts, start) - 1)
    func_start = funcs[pos][0] if pos < len(funcs) else start
    first = True

    for pc, nb, raw, mnem, ops, rule in dec.disasm(data, off0, off1 - off0, base):
        stats["insn"] += 1
        while pos + 1 < len(funcs) and pc >= funcs[pos + 1][0]:
            pos += 1
            func_start = funcs[pos][0]
            prov = {r: PARAM for r in REGS}
            block += 1
            loops.clear()
        if first:
            func_start = funcs[pos][0] if pos < len(funcs) else pc
            prov = {r: PARAM for r in REGS}
            first = False

        if mnem is None:
            stats["invalid"] += 1
            prov = {r: PARAM for r in REGS}
            block += 1
            continue

        if mnem == "setlb":
            loops.append(pc)
        refs = mem_refs(ops or "")
        if refs:
            for oi, d, b in refs:
                stats["memref"] += 1
                if d is None:
                    stats["ind"] += 1
                    continue
                form = "d8" if "D8" in rule.fields else (
                    "d16" if "D16A" in rule.fields else
                    ("d32" if "IMM32A" in rule.fields else "?"))
                if form not in forms:
                    stats["form_skip"] += 1
                    continue
                stats["hit"] += 1
                parts = split_operands(ops)
                rw = "w" if oi == len(parts) - 1 else "r"
                if mnem in ("bset", "bclr"):
                    rw = "rw"
                elif mnem == "btst":
                    rw = "r"
                src = parts[0] if parts and rw != "r" else None
                toks = regs_in(src) if src else []
                srcreg = toks[0] if toks else None
                cls, imm, detail = ("", None, "")
                if rw in ("w", "rw"):
                    cls, imm, detail = classify_writer(prov, srcreg, pc, block, bool(loops))
                site = (pc, d, b, WIDTH.get(mnem, 0), mnem,
                        " ".join("%02X" % x for x in raw), func_start,
                        cls, imm, bool(loops), seg_kind, (ops or "").strip())

                # ctx-tagged effective offset
                if b != "sp":
                    bt, bv, _ = prov.get(b, UNK)
                    eff = (bv + d) if bt == "ctx" else None
                else:
                    eff = None

                if seg_kind != "code" and not G["include_data"]:
                    continue
                if eff is not None:
                    e = agg_ctx[eff]
                    if rw == "w":
                        e["w"] += 1
                    if rw in ("r", "rw"):
                        e["r"] += 1
                    if rw == "rw":
                        e["rw"] += 1
                    _add_site(e, site, rw != "r")
                    e["bases"][b] += 1
                    e["widths"][WIDTH.get(mnem, 0)] += 1
                    e["forms"][form] += 1
                    e["nfun"].add(func_start)
                    if cls:
                        e["cls"][cls] += 1
                    if loops:
                        e["loops"] += 1

                e = agg[d]
                if rw == "w":
                    e["w"] += 1
                if rw in ("r", "rw"):
                    e["r"] += 1
                if rw == "rw":
                    e["rw"] += 1
                _add_site(e, site, rw != "r")
                e["bases"][b] += 1
                e["widths"][WIDTH.get(mnem, 0)] += 1
                e["forms"][form] += 1
                e["nfun"].add(func_start)
                if cls:
                    e["cls"][cls] += 1
                if loops:
                    e["loops"] += 1
                if d in G["keep"] or (eff is not None and eff in G["keep"]):
                    (e["all"] if rw != "r" else e["rall"]).append(site)

        if mnem in LOOP_CLOSE and loops:
            loops.pop()
        if mnem in BRANCH or mnem in ("call", "calls", "ret", "retf", "rets",
                                      "reti", "jmp"):
            block += 1
        update_prov(prov, mnem, ops or "", pc, block)

    return dict(agg), dict(agg_ctx), dict(stats)


def merge(dst, src):
    for k, e in src.items():
        d = dst.get(k)
        if d is None:
            dst[k] = e
            continue
        for key in ("w", "r", "rw", "loops"):
            d[key] += e[key]
        for key in ("wis", "ris"):
            lst = {}
            for x in d[key] + e[key]:
                lst[x[0]] = x
            d[key] = [lst[k2] for k2 in sorted(lst)][:5]
        for key in ("cls", "forms", "widths", "bases"):
            d[key].update(e[key])
        d["nfun"] |= e["nfun"]
        for key in ("all", "rall"):
            if e.get(key):
                d.setdefault(key, []).extend(e[key])
    return dst


# ---------------------------------------------------------------------------
# loading helpers
# ---------------------------------------------------------------------------

def load_functions(path):
    d = json.load(open(path, encoding="utf-8"))
    rows = []
    for r in d["rows"]:
        s = int(r[0], 16) if isinstance(r[0], str) else int(r[0])
        rows.append((s, int(r[1])))
    return rows


def function_ranges(funcs):
    """Merge adjacent function rows into contiguous code ranges."""
    out = []
    cur = None
    for s, n in funcs:
        e = s + n
        if cur and s <= cur[1]:
            cur[1] = max(cur[1], e)
        else:
            if cur:
                out.append(tuple(cur))
            cur = [s, e]
    if cur:
        out.append(tuple(cur))
    return out


def load_segments(path):
    if not os.path.exists(path):
        return []
    d = json.load(open(path, encoding="utf-8"))
    out = []
    for s in d.get("segments", []):
        out.append((int(s["start"], 16), int(s["end"], 16), s["kind"]))
    return out


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

CLS_ABBR = {"imm": "i", "copy": "c", "indirect": "n", "unknown": "u", "": "-"}


def off_label(d):
    return ("+0x%X" % d) if d >= 0 else ("-0x%X" % -d)


def fmt_site(s):
    """compact site cell: address + class letter (+ imm value)."""
    pc, d, b, w, mnem, raw, fs, cls, imm, lp, kind = s[:11]
    tag = CLS_ABBR.get(cls, "?")
    if cls == "imm" and imm is not None:
        return "0x%08X·i=0x%X" % (pc, imm)
    return "0x%08X·%s" % (pc, tag)


def fmt_sites(lst):
    return " ".join(fmt_site(s) for s in lst)


def score(entry):
    """suspicion score: immediate writers dominate, total writers next."""
    imm = entry["cls"].get("imm", 0)
    return imm * 3 + entry["w"]


def rows_for(agg, lo, hi):
    rows = [(d, e) for d, e in agg.items() if lo <= d <= hi]
    rows.sort(key=lambda kv: (-kv[1]["w"], -kv[1]["r"], kv[0]))
    return rows


def render_md(all_agg, ctx_agg, stats, args, t0):
    lo, hi = args.min, args.max
    rows = rows_for(all_agg, lo, hi)
    out = []
    out.append("# ctx 字段访问图（自动生成）\n")
    out.append("载荷 `analysis/00_0100_40300020.decoded.bin`，"
               "`RAM = 0x60021020 + payload_offset`；范围 `0x%X..0x%X`。\n" % (lo, hi))
    out.append("扫描：%d 条指令（%d 条无法解码），%d 个访存操作数，"
               "其中 %d 个寄存器索引（无静态 disp）跳过；"
               "%d 个 offset 进入表内。\n"
               % (stats.get("insn", 0), stats.get("invalid", 0),
                  stats.get("memref", 0), stats.get("ind", 0), len(rows)))
    imm_rows = [(d, e) for d, e in rows if e["cls"].get("imm", 0) > 0]
    imm_rows.sort(key=lambda kv: (-score(kv[1]), -kv[1]["w"], kv[0]))
    out.append("\n## 最可疑的字段（imm 写者优先，score = 3×imm + 写者数）\n")
    out.append("| offset | score | 写 | 读 | imm | copy | indirect | unknown | 前 5 写者 |\n")
    out.append("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :-- |\n")
    for d, e in imm_rows[:args.top]:
        c = e["cls"]
        out.append("| %s | %d | %d | %d | %d | %d | %d | %d | %s |\n" % (
            off_label(d), score(e), e["w"], e["r"], c.get("imm", 0), c.get("copy", 0),
            c.get("indirect", 0), c.get("unknown", 0), fmt_sites(e["wis"])))
    out.append("\n## 全表（按写者数降序）\n")
    out.append("| offset | 写者数 | 读者数 | 宽度 | 基址分布 | 写者类型 | 前 5 写者 | 前 5 读者 |\n")
    out.append("| ---: | ---: | ---: | :-- | :-- | :-- | :-- | :-- |\n")
    for d, e in rows:
        c = e["cls"]
        cls = "i%d c%d n%d u%d" % (c.get("imm", 0), c.get("copy", 0),
                                   c.get("indirect", 0), c.get("unknown", 0))
        widths = "/".join(str(w) for w in sorted(e["widths"]) if w)
        bases = " ".join("%s:%d" % (k, v) for k, v in e["bases"].most_common(4))
        out.append("| %s | %d | %d | %s | %s | %s | %s | %s |\n" % (
            off_label(d), e["w"], e["r"], widths, bases, cls,
            fmt_sites(e["wis"]), fmt_sites(e["ris"])))
    return "".join(out)


def render_tsv(all_agg, ctx_agg, stats, args, t0):
    rows = rows_for(all_agg, args.min, args.max)
    out = ["offset\twriters\treaders\trw\twidths\timm\tcopy\tindirect\tunknown\tloops\tw_sites\tr_sites"]
    for d, e in rows:
        c = e["cls"]
        out.append("0x%X\t%d\t%d\t%d\t%s\t%d\t%d\t%d\t%d\t%d\t%s\t%s" % (
            d, e["w"], e["r"], e["rw"],
            "/".join(str(w) for w in sorted(e["widths"]) if w),
            c.get("imm", 0), c.get("copy", 0), c.get("indirect", 0),
            c.get("unknown", 0), e["loops"],
            ";".join("%s|%s|%s|%s" % (fmt_site(s), s[3], s[2], s[6]) for s in e["wis"]),
            ";".join(fmt_site(s) for s in e["ris"])))
    # TSV is meant for machine use; keep it ASCII so Windows shells and
    # GBK terminals cannot mangle the separator.
    return "\n".join(out).replace("\u00b7", ":") + "\n"


def render_json(all_agg, ctx_agg, stats, args, t0):
    names = ("pc", "disp", "base", "width", "mnem", "bytes", "func", "cls",
             "imm", "loop", "seg", "text")

    def ser(agg):
        out = {}
        for d, e in agg.items():
            out[off_label(d)] = {
                "writers": e["w"], "readers": e["r"], "rw": e["rw"],
                "classes": dict(e["cls"]), "widths": dict(e["widths"]),
                "forms": dict(e["forms"]), "bases": dict(e["bases"]),
                "loops": e["loops"],
                "writer_sites": [dict(zip(names, s)) for s in e["wis"]],
                "reader_sites": [dict(zip(names, s)) for s in e["ris"]],
                "all_writers": [dict(zip(names, s)) for s in sorted(e.get("all") or [])],
                "all_readers": [dict(zip(names, s)) for s in sorted(e.get("rall") or [])],
            }
        return out
    return json.dumps({
        "payload": os.path.relpath(args.payload, ROOT).replace("\\", "/"),
        "base": "0x%X" % BASE, "min": "0x%X" % args.min, "max": "0x%X" % args.max,
        "forms": sorted(args.forms.split(",")),
        "stats": dict(stats),
        "fields": ser(all_agg),
        "ctx_fields": ser(ctx_agg),
    }, ensure_ascii=False, indent=1) + "\n"


def render_query(off, agg, args):
    e = agg.get(off)
    if not e:
        return "offset +0x%X: no access in the scanned code ranges/"                "forms (try --include-data or --forms d8,d16,d32).\n" % off
    out = []
    out.append("offset +0x%X  (%d writers, %d readers, %d read-modify-write)\n"
               % (off, e["w"], e["r"], e["rw"]))
    out.append("classes: %s   widths: %s   forms: %s   in-loop stores: %d\n"
               % (dict(e["cls"]), dict(e["widths"]), dict(e["forms"]), e["loops"]))
    out.append("base registers: %s\n" % dict(e["bases"]))
    out.append("functions (%d): %s\n" % (len(e["nfun"]),
               ", ".join("0x%08X" % f for f in sorted(e["nfun"])[:24])))
    writers = sorted(e.get("all") or e["wis"])
    readers = sorted(e.get("rall") or e["ris"])
    out.append("\nWRITERS (%d):\n" % len(writers))
    out.append("  %-10s %-14s %-4s %-5s %-9s %-6s %-18s %-11s %s\n"
               % ("pc", "base+disp", "w", "seg", "class", "imm", "bytes",
                  "func", "text"))
    for s in writers:
        pc, d, b, w, mnem, raw, fs, cls, imm, lp, kind, txt = s
        out.append("  0x%08X %-14s %-4d %-5s %-9s %-6s %-18s 0x%08X %-7s %s%s\n"
                   % (pc, "%s+%d" % (b, d), w, kind, cls or "-",
                      ("0x%X" % imm) if imm is not None else "-", raw, fs, mnem, txt,
                      " [in-loop]" if lp else ""))
    out.append("\nREADERS (%d):\n" % len(readers))
    out.append("  %-10s %-14s %-4s %-5s %-18s %-11s %s\n"
               % ("pc", "base+disp", "w", "seg", "bytes", "func", "text"))
    for s in readers:
        pc, d, b, w, mnem, raw, fs, cls, imm, lp, kind, txt = s
        out.append("  0x%08X %-14s %-4d %-5s %-18s 0x%08X %-7s %s\n"
                   % (pc, "%s+%d" % (b, d), w, kind, raw, fs, mnem, txt))
    return "".join(out)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Aggregate aN-relative field accesses in the E-M10 II payload.")
    ap.add_argument("--payload", default=DEFAULT_PAYLOAD)
    ap.add_argument("--min", default="0", help="lowest offset (decimal or 0xhex)")
    ap.add_argument("--max", default="0x1500", help="highest offset")
    ap.add_argument("--forms", default="d8,d16",
                    help="comma list of disp forms to count (d8,d16,d32)")
    ap.add_argument("--jobs", default=0, type=int, help="worker processes (0=auto, 1=serial)")
    ap.add_argument("--format", default="md", choices=("md", "tsv", "json"))
    ap.add_argument("--out", default="-", help="output file ('-' = stdout)")
    ap.add_argument("--top", default=20, type=int, help="suspicious-field rows")
    ap.add_argument("--offset", action="append", default=[],
                    help="query one offset: print every writer/reader site")
    ap.add_argument("--ctx", action="store_true",
                    help="use the proven-ctx table (offset = effective ctx offset)")
    ap.add_argument("--include-data", action="store_true",
                    help="count accesses decoded in non-code segments too")
    ap.add_argument("--code-only", action="store_true",
                    help="deprecated alias: code segments are already the default")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    def num(s):
        return int(s, 0) if isinstance(s, str) else int(s)

    a.min, a.max = num(a.min), num(a.max)
    if not os.path.exists(a.payload):
        raise SystemExit("payload not found: %s" % a.payload)
    funcs = load_functions(FUNCS)
    ranges = function_ranges(funcs)

    tasks = [(s, e, "code") for s, e in ranges]
    if a.include_data:
        for s, e, k in load_segments(SEGMENTS):
            if k != "code":
                tasks.append((s, e, k))
        tasks.sort()

    keep = [num(x) for x in a.offset]
    t0 = time.time()
    stats = collections.Counter()
    all_agg = {}
    ctx_agg = {}
    if a.jobs == 1:
        _init(a.payload, funcs, keep, a.forms.split(","), a.include_data)
        results = [scan_range(t) for t in tasks]
    else:
        import multiprocessing as mp
        n = a.jobs or min(8, os.cpu_count() or 1)
        ctx = mp.get_context("spawn")
        with ctx.Pool(n, initializer=_init,
                      initargs=(a.payload, funcs, keep, a.forms.split(","),
                                a.include_data)) as pool:
            results = pool.map(scan_range, tasks, chunksize=1)
    for agg, cagg, st in results:
        merge(all_agg, agg)
        merge(ctx_agg, cagg)
        stats.update(st)

    use_agg = ctx_agg if a.ctx else all_agg
    if a.offset and a.format == "md":
        txt = "".join(render_query(num(o), use_agg, a) for o in a.offset)
    elif a.format == "tsv":
        txt = render_tsv(use_agg, ctx_agg, stats, a, t0)
    elif a.format == "json":
        txt = render_json(all_agg, ctx_agg, stats, a, t0)
    else:
        txt = render_md(use_agg, ctx_agg, stats, a, t0)
    if a.out == "-":
        sys.stdout.write(txt)
    else:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(txt)
        if not a.quiet:
            print("wrote %s (%d bytes, %d sites, %.1fs)"
                  % (a.out, len(txt.encode("utf-8")), stats.get("hit", 0),
                     time.time() - t0), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
