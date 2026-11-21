#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MN10300 / AM33 / AM33-2 disassembler for the Olympus E-M10 Mark II firmware.

The instruction table is *not* hand-written: it is parsed out of GDB's sim
``*.igen`` model files (as vendored by github.com/cmdada/em10-fw under
``emu/isa/``), using exactly the format grammar of that project's
``emu/gen.py``:

    pattern := <byte> [+ <byte> ...]        # one '+' word == one opcode byte
    byte    := <field> [, <field> ...]      # fields fill the 8 bits exactly
    field   := <bits> "." <0xCONST | NAME [ ("="|"!") NAME ]>

So the instruction length is simply ``len(pattern.split("+"))`` -- that is the
part that matters most for synchronised disassembly, and it comes straight
from the ISA description instead of from guesswork.

Operand text comes from the canonical syntax comment that precedes every
format line in the .igen files, e.g.

    // 1111 0111 0000 0000 Rm1 Rn1 Rm2 Rn2; add_add Rm1, Rn1, Rm2, Rn2
    8.0xf7+8.0x00+4.RM1,4.RN1+4.RM2,4.RN2!RN1:D2:::add_add

The template after the ';' is rendered by substituting the bit fields
(Rm1 -> e4, imm8 -> 0x1c, d16 -> branch target, regs -> [d2,a0], ...).

Usage:
    python tools/mn10300_dis.py FILE [--base 0x60021020] [--off 0] [--len 4096]
    python tools/mn10300_dis.py FILE --stats    [--off N] [--len N]
    python tools/mn10300_dis.py FILE --sync     [--off N] [--len N]
    python tools/mn10300_dis.py --selftest
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REFS = os.path.join(ROOT, "refs")

# ---------------------------------------------------------------------------
# ISA table
# ---------------------------------------------------------------------------

IGEN_ENTRY_RE = re.compile(r"^[0-9]")
IGEN_INCLUDE_RE = re.compile(r":include::([A-Za-z0-9_\-]+):([A-Za-z0-9_.\-]+)")
MODEL = "am33_2"


def find_igen(name):
    """Locate refs/<...>_<name> (fetched copies are prefixed by repo path)."""
    direct = os.path.join(REFS, name)
    if os.path.exists(direct):
        return direct
    if os.path.isdir(REFS):
        for fn in sorted(os.listdir(REFS)):
            if fn.endswith(name) and fn.endswith(".igen"):
                return os.path.join(REFS, fn)
    return None


def load_igen_text():
    """mn10300.igen with its includes expanded, exactly like emu/gen.py."""
    root = find_igen("mn10300.igen")
    if not root:
        raise SystemExit("cannot find mn10300.igen under %s" % REFS)
    text = open(root, encoding="utf-8", errors="replace").read()

    def sub(m):
        f = find_igen(m.group(2))
        if f is None:
            return ""            # e.g. the am33-2 include, pulled in below
        return open(f, encoding="utf-8", errors="replace").read()

    text = IGEN_INCLUDE_RE.sub(sub, text)
    return text.split("\n")


def parse_pattern(pat):
    """Port of emu/gen.py:parse_pattern -> (nbytes, mask[], val[], fields, cons)."""
    words = pat.split("+")
    mask, val, fields, cons = [], [], {}, []
    for bi, w in enumerate(words):
        m = v = 0
        pos = 8
        for f in w.split(","):
            if "." in f:
                width, rest = f.split(".", 1)
                width = int(width)
            elif re.fullmatch(r"[01]+", f):
                width, rest = len(f), "0b" + f
            else:
                width, rest = pos, f
            pos -= width
            if rest == "0x":                     # igen writes a nibble as "4.0x"
                rest = "0"
            cmp_ = re.match(r"([A-Za-z_][A-Za-z0-9_]*)([=!])([A-Za-z_][A-Za-z0-9_]*)$", rest)
            if cmp_:
                rest = cmp_.group(1)
                cons.append((cmp_.group(1), cmp_.group(2), cmp_.group(3)))
            if re.match(r"^(0x[0-9a-fA-F]+|0b[01]+|[0-9]+)$", rest):
                c = int(rest, 0) if not re.fullmatch(r"0[0-9]+", rest) else int(rest, 10)
                fm = ((1 << width) - 1) << pos
                m |= fm
                v |= (c << pos) & fm
            else:
                if rest in fields:               # keep first definition
                    raise ValueError("dup field %s in %s" % (rest, pat))
                fields[rest] = (bi, pos, width)
        if pos != 0:
            raise ValueError("bad byte %r in %s" % (w, pat))
        mask.append(m)
        val.append(v)
    return len(words), mask, val, fields, cons


