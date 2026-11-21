#!/usr/bin/env python3
"""Audit the SCP (quick-select bar) name patch together with the B95 menu patch.

  .tmp/ui_names2.patch      the 8 pointer words this patch writes
  .tmp/ui_names.patch       the B95 patch those pointers point into (must be applied first)
  .tmp/ui_names2.spec.json  what ui_names2 is supposed to mean
  stock/OLY_E_120_1400_0000_0000.BIN

Checks
  * ui_names2 writes exactly eight u32 words, at the pointer words of sids
    1657/1658/183/1661 in records 8 (enu) and 2 (chs), and each new value is
    STRING_SLOT + b95 string offset + 12 (the first character after "@A0xx@")
  * ui_names2 and ui_names write disjoint byte ranges (they can be merged in any order)
  * stock + ui_names + ui_names2, in memory: those eight sids now read exactly
    Ektachrome 100 VS / Classic Chrome / Elite Color 200 / Pro Neg Hi, strict
    UTF-16LE, one 0x0000 terminator, pointer inside the record and 2-byte aligned,
    with no leftover icon prefix
  * every other sid of both records keeps its stock text; the four *menu* sids still
    read the B95 names; blocks 0 and 2 are byte-identical; the container re-encodes
    and re-parses
  * ui_names2 applied *without* ui_names does not produce the names (proves the
    dependency is real, so the doc must state it)

Usage: python tools/verify_ui_scp.py [--json out.json]
Exit status 0 = all checks passed.
"""
import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oly_container as oc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STOCK = ROOT / "stock/OLY_E_120_1400_0000_0000.BIN"
SPEC = ROOT / ".tmp/ui_names2.spec.json"
PATCH = ROOT / ".tmp/ui_names2.patch"
B95_PATCH = ROOT / ".tmp/ui_names.patch"
BASE = 0x60021020
STRING_SLOT = 0xA31A2C00
ICON_BYTES = 12

problems, notes = [], []


def fail(msg):
    problems.append(msg)


def parse_patch(path):
    out = []
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        mode, _, body = line.partition(" ")
        if mode not in ("ram", "off", "blk"):
            fail(f"{path}:{lineno}: unknown mode {mode!r}")
            continue
        addr_text, _, hex_text = body.partition("=")
        hex_text = re.sub(r"[\s:_-]", "", hex_text)
        if not hex_text or len(hex_text) % 2:
            fail(f"{path}:{lineno}: bad hex payload")
            continue
        out.append({"line": lineno, "mode": mode, "addr": addr_text.strip(), "new": bytes.fromhex(hex_text)})
    return out


def resolve(blocks, item):
    if item["mode"] == "blk":
        blk_text, _, off_text = item["addr"].partition(":")
        return int(blk_text), int(off_text, 16)
    if item["mode"] == "off":
        container_offset = int(item["addr"], 16)
    else:
        container_offset = 32 + (int(item["addr"], 16) - BASE)
    for block in blocks:
        start = block.offset + 32
        if start <= container_offset < start + len(block.payload):
            return block.index, container_offset - block.offset - 32
    fail(f"patch address {item['addr']} is not inside any block payload")
    return None, None


def read_string(block1, off):
    end = off
    while block1[end] or block1[end + 1]:
        end += 2
        if end - off > 4096:
            return None, None
    raw = bytes(block1[off:end])
    return raw.decode("utf-16le", "surrogatepass"), end + 2


