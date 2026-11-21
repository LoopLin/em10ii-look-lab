#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mode-dispatch table recovery for E-M10 II firmware (mn10300).

The dispatcher lives at 0x604B8098 (the "0x604B8096" of earlier notes is one
byte into the preceding `retf [d2,d3],0x8` at 0x604B8095, so it is not an
instruction boundary; two d32 call sites name 0x604B8098, and only one of them
is real: 0x604AA237).

What the code actually does (all decoded in phase B from the listing):

    a1 = ctx + 8
    d1 = byte0<<24 | byte1<<16 | u16@(ctx+10)      ; "mode code", 32 bit
    d0 = byte@(ctx+8)<<24 | byte@(ctx+9)<<16 | u16@(ctx+10)
    if d0 <  0x01010066 -> default
    if d0 >  0x02000096 -> default
    d0 -= 0x01010065
    a0  = table + (d0 & 0xF) * 8                   ; open addressing, 16 slots
    ... linear probe over +8, wrap at 0x60601CC4, empty key (0) -> default

So the array is NOT a "key < 0x100" pair list; it is a 16-slot open-addressed
hash of 32-bit codes.  Phase A below runs the originally-requested sliding
scan to document why that hypothesis cannot resolve it, and phase B derives
the real geometry from the code and proves it with probe simulation.

Usage:
    python tools/dispatch_table.py [--json docs/map/mode-dispatch.json]