class Rule(object):
    __slots__ = ("nb", "mask", "val", "fields", "cons", "mnem", "syntax",
                 "fixed", "tag", "line")

    def __init__(self, pat, tag, mnem, syntax, line):
        self.nb, self.mask, self.val, self.fields, self.cons = parse_pattern(pat)
        self.mnem = mnem
        self.syntax = syntax
        self.tag = tag
        self.line = line
        # specificity: number of fixed opcode bits, then constraint count
        self.fixed = sum(bin(x).count("1") for x in self.mask) + 8 * len(self.cons)

    def __repr__(self):
        return "<Rule %s %db %s>" % (self.mnem, self.nb, self.syntax)


PROSE_RE = re.compile(r"\([^()]*(\bis\b|!=|see above|\.)[^()]*\)")


def _strip_prose(tpl):
    """Drop the explanatory notes igen appends after the real operand list.

    e.g. "cmp imm8,Dn (imm8 is sign-extended.)" -> "cmp imm8,Dn"
         "jmp (d16,PC) (d16 is sign-extended.)"  -> "jmp (d16,PC)"
    """
    m = PROSE_RE.search(tpl)
    if m:
        tpl = tpl[:m.start()].rstrip()
    return tpl


#: Some igen entries carry no canonical syntax comment (the comment above them
#: is prose-only or missing).  Those are hand-filled here, using the operand
#: order of the .igen body and GDB's own disassembler wording.  Keyed by the
#: igen pattern (the text before the first ':').
SYNTAX_OVERRIDES = {
    "8.0xcd+8.D16A+8.D16B+8.REGS+8.IMM8":
        "call (d16,PC),regs,imm8",
    "8.0xdd+8.D32A+8.D32B+8.D32C+8.D32D+8.REGS+8.IMM8":
        "call (d32,PC),regs,imm8",
    "8.0xfa+4.0xf,10,2.AN0+8.D8+8.IMM8":
        "btst imm8,(d8,An)",
    "8.0xfa+4.0xf,00,2.AN0+8.D8+8.IMM8":
        "bset imm8,(d8,An)",
    "8.0xfa+4.0xf,01,2.AN0+8.D8+8.IMM8":
        "bclr imm8,(d8,An)",
    "8.0xfe+8.0x00+8.IMM32A+8.IMM32B+8.IMM32C+8.IMM32D+8.IMM8":
        "bset imm8,(imm32)",
    "8.0xfe+8.0x01+8.IMM32A+8.IMM32B+8.IMM32C+8.IMM32D+8.IMM8":
        "bclr imm8,(imm32)",
}


def _syntax_from_comment(lines, i):
    """The comment immediately above a format line carries the asm syntax."""
    for j in range(i - 1, max(-1, i - 6), -1):
        c = lines[j].strip()
        if c.startswith("//"):
            if ";" in c:
                return _strip_prose(c.split(";", 1)[1].strip())
            return None
        if c:
            return None
    return None


def load_rules(verbose=False):
    lines = load_igen_text()
    rules, seen, skipped, nosyntax = [], set(), 0, 0
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not IGEN_ENTRY_RE.match(ln):
            i += 1
            continue
        parts = ln.split(":")
        if len(parts) < 5:
            i += 1
            continue
        pat, tag, mnem = parts[0], parts[1], parts[4].strip()
        syntax = _syntax_from_comment(lines, i)
        if syntax is None:
            syntax = SYNTAX_OVERRIDES.get(pat)
        if syntax is None and verbose:
            print("no syntax: %s" % ln[:70], file=sys.stderr)
        i += 1
        models = []
        while i < len(lines) and not lines[i].startswith("{"):
            s = lines[i].strip()
            if s.startswith("*"):
                models.append(s[1:])
            i += 1
        # skip body
        depth = 0
        while i < len(lines):
            depth += lines[i].count("{") - lines[i].count("}")
            i += 1
            if depth <= 0 and lines[i - 1].startswith("}"):
                break
        if models and MODEL not in models:
            skipped += 1
            continue
        if pat in seen:
            continue
        seen.add(pat)
        try:
            r = Rule(pat, tag, mnem, syntax, ln)
        except Exception as exc:                 # noqa: BLE001
            if verbose:
                print("parse fail: %s (%s)" % (ln[:60], exc), file=sys.stderr)
            continue
        if not syntax:
            nosyntax += 1
            if verbose:
                print("no syntax: %s" % ln[:70], file=sys.stderr)
        rules.append(r)
    if verbose:
        print("%d rules (%d entries skipped by model, %d without syntax)"
              % (len(rules), skipped, nosyntax), file=sys.stderr)
    return rules


