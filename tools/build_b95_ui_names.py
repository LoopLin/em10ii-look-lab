#!/usr/bin/env python3
"""Build the B95 UI-name patch (4 picture-mode names x 2 languages) as a byte-exact spec.

This tool writes patch specs only: no firmware image, no card. The patches are
plain byte ranges applied offline by tools/mkpatch.py (or by om-emu, which takes
the same ranges as base64 overlays).

Where the strings live
----------------------
Each language table is a record in block 1: {u32 first, u32 end} then a pointer
array (2667 x u32, pointer = STRING_SLOT + record-relative offset) then a packed
string pool. The pool has no slack, so a name that no longer fits its old slot is
written elsewhere inside the same record and its sid's pointer is repointed.
Two dead/zero areas are used, and nothing else:

  span  the bytes of the three contiguous old slots (Vivid/Natural/Portrait,
        41 code units = 82 B). All three sids are repointed away, so the span is
        dead and becomes free space; the two names placed here fill it exactly.
  tail  the zero area after the pointer array (96 B in rec8, 116 B in rec2).

Every byte written is inside one of those two areas or is one of the 8 pointer
words, and both areas are asserted to be what this doc claims before writing.

Usage: python tools/build_b95_ui_names.py
  -> .tmp/ui_names.patch       mkpatch 'ram' lines (as requested)
  -> .tmp/ui_names.blk.patch   the same edits as 'blk 1:0xOFF' lines (decoded form)
  -> .tmp/ui_names.omemu.json  om-emu overlay {block, offset, old, new(base64)}
  -> .tmp/ui_names.spec.json   the machine-readable spec the verifier audits
"""
import base64
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oly_container as oc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STOCK = ROOT / "stock/OLY_E_120_1400_0000_0000.BIN"
OUT_PATCH = ROOT / ".tmp/ui_names.patch"
OUT_BLK = ROOT / ".tmp/ui_names.blk.patch"
OUT_OMEMU = ROOT / ".tmp/ui_names.omemu.json"
OUT_SPEC = ROOT / ".tmp/ui_names.spec.json"

STRING_SLOT = 0xA31A2C00
BASE = 0x60021020  # mkpatch analysis base: container offset = 32 + (ram - BASE)

# record -> plan.  Each entry: (sid, icon prefix, name, area) written in this order.
# Spans/tails are asserted below (old bytes / zero bytes), not assumed blind.
RECORDS = {
    8: {  # enut, the English table the menu reads
        "span": (0x80677E, 0x8067D0),   # Vivid(12)+Natural(14)+Portrait(15) code units
        "tail": (0x816980, 0x8169E0),
        "plan": [
            (1713, "@A094@", "Ektachrome 100 VS", "span"),  # was Vivid
            (1715, "@A096@", "Pro Neg Hi", "span"),         # was Portrait
            (1714, "@A095@", "Elite Color 200", "tail"),    # was Natural
            (613,  "@A024@", "Classic Chrome", "tail"),     # was Muted
        ],
    },
    2: {  # chst, simplified Chinese table (its four entries are English in stock too)
        "span": (0x749B8C, 0x749BDE),
        "tail": (0x75296C, 0x7529E0),
        "plan": [
            (1713, "@A094@", "Ektachrome 100 VS", "span"),
            (1715, "@A096@", "Pro Neg Hi", "span"),
            (1714, "@A095@", "Elite Color 200", "tail"),
            (613,  "@A024@", "Classic Chrome", "tail"),
        ],
    },
}


