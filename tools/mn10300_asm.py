#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mn10300_asm.py -- MN10300 / AM33-2 汇编器（编码器）.

本工具不是手写的 ISA 表，而是从 ``tools/mn10300_dis.py`` 的 igen 规则表
(687 条) **反向生成** 编码器：每条规则的 opcode mask/val/字段位域都直接
取自 Rule 对象，语法模板被重新解析成「操作数槽位 -> 字段」的映射，因此
dis 认识的每一条指令在原理上都能被重新编码。

设计要点
--------
* 一条规则 = 一个 ``Form``。Form 把 syntax 模板切成顶层逗号分组，每组变成
  一个 OpSpec：寄存器 / 立即数 / 位移 / 内存操作数 / regs 位图 / 字面关键字。
* 模板里的多字节立即数与位移都是 **LSB first**（与 dis 的 Operands.value 一致）。
* 分支（BRANCH8）的位移基准是 **指令起始地址**（dis 的 ``addr + signed(D8)``），
  编码时 ``disp = target - addr``。
* 同一助记符 + 同一操作数文本可能有多个候选编码（例如 ``mov 0x10,d3`` 可以是
  2 字节 imm8 形式，也可以是 7 字节 imm32 形式）。默认选 **最短编码**，长度相同时
  选固定位更多的规则（与反汇编器的 specificity 排序一致）。
* 约束（cons，例如 ``mov imm8,Dn`` 的 DN0=DM1）在编码后复核；只被约束单向
  覆盖的字段（冗余字段）由约束自动回填。
* 模板中 ``d16``/``d32``/``abs16`` 这类 token 在 dis 侧查不到字段（规则里叫
  IMM16A/B 等），反汇编文本会丢失数值；编码器按 **同宽度** 自动换成 IMM 族
  （或反之），所以这些指令仍然能被编码，只是无法从 dis 文本往返。见报告。

CLI
---
    python tools/mn10300_asm.py --test
    python tools/mn10300_asm.py asm --addr 0x60021020 "mov 0x60021020,d0"
    python tools/mn10300_asm.py asm --file patch.s
    python tools/mn10300_asm.py --coverage

``--file`` 的行格式: ``0x60021020: mov 0x60021020,d0``（``;``/``#`` 起注释），
也支持 ``label:`` 定义与以 label 作跳转目标（两遍汇编）。
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import mn10300_dis as D  # noqa: E402

__version__ = "1.0"

# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

#: 不占位域的关键字操作数（模板里原样出现，dis 也原样输出）
KEYWORDS = {
    "sp": "SP", "pc": "PC", "psw": "PSW", "mdr": "MDR", "usp": "USP",
    "ssp": "SSP", "msp": "MSP", "epsw": "EPSW", "lir": "LIR", "lar": "LAR",
    "mdrq": "MDRQ", "mcrh": "MCRH", "mcrl": "MCRL", "mcvf": "MCVF",
}

#: AM33 扩展寄存器名 -> 4 位编号（与 dis.xreg 互逆）
XREG_VALUES = {"sp": 0, "mdrq": 1, "mcrh": 2, "mcrl": 3, "mcvf": 4}

#: regs 位图：名字 -> 位（与 dis.REGS_ORDER 互逆；同一 bit 可有多个名字）
REGS_BITS = {}
for _bit, _names in D.REGS_ORDER:
    for _n in _names:
        REGS_BITS[_n] = _bit

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
REG_RE = re.compile(r"([adexADE X])([0-9]+)")
NUM_RE = re.compile(r"^[+-]?(0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|[0-9]+)$")


class AsmError(Exception):
    """无法编码。"""


def split_top(s):
    """按顶层逗号切分，尊重 ()/[] 嵌套。"""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [p.strip() for p in out]


def num(text):
    """解析十进制 / 0x / 0b / 0o / 负数；失败返回 None。"""
    t = text.strip().replace("_", "")
    if not NUM_RE.match(t):
        return None
    try:
        return int(t, 0)
    except ValueError:
        return None


def width_of_group(group):
    return 8 * len(D.DISP_GROUPS[group])


def _norm_lit(text):
    """字面量比较：去空白、小写。'(SP)' 与 'sp' 都归一成 '(sp)' / 'sp'。"""
    return re.sub(r"\s+", "", text or "").lower()


#: 文本相同、编码不同的 PC 相对形式，按「固件里实际出现次数」定优先级。
#: key=(助记符, 位移字节数) -> 偏好值（大者优先）。数据来自前 8MB 线性扫描：
#: call 7B 113611 / call 5B 57380；calls 6B 72 / calls 4B 0；jmp 3B 22909 / jmp 5B 1520。
PCREL_PREF = {
    ("call", 4): 1, ("call", 2): 0,
    ("calls", 4): 1, ("calls", 2): 0,
    ("jmp", 2): 1, ("jmp", 4): 0,
}


# ---------------------------------------------------------------------------
# 模板 -> 操作数槽位
# ---------------------------------------------------------------------------

class Spec(object):
    """一个操作数槽位（模板中的一个顶层分组）。"""

    __slots__ = ("kind", "field", "names", "width", "repair", "literal",
                 "branch", "plus", "parts", "raw")

    def __init__(self, kind, **kw):
        self.kind = kind
        self.field = kw.get("field")
        self.names = kw.get("names") or ()
        self.width = kw.get("width") or 0
        self.repair = kw.get("repair", False)
        self.literal = kw.get("literal")
        self.branch = kw.get("branch", False)
        self.plus = kw.get("plus", False)
        self.parts = kw.get("parts") or ()
        self.raw = kw.get("raw")

    def __repr__(self):
        return "<Spec %s %s>" % (self.kind, self.raw)