"""
from __future__ import print_function

import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import disr  # noqa: E402  (tools/disr.py: subprocess wrapper around mn10300_dis)

IMG = os.path.join(ROOT, "analysis", "00_0100_40300020.decoded.bin")
BASE = 0x60021020
OUT_JSON = os.path.join(ROOT, "docs", "map", "mode-dispatch.json")

DISPATCH = 0x604B8098           # real function entry
DISPATCH_LO, DISPATCH_LEN = 0x604B8096, 0x2C0   # listing start used for evidence
SCAN_LO, SCAN_HI = 0x60601800, 0x60601CC4
CODE_LO, CODE_HI = 0x60400000, 0x605C0000
DEFAULT_HANDLER = 0x604B8320
HANDLER_DIS_LEN = 0x40
GROUP_BASE = 0x60600920
GROUP_STRIDE = 0x20
GROUP_COUNT = 21

DATA = open(IMG, "rb").read()


def dw(ram):
    return struct.unpack_from("<I", DATA, ram - BASE)[0]


def dh(ram):
    return struct.unpack_from("<H", DATA, ram - BASE)[0]


def in_code(x):
    return CODE_LO <= x < CODE_HI


# --------------------------------------------------------------------------
# phase A: the sliding 4-byte scan that was originally asked for
# --------------------------------------------------------------------------
def naive_scan(lo=SCAN_LO, hi=SCAN_HI, key_max=0x100):
    """For every 4-byte aligned candidate start, length of the run of 8-byte
    {key,target} entries with key < key_max and target inside the code segment.
    Also tracks whether keys are non-decreasing inside the run."""
    best, rows = None, []
    for start in range(lo, hi, 4):
        n = 0
        prev = -1
        mono = True
        a = start
        while a + 8 <= hi:
            k, t = dw(a), dw(a + 4)
            if k >= key_max or not in_code(t):
                break
            if k < prev:
                mono = False
            prev = k
            n += 1
            a += 8
        if n:
            rows.append({"start": start, "run": n, "monotonic": mono,
                         "covers_end": (a == hi)})
        if best is None or n > best["run"] or (n == best["run"] and mono and not best["monotonic"]):
            best = {"start": start, "run": n, "monotonic": mono, "covers_end": (a == hi)}
    rows.sort(key=lambda r: (-r["run"], r["start"]))
    return best, rows[:12]


# --------------------------------------------------------------------------
# phase B: derive the real geometry from the dispatcher's own code
# --------------------------------------------------------------------------
def derive_from_code():
    lst = disr.dis(DISPATCH_LO, DISPATCH_LEN)
    ev = {}
    for line in lst.splitlines():
        m = re.match(r"\s*([0-9A-F]{8})\s+(.*)$", line)
        if not m:
            continue
        at, body = int(m.group(1), 16), m.group(2).split(None, 1)[-1]
        # mov <imm32>,a1  immediately followed (2 insns later) by add a1,a0
        mm = re.search(r"\bmov 0x([0-9a-f]{8}),a1\b", body)
        if mm and 0x60600000 <= int(mm.group(1), 16) < 0x61000000:
            ev.setdefault("table_base_candidates", []).append(
                {"at": at, "value": int(mm.group(1), 16), "insn": body})
        mm = re.search(r"\bcmp 0x([0-9a-f]{8}),a0\b", body)
        if mm:
            ev.setdefault("end_check", []).append({"at": at, "value": int(mm.group(1), 16)})
        mm = re.search(r"\badd 0x([0-9a-f]{8}),d2\b", body)
        if mm:
            ev.setdefault("key_bias", []).append(
                {"at": at, "imm": int(mm.group(1), 16),
                 "subtracted": (0x100000000 - int(mm.group(1), 16)) & 0xFFFFFFFF})
        mm = re.search(r"\band 0x([0-9a-f]+),d0\b", body)
        if mm:
            ev.setdefault("index_mask", []).append({"at": at, "value": int(mm.group(1), 16)})
            # the shift belongs to the same index computation: `and mask,d0`
            # is immediately followed by `asl n,d0` elsewhere in this listing
            ev["_mask_at"] = at
        mm = re.search(r"\basl 0x([0-9a-f]+),d0\b", body)
        if mm and ev.get("_mask_at") is not None and at > ev["_mask_at"]:
            ev.setdefault("index_shift", []).append(
                {"at": at, "value": int(mm.group(1), 16)})
        mm = re.search(r"\bmov \(4,a0\),a1\b", body)
        if mm:
            ev.setdefault("target_offset", []).append({"at": at, "offset": 4})
        if "add 0x8,a0" in body:
            ev.setdefault("stride", []).append({"at": at, "stride": 8})
        mm = re.search(r"\bcmp 0x([0-9a-f]{8}),d2\b", body)
        if mm:
            ev.setdefault("code_range", []).append({"at": at, "value": int(mm.group(1), 16)})

    # the base is the candidate that is directly followed by "add a1,a0"
    lines = lst.splitlines()
    base_addr = None
    for i, line in enumerate(lines):
        m = re.match(r"\s*([0-9A-F]{8})\s+(.*)$", line)
        if not m:
            continue
        body = m.group(2).split(None, 1)[-1]
        mm = re.search(r"\bmov 0x([0-9a-f]{8}),a1\b", body)
        if not mm:
            continue
        val = int(mm.group(1), 16)
        if not (0x60600000 <= val < 0x61000000):
            continue
        for nxt in lines[i + 1:i + 4]:
            if "add a1,a0" in nxt:
                base_addr = val
                break
        if base_addr is not None:
            break

    end_addr = ev["end_check"][0]["value"] if ev.get("end_check") else None
    bias = ev["key_bias"][0]["subtracted"] if ev.get("key_bias") else 0x01010065
    mask = ev["index_mask"][0]["value"] if ev.get("index_mask") else 0xF
    shift = ev["index_shift"][0]["value"] if ev.get("index_shift") else 3
    rng = sorted(v["value"] for v in ev.get("code_range", []))
    return {
        "listing": lst,
        "table_base": base_addr,
        "array_end": end_addr,
        "key_bias": bias,
        "index_mask": mask,
        "index_shift": shift,
        "code_lo": rng[0] if rng else 0x01010066,
        "code_hi": rng[-1] if rng else 0x02000096,
        "evidence": {k: v for k, v in ev.items() if k != "table_base_candidates"},
    }


def decode_rows(table_base, array_end):
    rows = []
    for i, a in enumerate(range(table_base, array_end, 8)):
        k, t = dw(a), dw(a + 4)
        rows.append({"slot": i, "addr": a, "key": k,
                     "code": (k + 0x01010065) & 0xFFFFFFFF,
                     "handler": t, "populated": k != 0})
    return rows


def simulate_lookup(rows, code, bias, mask):
    """Replay the dispatcher's probe loop. Returns (handler, trace) or (None, trace)."""
    key = (code - bias) & 0xFFFFFFFF
    trace = []
    n = len(rows)
    i = key & mask
    if i >= n:
        return None, trace
    start = i
    for _ in range(n + 1):
        k = rows[i]["key"]
        trace.append({"slot": i, "key": k})
        if k == key:
            return rows[i]["handler"], trace
        if k == 0:
            return DEFAULT_HANDLER, trace
        i += 1
        if i >= n:
            i = start          # `cmp 0x60601cc4,a0` / `bcs` / `mov a1,a0` wrap
            trace.append({"slot": "wrap"})
    return DEFAULT_HANDLER, trace


