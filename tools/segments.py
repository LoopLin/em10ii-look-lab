#!/usr/bin/env python3
"""Segment map for the E-M10 II firmware image (PEN-F atlas, table 1).

Scan the whole decoded image with a 1 KB entropy window, split
"high-entropy random data (images/fonts)" from "low-entropy structured
data / code", and write docs/map/segments.json.

Everything here is pure static analysis of

    analysis/00_0100_40300020.decoded.bin   (21,364,672 B)
    RAM = 0x60021020 + file_offset

Classification (per 1 KB window, first rule that matches):

    bss-like  H < 0.4 or (zero+0xff) >= 95 %      -- padding / unwritten
    image     H >= 7.2                            -- JPEG/font/graphics
    rodat     printable text (long run) or u32 pointer table
    code      instruction-shape score >= 7 (4 KB probe, see below)
    data      everything else (numeric tables, structs)

The code/data discriminator is an instruction-shape **score**, not a
single threshold: a legal-byte decode is useless (every first byte starts
some AM33-2 instruction; the tone pool decodes as "code" with mean length
2.2), so the score counts independent shape features measured over a 4 KB
probe window:

    +3  invalid (undecodable) bytes <= 1.0 %
    +3  mean instruction length >= 2.35  (data decodes ~1.2-1.9)
    +2  one-byte instructions <= 33 %    (code 12-35 %, data 25-85 %)
    +2  d32 call/jmp targets inside the image (n>=20) ... -2 if none
    +2  absolute operands (IMM16/24/32) pointing into the image (n>=50)
    +1  d16 calls >= 3 per KB
    +1  d16 call-target repetition >= 0.15 (real code calls hot callees)
    -2  u32 words inside the image >= 45 % (pointer table, not code)

Threshold 7 sits in the gap of the measured score histogram
(`--verbose` prints it): 4 940 windows score exactly 3 (the zero/table
mass; only "valid bytes" fires), the code mass is 12-14, and only 39
windows land on 6.  Max score of any known data landmark is 5.

Known residual error (documented in the json report, not hidden): a few
small pointer/record tables inside a code band score as code because
their 4-byte records point into code and decode to a plausible call
stream; printable-text detection catches the string blocks, but e.g.
0x6006a020 (3 KB of records ending in pointers to 0x6009xxxx) comes out
`code`.  These are 2-3 KB islands inside code, not whole regions, and
func_bounds.py drops them (no return-cut boundary survives).

    python tools/segments.py [--verbose] [--out docs/map/segments.json]
"""

import collections
import json
import math
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mn10300_dis as mn                      # noqa: E402

ROOT = os.path.dirname(HERE)

IMAGE = "analysis/00_0100_40300020.decoded.bin"
BASE = 0x60021020                             # RAM = BASE + file offset
PAYLOAD_END = 0x60DA4E08                      # end of the 0x0100 payload
WIN = 1024                                    # entropy window / map grain
PROBE_LEN = 4096                              # code-shape probe length
PROBE_STRIDE = 2048                           # probe grid (2x oversampling)
EDGE_STEP = 512                               # code range edge refinement

K_CODE = "code"
K_DATA = "data"
K_RODAT = "rodat"
K_BSS = "bss-like"
K_IMAGE = "image"

SCORE_CODE = 7

#: a "code" range must contain returns: real functions end with ret/retf/rets.
#: Applies to ranges up to RETS_SMALL_RANGE bytes; longer ranges are kept
#: (they always contain returns in practice) but the number is reported.
RETS_MIN_PER_KB = 0.5
RETS_SMALL_RANGE = 16384

#: anchors checked after classification; (addr, allowed kinds, why)
CALIBRATION = [
    ("0x60400000", (K_CODE,), "start of the known 0x0100 code block"),
    ("0x60479960", (K_CODE,), "LUT upload function (docs/hook-abi.md)"),
    ("0x605d8344", (K_DATA,), "tone table pool 0x200 x 64 B"),
    ("0x605d4829", (K_DATA,), "chroma table pool 0x64 x 65 B"),
    ("0x60600920", (K_DATA, K_RODAT), "21 x 0x20 group table (holds u32 ptrs)"),
    ("0x60601cc4", (K_DATA, K_RODAT), "mode-dispatch records (u32 ptrs)"),
    ("0x605ffdc8", (K_DATA, K_RODAT), "8 x 12 pointer table"),
    ("0x605f5a64", (K_DATA,), "HSL real table"),
    ("0x605c0000", (K_RODAT,), "ASCII string block (qsy Focus pos = %5d)"),
    ("0x603a0000", (K_RODAT,), "ASCII string block"),
    ("0x60026820", (K_RODAT,), "ASCII strings (heap/cheap_free:/slab dumps)"),
    ("0x601b0000", (K_IMAGE,), "inside the high-entropy graphics blob"),
    ("0x60da0000", (K_DATA, K_RODAT), "tail of the 0x0100 payload"),
]