# ---------------------------------------------------------------------------
# Operand rendering
# ---------------------------------------------------------------------------

BRANCH8 = set("""bra beq bne bgt bge blt ble bhi bls bcc bcs bnc bns bvc bvs
lcc lcs leq lge lgt lhi lle lls llt lne lra""".split())

#: fields that are PC-relative displacements, and how they are combined
DISP_GROUPS = {
    "D8": ("D8",),
    "D16": ("D16A", "D16B"),
    "D24": ("D24A", "D24B", "D24C"),
    "D32": ("D32A", "D32B", "D32C", "D32D"),
    "IMM16": ("IMM16A", "IMM16B"),
    "IMM24": ("IMM24A", "IMM24B", "IMM24C"),
    "IMM32": ("IMM32A", "IMM32B", "IMM32C", "IMM32D"),
}

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
TOKEN_SPLIT_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)")


def rreg(v):
    """AM33 extended register number -> name (translate_rreg)."""
    if 8 <= v <= 11:
        return "a%d" % (v - 8)
    if 12 <= v <= 15:
        return "d%d" % (v - 12)
    return "e%d" % v


def xreg(v):
    return {0: "sp", 1: "mdrq", 2: "mcrh", 3: "mcrl", 4: "mcvf"}.get(v, "x%d" % v)


REGS_ORDER = [(0x80, ("d2",)), (0x40, ("d3",)), (0x20, ("a2",)), (0x10, ("a3",)),
              (0x08, ("d0", "d1", "a0", "a1", "mdr", "lir", "lar")),
              (0x04, ("e2", "e3")), (0x02, ("e4", "e5", "e6", "e7")),
              (0x01, ("e0", "e1"))]


def regs_text(mask):
    out = []
    for bit, names in REGS_ORDER:
        if mask & bit:
            out.extend(names)
    return "[" + ",".join(out) + "]"