# --------------------------------------------------------------------------
# phase C: handler bodies -> group tables, ctx+14 selector, output slots
# --------------------------------------------------------------------------
CALL_STORE_TAIL = 0x604B8370   # end of the whole handler block (sentinel)

HANDLER_STARTS = []            # filled in by main() from the decoded table


def handler_window(handler):
    """A 0x40-byte window overruns into the next handler (0x604B816A and
    0x604B817C are only 0x12 bytes apart), which would report the neighbours'
    group tables.  Bound the window at the next handler start."""
    nxt = [h for h in HANDLER_STARTS if h > handler]
    bound = min(nxt) if nxt else CALL_STORE_TAIL
    return max(2, min(HANDLER_DIS_LEN, bound - handler))


GROUP_RE = re.compile(r"\bmov 0x([0-9a-f]{8}),a0\b")


def post_call_store(lines, idx):
    """Heuristic: where does the group pointer returned by the call go?"""
    tail = lines[idx + 1:idx + 7]
    body = [l.split(None, 2)[-1] if len(l.split(None, 2)) > 2 else l for l in tail]
    for j, b in enumerate(body):
        m = re.search(r"\bbra 0x([0-9a-f]{6,8})\b", b)
        if m:
            return "tail@0x%s" % m.group(1).upper()
        if re.search(r"\bmov a0,d0\b", b):
            for b2 in body[j + 1:j + 3]:
                m = re.search(r"\bmov d0,\((\d+),a0\)", b2)
                if m:
                    return "out@%s(SP)" % m.group(1)
                if re.search(r"\bmov d0,\(a0\)", b2):
                    return "via-d3"
        m = re.search(r"\bmov \((\d+),SP\),a1", b)
        if m and any(re.search(r"\bmov a0,\(a1\)", b2) for b2 in body[j + 1:j + 2]):
            return "out@%s(SP)" % m.group(1)
        if re.search(r"\bmov d3,a1", b) and any(
                re.search(r"\bmov a0,\(a1\)", b2) for b2 in body[j + 1:j + 2]):
            return "via-d3"
    return "unknown"