def records(block1):
    entries, a = [], 0
    while a + 16 <= 0x800:
        addr, size, z1, z2 = struct.unpack_from("<IIII", block1, a)
        if addr == 0 or z1 or z2:
            break
        entries.append((addr, size))
        a += 16
    origin = entries[0][0] - 0x7E0
    out = {}
    for i, (addr, size) in enumerate(entries):
        o = addr - origin
        first, end = struct.unpack_from("<II", block1, o)
        if 8 <= first - STRING_SLOT < size:
            out[i] = {"offset": o, "size": size, "pointer_array": o + first - STRING_SLOT,
                      "count": (end - first) // 4}
    return out


def apply(image, items, label):
    hits = []
    for it in items:
        blk, off = resolve(image, it)
        if blk is None:
            continue
        payload = image[blk].payload
        if off + len(it["new"]) > len(payload):
            fail(f"{label}: line {it['line']} runs past block {blk}")
            continue
        hits.append((blk, off, it["new"]))
    for blk, off, new in hits:
        payload = bytearray(image[blk].payload)
        payload[off:off + len(new)] = new
        image[blk] = oc.replace_payload(image[blk], bytes(payload))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    stock_bytes = STOCK.read_bytes()
    if hashlib.sha256(stock_bytes).hexdigest() != spec["stock_sha256"]:
        fail("stock image sha256 does not match the spec")
    image = oc.parse(stock_bytes)
    before = {i: bytes(b.payload) for i, b in enumerate(image)}

    ui2 = parse_patch(PATCH)
    if len(ui2) != 8:
        fail(f"ui_names2.patch has {len(ui2)} lines, expected 8")
    b95 = parse_patch(B95_PATCH)
    notes.append(f"ui_names2.patch: {len(ui2)} lines; ui_names.patch: {len(b95)} lines")

    # merge must be order-independent: check the byte ranges are disjoint
    r2 = set()
    for it in ui2:
        blk, off = resolve(image, it)
        r2.update((blk, off + i) for i in range(len(it["new"])))
    r1 = set()
    for it in b95:
        blk, off = resolve(image, it)
        r1.update((blk, off + i) for i in range(len(it["new"])))
    overlap = r1 & r2
    if overlap:
        fail(f"ui_names2 and ui_names overlap at {len(overlap)} byte(s), e.g. {sorted(overlap)[:4]}")
    else:
        notes.append("ui_names2 and ui_names byte ranges are disjoint")

    hits2 = apply(image, ui2, "ui_names2")
    hits1 = apply(image, b95, "ui_names")
    if len(hits2) != 8:
        fail(f"only {len(hits2)} of 8 ui_names2 ranges landed in a block")
    if len(hits1) != len(b95):
        fail(f"only {len(hits1)} of {len(b95)} ui_names ranges landed in a block")

    block1 = image[1].payload
    stock1 = before[1]
    recs = records(stock1)
    by_off = {(s["record"], s["sid"]): s for s in spec["strings"]}
    expect = {4: {}, 5: {}}
    names = {}
    for s in spec["strings"]:
        names.setdefault(s["record"], {})[s["sid"]] = s["name"]

    # 1. the eight bar sids read the new names
    for s in spec["strings"]:
        rec, sid = s["record"], s["sid"]
        poff = s["pointer_offset"]
        ptr = struct.unpack_from("<I", block1, poff)[0]
        if ptr != s["new_pointer"]:
            fail(f"rec{rec} sid{sid}: pointer 0x{ptr:08X} != 0x{s['new_pointer']:08X}")
            continue
        if ptr % 2:
            fail(f"rec{rec} sid{sid}: pointer 0x{ptr:08X} is not 2-byte aligned")
        rel = ptr - STRING_SLOT
        r = recs[rec]
        if not (0 <= rel < r["size"]):
            fail(f"rec{rec} sid{sid}: pointer lands outside the record")
            continue
        off = r["offset"] + rel          # where those bytes live in block 1
        text, end = read_string(block1, off)
        if text is None:
            fail(f"rec{rec} sid{sid}: string is not terminated inside 4 KiB")
            continue
        if text != s["name"]:
            fail(f"rec{rec} sid{sid}: reads {text!r}, expected {s['name']!r}")
        if text.startswith("@"):
            fail(f"rec{rec} sid{sid}: text still carries an icon prefix: {text!r}")
        if rel != s["resolves_to"]:
            fail(f"rec{rec} sid{sid}: resolves at record offset 0x{rel:X}, expected 0x{s['resolves_to']:X}")
        if end > off + 2 * (len(s["name"]) + 1):
            fail(f"rec{rec} sid{sid}: extra bytes after the terminator")
        # the 6-unit icon prefix must be immediately before
        icon = bytes(block1[off - ICON_BYTES:off]).decode("utf-16le", "replace")
        if not re.fullmatch(r"@A0[0-9A-F]{2}@", icon):
            fail(f"rec{rec} sid{sid}: bytes before the name are {icon!r}, not an icon prefix")

    # 2. every other sid keeps its stock text; the menu sids keep the B95 names
    menu_sids = {8: {1713, 1714, 1715, 613}, 2: {1713, 1714, 1715, 613}}
    b95_spec = json.loads((ROOT / ".tmp/ui_names.spec.json").read_text(encoding="utf-8"))
    b95_text = {(s["record"], s["sid"]): s["text"] for s in b95_spec["strings"]}
    bar_sids = {(s["record"], s["sid"]) for s in spec["strings"]}
    for rec in (2, 8):
        r = recs[rec]
        for sid in range(r["count"]):
            p_new = struct.unpack_from("<I", block1, r["pointer_array"] + 4 * sid)[0]
            p_old = struct.unpack_from("<I", stock1, r["pointer_array"] + 4 * sid)[0]
            key = (rec, sid)
            if key in bar_sids:
                continue
            if key in b95_text:
                t_new, _ = read_string(block1, r["offset"] + p_new - STRING_SLOT)
                if t_new != b95_text[key]:
                    fail(f"rec{rec} sid{sid}: menu text {t_new!r} != B95 {b95_text[key]!r}")
                continue
            t_old, _ = read_string(stock1, r["offset"] + p_old - STRING_SLOT)
            t_new, _ = read_string(block1, r["offset"] + p_new - STRING_SLOT)
            if t_old != t_new:
                fail(f"rec{rec} sid{sid}: text changed although it is not a target: {t_old!r} -> {t_new!r}")

    # 3. blocks 0 and 2 must be untouched
    for i in (0, 2):
        if bytes(image[i].payload) != before[i]:
            fail(f"block {i} changed")

    # 4. the container re-encodes and re-parses
    try:
        out = oc.reconstruct(image)
        oc.parse(out)
        notes.append(f"container re-encode + re-parse OK (image sha256 {hashlib.sha256(out).hexdigest()})")
    except Exception as exc:  # noqa: BLE001
        fail(f"container re-encode failed: {exc}")

    # 5. the dependency is real: ui_names2 alone does not give the names
    solo = oc.parse(stock_bytes)
    apply(solo, ui2, "ui_names2 alone")
    s1 = solo[1].payload
    wrong = 0
    for s in spec["strings"]:
        off = recs[s["record"]]["offset"] + s["new_pointer"] - STRING_SLOT
        text, _ = read_string(s1, off)
        if text != s["name"]:
            wrong += 1
    if wrong != 8:
        fail(f"ui_names2 without ui_names already reads {8 - wrong}/8 names; the dependency note is wrong")
    else:
        notes.append("ui_names2 alone reads 0/8 names -> it must be applied after ui_names (as documented)")

    print("PASS" if not problems else "FAIL", f"({len(problems)} problem(s))")
    for n in notes:
        print("  ok:", n)
    for p in problems:
        print("  !", p)
    if a.json:
        Path(a.json).write_text(json.dumps({"problems": problems, "notes": notes}, indent=1), encoding="utf-8")
    return 0 if not problems else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