def _field_head(field):
    m = re.match(r"[A-Z]+", field)
    return m.group(0) if m else field


def _is_reg_head(head):
    return head in ("DM", "DN", "AM", "AN", "RM", "RN", "RI", "RD",
                    "XRM", "XRN", "DI")


def _value_slot(rule, token):
    """模板 token -> (字段名列表, 总位数, repaired?)。

    字段名列表按 LSB -> MSB 排列（与 dis.DISP_GROUPS 的顺序一致）。
    """
    u = token.upper()
    if u in D.DISP_GROUPS:
        names = list(D.DISP_GROUPS[u])
        if all(n in rule.fields for n in names):
            return names, 8 * len(names), False
        want = 8 * len(names)
    elif u in rule.fields:
        return [u], rule.fields[u][2], False
    else:
        m = re.fullmatch(r"(?:IMM|D)(8|16|24|32)", u)
        if not m:
            return None
        want = int(m.group(1))

    # 同宽替换：模板说 d16 而规则里是 IMM16A/B（或反之）
    for cand in ("IMM%d" % want, "D%d" % want):
        if cand in rule.fields:
            return [cand], rule.fields[cand][2], True
        if cand in D.DISP_GROUPS:
            names = list(D.DISP_GROUPS[cand])
            if all(n in rule.fields for n in names):
                return names, want, True
    # 兜底：找总宽度相同的字段簇
    for cat in (D.DISP_GROUPS, rule.fields):
        if cat is rule.fields:
            for name in rule.fields:
                if rule.fields[name][2] == want:
                    return [name], want, True
        else:
            for g, names in cat.items():
                if 8 * len(names) == want and all(n in rule.fields for n in names):
                    return list(names), want, True
    return None


def _reg_field(rule, token):
    """模板 token -> 寄存器字段名（与 dis.Operands.lookup 等价，但更严格）。"""
    u = token.upper()
    if u in rule.fields and _is_reg_head(_field_head(u)):
        return u
    m = re.fullmatch(r"([A-Z]{1,2})([0-9]*)", u)
    if not m:
        return None
    role, digit = m.groups()
    cands = [f for f in rule.fields
             if _is_reg_head(_field_head(f))
             and re.fullmatch(role + r"[0-9A-Z]", f)]
    if digit:
        narrowed = [f for f in cands if f[len(role):].startswith(digit)]
        cands = narrowed or cands
    if len(cands) == 1:
        return cands[0]
    if cands:
        return sorted(cands)[0]
    return None


def _is_token_slot(rule, token):
    t = token.strip()
    if not t or not IDENT_RE.fullmatch(t):
        return False
    if t.lower() in KEYWORDS or t.lower() == "regs":
        return True
    if _value_slot(rule, t):
        return True
    if _reg_field(rule, t):
        return True
    return False


def _split_leading_prose(group):
    """'(An) (Processing unit byte)' -> ('(An)', '(Processing unit byte)')。"""
    t = group.strip()
    if not t.startswith("("):
        return t, None
    depth = 0
    for i, ch in enumerate(t):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                rest = t[i + 1:].strip()
                return t[:i + 1], (rest or None)
    return t, None


def parse_group(rule, group, branch_rule):
    """模板中的一个顶层分组 -> [Spec]（可能是多个，用于 mangled 模板）。"""
    raw = group
    t = group.strip()
    if not t:
        return []

    # --- 括号内存操作数 -------------------------------------------------
    if t.startswith("("):
        inner = t[1:-1] if t.endswith(")") else None
        if inner is None:
            return None
        parts = split_top(inner)
        # (disp,PC) / (disp,SP) / (disp,Rn) / (Rn) / (Rn+) / (Ri,Rn) / (abs)
        if len(parts) == 1:
            p = parts[0].strip()
            if p.endswith("+"):
                f = _reg_field(rule, p[:-1])
                if not f:
                    return None
                return [Spec("mem_reg", field=f, plus=True, raw=raw)]
            f = _reg_field(rule, p)
            if f:
                return [Spec("mem_reg", field=f, raw=raw)]
            if p.lower() in KEYWORDS:
                return [Spec("lit", literal=t, raw=raw)]
            slot = _mem_abs_slot(rule)
            if slot and (p.lower().startswith("abs") or p.lower() in
                         ("imm16", "imm24", "imm32") or num(p) is not None
                         or IDENT_RE.match(p)):
                names, width, rep = slot
                return [Spec("mem_abs", names=names, width=width,
                             repair=rep, raw=raw)]
            return None
        if len(parts) == 2:
            p0, p1 = parts[0].strip(), parts[1].strip()
            plus = p0.endswith("+") or p1.endswith("+")
            p0 = p0[:-1].strip() if p0.endswith("+") else p0
            p1 = p1[:-1].strip() if p1.endswith("+") else p1
            if p1.lower() == "pc":
                slot = _value_slot(rule, p0)
                if not slot:
                    return None
                names, width, rep = slot
                return [Spec("pc_paren", names=names, width=width,
                             repair=rep, branch=branch_rule, raw=raw)]
            if p1.lower() == "sp":
                slot = _value_slot(rule, p0)
                if not slot:
                    return None
                names, width, rep = slot
                return [Spec("mem_disp_lit", names=names, width=width,
                             repair=rep, literal="SP", plus=plus, raw=raw)]
            f0, f1 = _reg_field(rule, p0), _reg_field(rule, p1)
            if f0 and f1:
                # (Ri,Rn)：两个寄存器字段，模板顺序即文本顺序
                return [Spec("mem_index", field=f0, plus=False, raw=raw,
                             names=(f1,))]
            if f1:
                slot = _value_slot(rule, p0)
                if slot:
                    names, width, rep = slot
                    return [Spec("mem_disp_reg", names=names, width=width,
                                 repair=rep, field=f1, plus=plus, raw=raw)]
            return None
        return None

    # --- 裸分组 ---------------------------------------------------------
    words = t.split()
    if len(words) > 1:
        # 理论上前缀已在 Form 里剥离；这里只可能是 mangled 模板
        return None
    tok = words[0]

    if tok.lower() == "regs":
        return [Spec("regs", field=_regs_field(rule), raw=raw)]
    if tok.lower() in KEYWORDS:
        return [Spec("lit", literal=t, raw=raw)]
    f = _reg_field(rule, tok)
    if f:
        return [Spec("reg", field=f, raw=raw)]
    slot = _value_slot(rule, tok)
    if slot:
        names, width, rep = slot
        disp = tok.upper().startswith("D") and not tok.upper().startswith("DI")
        if disp and branch_rule:
            kind = "branch"
        elif disp:
            kind = "pc_disp"
        else:
            kind = "imm"
        return [Spec(kind, names=names, width=width, repair=rep, raw=raw)]

    # mangled：一个 token 里塞了两个寄存器（如 "DmAn"）
    decomposed = _decompose_regs(rule, tok)
    if decomposed:
        return [Spec("reg", field=f, raw=raw) for f in decomposed]
    return None


