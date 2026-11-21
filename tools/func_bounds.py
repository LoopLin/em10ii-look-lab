#!/usr/bin/env python3
"""Function boundaries for the E-M10 II image (PEN-F atlas, table 2).

Input is the segment map from tools/segments.py (docs/map/segments.json):
only its `code_ranges` are swept, so no time is spent decoding data as
instructions.

Method (pure static analysis):

1. Linear sweep of every code range, starting fresh at the range start.
   The range edges come from a score-verified map, so the sweep is in
   phase there (segments.json records a call-target self-consistency
   check per range; a range that began mid-function would show it).

2. A **cut** is the first instruction after a return (`ret` 0xdf,
   `retf` 0xde, `rets`, `rti`), plus the range start.  Returns are the
   only end-of-function marker AM33-2 leaves: there is no mandatory
   prologue and no fix-up table in this image.

3. A cut is dropped again when it is a branch/jump target but not a call
   target -- that is a *local label* of an early-return path, not a
   function start.  (Measured: only 543 of 83 959 post-return points are
   branch targets, so this is a small correction, but it is the one place
   the naive rule is provably wrong.)

4. Everything else is kept, including cuts with no call references at
   all: a function reached only through a dispatch table has no `call`
   pointing at it.  58 % of the cuts are in that class, which is a real
   property of this firmware (the LUT/ISP code is pointer-dispatched),
   not evidence against the cut -- but it does mean "confirmed" here
   means "post-return", not "independently corroborated".

5. Call targets that are instruction starts but lie *inside* a function
   are reported separately as `extra_entries`: secondary entry points
   (labels called directly, thunks).  `0x604b8049` is the important one:
   ATLAS §7b.6 calls it the shared tone selector and 17 sites call it,
   but it is not a function start by this method -- it is an interior
   entry of the function that starts at 0x604a5420.

Anchors: the ten addresses the atlas/lead gave are checked against the
sweep and against the cuts.  `verified_by` states the evidence per
anchor.  Anchors that turn out to be mid-instruction are reported with
the enclosing instruction and the neighbouring real cuts, because a
method that silently rounds a bad anchor proves nothing.  The repo has
already corrected one of them (0x604b8096 -> 0x604b8098, see
docs/map/mode-dispatch.json).

    python tools/func_bounds.py [--verbose] [--out docs/map/functions.json]
"""

import collections
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mn10300_dis as mn                      # noqa: E402
import segments as S                           # noqa: E402

ROOT = os.path.dirname(HERE)
SEGMENTS = "docs/map/segments.json"
OUT = "docs/map/functions.json"

BASE = S.BASE
RET_MNEMS = S.RET_MNEMS

#: instructions that may make up a "function" that is really just an
#: epilogue remnant or a one-line thunk (flagged, not removed)
THIN_MNEMS = {"ret", "retf", "rets", "rti", "add", "mov", "movm", "clr", "nop", "movu", "movbu"}
THIN_MAX_SIZE = 12

#: anchors handed over by the atlas / lead.  (addr, name, source, note)
ANCHORS = [
    ("0x60479960", "lut_upload", "docs/hook-abi.md",
     "LUT upload ABI; bytes 43 08 = mov a0,(8,SP); emulator stop 0x60479B8F"),
    ("0x60483209", "tone_register_api", "docs/firmware/simulator-harness.md",
     "tone table registration API (a0=obj, a1=slot ptr, d0=force); "
     "callers 0x6048306E / 0x60483137"),
    ("0x604b4b92", "chroma", "docs/map/ATLAS.md §3", "8 sites: chroma_x6, chroma_ptr_x2"),
    ("0x604b7aff", "sixaxis", "docs/map/ATLAS.md §3", "1 site"),
    ("0x604b7da9", "sixaxis", "docs/map/ATLAS.md §3", "3 sites"),
    ("0x604b8096", "group_dispatcher", "docs/map/ATLAS.md §3",
     "5 sites: group_x5; ATLAS §7 mode dispatcher"),
    ("0x604b974d", "hsl", "docs/map/ATLAS.md §3", "3 sites: hsl_real, hsl_flat, lut_ident"),
    ("0x604ba8a2", "group_0", "docs/map/ATLAS.md §3", "1 site"),
    ("0x604be501", "tone", "docs/map/ATLAS.md §3", "4 sites: tone_x4"),
    ("0x604b8049", "tone_selector", "docs/map/ATLAS.md §7b.6",
     "shared selector for the 21x8 slot tables (class 0x30/0x40, variant 2/3/4)"),
]


