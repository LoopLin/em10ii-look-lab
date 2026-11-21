#!/usr/bin/env python3
"""Build the SCP (quick-select bar) half of the picture-mode rename: .tmp/ui_names2.patch.

Why a second patch
------------------
The menu (docs/firmware/beta/B95-ui-names.md) reads the four picture-mode names from
sids 1713/1714/1715/613 ('@A094@Vivid', '@A095@Natural', '@A096@Portrait', '@A024@Muted').
The live-view quick-select bar (SCP -> picture-mode cell -> OK) reads them from a
*different* family of plain strings: sids 1657 (Vivid), 1658 (Natural), 183 (Muted),
1661 (Portrait) of the same two language records.  B95 rewrote only the menu family, so
the bar kept the stock names.

How the bar gets the new names without using any new bytes
----------------------------------------------------------
B95 already stores each new name inside the record as "@A0xx@" + name.  The bar's sids
only need the *name*, and the bar draws its own icon, so each of the eight pointers
(two records x four sids) is repointed to the B95 string's first character after the
6-unit icon prefix:  new_pointer = string_slot + b95_string_offset + 12.

That is eight u32 words and no other byte, so this patch has no free-space requirement
and cannot collide with B95: it is a pointer-only patch and DEPENDS on B95 being applied
first (a pointer into the middle of the stock string would read the old text).  The
dependency is asserted here, in tools/verify_ui_scp.py, and in the doc.

Usage: python tools/build_ui_names2.py
  -> .tmp/ui_names2.patch       mkpatch 'ram' lines
  -> .tmp/ui_names2.omemu.json  om-emu overlay (apply after .tmp/ui_names.omemu.json)
  -> .tmp/ui_names2.spec.json   machine-readable spec audited by tools/verify_ui_scp.py
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
B95_SPEC = ROOT / ".tmp/ui_names.spec.json"
B95_PATCH = ROOT / ".tmp/ui_names.patch"
OUT_PATCH = ROOT / ".tmp/ui_names2.patch"
OUT_OMEMU = ROOT / ".tmp/ui_names2.omemu.json"
OUT_SPEC = ROOT / ".tmp/ui_names2.spec.json"

STRING_SLOT = 0xA31A2C00
RAM_BASE_BLOCK1 = 0x61481020
ICON_UNITS = 6  # "@A094@" and friends: six UTF-16 code units

# The bar's sids -> the B95 name they must show.  Measured on the emulator with marker
# strings: sid 1657 -> mode 1, 1658 -> mode 3, 183 -> mode 4, 1661 -> mode 5.
TARGETS = {
    8: [(1657, "Ektachrome 100 VS"), (1658, "Elite Color 200"),
        (183, "Classic Chrome"), (1661, "Pro Neg Hi")],
    2: [(1657, "Ektachrome 100 VS"), (1658, "Elite Color 200"),
        (183, "Classic Chrome"), (1661, "Pro Neg Hi")],
}


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




def main():
    stock = oc.parse(STOCK.read_bytes())
    block1 = stock[1].payload
    recs = records(block1)
    b95 = json.loads(B95_SPEC.read_text(encoding="utf-8"))
    b95_off = {(s["record"], s["name"]): s["offset"] for s in b95["strings"]}

    lines = ["# B96 SCP / quick-select bar names: 8 pointer words only (needs .tmp/ui_names.patch first)",
             "# sid: 1657 Vivid, 1658 Natural, 183 Muted, 1661 Portrait  (records 8=enu, 2=chs)",
             "# new pointer = STRING_SLOT + b95 string offset + 12 (skip the 6-unit icon prefix)"]
    omemu, spec_strings = [], []
    for rec, targets in TARGETS.items():
        r = recs[rec]
        for sid, name in targets:
            src = b95_off[(rec, name)]
            want_text = next(s["text"] for s in b95["strings"]
                             if s["record"] == rec and s["name"] == name)
            assert want_text.endswith(name), (want_text, name)
            poff = r["pointer_array"] + 4 * sid
            old = struct.unpack_from("<I", block1, poff)[0]
            rel = src - r["offset"] + 2 * ICON_UNITS   # pointers are record-relative
            assert 0 <= rel < r["size"], (rec, sid, hex(rel))
            new = STRING_SLOT + rel
            ram = RAM_BASE_BLOCK1 + poff
            hexs = struct.pack("<I", new).hex().upper()
            lines.append(f"ram 0x{ram:08X}={hexs}  # rec{rec} sid {sid} {want_text.split('@')[-1]} -> +12 B")
            omemu.append({"block": 1, "offset": poff,
                          "old": base64.b64encode(struct.pack("<I", old)).decode(),
                          "new": base64.b64encode(struct.pack("<I", new)).decode(),
                          "label": f"SCP rec{rec} sid{sid}"})
            spec_strings.append({
                "record": rec, "sid": sid, "name": name,
                "b95_offset": src, "b95_text": want_text,
                "pointer_offset": poff, "ram": ram,
                "old_pointer": old, "new_pointer": new,
                "resolves_to": rel,
            })
            print(f"rec{rec} sid{sid:4d} 0x{poff:X} old 0x{old:08X} -> 0x{new:08X}  ({name})")
    OUT_PATCH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_OMEMU.write_text(json.dumps({"patches": omemu}, indent=1), encoding="utf-8")
    spec = {
        "name": "B96-ui-scp-names",
        "stock": "stock/OLY_E_120_1400_0000_0000.BIN",
        "stock_sha256": hashlib.sha256(STOCK.read_bytes()).hexdigest(),
        "block1_sha256": hashlib.sha256(block1).hexdigest(),
        "depends_on": {"patch": ".tmp/ui_names.patch",
                       "patch_sha256": hashlib.sha256(B95_PATCH.read_bytes()).hexdigest()},
        "string_slot": hex(STRING_SLOT),
        "ram_base_block1": hex(RAM_BASE_BLOCK1),
        "patch_sha256": hashlib.sha256(OUT_PATCH.read_bytes()).hexdigest(),
        "omemu_sha256": hashlib.sha256(OUT_OMEMU.read_bytes()).hexdigest(),
        "strings": spec_strings,
        "note": "eight u32 pointer words; no string bytes written, no new space used",
    }
    OUT_SPEC.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    print(f"patch {OUT_PATCH} sha256 {spec['patch_sha256']}")
    print(f"omemu {OUT_OMEMU} sha256 {spec['omemu_sha256']}")


if __name__ == "__main__":
    main()