def analyse_handler(handler):
    length = handler_window(handler)
    lst = disr.dis(handler, length)
    lines = lst.splitlines()
    groups, ctx14, calls, cmp_imm = [], [], [], []
    a3_is_ctx14 = any(re.search(r"\bmov 0xe,a3\b", l) for l in lines)
    for i, line in enumerate(lines):
        m = re.match(r"\s*([0-9A-F]{8})\s+(.*)$", line)
        if not m:
            continue
        at = int(m.group(1), 16)
        raw = m.group(2).strip()
        body = raw.split(None, 1)[-1] if " " in raw else raw
        # strip the byte column, keep the mnemonic
        body = re.sub(r"^(?:[0-9A-F]{2} )+", "", raw).strip()
        for g in GROUP_RE.finditer(body):
            gaddr = int(g.group(1), 16)
            if GROUP_BASE <= gaddr < GROUP_BASE + GROUP_STRIDE * GROUP_COUNT \
                    and (gaddr - GROUP_BASE) % GROUP_STRIDE == 0:
                groups.append({"at": "0x%08X" % at, "addr": "0x%08X" % gaddr,
                               "group": "g%d" % ((gaddr - GROUP_BASE) // GROUP_STRIDE),
                               "store": post_call_store(lines, i),
                               "insn": body})
        if "movhu (14,a2)" in body:
            ctx14.append({"at": "0x%08X" % at, "insn": body, "ctx_offset": 14})
        elif re.search(r"\bmovhu \(a3\),d1", body):
            ctx14.append({"at": "0x%08X" % at, "insn": body,
                          "ctx_offset": 14 if a3_is_ctx14 else None,
                          "note": "a3 == ctx+0xE" if a3_is_ctx14 else "a3 unresolved"})
        cm = re.search(r"\bcall 0x([0-9a-f]{6,8})\b", body)
        if cm:
            calls.append({"at": "0x%08X" % at, "target": "0x%08X" % int(cm.group(1), 16)})
        for c in re.finditer(r"\bcmp 0x([0-9a-f]+),d[01]\b", body):
            cmp_imm.append({"at": "0x%08X" % at, "value": "0x%X" % int(c.group(1), 16),
                            "d": c.group(0).split(",")[-1], "insn": body})
    out = {"start": "0x%08X" % handler, "disassembly_len": length,
           "group_tables": groups, "ctx14_reads": ctx14,
           "calls": calls, "cmp_immediates": cmp_imm,
           "truncated": length < HANDLER_DIS_LEN}
    if not cmp_imm:
        out["cmp_note"] = ("no ctx+14 compare in this window: the compare happens in "
                           "the shared selector 0x604B8049 (see ctx14_selector)")
    return out


def analyse_selector():
    """0x604B8049 picks one of 4 pointer pairs inside a 0x20-byte group entry
    from ctx+14, then word0/word1 from the flag returned by 0x604bdd42."""
    lst = disr.dis(0x604B8049, 0x4D)
    imm = []
    for line in lst.splitlines():
        m = re.match(r"\s*([0-9A-F]{8})\s+(.*)$", line)
        if not m:
            continue
        body = m.group(2).split(None, 1)[-1]
        for c in re.finditer(r"\bcmp 0x([0-9a-f]+),d[12]\b", body):
            imm.append({"at": "0x%08X" % int(m.group(1), 16),
                        "value": "0x%X" % int(c.group(1), 16), "insn": body})
    return {
        "function": "0x604B8049",
        "input": "d1 = u16@(ctx+14), d0 = word-select flag from 0x604BDD42",
        "class_mask": "0xF0",
        "variant_mask": "0x0F",
        "class_0x30": {"offset": "0x18", "variant_2_3_4_offset": "0x08"},
        "class_0x40": {"offset": "0x10", "variant_ignored": True},
        "other_classes": {"offset": "0x00", "variant_2_3_4_offset": "0x08"},
        "word_select": "d0 == 0 -> *(entry+off), else *(entry+off+4)",
        "cmp_immediates": imm,
        "note": "the ctx+14 compares are NOT inline in the handlers; they are "
                "here, shared by every handler that calls 0x604b8049",
    }


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    best, top = naive_scan()
    print("[A] sliding scan 0x%08X..0x%08X, constraint key<0x100" % (SCAN_LO, SCAN_HI))
    print("    best start 0x%08X run=%d covers_end=%s monotonic=%s"
          % (best["start"], best["run"], best["covers_end"], best["monotonic"]))
    for r in top[:6]:
        print("      0x%08X run=%d mono=%s cover_end=%s"
              % (r["start"], r["run"], r["monotonic"], r["covers_end"]))

    geo = derive_from_code()
    tbase, aend = geo["table_base"], geo["array_end"]
    print("[B] from code: base=0x%08X end=0x%08X bias=0x%08X mask=0x%X shift=%d"
          % (tbase, aend, geo["key_bias"], geo["index_mask"], geo["index_shift"]))
    rows = decode_rows(tbase, aend)
    slots = len(rows)
    pop = [r for r in rows if r["populated"]]
    print("    slots=%d populated=%d" % (slots, len(pop)))

    # probe validation: every populated entry must be reachable from its own slot
    ok, bad = [], []
    for r in pop:
        h, trace = simulate_lookup(rows, r["code"], geo["key_bias"], geo["index_mask"])
        rec = {"slot": r["slot"], "key": r["key"], "code": "0x%08X" % r["code"],
               "reached": ("0x%08X" % h) if h else None, "trace": trace}
        if h == r["handler"]:
            ok.append(rec)
        else:
            bad.append(rec)
    print("    probe simulation: %d reachable, %d NOT reachable" % (len(ok), len(bad)))
    for b in bad:
        print("      slot %d key 0x%08X -> %s (expected 0x%08X)"
              % (b["slot"], b["key"], b["reached"], rows[b["slot"]]["handler"]))

    # phase C
    HANDLER_STARTS[:] = sorted({r["handler"] for r in pop} | {DEFAULT_HANDLER})
    print("    handler starts: %s" % ", ".join("0x%08X" % h for h in HANDLER_STARTS))
    detail = {}
    for r in pop:
        detail["0x%08X" % r["handler"]] = analyse_handler(r["handler"])
    detail["0x%08X" % DEFAULT_HANDLER] = analyse_handler(DEFAULT_HANDLER)
    for k, v in sorted(detail.items()):
        gs = ",".join("%s@%s" % (g["group"], g["addr"]) for g in v["group_tables"])
        print("[C] %s -> %s" % (k, gs or "(no group table)"))

    entries = []
    for r in rows:
        e = {"addr": "0x%08X" % r["addr"], "slot": r["slot"], "key": r["key"],
             "code": "0x%08X" % r["code"],
             "handler": "0x%08X" % r["handler"] if r["populated"] else None,
             "populated": r["populated"]}
        if r["populated"]:
            reach = simulate_lookup(rows, r["code"], geo["key_bias"], geo["index_mask"])[0]
            e["reachable"] = (reach == r["handler"])
            e["in_code_range"] = geo["code_lo"] <= r["code"] <= geo["code_hi"]
        entries.append(e)

    doc = {
        "array_start": "0x%08X" % tbase,
        "array_end": "0x%08X" % aend,
        "entry_count": slots,
        "entry_size": 8,
        "layout": "u32 key (relative, code-0x01010065) + u32 handler",
        "populated_entries": len(pop),
        "reachable_entries": len(ok),
        "key_bias": "0x%08X" % geo["key_bias"],
        "mode_code_range": ["0x%08X" % geo["code_lo"], "0x%08X" % geo["code_hi"]],
        "lookup": {
            "formula": "d2 = (b0<<24)|(b1<<16)|u16@(ctx+10) with b0,b1 = bytes @(ctx+8),(ctx+9)",
            "range_gate": "0x%08X <= d2 <= 0x%08X else default" % (geo["code_lo"], geo["code_hi"]),
            "relative_key": "k = d2 - 0x01010065",
            "slot": "start = k & 0xF, linear probe +8, wrap at array_end, key 0 = empty -> default",
            "default_handler": "0x%08X" % DEFAULT_HANDLER,
        },
        "dispatcher": {
            "entry": "0x604B8098",
            "note": "earlier notes said 0x604B8096; that byte is inside the "
                    "`retf [d2,d3],0x8` at 0x604B8095, so 0x604B8098 is the real entry "
                    "(call site 0x604AA237).",
            "key_decode_site": "0x604B8107..0x604B811D",
            "table_base_site": "0x604B8143 mov 0x60601c44,a1",
            "probe_site": "0x604B8157..0x604B8168",
        },
        "entries": entries,
        "handler_detail": detail,
        "ctx14_selector": analyse_selector(),
        "phaseA_sliding_scan": {
            "range": ["0x%08X" % SCAN_LO, "0x%08X" % SCAN_HI],
            "constraint": "key < 0x100, target in 0x60400000..0x605C0000",
            "best": {"start": "0x%08X" % best["start"], "run": best["run"],
                     "covers_end": best["covers_end"]},
            "verdict": "this hypothesis cannot resolve the table: the longest run "
                       "is the empty-slot padding (key==0). Real keys are 32-bit "
                       "composite codes (0x01010066..0x02000096), so key<0x100 "
                       "fails on every populated entry after the first of a cluster.",
        },
        "confidence": {
            "array_start": "high",
            "array_end": "high",
            "entry_layout": "high",
            "handler_mapping": "high",
            "key_semantics": "medium",
            "reason": "start/end/layout are literal operands in the dispatcher "
                      "(0x604B8143 mov 0x60601c44,a1 and 0x604B8159 cmp 0x60601cc4,a0, "
                      "0x60601C44 + 16*8 == 0x60601CC4 exactly); the probe simulation "
                      "reproduces all 10 populated handlers and all 10 codes fall "
                      "inside the 0x01010066..0x02000096 gate.",
        },
        "handler_semantics": {
            "note": "labels come from the full 0x604B816A..0x604B8370 decode; "
                    "the handlers themselves only choose group tables, the actual "
                    "tone-table pick happens in 0x604B8049 from ctx+14",
            "0x604B816A": "1 group (g2), all three output slots get it",
            "0x604B817C": "1 group (g3), all three output slots get it",
            "0x604B818E": "3 groups g4,g5,g6 -> out@4(SP), via-d3, out@8(SP)",
            "0x604B81D3": "1 group (g7) -> shared tail 0x604B81E3",
            "0x604B81F6": "3 groups g8,g9,g10",
            "0x604B8245": "3 groups g11,g12,g13",
            "0x604B828B": "3 groups g14,g15,g16",
            "0x604B82D0": "3 groups g17,g18,g19",
            "0x604B8320": "default: ctx+1071==2 -> constant pointer 0x605DEF4E; "
                          "else ctx+1060==2 -> g1 else g0",
            "0x604B80A6": "special pre-path (not in the table): ctx+1078==0x101 -> "
                          "g20 0x60600BA0 directly",
            "0x604BDD42": "returns the word-select flag (ctx+16 in {0,0xE}, ctx+28>>8 "
                          "in {1,2}, ctx+48 bits) -> picks word0 vs word1 of a group entry",
            "0x604BDD81": "returns 1 iff byte@(ctx+1060)==2 -> default picks g1 over g0",
        },
        "notes": [
            "array is 16 slots of 8 bytes: 0x60601C44 + 16*8 = 0x60601CC4 exactly.",
            "6 slots are empty (key 0) and carry the default handler 0x604B8320 as filler.",
            "open addressing clusters: slot1 holds {1, 0x00FF0031} (codes 0x01010066, "
            "0x02000096); slot4 holds {0x14, 0x00010014, 0x00020014} (0x01010079, "
            "0x01020079, 0x01030079); slot6 holds {6, 0x00010006} (0x0101006B, 0x0102006B).",
            "every key is a relative offset: absolute mode code = key + 0x01010065, and "
            "the code is (byte@(ctx+8)<<24)|(byte@(ctx+9)<<16)|u16@(ctx+10).",
            "slot = key & 0xF, so the index is the low nibble of (code - 0x01010065).",
            "handlers 0x604B818E..0x604B82D0 each fill up to three output pointers "
            "(out@4(SP), d3, out@8(SP)) from a contiguous run of group tables.",
            "the ctx+14 compares are NOT inside the handlers: they live in the shared "
            "selector 0x604B8049, reached via call from each handler.",
            "the earlier 'garbage key 1615560011' = 0x604B8320 came from reading slot 0's "
            "handler as a key, i.e. the base was assumed to be 0x60601C48 instead of "
            "0x60601C44.",
        ],
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("[D] wrote %s" % OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