def _mem_abs_slot(rule):
    for cat in (("IMM32",), ("IMM24",), ("IMM16",), ("IMM8",),
                ("D32",), ("D24",), ("D16",), ("D8",)):
        g = cat[0]
        if g in D.DISP_GROUPS:
            names = list(D.DISP_GROUPS[g])
            if all(n in rule.fields for n in names):
                return names, 8 * len(names), False
        elif g in rule.fields:
            return [g], rule.fields[g][2], True
    return None


def _regs_field(rule):
    for f in rule.fields:
        if f.startswith("REGS"):
            return f
    return "REGS"


def _decompose_regs(rule, token):
    """把 "DmAn" 这类粘连 token 拆成两个寄存器字段。"""
    best = None

    def walk(rest, acc):
        nonlocal best
        if not rest:
            if best is None or len(acc) > len(best):
                best = list(acc)
            return
        for ln in (3, 2):
            if len(rest) < ln:
                continue
            cand = rest[:ln]
            f = _reg_field(rule, cand)
            if f and f not in acc:
                walk(rest[ln:], acc + [f])

    walk(token, [])
    return best if best and len(best) > 1 else None


# ---------------------------------------------------------------------------
# 寄存器编号
# ---------------------------------------------------------------------------

def reg_number(rule, field, text):
    """字段 + 文本 -> 字段值；不匹配返回 None。"""
    head = _field_head(field)
    width = rule.fields[field][2]
    t = text.strip().lower()

    if head in ("XRM", "XRN"):
        if t in XREG_VALUES:
            return XREG_VALUES[t]
        m = re.fullmatch(r"x(\d+)", t)
        return int(m.group(1)) if m else None

    if head in ("AM", "AN"):
        m = re.fullmatch(r"a(\d+)", t)
        if not m:
            return None
        return int(m.group(1))

    if head in ("DM", "DN"):
        m = re.fullmatch(r"d(\d+)", t)
        return int(m.group(1)) if m else None

    if head == "DI":
        m = re.fullmatch(r"d(\d+)", t)
        return int(m.group(1)) if m else None

    if head in ("RM", "RN", "RI", "RD"):
        if width == 4:
            m = re.fullmatch(r"([ade])(\d+)", t)
            if not m:
                return None
            fam, n = m.group(1), int(m.group(2))
            if fam == "a":
                return 8 + n if n < 4 else None
            if fam == "d":
                return 12 + n if n < 4 else None
            return n if n < 8 else None
        m = re.fullmatch(r"d(\d+)", t)
        return int(m.group(1)) if m else None
    return None


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------