class Operands(object):
    """Renders an igen syntax template against concrete field values."""

    # -- call / jmp (dN,PC) target -------------------------------------
    # SEMANTICS OF THE PC-RELATIVE CALL/JMP DISPLACEMENT (settled evidence,
    # see analysis/notes_calls.md for the full experiment log):
    #
    #   target = cia + signed(dN)          (cia = address of the opcode byte)
    #
    # `call (d32,PC)` is 0xDD, 7 bytes: DD w w w w REGS IMM8  (D32A..D32D at
    # bytes 1..4, LSB first).  `call (d16,PC)` is 0xCD, 5 bytes: CD h h REGS
    # IMM8.  `jmp (d16,PC)` is 0xCC, 3 bytes: CC h h.  `jmp (d32,PC)` is 0xDC,
    # 5 bytes.  The two trailing bytes of 0xDD / 0xCD are NOT part of the
    # displacement.
    #
    # Why cia+dN and why the 7-byte reading is right:
    #  * The trailing byte of every 0xDD site in image code is a live-register
    #    mask, 100% of 686 samples in 0x400000..0x408000 and 320/320 in
    #    0x1FF000..0x204000 ({00,20,30,50,80,A0,B0,C0,D0,E0,F0}); the byte
    #    after it is a small 4-aligned frame size (0x00..0x2c, 88% of the
    #    tail-00 calls).  A displacement does not look like that, so the d32
    #    field is bytes 1..4.
    #  * With target = cia + d32, 5964/5989 (99.6%) of DD sites in
    #    0x400000..0x440000 land inside the image, 3510 (58.9%) on addresses
    #    that two/three independent decode sweeps agree are instruction starts,
    #    and the top targets repeat hundreds of times (60448E70 x487,
    #    60448DC9 x425, 60448905 x404) and decode into ordinary MN10300 code.
    #    (cia+len+d32 shifts every target by one instruction, cia-2+d32 by 2;
    #    2*d32 gives 4491 in-image, absolute/0x40000000+/0x60000000+ forms give
    #    ~0-7%; a 23-bit "short" reading (cia+first 3 bytes, spilled register
    #    byte as the 4th) gives only 37.9% against DD's 99.6%.)
    #  * CD/CC give 2580/2580 and 2949/2949 in-image under cia+d16, and the
    #    cc field for a given callee is constant across callers -> a per-
    #    function ABI attribute, i.e. real call operands.
    #  * Caveat: targets in the 0x400000.. region almost never start with a
    #    `movm` prologue (that region has only ~30 movm / 256 KB) and a
    #    different decode exists that satisfies the same "code" statistics,
    #    so the interpretation is strongly supported but not proven by a
    #    CALLER->CALLEE semantic trace.  --callsem=raw disables resolution.
    CALL_LINK = True
    _TARGET_RE = re.compile(r"\((?:[^()]*?),\s*(?:PC|pc)\s*\)")

    def is_call(self):
        """True for `call/jmp (dN,PC)` forms that carry a link target."""
        f = self.rule.fields
        return ("D16A" in f or "D32A" in f) and self.rule.mnem in ("call", "jmp")

    def target(self):
        """cia + signed displacement, 32-bit wrap."""
        f = self.rule.fields
        d = self.signed("D32") if "D32A" in f else self.signed("D16")
        return (self.addr + d) & 0xFFFFFFFF

    def patch_target(self, text, tgt):
        """Replace `(d32,PC)` etc. in the rendered text with `0xXXXXXXXX`."""
        s = self._TARGET_RE.sub("0x%08x" % tgt, text, count=1)
        if s == text:
            return ("0x%08x" % tgt) + (" " + text if text else "")
        return s

    def __init__(self, rule, data, off, addr):
        self.rule = rule
        self.raw = {}                            # field name -> int
        for name, (bi, pos, width) in rule.fields.items():
            if off + bi < len(data):
                self.raw[name] = (data[off + bi] >> pos) & ((1 << width) - 1)
            else:
                self.raw[name] = 0
        self.addr = addr

    # -- helpers ----------------------------------------------------------
    def value(self, group):
        names = DISP_GROUPS[group]
        # multi-byte VALUES (immediates and d16/d32/jmp displacements) are
        # little-endian and go through this path.  Evidence: 85.3% of
        # `mov imm32,An` immediates land in 0x60000000-0x61000000 (this
        # firmware's range) with LSB order vs 0.8% with MSB order; `2C CC 06`
        # = +0x06cc (`24 4B 10` = +0x104b); 2949/2949 `jmp (d16,PC)` targets
        # are in-image with LSB order vs 1464/2949 with MSB order.
        # NOTE: the *masked* 2-byte fields (`movbu (d16,An)`, and the 2 bytes
        # of `call (d16,PC)` / `jmp (d16,PC)`) are big-endian byte pairs in
        # this igen build -- `F0 FA` = +0x0a, `F0 80 FF` = +0x0080 -- but
        # their sign/order cannot be settled from this ROM; see
        # analysis/notes_calls.md ("掩码 D16") before trusting those targets.
        v = 0
        for i, n in enumerate(names):
            if n in self.raw:
                v |= self.raw[n] << (8 * i)
        return v

    def signed(self, group):
        v = self.value(group)
        bits = 8 * len(DISP_GROUPS[group])
        if v >> (bits - 1):
            v -= 1 << bits
        return v

    def lookup(self, token):
        """Token from the syntax comment -> field name in this rule."""
        u = token.upper()
        if u in self.rule.fields:
            return u
        if u in DISP_GROUPS and all(n in self.rule.fields for n in DISP_GROUPS[u][1:]):
            return u
        m = re.fullmatch(r"([A-Z]{1,2})([0-9]*)", u)
        if not m:
            return None
        role, digit = m.groups()
        cands = [f for f in self.rule.fields if re.fullmatch(role + r"[0-9A-Z]", f)]
        if digit:
            cands2 = [f for f in cands if f[len(role):].startswith(digit)]
            cands = cands2 or cands
        if len(cands) == 1:
            return cands[0]
        if cands:
            return sorted(cands)[0]
        return None

    # -- field value -> text ---------------------------------------------
    def field_text(self, field, branch):
        if field in DISP_GROUPS:
            if branch and field in ("D8", "D16", "D24", "D32"):
                return "0x%08x" % ((self.addr + self.signed(field)) & 0xFFFFFFFF)
            if field.startswith("D"):
                return "%d" % self.signed(field)
            return "0x%x" % self.value(field)
        v = self.raw.get(field)
        if v is None:
            return None
        width = self.rule.fields[field][2]
        head = field.rstrip("0123456789")
        if head in ("DM", "DN"):
            return "d%d" % v
        if head in ("AM", "AN"):
            return "a%d" % v
        if head in ("RM", "RN", "RI", "RD"):
            return rreg(v) if width == 4 else "d%d" % v
        if head in ("XRM", "XRN"):
            return xreg(v)
        if head == "DI":
            return "d%d" % v
        if head == "REGS":
            return regs_text(v)
        if head in ("IMM", "IMM4"):
            return "0x%x" % v
        if head == "DI":
            return "d%d" % v
        return "0x%x" % v

    def render(self):
        tpl = self.rule.syntax
        if not tpl:
            return self.fallback()
        branch = self.rule.mnem in BRANCH8
        # strip the mnemonic that starts the template
        body = tpl
        m = TOKEN_RE.match(body)
        if m and m.group(0).lower() == self.rule.mnem.lower():
            body = body[m.end():].lstrip()
        # igen writes absolute-address forms as `(abs32)` with no operand
        # token; fill the value in so `mov (abs32),d0` becomes `mov (0x..),d0`
        if "abs32" in body and "IMM32A" in self.rule.fields:
            body = body.replace("abs32", "0x%x" % self.value("IMM32"))
        # same for the 16-bit absolute forms: the address lives in IMM16A/IMM16B
        if "abs16" in body and "IMM16A" in self.rule.fields and "IMM16B" in self.rule.fields:
            v16 = self.raw.get("IMM16A", 0) | (self.raw.get("IMM16B", 0) << 8)
            body = body.replace("abs16", "0x%x" % v16)
        # d16 relative call: resolve the target so `call (d16,PC)` round-trips
        if "(d16,PC)" in body and "D16A" in self.rule.fields and "D16B" in self.rule.fields:
            d16 = self.raw.get("D16A", 0) | (self.raw.get("D16B", 0) << 8)
            if d16 >> 15:
                d16 -= 0x10000
            body = body.replace("(d16,PC)", "(0x%08x,PC)" % ((self.addr + d16) & 0xFFFFFFFF))
        out = []
        for part in TOKEN_SPLIT_RE.split(body):
            if not part:
                continue
            if TOKEN_RE.fullmatch(part):
                f = self.lookup(part)
                if f is not None:
                    t = self.field_text(f, branch)
                    if t is not None:
                        out.append(t)
                        continue
                out.append(part)
            else:
                out.append(part)
        text = "".join(out)
        if self.is_call() and self.CALL_LINK:
            # call/jmp (dN,PC): the igen template keeps the raw displacement
            # `(d32,PC)`; what the CPU stores is target = cia + dN (see the
            # evidence block above class Operands).  The target is patched into
            # the already-rendered text so the field layout is unchanged.
            return self.patch_target(text, self.target())
        return text
    def fallback(self):
        """No syntax comment: dump operands in bit order (rare)."""
        return " ".join("%s=0x%x" % (k, v) for k, v in sorted(
            self.raw.items(), key=lambda kv: self.rule.fields[kv[0]][:2]))


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

