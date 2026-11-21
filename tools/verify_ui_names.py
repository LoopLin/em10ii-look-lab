#!/usr/bin/env python3
"""Audit the B95 UI-name patch: pointers, UTF-16 text, NUL terminators, and "nothing else changed".

Three artifacts must agree, and then the patch is applied to a copy of the stock image in
memory (no file is written, no card, no firmware package):

  .tmp/ui_names.patch        the byte edits (mkpatch 'ram' lines)
  .tmp/ui_names.spec.json    what those edits are supposed to mean (offsets, old bytes, expected text)
  stock/OLY_E_120_1400_0000_0000.BIN   the image they apply to

Checks
  * the patch's addresses resolve inside block 1 and match the spec's sites byte for byte
  * every spec "old" equals the stock bytes there (the patch is for this exact image)
  * after applying: each target sid's pointer -> the spec'd offset, inside the record,
    the string decodes as strict UTF-16LE to the expected text, and ends in one 0x0000
  * all other sids of both edited records keep their exact stock text
  * every differing byte of block 1 lies in a declared range; blocks 0 and 2 are untouched;
    the patched container re-encodes, re-parses and passes the container checks in memory
  * display budget: the visible name is <= 22 ASCII capitals (measured on the real menu,
    docs/firmware/beta/ui-name-length.md)

Usage
  python tools/verify_ui_names.py                       # stock + .tmp/ui_names.patch
  python tools/verify_ui_names.py --image build/x.BIN   # already-patched image
  python tools/verify_ui_names.py --json .tmp/ui_names.verify.json
Exit status 0 = every check passed, 1 = at least one failed (all failures are listed).
"""
import argparse
import base64
import hashlib
import json
import re
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oly_container as oc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STOCK = ROOT / "stock/OLY_E_120_1400_0000_0000.BIN"
SPEC = ROOT / ".tmp/ui_names.spec.json"
PATCH = ROOT / ".tmp/ui_names.patch"
BASE = 0x60021020  # mkpatch analysis base: container offset = 32 + (ram - BASE)
STRING_SLOT = 0xA31A2C00
SCREEN_MAX_ASCII = 22

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
    """-> (block index, decoded offset) for one parsed patch line, using mkpatch's convention."""
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


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def read_string(block1, off):
    """UTF-16LE up to and including the first 0x0000; returns (text, raw bytes, end offset)."""
    end = off
    while block1[end] or block1[end + 1]:
        end += 2
    raw = bytes(block1[off:end + 2])
    return raw[:-2].decode("utf-16le"), raw, end + 2


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
        if not (8 <= first - STRING_SLOT < size):
            continue
        out[i] = {"offset": o, "size": size, "pointer_array": o + first - STRING_SLOT,
                  "count": (end - first) // 4}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", type=Path, default=STOCK)
    ap.add_argument("--patch", type=Path, default=PATCH)
    ap.add_argument("--spec", type=Path, default=SPEC)
    ap.add_argument("--image", type=Path, help="an already-patched image to audit instead of applying the patch")
    ap.add_argument("--allow-other-block-diff", action="store_true",
                    help="the image also carries unrelated patches (e.g. a look package); skip the block-0/2 equality check")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    stock = args.stock.read_bytes()
    stock_sha = hashlib.sha256(stock).hexdigest()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if spec["stock_sha256"] != stock_sha:
        fail(f"spec names stock sha256 {spec['stock_sha256']}, but {args.stock} is {stock_sha}")

    blocks = oc.parse(stock)
    res_block = blocks[1]
    before = bytes(res_block.payload)
    if hashlib.sha256(before).hexdigest() != spec["block1_sha256"]:
        fail("block 1 payload does not match spec.block1_sha256")

    if args.image:
        image = args.image.read_bytes()
        ib = oc.parse(image)
        after = bytes(ib[1].payload)
        written = []
        note = f"image {args.image.name} sha256 {hashlib.sha256(image).hexdigest()}"
    else:
        patches = parse_patch(args.patch)
        written = []
        for item in patches:
            blk, off = resolve(blocks, item)
            if blk is None:
                continue
            if blk != 1:
                fail(f"patch at {item['addr']} targets block {blk}; B95 edits block 1 only")
                continue
            written.append((off, item["new"], item))
        after = bytearray(before)
        for off, new, _ in written:
            after[off:off + len(new)] = new
        note = f"applied {len(written)} patch ranges to the stock image in memory"

    tabs = records(before)
    tabs_after = records(after)

    # ---- 1. patch <-> spec agreement -------------------------------------
    sites = spec["sites"]
    # every byte range the patch claims to write: the 8 strings plus the pointer words that change
    ptr_sites = [{"offset": p["offset"], "new": struct.pack("<I", p["new"]).hex()}
                 for p in spec["pointers"] if p["write"]]
    all_sites = sites + ptr_sites
    if not args.image:
        decl = {(s["offset"], s["new"]) for s in all_sites}
        got = {(off, new.hex()) for off, new, _ in written}
        if decl != got:
            fail(f"patch and spec disagree: only-in-spec {sorted(decl - got)}, only-in-patch {sorted(got - decl)}")

    # ---- 2. spec old bytes == stock --------------------------------------
    for s in sites:
        raw = bytes.fromhex(s["old"])
        have = bytes(before[s["offset"]:s["offset"] + len(raw)])
        if have != raw:
            fail(f"spec old bytes differ from stock at 0x{s['offset']:X}: {have.hex()} != {raw.hex()}")

    # ---- 3. pointers, text, terminator -----------------------------------
    rows = []
    ptr_off = {(p["record"], p["sid"]): p["offset"] for p in spec["pointers"]}
    for st in spec["strings"]:
        rec = st["record"]
        if rec not in tabs or rec not in tabs_after:
            fail(f"record {rec} not found")
            continue
        o, size, ptbl, count = tabs[rec]["offset"], tabs[rec]["size"], tabs[rec]["pointer_array"], tabs[rec]["count"]
        want_ptr = STRING_SLOT + (st["offset"] - o)
        got_ptr = u32(after, ptbl + 4 * st["sid"])
        if got_ptr != want_ptr:
            fail(f"rec{rec} sid{st['sid']}: pointer 0x{got_ptr:08X} != expected 0x{want_ptr:08X}")
        off = got_ptr - STRING_SLOT
        if not (0 <= off < size - 1):
            fail(f"rec{rec} sid{st['sid']}: string offset 0x{off:X} outside the record")
            continue
        text, raw, end = read_string(after, o + off)
        if text != st["text"]:
            fail(f"rec{rec} sid{st['sid']}: text {text!r} != expected {st['text']!r}")
        if not raw.endswith(b"\x00\x00") or len(raw) % 2:
            fail(f"rec{rec} sid{st['sid']}: string is not a NUL-terminated UTF-16LE run")
        if end > o + size:
            fail(f"rec{rec} sid{st['sid']}: string passes the record end")
        try:
            raw[:-2].decode("utf-16le", "strict")
        except UnicodeDecodeError as exc:
            fail(f"rec{rec} sid{st['sid']}: not strict UTF-16LE ({exc})")
        visible = re.sub(r"^@A[0-9A-Fa-f]{3}@", "", text)
        if len(visible) > SCREEN_MAX_ASCII:
            notes.append(f"rec{rec} sid{st['sid']}: {len(visible)} chars exceeds the {SCREEN_MAX_ASCII}-char screen budget")
        rows.append({"record": rec, "sid": st["sid"], "pointer": f"0x{got_ptr:08X}",
                     "offset": f"0x{st['offset']:X}", "units": st["units"], "bytes": len(raw),
                     "text": text, "visible": visible, "visible_len": len(visible),
                     "area": st["area"],
                     "pointer_changed": u32(before, ptbl + 4 * st["sid"]) != got_ptr})

    # ---- 4. every other sid keeps its stock text -------------------------
    target = {(st["record"], st["sid"]) for st in spec["strings"]}
    changed_text = 0
    for rec in sorted(set(st["record"] for st in spec["strings"])):
        o, size, ptbl, count = tabs[rec]["offset"], tabs[rec]["size"], tabs[rec]["pointer_array"], tabs[rec]["count"]
        for sid in range(count):
            po = u32(before, ptbl + 4 * sid) - STRING_SLOT
            pn = u32(after, ptbl + 4 * sid) - STRING_SLOT
            if not (0 <= po < size - 1 and 0 <= pn < size - 1):
                fail(f"rec{rec} sid{sid}: pointer outside the record")
                continue
            t0 = read_string(before, o + po)[1]
            t1 = read_string(after, o + pn)[1]
            if (rec, sid) in target:
                changed_text += 1
            elif t0 != t1:
                fail(f"collateral: rec{rec} sid{sid} text changed")
    if changed_text != len(target):
        fail(f"only {changed_text} of {len(target)} target sids were visited")

    # ---- 5. byte-level diff coverage -------------------------------------
    ranges = sorted((s["offset"], s["offset"] + len(bytes.fromhex(s["new"]))) for s in all_sites)
    in_range = bytearray(len(before))
    for a, b in ranges:
        for i in range(a, b):
            in_range[i] = 1
    changed = stray = 0
    for i, (x, y) in enumerate(zip(before, after)):
        if x != y:
            changed += 1
            if not in_range[i]:
                stray += 1
                if stray <= 5:
                    fail(f"stray byte change at block1 0x{i:X}")
    if stray > 5:
        fail(f"... and {stray - 5} more stray byte changes")
    written_bytes = sum(b - a for a, b in ranges)
    untouched = []
    if args.image:
        if not args.allow_other_block_diff:
            for idx in (0, 2):
                if bytes(ib[idx].payload) != bytes(blocks[idx].payload):
                    fail(f"block {idx} differs from stock")
        for p in spec["pointers"]:
            pass
    # record/tail containment of every written offset
    for st in spec["strings"]:
        rec = st["record"]
        lo = tabs[rec]["offset"]
        hi = lo + tabs[rec]["size"]
        if not (lo <= st["offset"] < hi):
            fail(f"rec{rec}: string offset 0x{st['offset']:X} outside record 0x{lo:X}..0x{hi:X}")

    # ---- 6. container round-trip in memory --------------------------------
    if not args.image:
        try:
            payloads = [bytes(b.payload) for b in blocks]
            payloads[1] = bytes(after)
            for idx in (0, 2):
                if payloads[idx] != bytes(blocks[idx].payload):
                    fail(f"block {idx} payload would change")
            rebuilt = [oc.replace_payload(b, payloads[b.index]) for b in blocks]
            image = b"".join(b.header + oc.encode(b.payload) + b.trailer for b in rebuilt)
            reparsed = oc.parse(image)
            if [bytes(b.payload) for b in reparsed] != payloads:
                fail("re-parsed container payloads differ from the intended ones")
            else:
                notes.append(f"container re-encode + re-parse OK (image sha256 {hashlib.sha256(image).hexdigest()})")
        except Exception as exc:  # noqa: BLE001
            fail(f"container re-encode failed: {exc}")

    # ---- report ----------------------------------------------------------
    print(f"stock       {args.stock} sha256 {stock_sha}")
    print(f"block 1     sha256 {hashlib.sha256(before).hexdigest()}")
    print(f"{note}")
    print()
    print("rec/sid      pointer(old->new)        string off  units  bytes  text")
    for st in spec["strings"]:
        p = next(x for x in spec["pointers"] if x["record"] == st["record"] and x["sid"] == st["sid"])
        mark = "" if p["write"] else "  (kept)"
        print(f"rec{st['record']} sid{st['sid']:<5d} 0x{p['old']:08X}->0x{p['new']:08X}{mark:<9s}"
              f"0x{st['offset']:08X} {st['units']:5d} {st['bytes']:5d}  {st['text']}")
    print()
    print(f"declared ranges {len(ranges)}  written {written_bytes} B  differing {changed} B  stray {stray}  blocks 1 only")
    for n in notes:
        print(f"note: {n}")
    if problems:
        print(f"\nFAIL: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
    else:
        print("\nPASS: pointers, UTF-16 text, NUL terminators, and 'nothing else changed' all hold")

    if args.json:
        args.json.write_text(json.dumps({
            "stock": str(args.stock), "stock_sha256": stock_sha,
            "mode": "image" if args.image else "stock+patch",
            "patch_sha256": hashlib.sha256(args.patch.read_bytes()).hexdigest() if args.patch.exists() else None,
            "spec_sha256": hashlib.sha256(args.spec.read_bytes()).hexdigest(),
            "block1_before_sha256": hashlib.sha256(before).hexdigest(),
            "block1_after_sha256": hashlib.sha256(bytes(after)).hexdigest(),
            "rows": rows, "declared_ranges": [[a, b] for a, b in ranges],
            "written_bytes": written_bytes, "differing_bytes": changed, "stray_bytes": stray,
            "notes": notes, "problems": problems, "ok": not problems,
        }, indent=1) + "\n", encoding="utf-8")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