class Form(object):
    """一条 igen 规则 -> 一种可编码形式。"""

    __slots__ = ("rule", "index", "mnem", "specs", "prefix", "prose",
                 "status", "note", "nb", "pcrel")

    def __init__(self, rule, index):
        self.rule = rule
        self.index = index
        self.mnem = rule.mnem.lower()
        self.nb = rule.nb
        self.specs = []
        self.prefix = ""
        self.prose = None
        self.status = "ok"
        self.note = ""
        self.pcrel = 0
        self._build()

    # -- 构建 ----------------------------------------------------------
    def _build(self):
        tpl = self.rule.syntax
        if not tpl or not tpl.strip():
            self.status = "fallback"
            self.note = "规则无 syntax 模板；只接受 FIELD=value 回退文本"
            return
        body = tpl
        m = D.TOKEN_RE.match(body)
        if m and m.group(0).lower() == self.mnem:
            body = body[m.end():]
        body = body.strip()
        if not body:
            # 纯助记符（nop / rets / ...），没有操作数
            return
        groups = split_top(body)

        # 前缀（igen 里 udfu01 / als2 / mobvu 这类拼写残留）
        g0 = groups[0]
        if not g0.startswith("(") and not g0.startswith("["):
            m2 = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s+(.+)$", g0)
            if m2 and not _is_token_slot(self.rule, m2.group(1)):
                self.prefix = m2.group(1)
                groups[0] = m2.group(2)

        for i, g in enumerate(groups):
            g_main, prose = _split_leading_prose(g)
            if prose:
                if i != len(groups) - 1:
                    self.status, self.note = "badtpl", "尾注不在末尾"
                    return
                self.prose = prose
            specs = parse_group(self.rule, g_main, self.mnem in D.BRANCH8)
            if specs is None:
                self.status = "badtpl"
                self.note = "无法解析模板分组 %r" % (g_main,)
                return
            self.specs.extend(specs)

        if any(s.repair for s in self.specs):
            self.status = "repaired"
            self.note = "模板 token 与字段名不同族（dis 文本会丢值），已按同宽修复"

        for s in self.specs:
            if s.kind in ("branch", "pc_disp", "pc_paren"):
                self.pcrel = s.width // 8
                break

    # -- 编码 ----------------------------------------------------------
    def encode(self, ops_text, addr):
        """-> bytes；失败抛 AsmError。"""
        text = (ops_text or "").strip()
        if self.status == "fallback":
            return self._encode_fallback(text)
        if self.prefix:
            m = re.match(r"(?i)^%s\b[ \t]*" % re.escape(self.prefix), text)
            if m:
                text = text[m.end():]
        if self.prose:
            p = self.prose.strip()
            if text.lower().endswith(p.lower()):
                text = text[:len(text) - len(p)].strip()
            else:
                raise AsmError("模板尾注不匹配: 期望 %r" % p)
        ops = split_top(text) if text else []
        if len(ops) != len(self.specs):
            raise AsmError("操作数个数 %d，模板要求 %d" % (len(ops), len(self.specs)))

        out = {}
        for spec, op in zip(self.specs, ops):
            self._assign(spec, op, addr, out)

        # 约束回填（如 DN0=DM1 的冗余字段）
        for _ in range(4):
            changed = False
            for a, kind, b in self.rule.cons:
                if kind != "=":
                    continue
                if a in out and b not in out:
                    out[b] = out[a]
                    changed = True
                elif b in out and a not in out:
                    out[a] = out[b]
                    changed = True
            if not changed:
                break
        missing = [f for f in self.rule.fields if f not in out]
        if missing:
            raise AsmError("字段无法由操作数确定: %s" % ",".join(sorted(missing)))
        return self._emit(out)

    # -- 空语法规则的字段级编码 ----------------------------------------
    FALLBACK_RE = re.compile(r"([A-Z][A-Z0-9]*)\s*=\s*(0[xX][0-9a-fA-F]+|\d+)")

    def _encode_fallback(self, text):
        """cmdada 表里 syntax 为空的规则：只接受 'FIELD=0x..' 回退文本。

        dis 对这类规则输出 `DM1=0x3 IMM16A=0x1c IMM16B=0x80`（field=value 列表），
        这里直接按字段赋值，因此 dis->asm 往返仍可闭合。
        """
        vals = {}
        for name, v in self.FALLBACK_RE.findall(text or ""):
            vals[name] = num(v)
        have = sorted(vals)
        want = sorted(self.rule.fields)
        if have != want:
            raise AsmError("空语法规则只接受全部字段赋值（%s），得到 %r"
                           % (",".join(want), text))
        out = {}
        for name, v in vals.items():
            if v is None:
                raise AsmError("字段 %s 的值无效" % name)
            w = self.rule.fields[name][2]
            if not (0 <= v < (1 << w)):
                raise AsmError("字段 %s 值 %d 超出 %d 位" % (name, v, w))
            out[name] = v
        return self._emit(out)

    def _emit(self, out):
        for a, kind, b in self.rule.cons:
            if a in out and b in out:
                if kind == "=" and out[a] != out[b]:
                    raise AsmError("约束 %s=%s 不满足 (%d!=%d)" % (a, b, out[a], out[b]))
                if kind == "!" and out[a] == out[b]:
                    raise AsmError("约束 %s!=%s 不满足 (%d)" % (a, b, out[a]))

        buf = bytearray(self.rule.val)
        for name, v in out.items():
            bi, pos, width = self.rule.fields[name]
            if v < 0 or v > (1 << width) - 1:
                raise AsmError("字段 %s 值 %d 超出 %d 位" % (name, v, width))
            buf[bi] |= (v & ((1 << width) - 1)) << pos
        for bi in range(self.rule.nb):
            if (buf[bi] & self.rule.mask[bi]) != self.rule.val[bi]:
                raise AsmError("内部错误：第 %d 字节与 opcode 冲突" % bi)
        return bytes(buf)

    def _assign(self, spec, op, addr, out):
        k = spec.kind
        if k == "reg":
            v = reg_number(self.rule, spec.field, op)
            if v is None:
                raise AsmError("%r 不是字段 %s 的合法寄存器" % (op, spec.field))
            width = self.rule.fields[spec.field][2]
            if v > (1 << width) - 1:
                raise AsmError("寄存器编号 %d 超出 %d 位" % (v, width))
            out[spec.field] = v
            return
        if k == "regs":
            out[spec.field] = self._regs_mask(op)
            return
        if k == "lit":
            if _norm_lit(op) != _norm_lit(spec.literal):
                raise AsmError("期望字面量 %s，得到 %r" % (spec.literal, op))
            return
        if k == "imm":
            v = num(op)
            if v is None:
                raise AsmError("立即数无效: %r" % (op,))
            self._store(spec, v, out, signed=False)
            return
        if k in ("branch", "pc_disp", "pc_paren"):
            self._assign_pc(spec, op, addr, out)
            return
        if k == "mem_abs":
            # 必须带括号，否则 "mov 0x10,d0" 会被误配到 "mov (abs16),Dn"
            inner = op.strip()
            if not (inner.startswith("(") and inner.endswith(")")):
                raise AsmError("期望绝对地址操作数 (addr)，得到 %r" % (op,))
            body = inner[1:-1].strip()
            if body.lower().startswith("abs"):
                raise AsmError("dis 文本丢了绝对地址值（%s），无法编码" % inner)
            v = num(body)
            if v is None:
                raise AsmError("绝对地址无效: %r" % (op,))
            self._store(spec, v, out, signed=False)
            return
        if k == "mem_reg":
            inner = op.strip()
            if not inner.startswith("(") or not inner.endswith(")"):
                raise AsmError("期望内存操作数，得到 %r" % (op,))
            body = inner[1:-1].strip()
            if body.endswith("+"):
                if not spec.plus:
                    raise AsmError("模板不是后自增形式")
                body = body[:-1].strip()
            elif spec.plus:
                raise AsmError("模板要求后自增 (Rn+)")
            v = reg_number(self.rule, spec.field, body)
            if v is None:
                raise AsmError("%r 不是字段 %s 的合法寄存器" % (body, spec.field))
            out[spec.field] = v
            return
        if k == "mem_index":
            inner = op.strip()
            if not inner.startswith("(") or not inner.endswith(")"):
                raise AsmError("期望内存操作数，得到 %r" % (op,))
            parts = split_top(inner[1:-1])
            if len(parts) != 2:
                raise AsmError("期望 (Ri,Rn) 形式，得到 %r" % (op,))
            fields = (spec.field, spec.names[0])
            for f, p in zip(fields, parts):
                v = reg_number(self.rule, f, p)
                if v is None:
                    raise AsmError("%r 不是字段 %s 的合法寄存器" % (p, f))
                out[f] = v
            return
        if k in ("mem_disp_reg", "mem_disp_lit"):
            inner = op.strip()
            if not inner.startswith("(") or not inner.endswith(")"):
                raise AsmError("期望内存操作数，得到 %r" % (op,))
            parts = split_top(inner[1:-1])
            if len(parts) != 2:
                raise AsmError("期望 (disp,reg) 形式，得到 %r" % (op,))
            dtext, rtext = parts[0].strip(), parts[1].strip()
            if rtext.endswith("+"):
                if not spec.plus:
                    raise AsmError("模板不是后自增形式")
                rtext = rtext[:-1].strip()
            elif spec.plus:
                raise AsmError("模板要求后自增 (disp,Rn+)")
            v = num(dtext)
            if v is None:
                raise AsmError("位移无效: %r" % (dtext,))
            self._store(spec, v, out, signed=True)
            if k == "mem_disp_lit":
                if rtext.lower() != spec.literal.lower():
                    raise AsmError("期望基址 %s，得到 %r" % (spec.literal, rtext))
            else:
                rv = reg_number(self.rule, spec.field, rtext)
                if rv is None:
                    raise AsmError("%r 不是字段 %s 的合法寄存器" % (rtext, spec.field))
                out[spec.field] = rv
            return
        raise AsmError("内部错误：未知槽位 %s" % k)

    def _assign_pc(self, spec, op, addr, out):
        t = op.strip()
        paren = t.startswith("(") and t.endswith(")")
        body = t[1:-1].strip() if paren else t
        if paren:
            parts = split_top(body)
            if len(parts) == 2 and parts[1].strip().lower() == "pc":
                body = parts[0].strip()
        v = num(body)
        if v is None:
            raise AsmError("跳转目标无效: %r" % (op,))
        signed_max = (1 << (spec.width - 1)) - 1
        if spec.kind == "branch":
            # dis 把分支渲染成 (绝对地址,PC)；也接受裸地址与负数原始位移
            if v < 0:
                disp = v
            elif paren and v <= signed_max:
                disp = v
            else:
                disp = v - addr
        elif spec.kind == "pc_paren":
            if paren:
                # jmp/calls 的 dis 文本是原始位移；超出位移范围才当绝对地址
                disp = v if -signed_max - 1 <= v <= signed_max else v - addr
            else:
                disp = v - addr
        else:  # pc_disp（裸位移 token）
            disp = v if v < 0 else (v if v <= signed_max else v - addr)
        lo = -(1 << (spec.width - 1))
        hi = (1 << (spec.width - 1)) - 1
        if not (lo <= disp <= hi):
            raise AsmError("位移 %d 超出 %d 位" % (disp, spec.width))
        self._store(spec, disp, out, signed=True)

    def _store(self, spec, value, out, signed):
        width = spec.width
        if signed:
            # 位移/分支是有符号字段：+232 不能塞进 d8（那时是 -24）
            lo, hi = -(1 << (width - 1)), (1 << (width - 1)) - 1
            if not (lo <= value <= hi):
                raise AsmError("有符号值 %d 超出 %d 位范围 [%d,%d]"
                               % (value, width, lo, hi))
        else:
            if not (0 <= value <= (1 << width) - 1):
                if signed is False and -(1 << (width - 1)) <= value < 0:
                    value &= (1 << width) - 1
                else:
                    raise AsmError("值 %d 超出 %d 位" % (value, width))
        v = value & ((1 << width) - 1)
        pos = 0
        for name in spec.names:
            w = self.rule.fields[name][2]
            out[name] = (v >> pos) & ((1 << w) - 1)
            pos += w
        if pos != width:
            raise AsmError("内部错误：字段总宽 %d != %d" % (pos, width))

    def _regs_mask(self, op):
        t = op.strip()
        if not (t.startswith("[") and t.endswith("]")):
            raise AsmError("期望寄存器位图 [..]，得到 %r" % (op,))
        inner = t[1:-1].strip()
        mask = 0
        if inner:
            for name in split_top(inner):
                n = name.strip().lower()
                if n not in REGS_BITS:
                    raise AsmError("regs 里有未知寄存器 %r" % (name,))
                mask |= REGS_BITS[n]
        return mask


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------