class Decoder(object):
    def __init__(self, rules=None, verbose=False):
        self.rules = rules if rules is not None else load_rules(verbose)
        # The first byte is matched as (byte & mask) == val, so rules must be
        # indexed by (mask, val) -- indexing by val alone drops e.g. `clr Dn`
        # (mask 0xf3, val 0x00) for byte 0x08.
        self.by_mask = {}
        for r in self.rules:
            m = r.mask[0]
            self.by_mask.setdefault(m, {}).setdefault(r.val[0], []).append(r)
        self.masks = sorted(self.by_mask, key=lambda m: -bin(m).count("1"))
        for d in self.by_mask.values():
            for lst in d.values():
                lst.sort(key=lambda r: -r.fixed)

    def candidates(self, b):
        out = []
        for m in self.masks:
            lst = self.by_mask[m].get(b & m)
            if lst:
                out.extend(lst)
        out.sort(key=lambda r: -r.fixed)
        return out

    def match(self, data, off, addr):
        """-> (length, mnemonic, operand text, rule) or None."""
        if off >= len(data):
            return None
        cands = self.candidates(data[off])
        if not cands:
            return None
        n = len(data)
        for r in cands:
            nb = r.nb
            if off + nb > n:
                continue
            ok = True
            for bi in range(1, nb):
                m = r.mask[bi]
                if m and (data[off + bi] & m) != r.val[bi]:
                    ok = False
                    break
            if not ok:
                continue
            if r.cons:
                op = Operands(r, data, off, addr)
                good = True
                for a, kind, b in r.cons:
                    va, vb = op.raw.get(a), op.raw.get(b)
                    if kind == "=" and va != vb:
                        good = False
                        break
                    if kind == "!" and va == vb:
                        good = False
                        break
                if not good:
                    continue
            else:
                op = Operands(r, data, off, addr)
            return nb, r.mnem, op.render(), r
        return None

    # -- listing ----------------------------------------------------------
    def disasm(self, data, off=0, length=None, base=0):
        end = len(data) if length is None else min(len(data), off + length)
        pc = base + off
        while off < end:
            res = self.match(data, off, pc)
            if res is None:
                yield (pc, 1, data[off:off + 1], None, None, None)
                off += 1
                pc += 1
                continue
            nb, mnem, ops, rule = res
            yield (pc, nb, data[off:off + nb], mnem, ops, rule)
            off += nb
            pc += nb


