#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_m1.py -- independent verifier for build/M1_sdprobe.BIN.

It only trusts bytes: it re-parses the container, re-computes the diff against
build/RELEASE_v1.BIN, and checks every claim the M1 change list makes.

Checks
  1. container size / 3 blocks / additive checksum / header+trailer
  2. blocks 1 and 2 byte-identical to RELEASE_v1
  3. block 0 differs from RELEASE_v1 only inside the two declared ranges
     (5-byte SU hook, stub+literals inside the registered free-FF pocket)
  4. hook is `DC disp32` and resolves to the stub
  5. every recorded call is `DD disp32 mask frame` with the right target
  6. every jump recorded in sim.json resolves to 0x605DECD4
  7. every literal is byte-exact (paths, "rb", "wb", "OUT.BIN", "EM10M1")
  8. tone / matrix / chroma / existing stubs / other hooks are untouched
  9. guard+buffer+scratch bytes are still FF in the file (runtime-only writes)
 10. sim.json carries the matching sha256 and closed=True

Exit code 0 = all checks pass, 1 = at least one failure.
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import oly_container as oc  # noqa: E402

BASE = 0x60021020
BASE_BIN = ROOT / "build" / "RELEASE_v1.BIN"
M1_BIN = ROOT / "build" / "M1_sdprobe.BIN"
SIM = ROOT / "build" / "M1_sdprobe.sim.json"
BASE_SHA256 = "0b08a232cee544c5724e8ed661fcad97d75d1e9f9886229c30ea01d2280cafad"

HOOK = 0x60479A2B
HOOK_OLD = bytes.fromhex("dc a9 52 16 00")
CHAIN = 0x605DECD4
CODE = 0x60672C58
GUARD = 0x60672C20
POCKET_LO = 0x60672C20
POCKET_HI = 0x60672FF6
EXIF = 0x61894CA0

errors: list[str] = []
checks = 0


def chk(cond: bool, msg: str) -> bool:
    global checks
    checks += 1
    if not cond:
        errors.append(msg)
    return bool(cond)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(path: Path):
    data = path.read_bytes()
    return data, oc.parse(data)


def same(payload: bytes, base: bytes, lo: int, hi: int, name: str) -> None:
    a = payload[lo - BASE:hi - BASE]
    b = base[lo - BASE:hi - BASE]
    chk(a == b, "%s @ %#x..%#x changed" % (name, lo, hi))


def diff_runs(payload: bytes, base: bytes):
    out = []
    i, n = 0, min(len(payload), len(base))
    while i < n:
        if payload[i] != base[i]:
            j = i
            while j < n and payload[j] != base[j]:
                j += 1
            out.append((BASE + i, j - i))
            i = j
        else:
            i += 1
    return out