class Assembler(object):
    def __init__(self, rules=None, prefer="short"):
        self.rules = rules if rules is not None else D.load_rules()
        self.forms = [Form(r, i) for i, r in enumerate(self.rules)]
        self.prefer = prefer
        self.by_mnem = {}
        for f in self.forms:
            self.by_mnem.setdefault(f.mnem, []).append(f)

    def _pref(self, f):
        """同一文本多种编码时的偏好分（大者优先）。"""
        if self.prefer == "short":
            return 0
        return PCREL_PREF.get((f.mnem, f.pcrel), 0)

    @staticmethod
    def _imm24_penalty(f):
        """imm24 形式降权：厂商编译器基本不用它（8MB 固件里仅 1 次），
        且与 imm32 形式等长，优先选 imm32 才能与固件逐字节一致。"""
        return 1 if "imm24" in (f.rule.syntax or "") else 0

    # -- 单条 ----------------------------------------------------------
    def candidates(self, mnem, ops, addr, force_len=None):
        forms = self.by_mnem.get(mnem.lower())
        if not forms:
            raise AsmError("未知助记符 %r（规则表里没有）" % mnem)
        ok, errs = [], []
        for f in forms:
            if f.status in ("nosyntax", "badtpl"):
                errs.append((f, f.note or f.status))
                continue
            try:
                b = f.encode(ops, addr)
            except AsmError as exc:
                errs.append((f, str(exc)))
                continue
            if force_len is not None and len(b) != force_len:
                errs.append((f, "编码长度 %d != 指定 %d" % (len(b), force_len)))
                continue
            ok.append(((-self._pref(f), self._imm24_penalty(f), len(b),
                        -f.rule.fixed, f.index), b, f))
        if not ok:
            # 给出最有信息量的失败原因
            msg = "; ".join("%s(%db): %s" % (f.rule.syntax or "?", f.rule.nb, e)
                            for f, e in errs[:4])
            raise AsmError("%s %s 无法编码: %s" % (mnem, ops, msg))
        ok.sort(key=lambda x: x[0])
        return [(b, f) for _k, b, f in ok]

    def assemble(self, text, addr=0):
        """'mov 0x10,d0' -> (bytes, form, 候选数)。

        末尾可加 '!N' 强制使用长度为 N 的编码（用于 call/calls/jmp 的
        d16/d32 同文本歧义，例如 'calls (219,PC) !6'）。
        """
        t = text.strip()
        m = re.match(r"([A-Za-z_][A-Za-z0-9_.]*)\s*(.*)$", t, re.S)
        if not m:
            raise AsmError("无法解析: %r" % (text,))
        mnem, ops = m.group(1), m.group(2).strip()
        force = None
        m2 = re.search(r"\s*!\s*(\d+)\s*$", ops)
        if m2:
            force = int(m2.group(1))
            ops = ops[:m2.start()].strip()
        cands = self.candidates(mnem, ops, addr, force_len=force)
        n = len(cands)
        best = cands[0]
        return best[0], best[1], n

    # -- 覆盖统计 ------------------------------------------------------
    def coverage(self):
        rows = []
        for f in self.forms:
            rows.append((f.mnem, f.rule.syntax or "", f.rule.nb, f.status, f.note))
        return rows