def format_line(addr, raw, mnem, ops):
    hexs = " ".join("%02X" % b for b in raw)
    if mnem is None:
        return "  %08X  %-24s  .byte 0x%02X" % (addr, hexs, raw[0])
    return "  %08X  %-24s  %s%s" % (addr, hexs, mnem, (" " + ops) if ops else "")


# ---------------------------------------------------------------------------
# Self-test vectors (E-M5II disassembly quoted in em10-fw tools/firmware-hints)
# ---------------------------------------------------------------------------

SELFTEST = [
    ("08", "clr", "d2"),
    ("1C", "exthu", "d0"),
    ("A0 06", "cmp", "0x6,d0"),
    ("C8 0C", "beq", None),
    ("CA 0D", "bra", None),
    ("88", "mov", "d2,d0"),
    ("60", "mov", "d0,(a0)"),
    ("24 4B 10", "mov", "0x104b,a0"),
    ("2C CC 06", "mov", "0x6cc,d0"),
    ("DF 80 08", "ret", None),
    ("FC CC 11 22 33 44", "mov", "0x44332211,d0"),
    ("FC CD 11 22 33 44", "mov", "0x44332211,d1"),
    ("FC CE 11 22 33 44", "mov", "0x44332211,d2"),
    ("FC DC 11 22 33 44", "mov", "0x44332211,a0"),
    ("DD 00 00 10 00 80 20", "call", None),
    ("CE 80", "movm", None),
    ("CB", "nop", ""),
    ("FF", "break", ""),
]

# call/jmp link tests: (bytes, cia, expected rendered target)
#   DD 00 00 10 00 80 20 : d32 = 0x00100000, regs = 0x80, imm8 = 0x20
#                          -> 0x60221020 + 0x100000        = 0x60321020
#   DD 57 07 FD FF 00 00 : real site 0x60421029, d32 = -0x2e890 -> 0x603f1780
#   CD 3F FF 00 04       : real site 0x6045C8A3, d16 = 0xFF3F = -0xc1 -> 0x6045c7e2
#                          (d16 is LSB-first: in a 0x1DFEE0..0x21FEE0 sequential decode,
#                          LSB targets land on an instruction boundary 1892/1904 = 99.4%,
#                          MSB only 675/1904 = 35.5%; jmp d16 is 513/513 vs 244/513)
#   CC 0D 00             : cia 0x2000, d16 = +0xd             -> 0x0000200d
CALL_TARGET_TESTS = [
    ("DD 00 00 10 00 80 20", 0x60221020, 0x60321020),
    ("DD 57 07 FD FF 00 00", 0x60421029, 0x603f1780),
    ("CD 3F FF 00 04", 0x6045C8A3, 0x6045c7e2),
    ("CC 0D 00", 0x2000, 0x200d),
]