def records(block1):
    """{rec: (decoded offset, size, pool start, pointer count, pointer-array start)} from the block index."""
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
        out[i] = (o, size, first - STRING_SLOT, (end - first) // 4, o + first - STRING_SLOT)
    return out


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def raw_string(block1, off):
    end = off
    while block1[end] or block1[end + 1]:
        end += 2
    return bytes(block1[off:end + 2])


def main():
    data = STOCK.read_bytes()
    stock_sha = hashlib.sha256(data).hexdigest()
    blocks = oc.parse(data)
    b1 = blocks[1]
    block1 = b1.payload
    ram_base = BASE + b1.offset
    tabs = records(block1)

    sites, pointers, strings = [], [], []
    for rec, spec in RECORDS.items():
        o, size, pool, count, ptbl = tabs[rec]
        span_start, span_end = spec["span"]
        tail_start, tail_end = spec["tail"]

        # --- assert the two areas are what the plan claims -------------------
        span = bytes(block1[span_start:span_end])
        expected_span = b"".join(raw_string(block1, o + u32(block1, ptbl + 4 * sid) - STRING_SLOT)
                                 for sid in (1713, 1714, 1715))
        assert span == expected_span, f"rec{rec}: span is not exactly the three old slots"
        tail = bytes(block1[tail_start:tail_end])
        assert not any(tail), f"rec{rec}: tail bytes are not all zero"
        assert tail_start >= ptbl + 4 * count, f"rec{rec}: tail overlaps the pointer array"
        assert tail_end <= o + size, f"rec{rec}: tail passes the record end"

        cur = {"span": span_start, "tail": tail_start}
        limit = {"span": span_end, "tail": tail_end}
        for sid, icon, name, area in spec["plan"]:
            text = icon + name
            raw = (text + "\0").encode("utf-16le")
            off = cur[area]
            assert off + len(raw) <= limit[area], f"rec{rec} {name!r} ({len(raw)} B) overflows the {area}"
            sites.append({"block": 1, "offset": off, "old": bytes(block1[off:off + len(raw)]).hex(),
                          "new": raw.hex(), "label": f"rec{rec} sid{sid} {name!r} ({area})"})
            ptr_off = ptbl + 4 * sid
            old_p = u32(block1, ptr_off)
            new_p = STRING_SLOT + (off - o)
            pointers.append({"block": 1, "record": rec, "sid": sid, "offset": ptr_off,
                             "old": old_p, "new": new_p, "text": text, "area": area,
                             "write": old_p != new_p})
            strings.append({"record": rec, "sid": sid, "text": text, "name": name, "area": area,
                            "offset": off, "units": len(text) + 1, "bytes": len(raw)})
            cur[area] += len(raw)

    # --- self checks -------------------------------------------------------
    writes = [p for p in pointers if p["write"]]
    assert len(sites) == 8 and len(writes) == 6, (len(sites), len(writes))
    ranges = sorted((s["offset"], s["offset"] + len(s["new"]) // 2) for s in sites)
    for (a, b), (c, d) in zip(ranges, ranges[1:]):
        assert b <= c, f"string ranges overlap: {a:#x}..{b:#x} vs {c:#x}"
    assert not ({p["offset"] for p in writes} & {a for a, _ in ranges})
    for p in writes:
        assert 0 <= p["new"] - STRING_SLOT < tabs[p["record"]][1]

    # --- simulate the patch on a copy and audit the result -----------------
    after = bytearray(block1)
    for site in sites:
        raw = bytes.fromhex(site["new"])
        after[site["offset"]:site["offset"] + len(raw)] = raw
    for p in pointers:
        if p["write"]:
            struct.pack_into("<I", after, p["offset"], p["new"])
    for s in strings:
        off = u32(after, tabs[s["record"]][4] + 4 * s["sid"]) - STRING_SLOT
        got = raw_string(after, tabs[s["record"]][0] + off)
        assert got == (s["text"] + "\0").encode("utf-16le"), (s["text"], got)
    target = {(s["record"], s["sid"]) for s in strings}
    changed_sids = set()
    for rec in RECORDS:
        o, size, pool, count, ptbl = tabs[rec]
        for sid in range(count):
            old_txt = raw_string(block1, o + u32(block1, ptbl + 4 * sid) - STRING_SLOT)
            new_txt = raw_string(after, o + u32(after, ptbl + 4 * sid) - STRING_SLOT)
            if (rec, sid) in target:
                changed_sids.add((rec, sid))
            elif old_txt != new_txt:
                raise SystemExit(f"collateral: rec{rec} sid{sid} text changed")
    assert changed_sids == target, (changed_sids ^ target)

    # --- emit --------------------------------------------------------------
    patch = [
        "# B95 UI names: the four picture-mode names, English (rec8/enut) and Chinese (rec2/chst)",
        "# tables of block 1, plus their 8 string-pointer words. Nothing else is touched.",
        f"# target image: {STOCK.relative_to(ROOT).as_posix()}  sha256 {stock_sha}",
        "# applies to stock, or on top of the look package (.tmp/b90.patch): the two touch",
        "# different blocks (0 vs 1) and no shared address; mkpatch accepts both files together.",
        "# A resource block has no RAM mapping of its own, so each 'ram' address here is",
        "# mkpatch's container mapping: ram = 0x60021020 + (container payload offset).",
        "# The decoded block-1 offset is in every comment; .tmp/ui_names.blk.patch has the same",
        "# edits as 'blk 1:0xOFF' lines for tools that address the decoded block directly.",
        "",
    ]
    blk = ["# B95 UI names, decoded form (blk 1:0xOFF); byte-identical edits to .tmp/ui_names.patch", ""]
    for s in sites:
        patch.append(f"ram 0x{ram_base + s['offset']:X}={s['new']}  # {s['label']} @ blk1 0x{s['offset']:X}, {len(s['new']) // 2} B")
        blk.append(f"blk 1:0x{s['offset']:X}={s['new']}  # {s['label']}, {len(s['new']) // 2} B")
    for p in writes:
        new = struct.pack("<I", p["new"]).hex()
        patch.append(f"ram 0x{ram_base + p['offset']:X}={new}  # rec{p['record']} sid{p['sid']} pointer "
                     f"0x{p['old']:08X} -> 0x{p['new']:08X} ({p['text']!r})")
        blk.append(f"blk 1:0x{p['offset']:X}={new}  # rec{p['record']} sid{p['sid']} pointer "
                   f"0x{p['old']:08X} -> 0x{p['new']:08X} ({p['text']!r})")
    for p in pointers:
        if not p["write"]:
            patch.append(f"# rec{p['record']} sid{p['sid']} pointer unchanged at 0x{p['old']:08X} "
                         f"(the new string reuses its old slot)")
            blk.append(f"# rec{p['record']} sid{p['sid']} pointer unchanged at 0x{p['old']:08X}")
    OUT_PATCH.write_text("\n".join(patch) + "\n", encoding="utf-8", newline="\n")
    OUT_BLK.write_text("\n".join(blk) + "\n", encoding="utf-8", newline="\n")

    overlays = []
    for s in sites:
        overlays.append({"block": 1, "offset": s["offset"],
                         "old": base64.b64encode(bytes.fromhex(s["old"])).decode(),
                         "new": base64.b64encode(bytes.fromhex(s["new"])).decode(),
                         "label": s["label"]})
    for p in writes:
        overlays.append({"block": 1, "offset": p["offset"],
                         "old": base64.b64encode(struct.pack("<I", p["old"])).decode(),
                         "new": base64.b64encode(struct.pack("<I", p["new"])).decode(),
                         "label": f"rec{p['record']} sid{p['sid']} pointer"})
    OUT_OMEMU.write_text(json.dumps({"patches": overlays}, indent=2) + "\n", encoding="utf-8", newline="\n")

    spec_out = {
        "name": "B95-ui-names",
        "stock": STOCK.relative_to(ROOT).as_posix(),
        "stock_sha256": stock_sha,
        "block1_sha256": hashlib.sha256(block1).hexdigest(),
        "patch_sha256": hashlib.sha256(OUT_PATCH.read_bytes()).hexdigest(),
        "blk_patch_sha256": hashlib.sha256(OUT_BLK.read_bytes()).hexdigest(),
        "omemu_sha256": hashlib.sha256(OUT_OMEMU.read_bytes()).hexdigest(),
        "ram_base_block1": f"0x{ram_base:X}",
        "string_slot": f"0x{STRING_SLOT:X}",
        "records": {str(rec): {"offset": f"0x{tabs[rec][0]:X}", "size": f"0x{tabs[rec][1]:X}",
                               "pointer_array": f"0x{tabs[rec][4]:X}", "span": [f"0x{v:X}" for v in RECORDS[rec]["span"]],
                               "tail": [f"0x{v:X}" for v in RECORDS[rec]["tail"]]} for rec in RECORDS},
        "strings": strings,
        "pointers": pointers,
        "sites": sites,
        "changed_bytes": sum(len(bytes.fromhex(s["new"])) for s in sites) + 4 * len(writes),
        "pointer_words_written": len(writes),
        "note": "all edits inside block 1 records 8 (enut) and 2 (chst); block 0 and block 2 untouched",
    }
    OUT_SPEC.write_text(json.dumps(spec_out, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: spec_out[k] for k in ("stock_sha256", "block1_sha256", "patch_sha256",
                                               "omemu_sha256", "changed_bytes")}, indent=1))
    for s in strings:
        print(f"  rec{s['record']} sid{s['sid']:5d} 0x{s['offset']:08X} {s['bytes']:3d} B {s['area']:5s} {s['text']!r}")
    for p in pointers:
        print(f"  rec{p['record']} sid{p['sid']:5d} ptbl 0x{p['offset']:08X} 0x{p['old']:08X} -> 0x{p['new']:08X}"
              f"{'' if p['write'] else '  (unchanged)'}")


if __name__ == "__main__":
    main()