# --------------------------------------------------------------------------
# decoder
# --------------------------------------------------------------------------
def load_image(path=IMAGE):
    with open(os.path.join(ROOT, path), "rb") as f:
        return f.read()


def fast_decoder():
    """(candidates-per-first-byte, table) for a match loop without text rendering.

    mn10300_dis.Decoder.match renders operand text (~190 KB/s); for bulk
    sweeps only (length, rule) is needed (~1.9 MB/s).
    """
    dec = mn.Decoder(verbose=False)
    return dec, [dec.candidates(b) for b in range(256)]


def _match(cand, data, off, end):
    for r in cand[data[off]]:
        nb = r.nb
        if off + nb > end:
            continue
        ok = True
        for bi in range(1, nb):
            msk = r.mask[bi]
            if msk and (data[off + bi] & msk) != r.val[bi]:
                ok = False
                break
        if ok:
            return nb, r
    return None


# --------------------------------------------------------------------------
# instruction-shape probe
# --------------------------------------------------------------------------
RET_MNEMS = ("ret", "retf", "rets", "rti")


def probe(data, cand, addr, length=PROBE_LEN, base=BASE):
    """Decode `length` bytes at `addr` and return shape features.

    The probe starts *fresh* at `addr`; no phase is inherited from the
    neighbours, so a desynced sweep cannot leak into a clean region.
    """
    n = len(data)
    off = addr - base
    end = min(off + length, n)
    tot = inv = one = br = rets = 0
    tlen = 0
    d32n = d32in = immn = immin = 0
    calln = 0
    targets = collections.Counter()
    p = off
    while p < end:
        hit = _match(cand, data, p, end)
        if hit is None:
            inv += 1
            tot += 1
            tlen += 1
            p += 1
            continue
        nb, r = hit
        tot += 1
        tlen += nb
        if nb == 1:
            one += 1
        if r.mnem in mn.BRANCH8 and "D8" in r.fields:
            br += 1
        if r.mnem in RET_MNEMS:
            rets += 1
        if r.mnem in ("call", "jmp") and ("D16A" in r.fields or "D32A" in r.fields):
            op = mn.Operands(r, data, p, base + p)
            tgt = op.target()
            if "D32A" in r.fields:
                d32n += 1
                if base <= tgt < base + n:
                    d32in += 1
            else:
                calln += 1
                targets[tgt] += 1
        for grp in ("IMM32", "IMM24", "IMM16"):
            if grp in r.fields or (grp + "A") in r.fields:
                op = mn.Operands(r, data, p, base + p)
                v = op.value(grp)
                immn += 1
                if base <= v < base + n:
                    immin += 1
                break
        p += nb
    kb = max((end - off) / 1024.0, 1e-9)
    span = data[off:end]
    printable = sum(1 for b in span if 0x20 <= b <= 0x7E) / max(len(span), 1)
    ptrs = words = 0
    for k in range(0, (end - off) - 3, 4):
        words += 1
        if base <= struct.unpack_from("<I", data, off + k)[0] < base + n:
            ptrs += 1
    return {
        "invalid_pct": 100.0 * inv / tot,
        "mean_len": tlen / tot,
        "one_byte_pct": 100.0 * one / tot,
        "branch_per_kb": br / kb,
        "call_per_kb": calln / kb,
        "ret_per_kb": rets / kb,
        "d32_n": d32n,
        "d32_in_pct": 100.0 * d32in / d32n if d32n else 0.0,
        "imm_n": immn,
        "imm_ref_pct": 100.0 * immin / immn if immn else 0.0,
        "call_rep": 1.0 - float(len(targets)) / calln if calln else 0.0,
        "ptr_frac": ptrs / words if words else 0.0,
        "printable": printable,
        "insn": tot,
    }