# --------------------------------------------------------------------------
# sweep
# --------------------------------------------------------------------------
def _branch_target(r, op):
    """Target of a branch/jump instruction, else None.

    `Operands.target()` only understands the call/jmp displacement groups
    (D16A/D32A) and silently returns `addr` for an 8-bit branch, so the
    d8 form is computed here the way the disassembler's own sync_check
    does it: cia + signed(D8) (that convention scores 95 % on this image).
    """
    if r.mnem in mn.BRANCH8 and "D8" in r.fields:
        return (op.addr + op.signed("D8")) & 0xFFFFFFFF
    if r.mnem in ("jmp", "bra"):
        for g in ("D8", "D16B", "D32B"):
            if g in r.fields:
                return op.target()
    return None


def sweep_range(data, cand, s, e, base=BASE, anchor_at=None):
    """One pass over [s,e).

    Returns dict with
        bounds      list of dicts: first instruction after a return
                    {addr, ret, ret_mnem, pad, n_insn, thin}
        calls       list of (site, target)   -- call/jmp with a displacement
        branch      list of (site, target)   -- local branches/jumps
        starts      bytearray bitmask of instruction starts
        insns, rets, invalid
        anchor_hit  {addr: (addr, len, mnem)} for anchors in this range
    """
    p = s - base
    end = e - base
    starts = bytearray(len(data))
    calls = []
    branch = []
    bounds = []
    anchor_hit = {}
    insns = rets = invalid = 0
    last_ret = None            # (addr, mnem) of the return just decoded
    pending_pad = 0            # undecodable bytes since that return
    cur = {"start": s, "n": 0, "thin": True, "mnems": []}
    while p < end:
        hit = S._match(cand, data, p, end)
        addr = base + p
        if hit is None:
            invalid += 1
            if last_ret is not None:
                pending_pad += 1
            p += 1
            continue
        nb, r = hit
        if last_ret is not None:
            cur["done"] = True
            bounds.append({"addr": addr, "ret": last_ret[0], "ret_mnem": last_ret[1],
                           "pad": pending_pad, "prev": cur})
            cur = {"start": addr, "n": 0, "thin": True, "mnems": []}
            last_ret = None
            pending_pad = 0
        starts[addr - base] = 1
        insns += 1
        cur["n"] += 1
        if cur["n"] <= 4:
            cur["mnems"].append(r.mnem)
        if r.mnem not in THIN_MNEMS:
            cur["thin"] = False
        if anchor_at and addr in anchor_at:
            anchor_hit[addr] = (addr, nb, r.mnem)
        if r.mnem in RET_MNEMS:
            rets += 1
            last_ret = (addr, r.mnem)
        if r.mnem in ("call", "jmp") and ("D16A" in r.fields or "D32A" in r.fields):
            calls.append((addr, mn.Operands(r, data, p, addr).target()))
        bt = _branch_target(r, mn.Operands(r, data, p, addr)) if (
            r.mnem in mn.BRANCH8 or r.mnem in ("jmp", "bra")) else None
        if bt is not None:
            branch.append((addr, bt))
        p += nb
    bounds.append({"addr": None, "prev": cur})     # tail marker (range end)
    return {"bounds": bounds, "calls": calls, "branch": branch, "starts": starts,
            "insns": insns, "rets": rets, "invalid": invalid, "anchor_hit": anchor_hit}