def selftest(dec):
    print("%-22s %-9s %-5s %s" % ("bytes", "expect", "len", "decoded"))
    print("-" * 78)
    bad = 0
    saved = Operands.CALL_LINK
    try:
        for hexs, mnem, ops in SELFTEST:
            b = bytes(int(x, 16) for x in hexs.split())
            # every vector is also decoded in raw mode (CALL_LINK=False) so that
            # the call/jmp link rendering can never silently change the decode
            for link in (saved, False):
                Operands.CALL_LINK = link
                res = dec.match(b, 0, 0x60021020)
            if res is None:
                print("%-22s %-9s %-5s INVALID" % (hexs, mnem, "-"))
                bad += 1
                continue
            nb, m, o, _ = res
            ok = (m == mnem) and (ops is None or o == ops)
            if not ok:
                bad += 1
            print("%-22s %-9s %-5s %s%s  %s" % (
                hexs, mnem, nb, m, (" " + o) if o else "",
                "OK" if ok else ("MISMATCH ops=%r" % (ops,))))
            if nb != len(b):
                print("    length: decoded %d bytes, vector has %d" % (nb, len(b)))
                bad += 1
        Operands.CALL_LINK = saved
        # explicit check of the documented call/jmp target rule target = cia + dN
        # (in link mode the target is the leading `0xXXXXXXXX` token of the text)
        for hexs, addr, want in CALL_TARGET_TESTS:
            b = bytes(int(x, 16) for x in hexs.split())
            res = dec.match(b, 0, addr)
            got = None
            if res is not None:
                m = re.match(r"0x([0-9a-fA-F]{8})", res[2] or "")
                got = int(m.group(1), 16) if m else None
            ok = got == want
            if not ok:
                bad += 1
            print("%-22s @%08X %-9s %-5s %s  %s" % (
                hexs, addr, "target", "-", res[2] if res else "INVALID",
                "OK" if ok else ("MISMATCH want 0x%08x got %r" % (want, got))))
    finally:
        Operands.CALL_LINK = saved
    print("-" * 78)
    print("%d/%d vectors ok" % (len(SELFTEST) - bad, len(SELFTEST)))
    return bad


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def stats(dec, data, off, length, base):
    n = 0
    hist = collections.Counter()
    mn = collections.Counter()
    cons = 0
    addr = base + off
    for pc, nb, raw, mnem, ops, rule in dec.disasm(data, off, length, base):
        n += 1
        hist[nb] += 1
        if mnem is None:
            cons = 0
            mn[".byte"] += 1
        else:
            mn[mnem] += 1
            cons += 1
    total = sum(hist.values())
    print("range 0x%x..0x%x (%d bytes, %d decoded units)"
          % (off, off + length, length, total))
    print("%-6s %-10s %s" % ("len", "count", "share"))
    for L in sorted(hist):
        print("%-6d %-10d %5.2f%%" % (L, hist[L], 100.0 * hist[L] / total))
    inv = mn[".byte"]
    print("invalid bytes (could not decode): %d  (%.3f%%)"
          % (inv, 100.0 * inv / max(total, 1)))
    mean = sum(k * v for k, v in hist.items()) / total
    print("mean instruction length: %.3f bytes" % mean)
    print("\ntop mnemonics:")
    for k, v in mn.most_common(20):
        print("  %-12s %d" % (k, v))
    return hist, mn


def sync_check(dec, data, off, length, base, b8_base="cia"):
    """Do branch targets land on instruction boundaries?"""
    bstart = {}
    for pc, nb, raw, mnem, ops, rule in dec.disasm(data, off, length, base):
        bstart[pc] = nb
    total = hit = 0
    per = collections.Counter()
    for pc, nb, raw, mnem, ops, rule in dec.disasm(data, off, length, base):
        if mnem in BRANCH8 and rule is not None and "D8" in rule.fields:
            op = Operands(rule, data, pc - base, pc)
            d = op.signed("D8")
            tgt = (pc + d) & 0xFFFFFFFF
            total += 1
            per[mnem] += 1
            if tgt in bstart:
                hit += 1
    print("8-bit branches: %d, target on decoded boundary: %d (%.1f%%)"
          % (total, hit, 100.0 * hit / max(total, 1)))
    for k, v in per.most_common(12):
        print("  %-8s %d" % (k, v))
    return total, hit


def region_verdict(dec, data, off, length, base):
    """Legal-instruction density of a region, used as a discriminator."""
    ok = tot = 0
    run = 0
    best = 0
    for pc, nb, raw, mnem, ops, rule in dec.disasm(data, off, length, base):
        tot += 1
        if mnem is not None:
            ok += 1
            run += 1
            best = max(best, run)
        else:
            run = 0
    return ok, tot, best


# ---------------------------------------------------------------------------
# Region profiling: the discriminator that actually works
# ---------------------------------------------------------------------------
#
# Per-instruction legality is useless on this ISA: every one of the 256 first
# byte values starts at least one AM33-2 instruction, so a string blob decodes
# "legally" forever.  What separates code from data is the *shape* of the
# decode: code has multi-byte instructions, branches, calls, and absolute
# addresses that point back into the firmware image; strings and padding have
# none of that.

import struct                                    # noqa: E402  (kept local-ish)