def code_score(f):
    """Instruction-shape score, see module docstring. Code iff >= SCORE_CODE."""
    s = 0
    if f["invalid_pct"] <= 1.0:
        s += 3
    if f["mean_len"] >= 2.35:
        s += 3
    if f["one_byte_pct"] <= 33.0:
        s += 2
    if f["d32_n"] >= 20:
        s += 2 if f["d32_in_pct"] >= 80.0 else (-2 if f["d32_in_pct"] <= 20.0 else 0)
    if f["imm_n"] >= 50 and f["imm_ref_pct"] >= 12.0:
        s += 2
    if f["call_per_kb"] >= 3.0:
        s += 1
    if f["call_rep"] >= 0.15:
        s += 1
    if f["ptr_frac"] >= 0.45:
        s -= 2
    return s


def score_grid(data, cand, base=BASE):
    """Score every 1 KB window from probes on a 2 KB grid (box-averaged)."""
    n = len(data)
    nwin = (n + WIN - 1) // WIN
    acc = np.zeros(nwin)
    cnt = np.zeros(nwin)
    for addr in range(base, base + n - PROBE_LEN, PROBE_STRIDE):
        s = code_score(probe(data, cand, addr))
        first = (addr - base) // WIN
        for k in range(first, min(first + PROBE_LEN // WIN, nwin)):
            acc[k] += s
            cnt[k] += 1
    return np.where(cnt > 0, acc / np.maximum(cnt, 1), 0.0)


# --------------------------------------------------------------------------
# window features (numpy)
# --------------------------------------------------------------------------
def _printable_run(buf):
    """Longest run of printable bytes (0x20..0x7e, plus \\t\\n\\r)."""
    best = cur = 0
    for b in buf:
        if 0x20 <= b <= 0x7E or b in (9, 10, 13):
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


def window_features(data, base=BASE):
    n = len(data)
    nwin = (n + WIN - 1) // WIN
    arr = np.frombuffer(data, dtype=np.uint8)
    ent = np.zeros(nwin)
    zero = np.zeros(nwin)
    ff = np.zeros(nwin)
    printable = np.zeros(nwin)
    ptr = np.zeros(nwin)
    run = np.zeros(nwin, dtype=np.int32)
    lo, hi = base, base + n
    for i in range(nwin):
        o = i * WIN
        buf = arr[o:o + WIN]
        cnt = np.bincount(buf, minlength=256).astype(np.float64)
        p = cnt[cnt > 0] / buf.size
        ent[i] = float(-(p * np.log2(p)).sum())
        zero[i] = float(cnt[0] / buf.size)
        ff[i] = float(cnt[255] / buf.size)
        printable[i] = float(((buf >= 0x20) & (buf <= 0x7E)).mean())
        words = buf[: (buf.size // 4) * 4].view("<u4")
        ptr[i] = float(((words >= lo) & (words < hi)).mean()) if words.size else 0.0
        if printable[i] >= 0.5:
            run[i] = _printable_run(buf)
    return {"entropy": ent, "zero": zero, "ff": ff,
            "printable": printable, "ptr": ptr, "run": run}


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------
def classify(feat, scores):
    """Per 1 KB window kind (list of str)."""
    kinds = []
    for i in range(len(scores)):
        h = feat["entropy"][i]
        zf = feat["zero"][i] + feat["ff"][i]
        if h < 0.40 or zf >= 0.95:
            kinds.append(K_BSS)
        elif h >= 7.20:
            kinds.append(K_IMAGE)
        elif feat["printable"][i] >= 0.55 and feat["run"][i] >= 24:
            kinds.append(K_RODAT)
        elif scores[i] >= SCORE_CODE:
            kinds.append(K_CODE)
        elif feat["ptr"][i] >= 0.30:
            kinds.append(K_RODAT)
        else:
            kinds.append(K_DATA)
    return kinds


def smooth(kinds, scores):
    """Median-of-3 on the score for the code/not-code split, then labels.

    A single 1 KB window inside a 40 KB code band occasionally dips
    (literal pool); require code-ness of the neighbourhood for code, but
    let a window keep a data verdict if its own score is far below the
    threshold (real table: score <= 5).
    """
    out = list(kinds)
    for i in range(1, len(kinds) - 1):
        if kinds[i] == K_CODE:
            nb = sorted(scores[i - 1:i + 2])[1]
            if nb < SCORE_CODE - 2 and scores[i] < SCORE_CODE + 2:
                out[i] = K_DATA
        elif kinds[i] == K_DATA and scores[i] >= SCORE_CODE + 2:
            if kinds[i - 1] == K_CODE or kinds[i + 1] == K_CODE:
                out[i] = K_CODE
    return out


def refine_edge(data, cand, addr, step, kinds, base=BASE, max_steps=2):
    """Shift a window-grain code edge by EDGE_STEP while it still looks code.

    `kinds` vetoes walking into a window that was classified as text /
    pointer data (string blocks decode to a plausible call stream: the
    ASCII dumps at 0x60026820 score 9, so score alone would swallow them),
    and at most `max_steps` * EDGE_STEP is allowed either way.
    """
    n = len(data)
    lim_lo, lim_hi = base, base + n - PROBE_LEN
    a = addr
    for _ in range(max_steps):
        nxt = a + step
        if nxt < lim_lo or nxt > lim_hi:
            break
        wi = (nxt - base) // WIN
        in_code_window = 0 <= wi < len(kinds) and kinds[wi] == K_CODE
        f = probe(data, cand, nxt)
        if code_score(f) < SCORE_CODE:
            break
        if not in_code_window and (f["ptr_frac"] >= 0.30 or f["printable"] >= 0.55):
            break
        a = nxt
    return a


def sweep_range(data, cand, s, e, base=BASE):
    """Linear sweep of [s,e) -> instruction starts, returns, call targets."""
    p = s - base
    end = e - base
    starts = set()
    tgts = []
    insns = inv = rets = 0
    while p < end:
        hit = _match(cand, data, p, end)
        if hit is None:
            inv += 1
            p += 1
            continue
        nb, r = hit
        starts.add(base + p)
        insns += 1
        if r.mnem in RET_MNEMS:
            rets += 1
        if r.mnem in ("call", "jmp") and ("D16A" in r.fields or "D32A" in r.fields):
            tgts.append(mn.Operands(r, data, p, base + p).target())
        p += nb
    return {"starts": starts, "targets": tgts, "insn": insns, "inv": inv, "rets": rets}


def verify_ranges(data, cand, ranges, base=BASE):
    """Sweep every code range and test it against itself and the whole map.

    * call_sync_local   call/jmp targets landing on an instruction start of
                        the same range (catches data tables that only *look*
                        like call streams);
    * call_sync_global  targets landing on a start of *any* code range
                        (the fair number: real code calls across ranges);
    * rets_per_kb       every real function ends with ret/retf/rets, so a
                        multi-KB "code" range with no returns is a table.

    Ranges failing the returns test are dropped from the map (recorded in
    `dropped`), because the score can be fooled by 4-byte records whose
    values point into code.
    """
    infos = [sweep_range(data, cand, s, e, base) for s, e in ranges]
    gstarts = set()
    for i in infos:
        gstarts |= i["starts"]
    kept, dropped = [], []
    for (s, e), info in zip(ranges, infos):
        calls = info["targets"]
        loc = sum(1 for t in calls if t in info["starts"])
        glo = sum(1 for t in calls if t in gstarts)
        kb = max((e - s) / 1024.0, 1e-9)
        rec = {"start": hex(s), "end": hex(e), "size": e - s,
               "insn": info["insn"],
               "invalid_pct": round(100.0 * info["inv"] / max(info["insn"] + info["inv"], 1), 2),
               "rets": info["rets"], "rets_per_kb": round(info["rets"] / kb, 2),
               "calls": len(calls),
               "call_sync_local": round(loc / len(calls), 3) if calls else None,
               "call_sync_global": round(glo / len(calls), 3) if calls else None}
        if rec["rets_per_kb"] < RETS_MIN_PER_KB and (e - s) <= RETS_SMALL_RANGE:
            rec["dropped"] = "no returns: %d ret in %d B" % (info["rets"], e - s)
            dropped.append(rec)
        else:
            kept.append((s, e, rec))
    return kept, dropped


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def build(verbose=False):
    t0 = time.time()
    data = load_image()
    n = len(data)
    _dec, cand = fast_decoder()
    feat = window_features(data)
    scores = score_grid(data, cand)
    kinds = smooth(classify(feat, scores), scores)

    nwin = len(kinds)
    # code ranges at 1 KB grain, then edges refined to EDGE_STEP bytes
    code_ranges = []
    i = 0
    while i < nwin:
        if kinds[i] != K_CODE:
            i += 1
            continue
        j = i
        while j + 1 < nwin and kinds[j + 1] == K_CODE:
            j += 1
        start = BASE + i * WIN
        end = min(BASE + (j + 1) * WIN, BASE + n)
        rs = refine_edge(data, cand, start, -EDGE_STEP, kinds)
        if rs < start:
            start = rs
        re_ = refine_edge(data, cand, end - EDGE_STEP, EDGE_STEP, kinds)
        if re_ > end - EDGE_STEP:
            end = re_ + EDGE_STEP
        code_ranges.append([start, end])
        i = j + 1
    code_ranges = merge_ranges(sorted(code_ranges), 1024)

    # segments: code ranges + the gaps split by window kind
    kept, dropped = verify_ranges(data, cand, code_ranges)
    code_ranges = [(s, e) for s, e, _r in kept]
    verified = [r for _s, _e, r in kept]
    segs = code_ranges_to_segments(code_ranges, kinds, feat, scores, nwin, n)
    cal = check_calibration(segs)
    code_bytes = sum(e - s for s, e in code_ranges)
    res = {
        "schema": "em10fw/segments/1",
        "image": {
            "path": IMAGE,
            "base": hex(BASE),
            "size": n,
            "span": [hex(BASE), hex(BASE + n)],
            "payload_end": hex(PAYLOAD_END),
        },
        "window": WIN,
        "method": {
            "probe_len": PROBE_LEN,
            "probe_stride": PROBE_STRIDE,
            "edge_step": EDGE_STEP,
            "code_score_threshold": SCORE_CODE,
            "code_score": [
                "+3 invalid_pct<=1.0", "+3 mean_len>=2.35", "+2 one_byte_pct<=33",
                "+2 d32_n>=20 and d32_in_pct>=80 else -2 if d32_in_pct<=20",
                "+2 imm_n>=50 and imm_ref_pct>=12", "+1 call_per_kb>=3",
                "+1 call_rep>=0.15", "-2 ptr_frac>=0.45",
            ],
            "kinds": {
                K_BSS: "H<0.4 or (zero+ff)>=95%, padding/unwritten",
                K_IMAGE: "H>=7.2, compressed graphics/fonts",
                K_RODAT: "printable text or u32 pointer table (read-only)",
                K_CODE: "instruction-shape score >= 7",
                K_DATA: "numeric tables/structs",
            },
            "note": "codes' 1 KB windows are entropy windows; code edges refined to "
                    "%d B. 'rodat' is still read-only data -- pointer tables and "
                    "string blocks -- so it is a *sub-kind* of 'data' for the atlas." % EDGE_STEP,
        },
        "segments": segs,
        "code_ranges": verified,
        "dropped_code_ranges": dropped,
        "calibration": cal,
        "stats": {
            "windows": nwin,
            "segments": len(segs),
            "code_ranges": len(code_ranges),
            "code_bytes": code_bytes,
            "code_share_pct": round(100.0 * code_bytes / n, 2),
            "bytes_by_kind": _bytes_by_kind(segs),
            "calibration_ok": sum(1 for c in cal if c["ok"]),
            "calibration_total": len(cal),
            "runtime_s": round(time.time() - t0, 1),
        },
    }
    if verbose:
        _report(res, data, scores)
    return res


def merge_ranges(ranges, gap):
    out = []
    for s, e in ranges:
        if out and s - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def code_ranges_to_segments(code_ranges, kinds, feat, scores, nwin, n):
    """Emit the segment list: code ranges as-is, gaps split by window kind."""
    segs = []
    pos = BASE
    for cs, ce in code_ranges:
        cs = max(cs, pos)
        if ce <= cs:
            continue
        segs.extend(_gap_segments(pos, cs, kinds, feat, scores, nwin, n))
        segs.append(_code_segment(cs, ce, feat))
        pos = ce
    segs.extend(_gap_segments(pos, BASE + n, kinds, feat, scores, nwin, n))
    return segs


def _code_segment(cs, ce, feat):
    i0 = max(0, (cs - BASE) // WIN)
    i1 = min(len(feat["entropy"]), (ce - BASE + WIN - 1) // WIN)
    ent = float(feat["entropy"][i0:i1].mean()) if i1 > i0 else 0.0
    return {"start": hex(cs), "end": hex(ce), "size": ce - cs,
            "entropy": round(ent, 3), "kind": K_CODE,
            "h_max": round(float(feat["entropy"][i0:i1].max()), 2) if i1 > i0 else 0.0,
            "printable": round(float(feat["printable"][i0:i1].mean()), 2)}


def _gap_segments(a, b, kinds, feat, scores, nwin, n):
    """Split [a,b) into segments of constant window kind."""
    if b <= a:
        return []
    out = []
    i0 = max(0, (a - BASE) // WIN)
    i1 = min(nwin - 1, (b - BASE - 1) // WIN)
    cur_kind = K_DATA
    cur_start = a
    for i in range(i0, i1 + 1):
        k = kinds[i]
        if k == K_CODE:                      # never happens inside a gap
            k = K_DATA
        if k != cur_kind:
            seg_end = BASE + i * WIN
            if seg_end > cur_start:
                out.append(_seg_meta(cur_start, seg_end, cur_kind, feat, i0, i))
            cur_kind = k
            cur_start = seg_end
    out.append(_seg_meta(cur_start, b, cur_kind, feat, i0, i1 + 1))
    out = _merge_adjacent(out)
    return out


def _seg_meta(a, b, kind, feat, i0, i1):
    lo = max(0, (a - BASE) // WIN)
    hi = min(len(feat["entropy"]), max((b - BASE + WIN - 1) // WIN, lo + 1))
    ent = float(feat["entropy"][lo:hi].mean())
    return {
        "start": hex(a), "end": hex(b), "size": b - a,
        "entropy": round(ent, 3), "kind": kind,
        "h_max": round(float(feat["entropy"][lo:hi].max()), 2),
        "zero_frac": round(float(feat["zero"][lo:hi].mean()), 2),
        "ff_frac": round(float(feat["ff"][lo:hi].mean()), 2),
        "printable": round(float(feat["printable"][lo:hi].mean()), 2),
        "ptr_frac": round(float(feat["ptr"][lo:hi].mean()), 2),
    }


def _merge_adjacent(segs):
    out = []
    for s in segs:
        if s["size"] <= 0:
            continue
        if out and out[-1]["kind"] == s["kind"]:
            prev = out[-1]
            tot = prev["size"] + s["size"]
            for k in ("entropy", "h_max", "zero_frac", "ff_frac", "printable", "ptr_frac"):
                if k in prev and k in s:
                    prev[k] = round((prev[k] * prev["size"] + s[k] * s["size"]) / tot, 2)
                elif k in s:
                    prev[k] = s[k]
            prev["end"] = s["end"]
            prev["size"] = tot
        else:
            out.append(dict(s))
    return out


def _bytes_by_kind(segs):
    acc = collections.Counter()
    for s in segs:
        acc[s["kind"]] += s["size"]
    return dict(sorted(acc.items()))


def check_calibration(segs):
    out = []
    for astr, allowed, why in CALIBRATION:
        a = int(astr, 16)
        got = None
        for s in segs:
            if int(s["start"], 16) <= a < int(s["end"], 16):
                got = s["kind"]
                break
        out.append({"addr": astr, "expect": list(allowed), "got": got,
                    "ok": got in allowed, "why": why})
    return out


def _report(res, data, scores):
    st = res["stats"]
    print("image %d B, windows %d, segments %d, code ranges %d (%d B, %.1f%%) [%.1fs]"
          % (res["image"]["size"], st["windows"], st["segments"], st["code_ranges"],
             st["code_bytes"], st["code_share_pct"], st["runtime_s"]))
    print("bytes by kind:", st["bytes_by_kind"])
    print("calibration %d/%d" % (st["calibration_ok"], st["calibration_total"]))
    for c in res["calibration"]:
        print("  %s %-9s expect %-18s %s" % ("ok " if c["ok"] else "BAD", c["got"],
                                             ",".join(c["expect"]), c["addr"] + "  " + c["why"]))
    print("code ranges:")
    for r in res["code_ranges"]:
        print("  %s..%s %8d B" % (r["start"], r["end"], r["size"]))
    h = collections.Counter(int(s) for s in scores)
    print("code score histogram (1 KB windows, box-avg of 4 KB probes):",
          " ".join("%d:%d" % (k, h[k]) for k in sorted(h)))


def main(argv):
    verbose = "--verbose" in argv or "-v" in argv
    out = os.path.join(ROOT, "docs", "map", "segments.json")
    for i, a in enumerate(argv):
        if a == "--out":
            out = argv[i + 1]
    res = build(verbose=verbose)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print("wrote", os.path.relpath(out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