# ---------------------------------------------------------------------------
# 多行 / 标签 / 补丁文件
# ---------------------------------------------------------------------------

LINE_ADDR_RE = re.compile(r"^\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*:\s*(.*)$")
LABEL_RE = re.compile(r"^\s*([A-Za-z_.][A-Za-z0-9_.]*)\s*:\s*(.*)$")


def parse_patch(text):
    """-> [(lineno, addr_or_None, label_or_None, instr_or_None, raw)]"""
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        raw = ln
        s = re.sub(r"[;#].*$", "", ln).strip()
        if not s:
            continue
        m = LINE_ADDR_RE.match(s)
        if m:
            out.append((i, int(m.group(1), 0), None, m.group(2).strip(), raw))
            continue
        m = LABEL_RE.match(s)
        if m and not m.group(2):
            out.append((i, None, m.group(1), None, raw))
            continue
        out.append((i, None, None, s, raw))
    # `标签:` 紧跟 `0xADDR: 指令` 时，把标签绑到那个地址上
    merged = []
    i = 0
    while i < len(out):
        cur = out[i]
        if (cur[2] is not None and i + 1 < len(out)
                and out[i + 1][1] is not None and out[i + 1][2] is None):
            nxt = out[i + 1]
            merged.append((cur[0], nxt[1], cur[2], nxt[3], cur[4]))
            i += 2
            continue
        merged.append(cur)
        i += 1
    return merged