def main() -> int:
    if not BASE_BIN.exists() or not M1_BIN.exists() or not SIM.exists():
        print("missing baseline, M1 binary or sim.json")
        return 1

    base_raw, base_blocks = load(BASE_BIN)
    m1_raw, m1_blocks = load(M1_BIN)
    sim = json.loads(SIM.read_text(encoding="utf-8"))

    chk(len(base_raw) == 69_599_232, "baseline size")
    chk(sha256(base_raw) == BASE_SHA256, "baseline sha256 != RELEASE_v1")
    chk(len(m1_raw) == 69_599_232, "M1 size != 69,599,232")
    chk(len(m1_blocks) == 3, "container does not parse into 3 blocks")
    chk(len(base_blocks) == 3, "baseline does not parse into 3 blocks")

    b0, bb0 = m1_blocks[0], base_blocks[0]
    payload, base = b0.payload, bb0.payload
    chk(len(payload) == len(base), "payload length changed")

    # container integrity
    for blk in m1_blocks:
        tot = (oc.sum32(blk.header) + oc.sum32(blk.payload) + oc.sum32(blk.trailer)) & 0xFFFFFFFF
        chk(tot == 0, "checksum != 0 at block %d" % blk.index)
    chk(b0.header == bb0.header, "block0 header changed")
    chk(b0.trailer[:28] == bb0.trailer[:28], "block0 trailer metadata changed")

    # blocks 1/2 untouched
    for i in (1, 2):
        chk(m1_blocks[i].payload == base_blocks[i].payload,
            "block %d payload changed" % i)
        chk(m1_blocks[i].header == base_blocks[i].header,
            "block %d header changed" % i)
        chk(m1_blocks[i].trailer == base_blocks[i].trailer,
            "block %d trailer changed" % i)

    # sim.json contract
    chk(sim.get("sha256") == sha256(m1_raw), "sim.json sha256 mismatch")
    chk(sim.get("closed") is True, "sim.json closed is not True")
    chk(sim.get("verifier") == "verify_m1.py", "sim.json verifier")
    chk(sim.get("base_sha256") == BASE_SHA256, "sim.json base_sha256")
    chk(isinstance(sim.get("stub"), dict), "sim.json stub metadata missing")
    stub_meta = sim.get("stub", {})
    stub_size = stub_meta.get("size", -1)
    code_end = CODE + stub_size
    chk(isinstance(stub_size, int) and 0 < stub_size, "bad stub size")
    chk(stub_meta.get("code") and int(stub_meta["code"], 16) == CODE, "stub code address")
    chk(int(stub_meta.get("end", "0"), 16) == code_end, "stub end address")
    chk(int(stub_meta.get("guard", "0"), 16) == GUARD, "stub guard")
    chk(int(stub_meta.get("chain", "0"), 16) == CHAIN, "stub chain")
    chk(int(stub_meta.get("exif", "0"), 16) == EXIF, "stub exif")
    chk(code_end <= POCKET_HI, "stub crosses pocket end")

    # declared ranges
    changes = sim.get("changes", [])
    chk(len(changes) == 2, "sim.json changes must have 2 entries")
    if len(changes) == 2:
        chk(int(changes[0][0], 16) == HOOK and changes[0][1] == 5, "hook range metadata")
        chk(int(changes[1][0], 16) == CODE and changes[1][1] == stub_size,
            "stub range metadata")

    # diff confinement
    runs = diff_runs(payload, base)
    allowed = [(HOOK, 5), (CODE, stub_size)]

    def inside(lo: int, n: int) -> bool:
        return any(lo >= a and lo + n <= a + ln for a, ln in allowed)

    bad = [(hex(a), n) for a, n in runs if not inside(a, n)]
    chk(not bad, "diff outside declared ranges: %s" % bad)
    chk(payload[HOOK - BASE:HOOK - BASE + 5] != HOOK_OLD, "hook jmp not changed")
    chk(any(a >= CODE and a + n <= code_end for a, n in runs), "stub range not changed")
    chk(base[HOOK - BASE:HOOK - BASE + 5] == HOOK_OLD, "baseline hook bytes unexpected")

    # hook -> stub
    hb = payload[HOOK - BASE:HOOK - BASE + 5]
    chk(hb[0] == 0xDC, "hook is not a jmp")
    if hb[0] == 0xDC:
        disp = struct.unpack_from("<i", hb, 1)[0]
        chk(HOOK + disp == CODE, "hook jmp target %#x != stub %#x"
            % (HOOK + disp, CODE))

    # stub head/tail and pocket cleanliness in the file
    chk(payload[POCKET_LO - BASE:CODE - BASE] == b"\xff" * (CODE - POCKET_LO),
        "guard/buffer/scratch area is not FF in the file")
    chk(code_end <= POCKET_HI, "stub end beyond pocket")
    chk(payload[code_end - BASE:POCKET_HI - BASE]
        == b"\xff" * (POCKET_HI - code_end), "pocket tail is not FF")
    chk(payload[CODE - BASE:CODE - BASE + 5] == bytes.fromhex("f8 fe fc 4f 00"),
        "stub prologue bytes unexpected")

    # every recorded jump resolves to the existing release stub
    jumps = stub_meta.get("jumps", [])
    chk(len(jumps) == 2, "sim.json must record 2 chain jumps")
    for hx in jumps:
        site = int(hx, 16)
        b = payload[site - BASE:site - BASE + 5]
        ok = b[0] == 0xDC
        if ok:
            ok = site + struct.unpack_from("<i", b, 1)[0] == CHAIN
        chk(ok, "jump at %#x does not resolve to %#x" % (site, CHAIN))

    # every recorded call: DD disp32 mask frame
    calls = stub_meta.get("calls", [])
    chk(len(calls) == 27, "sim.json must record 27 calls, got %d" % len(calls))
    for hx, thx, frame, mask in calls:
        site, target = int(hx, 16), int(thx, 16)
        b = payload[site - BASE:site - BASE + 7]
        ok = len(b) == 7 and b[0] == 0xDD
        if ok:
            ok = site + struct.unpack_from("<i", b, 1)[0] == target
        ok = ok and b[5] == mask and b[6] == frame
        chk(ok, "call at %#x -> %#x mask/frame wrong: %s"
            % (site, target, b.hex()))

    # semantic check: every call must land on a fileif API or a code helper,
    # never inside the string table after 0x60672F66 (that would execute data).
    fileif = {0x6056556D, 0x6056570C, 0x605649BB, 0x60564832, 0x605648AF}
    helpers = stub_meta.get("helpers", {})
    hexn = int(helpers["hexn"], 16)
    puts = int(helpers["puts"], 16)
    targets = [int(thx, 16) for _, thx, _, _ in calls]
    chk(all(t in fileif or t in (hexn, puts) for t in targets),
        "call target outside fileif/helpers: %s"
        % [hex(t) for t in sorted(set(targets) - fileif - {hexn, puts})])
    chk(targets.count(puts) == 6, "expected 6 call puts, got %d"
        % targets.count(puts))
    chk(targets.count(hexn) == 10, "expected 10 call hexn, got %d"
        % targets.count(hexn))

    # literals
    strings = stub_meta.get("strings", {})
    expected = {
        "PING.BIN": b"PING.BIN",
        "\\PING.BIN": b"\\PING.BIN",
        "A:\\PING.BIN": b"A:\\PING.BIN",
        "DCOLYMP\\EM10MOD\\PING.BIN": b"DCOLYMP\\EM10MOD\\PING.BIN",
        "A:\\DCOLYMP\\EM10MOD\\PING.BIN": b"A:\\DCOLYMP\\EM10MOD\\PING.BIN",
        "rb": b"rb",
        "OUT.BIN": b"OUT.BIN",
        "wb": b"wb",
        "EM10M1\\0\\0": b"EM10M1\0\0",
        "M1ms=": b"M1ms=",
        ",I=": b",I=",
        ",rd=": b",rd=",
        ",d=": b",d=",
        ",wr=": b",wr=",
        ",p=": b",p=",
    }
    chk(set(strings) == set(expected), "sim.json literal set mismatch")
    for name, want in expected.items():
        rec = strings.get(name)
        if not chk(isinstance(rec, list) and len(rec) == 2, "literal %r missing" % name):
            continue
        addr, ln = int(rec[0], 16), rec[1]
        got = payload[addr - BASE:addr - BASE + ln]
        chk(got == want, "literal %r @ %#x: %s != %s"
            % (name, addr, got.hex(), want.hex()))
        if name.endswith("BIN") or name in ("rb", "wb"):
            chk(payload[addr - BASE + ln] == 0, "literal %r not NUL terminated" % name)

    # untouched named regions
    untouched = [
        (0x605D8344, 0x605DEF44, "tone pool (54 tables)"),
        (0x605DEEE0, 0x605DEF28, "4 matrix tables"),
        (0x605DEB44, 0x605DECD4, "4 chroma blocks"),
        (0x605DECD4, 0x605DED18, "existing SU stub"),
        (0x605DED1C, 0x605DED42, "chroma selector stub"),
        (0x605DED44, 0x605DED57, "SD stub"),
        (0x601ADE04, 0x601ADE36, "MX stub"),
        (0x604B4CAD, 0x604B4CB3, "chroma hook"),
        (0x604B6AD6, 0x604B6ADB, "matrix hook"),
        (0x604B811D, 0x604B8123, "SD hook"),
        (0x604B9D1A, 0x604B9D1C, "single byte 1"),
        (0x604B9F1F, 0x604B9F20, "single byte 2"),
    ]
    for lo, hi, name in untouched:
        same(payload, base, lo, hi, name)

    # EXIF area is RAM above the payload; a file write there would be a bug.
    # 95 chars + NUL = 96 B, the exact budget from EXIF+0x10 to area end.
    chk(EXIF >= BASE + len(payload), "EXIF address is not above payload")
    chk(EXIF + 96 <= 0x61894D04, "probe text would overflow UserComment area")
    fmt = stub_meta.get("text_format", "")
    tlen = stub_meta.get("text_len")
    sample = fmt.replace("%016llX", "%016X") % (0xFFFF, "4", 0xFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFF,
                    0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    chk(tlen == 95 and len(sample) == 95,
        "text_format/text_len do not describe a 95-char field: %r %r" % (tlen, len(sample)))

    # report
    print("M1 bin   : %s" % M1_BIN)
    print("sha256   : %s" % sha256(m1_raw))
    print("stub     : %d B @ %#x .. %#x (pocket %#x..%#x)"
          % (stub_size, CODE, code_end, POCKET_LO, POCKET_HI))
    print("diff runs: %s" % ", ".join("%#x+%d" % r for r in runs))
    if errors:
        print("\nFAIL (%d/%d checks failed)" % (len(errors), checks))
        for e in errors:
            print("  - " + e)
        return 1
    print("\nPASS: %d checks" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