def profile(dec, data, off, length, base, img_lo=None, img_hi=None):
    # `base` is the load address of data[off]; the image range is derived from
    # byte 0 so that "does this immediate point back into the firmware?" is
    # asked against the whole file, not against the region being profiled.
    file_base = base - off
    lo = file_base if img_lo is None else img_lo
    hi = (file_base + len(data)) if img_hi is None else img_hi
    tot = inv = one = 0
    tlen = 0
    br = call = immref = immtot = 0
    ascii_bytes = 0
    seen = {}
    for pc, nb, raw, mnem, ops, rule in dec.disasm(data, off, length, base):
        tot += 1
        tlen += nb
        seen[pc] = nb
        if mnem is None:
            inv += 1
            continue
        if nb == 1:
            one += 1
        if mnem in BRANCH8 and rule is not None and "D8" in rule.fields:
            br += 1
        if mnem in ("call", "calls"):
            call += 1
        if rule is not None:
            for g in ("IMM32", "IMM24", "IMM16"):
                if g in rule.fields or (g == "IMM32" and "IMM32A" in rule.fields):
                    op = Operands(rule, data, pc - base, pc)
                    if g == "IMM16" and "IMM16A" not in rule.fields:
                        continue
                    v = op.value(g)
                    immtot += 1
                    if lo <= v < hi:
                        immref += 1
                    break
    nb_region = min(len(data), off + length) - off
    ascii_bytes = sum(1 for b in data[off:off + nb_region] if 0x20 <= b < 0x7f)
    kb = nb_region / 1024.0
    br_tgt = br_hit = 0
    for pc, n in seen.items():
        pass
    return {
        "bytes": nb_region,
        "units": tot,
        "invalid_pct": 100.0 * inv / max(tot, 1),
        "mean_len": tlen / float(max(tot, 1)),
        "one_byte_pct": 100.0 * one / max(tot, 1),
        "branch_per_kb": br / max(kb, 1e-9),
        "call_per_kb": call / max(kb, 1e-9),
        "imm_ref_pct": 100.0 * immref / max(immtot, 1),
        "imm_ref_n": immtot,
        "ascii_pct": 100.0 * ascii_bytes / max(nb_region, 1),
    }


def print_profile(label, p):
    print("%-14s bytes %-8d units %-7d invalid %5.2f%%  mean len %.2f  "
          "1-byte %5.1f%%  br/KB %6.1f  call/KB %5.1f  imm->image %5.1f%% "
          "(n=%d)  ascii %5.1f%%"
          % (label, p["bytes"], p["units"], p["invalid_pct"], p["mean_len"],
             p["one_byte_pct"], p["branch_per_kb"], p["call_per_kb"],
             p["imm_ref_pct"], p["imm_ref_n"], p["ascii_pct"]))


def byte_coverage(dec):
    """How many of the 256 first-byte values start some instruction."""
    return sum(1 for b in range(256) if dec.candidates(b))


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="MN10300/AM33 disassembler")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--base", default="0x0", help="load address of byte 0")
    ap.add_argument("--off", default="0", help="start offset (int or 0xhex)")
    ap.add_argument("--len", default="4096", help="byte count")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--profile", action="store_true",
                    help="code/data discriminator over one or more regions")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--callsem", default="link", choices=("link", "raw"),
                    help="call/jmp (dN,PC): link = render target 0xXXXXXXXX "
                         "(cia + signed dN, the resolved semantics), "
                         "raw = keep the igen template with the raw displacement")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    Operands.CALL_LINK = (a.callsem == "link")

    def num(s):
        return int(s, 0)

    if a.selftest or not a.file:
        dec = Decoder(verbose=a.verbose)
        print("%d rules loaded\n" % len(dec.rules))
        return 1 if selftest(dec) else 0

    data = open(a.file, "rb").read()
    dec = Decoder(verbose=a.verbose)
    off, length, base = num(a.off), num(a.len), num(a.base)
    if a.stats:
        stats(dec, data, off, length, base)
    elif a.sync:
        sync_check(dec, data, off, length, base)
    elif a.profile:
        print("first-byte coverage: %d/256 values start an instruction\n"
              % byte_coverage(dec))
        print_profile("this region", profile(dec, data, off, length, base))
    else:
        for pc, nb, raw, mnem, ops, rule in dec.disasm(data, off, length, base):
            print(format_line(pc, raw, mnem, ops))
    return 0


if __name__ == "__main__":
    sys.exit(main())