def _thin(b):
    prev = b["prev"]
    return prev["thin"] and prev["n"] > 0


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def build(verbose=False):
    t0 = time.time()
    with open(os.path.join(ROOT, SEGMENTS), encoding="utf-8") as f:
        seg = json.load(f)
    ranges = [(int(r["start"], 16), int(r["end"], 16)) for r in seg["code_ranges"]]
    data = S.load_image(seg["image"]["path"])
    _dec, cand = S.fast_decoder()

    anchor_at = {int(a, 16) for a, _n, _s, _note in ANCHORS}
    results = []
    for s, e in ranges:
        r = sweep_range(data, cand, s, e, anchor_at=anchor_at)
        r["range"] = (s, e)
        results.append(r)

    # ---- global target census -------------------------------------------
    refs = collections.Counter()
    brefs = collections.Counter()
    for r in results:
        for _site, tgt in r["calls"]:
            refs[tgt] += 1
        for _site, tgt in r["branch"]:
            brefs[tgt] += 1
    all_starts = bytearray(len(data))
    for r in results:
        for i, v in enumerate(r["starts"]):
            if v:
                all_starts[i] = 1

    # ---- per-range phase evidence ---------------------------------------
    # 8-bit branches are the cheapest independent check that the sweep is
    # in phase: if it drifted, their targets stop landing on instruction
    # starts.  (The convention -- cia + signed(d8) -- is the one the
    # disassembler's own sync_check scores at ~95 %.)
    range_stats = []
    for idx, r in enumerate(results):
        s_, e_ = r["range"]
        def _hit(pairs):
            loc = [(st, t) for st, t in pairs if BASE <= t < BASE + len(data)]
            if not loc:
                return None, 0
            good = sum(1 for _st, t in loc if all_starts[t - BASE])
            return round(100.0 * good / len(loc), 1), len(loc)
        bp, bn = _hit(r["branch"])
        cp, cn = _hit(r["calls"])
        range_stats.append({
            "rng": idx, "start": hex(s_), "end": hex(e_), "size": e_ - s_,
            "insns": r["insns"], "rets": r["rets"], "invalid": r["invalid"],
            "branch_targets": bn, "branch_on_start_pct": bp,
            "call_targets": cn, "call_on_start_pct": cp,
            "phase_ok": bool((bp is None or bp >= 90.0) and (cp is None or cp >= 90.0)),
        })

    # ---- cuts -> candidates ---------------------------------------------
    candidates = []
    label_cuts = []            # post-return points dropped as local labels
    for idx, r in enumerate(results):
        s, e = r["range"]
        pts = [{"addr": s, "ret": None, "ret_mnem": None, "pad": 0,
                "prev": {"thin": False, "n": 0, "mnems": []}}]
        for b in r["bounds"]:
            if b["addr"] is not None:
                pts.append(b)
        # veto first, then size the survivors from the surviving neighbour:
        # a dropped label cut merges, it does not punch a hole
        kept = []
        for b in pts:
            a = b["addr"]
            if b["ret"] is not None and refs.get(a, 0) == 0 and brefs.get(a, 0) > 0:
                label_cuts.append({"addr": a, "refs_branch": brefs.get(a, 0),
                                   "ret": b["ret"]})
                continue
            kept.append(b)
        for i, b in enumerate(kept):
            a = b["addr"]
            nxt = kept[i + 1]["addr"] if i + 1 < len(kept) else e
            candidates.append({
                "start": a, "end": nxt, "size": nxt - a,
                "ret": b["ret"], "refs": refs.get(a, 0), "rng": idx,
                "thin": bool(b["ret"] is not None and b["pad"] == 0 and _thin(b)
                             and nxt - a <= THIN_MAX_SIZE and refs.get(a, 0) == 0),
            })
    candidates.sort(key=lambda c: c["start"])
    cutset = {c["start"] for c in candidates}
    extra_entries = []
    for tgt, n in refs.items():
        if BASE <= tgt < BASE + len(data) and tgt not in cutset and all_starts[tgt - BASE]:
            extra_entries.append({"addr": tgt, "refs": n, "branch_refs": brefs.get(tgt, 0)})
    extra_entries.sort(key=lambda x: x["addr"])

    # ---- anchors ---------------------------------------------------------
    anchors = []
    for astr, name, src, note in ANCHORS:
        a = int(astr, 16)
        rec = {"addr": astr, "name": name, "source": src, "note": note,
               "call_refs": refs.get(a, 0), "branch_refs": brefs.get(a, 0)}
        hit = None
        covering = None
        for r in results:
            s, e = r["range"]
            if s <= a < e:
                covering = (s, e)
                hit = r["anchor_hit"].get(a)
                break
        rec["in_code_range"] = "{:#010x}..{:#010x}".format(*covering) if covering else None
        rec["is_instruction_start"] = bool(hit)
        if hit:
            rec["instruction"] = {"addr": hex(hit[0]), "len": hit[1], "mnem": hit[2]}
        else:
            rec["instruction"] = _enclosing(data, cand, a, covering)
        cand_hit = next((c for c in candidates if c["start"] == a), None)
        rec["is_function_start"] = cand_hit is not None
        lo = max([c["start"] for c in candidates if c["start"] <= a and _in(c, covering)],
                 default=None)
        hi = min([c["start"] for c in candidates if c["start"] > a and _in(c, covering)],
                 default=None)
        rec["nearest_cut_le"] = hex(lo) if lo is not None else None
        rec["nearest_cut_gt"] = hex(hi) if hi is not None else None
        vb = []
        if cand_hit:
            vb.append("function-start")
            if cand_hit["ret"] is not None:
                vb.append("post-%s@%s" % (_ret_mnem(cand_hit["ret"], results), hex(cand_hit["ret"])))
            if cand_hit["refs"]:
                vb.append("call-target x%d" % cand_hit["refs"])
            rec["function"] = {"start": hex(cand_hit["start"]), "end": hex(cand_hit["end"]),
                               "size": cand_hit["size"]}
        elif rec["is_instruction_start"]:
            inside = next((c for c in candidates if c["start"] < a < c["end"]), None)
            vb.append("instruction-start but NOT a cut")
            if inside:
                vb.append("interior entry of function %s (+%d B), called by %d site(s)"
                          % (hex(inside["start"]), a - inside["start"], rec["call_refs"]))
            if rec["branch_refs"]:
                vb.append("branch-target x%d" % rec["branch_refs"])
        if not rec["is_instruction_start"]:
            enc = rec["instruction"] or {}
            vb.append("MID-INSTRUCTION: byte %d of %s at %s"
                      % (enc.get("offset_in_insn", -1), enc.get("mnem", "?"),
                         enc.get("enclosed_by", "?")))
            nxt = rec["nearest_cut_gt"]
            if nxt and int(nxt, 16) - a <= 8:
                vb.append("real start is %s (+%d)" % (nxt, int(nxt, 16) - a))
        rec["verified_by"] = vb
        anchors.append(rec)

    # ---- stats -----------------------------------------------------------
    sizes = [c["size"] for c in candidates]
    per_range = collections.Counter(c["rng"] for c in candidates)
    code_bytes = sum(e - s for s, e in ranges)
    stats = {
        "count": len(candidates),
        "avg_size": round(statistics.mean(sizes), 1) if sizes else 0,
        "median_size": int(statistics.median(sizes)) if sizes else 0,
        "p90_size": int(sorted(sizes)[int(0.9 * (len(sizes) - 1))]) if sizes else 0,
        "min_size": min(sizes) if sizes else 0,
        "max_size": max(sizes) if sizes else 0,
        "with_call_refs": sum(1 for c in candidates if c["refs"]),
        "post_return_only": sum(1 for c in candidates if c["ret"] is not None and not c["refs"]),
        "range_starts": sum(1 for c in candidates if c["ret"] is None),
        "thin_stubs": sum(1 for c in candidates if c["thin"]),
        "label_cuts_dropped": len(label_cuts),
        "tiny_lt_8B": sum(1 for s in sizes if s < 8),
        "lt_16B": sum(1 for s in sizes if s < 16),
        "code_bytes": code_bytes,
        "coverage_pct": round(100.0 * sum(sizes) / code_bytes, 2) if code_bytes else 0,
        "code_ranges": len(ranges),
        "functions_per_range": {str(i): n for i, n in sorted(per_range.items())},
        "call_instructions": sum(len(r["calls"]) for r in results),
        "branch_instructions": sum(len(r["branch"]) for r in results),
        "distinct_call_targets": len(refs),
        "extra_entries": len(extra_entries),
        "anchors_total": len(anchors),
        "anchors_instruction_start": sum(1 for a in anchors if a["is_instruction_start"]),
        "anchors_function_start": sum(1 for a in anchors if a["is_function_start"]),
        "runtime_s": round(time.time() - t0, 1),
    }
    res = {
        "schema": "em10fw/functions/1",
        "image": seg["image"],
        "source": {"segments": SEGMENTS, "code_ranges": len(ranges)},
        "method": {
            "sweep": "linear sweep of each code range from its first byte",
            "cut": "first instruction after ret/retf/rets/rti, plus range start",
            "cut_veto": "drop the cut when the address is a branch/jump target but not a "
                        "call target (local label of an early-return path)",
            "thin": "cut whose whole body is <= %d B of ret/mov/add-SP only and has no "
                    "call refs -- flagged, kept (thin wrapper or epilogue remnant)"
                    % THIN_MAX_SIZE,
            "extra_entries": "call targets on instruction starts that are not cuts = "
                             "secondary entry points, not function starts",
            "caveat": "no prologue signature is used, so an unreachable stub after a "
                      "return can still become its own candidate; use `refs`/`thin` to "
                      "filter.  Tail-call-only functions (jmp to entry) are cut by the "
                      "return of the caller, not by their own entry.",
        },
        "stats": stats,
        "ranges": range_stats,
        "anchors": anchors,
        "candidates": [
            {"start": hex(c["start"]), "end": hex(c["end"]), "size": c["size"],
             "conf": int(c["ret"] is not None or c["refs"] > 0),
             "refs": c["refs"], "ret": hex(c["ret"]) if c["ret"] else None,
             "thin": int(c["thin"]), "rng": c["rng"]}
            for c in candidates
        ],
        "label_cuts": [{"addr": hex(x["addr"]), "branch_refs": x["refs_branch"],
                        "ret": hex(x["ret"])} for x in label_cuts],
        "extra_entries": [{"addr": hex(x["addr"]), "refs": x["refs"],
                           "branch_refs": x["branch_refs"]} for x in extra_entries],
    }
    for r in res["ranges"]:
        r["functions"] = res["stats"]["functions_per_range"].get(str(r["rng"]), 0)
    if verbose:
        _report(res, candidates, res["ranges"])
    return res