def assemble_patch(asm, text, base=None):
    """两遍汇编。-> (listing, labels, errors)"""
    items = parse_patch(text)
    labels = {}
    addr = base
    for _pass in range(3):
        cur = base
        new_labels = {}
        listing = []
        errors = []
        for lineno, a, lab, ins, raw in items:
            if a is not None:
                cur = a
            if lab is not None:
                new_labels[lab] = cur if cur is not None else 0
            if ins is None:
                continue
            if cur is None:
                errors.append((lineno, "缺少地址（行内没有 0x..: 且未给 --addr）"))
                continue
            # 标签替换
            expr = ins
            resolved = True
            for name in sorted(labels, key=len, reverse=True):
                expr = re.sub(r"(?<![A-Za-z0-9_.])%s(?![A-Za-z0-9_.])"
                              % re.escape(name), "0x%x" % new_labels.get(name, labels[name]), expr)
            try:
                b, form, n = asm.assemble(expr, cur)
            except AsmError as exc:
                errors.append((lineno, str(exc)))
                listing.append((cur, None, ins, raw))
                continue
            listing.append((cur, b, ins, raw))
            cur += len(b)
        # 前向标签要收敛：本轮标签若与上一轮不同，即使没报错也再跑一遍，
        # 否则会用「上一轮的旧地址」编码（实测 blt 分支偏差 2 字节）。
        stable = (new_labels == labels)
        labels = new_labels
        if stable and not any(e for e in errors):
            return listing, labels, errors
    return listing, labels, errors


# ---------------------------------------------------------------------------
# 往返自测
# ---------------------------------------------------------------------------

DEFAULT_BINS = [
    ("analysis/00_0100_40300020.decoded.bin", 0x60021020),
]


def _iter_bins(args):
    if args.bin:
        yield args.bin, args.base
        return
    root = os.path.dirname(_HERE)
    for rel, base in DEFAULT_BINS:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            yield p, (args.base if args.base is not None else base)


def run_roundtrip(asm, args):
    dec = D.Decoder(asm.rules)
    rng = random.Random(args.seed)
    want = args.n
    picked = []          # reservoir
    seen = 0
    per_rule_total = {}
    for path, base in _iter_bins(args):
        with open(path, "rb") as fh:
            data = fh.read()
        length = args.length if args.length else len(data)
        off = 0
        end = min(len(data), off + length)
        for pc, nb, raw, mnem, ops, rule in dec.disasm(data, off, length, base):
            if end <= pc - base:
                break
            if rule is None or nb <= 1 or not mnem:
                continue
            seen += 1
            key = (mnem, rule.syntax or "")
            per_rule_total[key] = per_rule_total.get(key, 0) + 1
            item = (pc, bytes(raw), mnem, ops, rule)
            if len(picked) < want:
                picked.append(item)
            else:
                j = rng.randrange(seen)
                if j < want:
                    picked[j] = item
    if not picked:
        print("没有可用的样本（检查 --bin/--len）", file=sys.stderr)
        return 2

    other = "firmware" if asm.prefer == "short" else "short"
    alt = Assembler(asm.rules, prefer=other)

    fails = []
    ok = ok_alt = 0
    stat = {}          # (mnem, syntax, nb) -> [总数, 本模式通过, 对照模式通过]
    for pc, raw, mnem, ops, rule in picked:
        text = (mnem + (" " + ops if ops else "")).strip()
        key = (rule.mnem, rule.syntax or "<无模板>", rule.nb)
        st = stat.setdefault(key, [0, 0, 0])
        st[0] += 1
        try:
            b, form, n = asm.assemble(text, pc)
        except AsmError as exc:
            b = None
            fails.append((text, pc, raw, "ASM-ERR: " + str(exc), rule))
        if b is not None and b == raw:
            ok += 1
            st[1] += 1
        elif b is not None:
            fails.append((text, pc, raw, "MISMATCH: " + " ".join("%02X" % x for x in b), rule))
        try:
            b2, _f2, _n2 = alt.assemble(text, pc)
        except AsmError:
            b2 = None
        if b2 is not None and b2 == raw:
            ok_alt += 1
            st[2] += 1
    total = len(picked)
    rate = 100.0 * ok / total
    print("往返自测: %d 条样本, %d 条字节完全一致, 失败 %d 条 -> 通过率 %.2f%%"
          % (total, ok, len(fails), rate))
    print("（样本来自 %d 条长度>1 的已解码指令，seed=%d，偏好=%s）"
          % (seen, args.seed, asm.prefer))
    print("对照：改用 %s 偏好时 %.2f%%（%d/%d）"
          % (other, 100.0 * ok_alt / total, ok_alt, total))
    print()
    print("按指令族（规则模板）统计，前 30 族：")
    print("  %6s %6s %6s  %-9s %s" % ("样本", "通过", "对照", "助记符", "规则模板"))
    for key, st in sorted(stat.items(), key=lambda kv: -kv[1][0])[:30]:
        print("  %6d %6d %6d  %-9s %s" % (st[0], st[1], st[2], key[0], key[1][:40]))

    # 按规则聚合失败
    agg = {}
    for text, pc, raw, why, rule in fails:
        key = (rule.mnem, rule.syntax or "", rule.nb, why.split(":")[0])
        a = agg.setdefault(key, {"n": 0, "examples": [], "why": why})
        a["n"] += 1
        if len(a["examples"]) < 2:
            a["examples"].append((text, pc))
    print("失败聚类（按 规则/助记符 排序，共 %d 类）：" % len(agg))
    for (mnem, syn, nb, kind), a in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        print("  %4d  %-10s %-26s %db  %s" % (a["n"], mnem, syn[:26], nb, kind))
        for text, pc in a["examples"]:
            print("        e.g. %08X  %s" % (pc, text[:96]))
    if args.verbose:
        print()
        for text, pc, raw, why, rule in fails[:args.verbose]:
            print("%08X  %-52s %-26s %s" % (pc, text[:52],
                                            " ".join("%02X" % b for b in raw), why))
    return 0 if rate >= args.min_rate else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt(b):
    return " ".join("%02X" % x for x in b)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="MN10300/AM33-2 汇编器（由 mn10300_dis.py 规则表反向生成）")
    ap.add_argument("--version", action="version", version="mn10300_asm " + __version__)
    ap.add_argument("--test", action="store_true", help="跑 dis->asm 往返自测")
    ap.add_argument("--coverage", action="store_true", help="打印每条规则的编码能力")
    ap.add_argument("--n", type=int, default=2000, help="往返样本数（默认 2000）")
    ap.add_argument("--seed", type=int, default=1, help="往返采样种子")
    ap.add_argument("--bin", help="固件/解码镜像路径")
    ap.add_argument("--base", type=lambda s: int(s, 0), default=None, help="镜像加载地址")
    ap.add_argument("--length", type=lambda s: int(s, 0), default=0, help="只解码前 N 字节")
    ap.add_argument("--min-rate", type=float, default=0.0,
                    help="通过率下限（百分数 0-100，例如 90 表示 90%%）；低于它返回码 1")
    ap.add_argument("--verbose", type=int, default=0, help="打印前 N 条失败明细")
    ap.add_argument("--prefer", choices=("firmware", "short"), default="short",
                    help="同文本多编码时取最短形式（默认，往返通过率最高）还是固件常用形式")

    sub = ap.add_subparsers(dest="cmd")
    p_asm = sub.add_parser("asm", help="汇编一条或多条指令")
    p_asm.add_argument("--addr", type=lambda s: int(s, 0), default=0, help="起始地址")
    p_asm.add_argument("--file", help="补丁源文件（地址: 指令 / label:）")
    p_asm.add_argument("-o", "--bin-out", help="把结果写成二进制")
    p_asm.add_argument("--dis", action="store_true", help="反汇编校验编码结果")
    p_asm.add_argument("instr", nargs="*", help="指令文本")

    args = ap.parse_args(argv)
    asm = Assembler(prefer=args.prefer)

    if args.coverage:
        rows = asm.coverage()
        from collections import Counter
        c = Counter(r[3] for r in rows)
        print("规则总数 %d；编码能力: %s" % (len(rows), dict(c)))
        for mnem, syn, nb, status, note in rows:
            if status != "ok":
                print("  %-6s %-10s %-28s %db  %s" % (status, mnem, syn[:28], nb, note))
        return 0

    if args.test:
        return run_roundtrip(asm, args)

    if args.cmd == "asm":
        listing = []
        if args.file:
            with open(args.file, "r", encoding="utf-8") as fh:
                text = fh.read()
            listing, labels, errors = assemble_patch(asm, text, args.addr or None)
            for lineno, msg in errors:
                print("第 %d 行: %s" % (lineno, msg), file=sys.stderr)
            if labels:
                print("标签: " + "  ".join("%s=0x%X" % (k, v) for k, v in sorted(labels.items())),
                      file=sys.stderr)
        else:
            cur = args.addr
            errors = []
            for ins in args.instr:
                try:
                    b, form, n = asm.assemble(ins, cur)
                except AsmError as exc:
                    # 单条失败不拖垮整批：记账、跳过、最后返回码 1
                    errors.append((0, "%s  <- %s" % (str(exc), ins)))
                    listing.append((cur, None, ins, str(exc)))
                    continue
                listing.append((cur, b, ins, ""))
                cur += len(b)
            for _lineno, msg in errors:
                print("错误: " + msg, file=sys.stderr)
        for addr, b, ins, _raw in listing:
            if b is None:
                print("  %08X  %-40s  <失败: %s>" % (addr, ins, _raw or ""))
                continue
            extra = ""
            if args.dis:
                dec = D.Decoder(asm.rules)
                pc, nb, raw, mnem, ops, rule = next(dec.disasm(b, 0, len(b), addr))
                extra = "  ; " + mnem + ((" " + ops) if ops else "")
            print("  %08X  %-24s  %s%s" % (addr, _fmt(b), ins, extra))
        if args.bin_out and listing:
            lo = min(a for a, b, _, _ in listing)
            buf = bytearray()
            for a, b, _, _ in listing:
                if b is None:
                    continue
                pad = a - lo - len(buf)
                if pad > 0:
                    buf.extend(b"\x00" * pad)
                buf.extend(b)
            with open(args.bin_out, "wb") as fh:
                fh.write(buf)
            print("写出 %d 字节 -> %s (基址 0x%X)" % (len(buf), args.bin_out, lo))
        return 1 if errors else 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