def _ret_mnem(ret_addr, results):
    for r in results:
        for b in r["bounds"]:
            if b.get("ret") == ret_addr:
                return b["ret_mnem"]
    return "ret"


def _in(c, covering):
    return covering is not None and covering[0] <= c["start"] < covering[1]


def _enclosing(data, cand, a, covering):
    """Instruction that encloses address `a` (for mid-instruction anchors)."""
    if not covering:
        return None
    s, e = covering
    p = s - BASE
    end = e - BASE
    while p < end:
        hit = S._match(cand, data, p, end)
        addr = BASE + p
        if hit is None:
            p += 1
            continue
        nb, r = hit
        if addr <= a < addr + nb:
            return {"enclosed_by": hex(addr), "len": nb, "mnem": r.mnem,
                    "offset_in_insn": a - addr, "bytes": data[p:p + nb].hex(" ")}
        p += nb
    return None


def _report(res, candidates, ranges):
    st = res["stats"]
    print("code ranges %d, code %d B -> functions %d (avg %.1f B, median %d B, p90 %d B)"
          % (st["code_ranges"], st["code_bytes"], st["count"], st["avg_size"],
             st["median_size"], st["p90_size"]))
    print("with call refs %d, post-return only %d, range starts %d, thin stubs %d, "
          "label cuts dropped %d" % (st["with_call_refs"], st["post_return_only"],
                                     st["range_starts"], st["thin_stubs"],
                                     st["label_cuts_dropped"]))
    print("size<8B %d, <16B %d, coverage %.2f%%, calls %d, branches %d, "
          "distinct call targets %d, extra entries %d"
          % (st["tiny_lt_8B"], st["lt_16B"], st["coverage_pct"], st["call_instructions"],
             st["branch_instructions"], st["distinct_call_targets"], st["extra_entries"]))
    print("anchors: %d/%d are instruction starts, %d/%d are function starts"
          % (st["anchors_instruction_start"], st["anchors_total"],
             st["anchors_function_start"], st["anchors_total"]))
    for a in res["anchors"]:
        print("  %-12s %-18s %s" % (a["addr"], a["name"], "; ".join(a["verified_by"])))
    bad = [r for r in ranges if not r["phase_ok"]]
    print("phase: %d/%d code ranges >=90%% of branch+call targets land on instruction "
          "starts" % (len(ranges) - len(bad), len(ranges)))
    for r in bad:
        print("  weak %s..%s br=%s%% (n=%d) call=%s%% (n=%d)"
              % (r["start"], r["end"], r["branch_on_start_pct"], r["branch_targets"],
                 r["call_on_start_pct"], r["call_targets"]))
    print("largest functions:")
    for c in sorted(candidates, key=lambda c: -c["size"])[:5]:
        print("  %#010x..%#010x %7d B refs=%d" % (c["start"], c["end"], c["size"], c["refs"]))


def main(argv):
    verbose = "--verbose" in argv or "-v" in argv
    out = os.path.join(ROOT, OUT)
    for i, a in enumerate(argv):
        if a == "--out":
            out = argv[i + 1]
    res = build(verbose=verbose)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print("wrote", os.path.relpath(out, ROOT), "(%.1f KB)" % (os.path.getsize(out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
