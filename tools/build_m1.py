#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_m1.py -- M1 SD/fileif probe pack.

Baseline : build/RELEASE_v1.BIN (sha256 0b08a232...)
Output   : build/M1_sdprobe.BIN
           build/M1_sdprobe.sim.json

M1 = RELEASE_v1 + two byte ranges only:
  1. 0x60479A2B, 5 B : existing SU-hook jmp retargeted from 0x605DECD4
                       to the M1 stub at 0x60672C58.
  2. 0x60672C58, N B : M1 stub + literals inside the registered free-FF
                       pocket 0x60672C20..0x60672FF6.

The stub runs once per boot (guard word at 0x60672C20), calls
fileif_get_media_status, tries five fopen paths with mode "rb", reads 16 B on
the first success, then tries fopen("OUT.BIN","wb") + fwrite 8 B.  All results
are formatted as 95 ASCII chars into 0x61894CA0 (EXIF UserComment +0x10),
leaving the existing k=N / key fields at +0x00..+0x07 untouched.  Finally it
jumps to the *existing* 0x605DECD4 stub, so release tone/k= behaviour is
unchanged.

No file in this repository other than build/ is modified by running this.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import oly_container as oc  # noqa: E402

BASE = 0x60021020
SRC_BIN = ROOT / "build" / "RELEASE_v1.BIN"
OUT_BIN = ROOT / "build" / "M1_sdprobe.BIN"
OUT_SIM = ROOT / "build" / "M1_sdprobe.sim.json"

BASE_SHA256 = "0b08a232cee544c5724e8ed661fcad97d75d1e9f9886229c30ea01d2280cafad"

HOOK = 0x60479A2B
HOOK_OLD = bytes.fromhex("dc a9 52 16 00")       # jmp 0x605DECD4 in RELEASE_v1
CHAIN = 0x605DECD4                                # existing release SU stub
CODE = 0x60672C58
POCKET_LO = 0x60672C20
POCKET_HI = 0x60672FF6
EXIF = 0x61894CA0                                 # UserComment + 0x10
GUARD = 0x60672C20

TOOLBIN = ROOT / ".tmp" / "olya" / "toolchains" / "mn10300-elf" / "bin"
AS = str(TOOLBIN / "mn10300-elf-as.exe")
LD = str(TOOLBIN / "mn10300-elf-ld.exe")
OBJCOPY = str(TOOLBIN / "mn10300-elf-objcopy.exe")
OBJDUMP = str(TOOLBIN / "mn10300-elf-objdump.exe")

# ---------------------------------------------------------------------------
# Stub source.  Call immediates and the final jump are placeholders; they are
# replaced by real PC-relative displacements after the first assembly pass.
# `call` frame bytes are patched to each callee's own `ret ...,imm8` value.
# ---------------------------------------------------------------------------
STUB_TMPL = r"""
	.am33
	.text
	.globl	_start

	.equ	GUARD,0x60672C20
	.equ	BUF,0x60672C24
	.equ	MS,0x60672C34
	.equ	P0,0x60672C38
	.equ	P1,0x60672C3C
	.equ	P2,0x60672C40
	.equ	P3,0x60672C44
	.equ	P4,0x60672C48
	.equ	IDX,0x60672C4C
	.equ	RD,0x60672C50
	.equ	WR,0x60672C54
	.equ	EXIF,0x61894CA0

_start:
	add	-4,sp
	mov	a3,(sp)
	add	-4,sp
	mov	a0,(sp)
	add	-4,sp
	mov	d2,(sp)

	mov	GUARD,a2
	mov	(a2),d1
	cmp	0x1,d1
	beq	restore
	cmp	0x2,d1
	beq	restore
	mov	0x1,d1
	mov	d1,(a2)
	bra	probe

restore:
	mov	(sp),d2
	add	4,sp
	mov	(sp),a0
	add	4,sp
	mov	(sp),a3
	add	4,sp
	jmp	0x11119999

probe:
	/* no candidate is "succeeded" until a later attempt says so */
	mov	0x5,d1
	mov	d1,(IDX)

	/* 1. media status, no arguments (task signature); d0=0 makes the
	   static-inferred (void) call deterministic instead of passing garbage */
	clr	d0
	call	0x11110001,[d2,a2],0xc
	mov	d0,(MS)

	/* 2. five fopen(path, 0, "rb") attempts, in task order */
	mov	s_p0,a0
	clr	d0
	mov	s_rb,a1
	call	0x11110002,[d2,d3,a2,a3],0x2c
	mov	a0,(P0)
	cmp	0x0,a0
	beq	t1
	mov	a0,d1
	add	0x1,d1
	beq	t1
	clr	d0
	mov	d0,(IDX)
	bra	found

t1:
	mov	s_p1,a0
	clr	d0
	mov	s_rb,a1
	call	0x11110003,[d2,d3,a2,a3],0x2c
	mov	a0,(P1)
	cmp	0x0,a0
	beq	t2
	mov	a0,d1
	add	0x1,d1
	beq	t2
	mov	0x1,d0
	mov	d0,(IDX)
	bra	found

t2:
	mov	s_p2,a0
	clr	d0
	mov	s_rb,a1
	call	0x11110004,[d2,d3,a2,a3],0x2c
	mov	a0,(P2)
	cmp	0x0,a0
	beq	t3
	mov	a0,d1
	add	0x1,d1
	beq	t3
	mov	0x2,d0
	mov	d0,(IDX)
	bra	found

t3:
	mov	s_p3,a0
	clr	d0
	mov	s_rb,a1
	call	0x11110005,[d2,d3,a2,a3],0x2c
	mov	a0,(P3)
	cmp	0x0,a0
	beq	t4
	mov	a0,d1
	add	0x1,d1
	beq	t4
	mov	0x3,d0
	mov	d0,(IDX)
	bra	found

t4:
	mov	s_p4,a0
	clr	d0
	mov	s_rb,a1
	call	0x11110006,[d2,d3,a2,a3],0x2c
	mov	a0,(P4)
	cmp	0x0,a0
	beq	wr
	mov	a0,d1
	add	0x1,d1
	beq	wr
	mov	0x4,d0
	mov	d0,(IDX)

found:
	mov	a0,a2
	mov	BUF,a0
	mov	0x1,d0
	mov	0x10,d1
	mov	a2,a1
	call	0x11110007,[d2,d3,a2,a3],0x18
	mov	d0,(RD)
	mov	a2,a0
	call	0x11110008,[d2,a2,a3],0x10

wr:
	mov	s_out,a0
	clr	d0
	mov	s_wb,a1
	call	0x11110009,[d2,d3,a2,a3],0x2c
	mov	a0,(WR)
	cmp	0x0,a0
	beq	fmt
	mov	a0,d1
	add	0x1,d1
	beq	fmt
	mov	a0,a2
	mov	s_msg,a0
	mov	0x1,d0
	mov	0x8,d1
	mov	a2,a1
	call	0x1111000A,[d2,d3,a2,a3],0x18
	mov	d0,(WR)
	mov	a2,a0
	call	0x1111000B,[d2,a2,a3],0x10

fmt:
	/* "M1ms=" */
	mov	EXIF,a0
	mov	s_l0,a2
	mov	0x5,d1
	call	0x1111000C,[],0x0
	mov	(MS),d2
	asl	16,d2
	mov	0x4,d1
	call	0x1111000D,[],0x0

	/* ",I=" + one char */
	mov	s_l1,a2
	mov	0x3,d1
	call	0x1111000E,[],0x0
	mov	(IDX),d0
	cmp	0x5,d0
	beq	ix
	add	0x30,d0
	bra	is
ix:
	mov	0x58,d0
is:
	movbu	d0,(a0)
	inc	a0

	/* ",rd=" + 4 hex */
	mov	s_l2,a2
	mov	0x4,d1
	call	0x1111000F,[],0x0
	mov	(RD),d2
	asl	16,d2
	mov	0x4,d1
	call	0x11110010,[],0x0

	/* ",d=" + 16 hex (first 8 B as 2 words, big-endian display) */
	mov	s_l3,a2
	mov	0x3,d1
	call	0x11110011,[],0x0
	mov	(BUF),d2
	mov	0x8,d1
	call	0x11110012,[],0x0
	mov	(BUF+4),d2
	mov	0x8,d1
	call	0x11110013,[],0x0

	/* ",wr=" + 8 hex */
	mov	s_l4,a2
	mov	0x4,d1
	call	0x11110016,[],0x0
	mov	(WR),d2
	mov	0x8,d1
	call	0x11110017,[],0x0

	/* ",p=" + 5 * 8 hex */
	mov	s_l5,a2
	mov	0x3,d1
	call	0x11110018,[],0x0
	mov	(P0),d2
	mov	0x8,d1
	call	0x11110019,[],0x0
	mov	(P1),d2
	mov	0x8,d1
	call	0x1111001A,[],0x0
	mov	(P2),d2
	mov	0x8,d1
	call	0x1111001B,[],0x0
	mov	(P3),d2
	mov	0x8,d1
	call	0x1111001C,[],0x0
	mov	(P4),d2
	mov	0x8,d1
	call	0x1111001D,[],0x0

	clr	d0
	movbu	d0,(a0)
	mov	GUARD,a2
	mov	0x2,d1
	mov	d1,(a2)

finish:
	mov	(sp),d2
	add	4,sp
	mov	(sp),a0
	add	4,sp
	mov	(sp),a3
	add	4,sp
	jmp	0x11119999

/* d1 = digit count, d2 = value (left aligned), a0 = destination */
hexn:
	mov	d2,d0
	asr	28,d0
	and	0xf,d0
	add	0x30,d0
	cmp	0xa,d0
	blt	hs
	add	0x27,d0
hs:
	movbu	d0,(a0)
	inc	a0
	asl	4,d2
	add	-1,d1
	bne	hexn
	ret	[],0x0

/* d1 = length, a2 = source, a0 = destination */
puts:
	movbu	(a2),d0
	movbu	d0,(a0)
	inc	a2
	inc	a0
	add	-1,d1
	bne	puts
	ret	[],0x0

s_p0:	.asciz	"PING.BIN"
s_p1:	.asciz	"\\PING.BIN"
s_p2:	.asciz	"A:\\PING.BIN"
s_p3:	.asciz	"DCOLYMP\\EM10MOD\\PING.BIN"
s_p4:	.asciz	"A:\\DCOLYMP\\EM10MOD\\PING.BIN"
s_rb:	.asciz	"rb"
s_out:	.asciz	"OUT.BIN"
s_wb:	.asciz	"wb"
s_msg:	.byte	0x45,0x4d,0x31,0x30,0x4d,0x31,0x00,0x00
s_l0:	.ascii	"M1ms="
s_l1:	.ascii	",I="
s_l2:	.ascii	",rd="
s_l3:	.ascii	",d="
s_l4:	.ascii	",wr="
s_l5:	.ascii	",p="
"""

# placeholder -> (target, call frame byte).  frame == callee ret imm8.
CALL_SPECS = [
    # placeholder, target, call frame (callee ret imm8), ret-mask byte
    (0x11110001, 0x6056556D, 0x0C, 0xA0),   # fileif_get_media_status
    (0x11110002, 0x6056570C, 0x2C, 0xF0),   # fopen p0
    (0x11110003, 0x6056570C, 0x2C, 0xF0),   # p1
    (0x11110004, 0x6056570C, 0x2C, 0xF0),   # p2
    (0x11110005, 0x6056570C, 0x2C, 0xF0),   # p3
    (0x11110006, 0x6056570C, 0x2C, 0xF0),   # p4
    (0x11110007, 0x605649BB, 0x18, 0xF0),   # fread
    (0x11110008, 0x60564832, 0x10, 0xB0),   # fclose
    (0x11110009, 0x6056570C, 0x2C, 0xF0),   # fopen OUT.BIN
    (0x1111000A, 0x605648AF, 0x18, 0xF0),   # fwrite
    (0x1111000B, 0x60564832, 0x10, 0xB0),   # fclose
    (0x1111000C, "puts", 0x00, 0x00),
    (0x1111000D, "hexn", 0x00, 0x00),
    (0x1111000E, "puts", 0x00, 0x00),
    (0x1111000F, "puts", 0x00, 0x00),
    (0x11110010, "hexn", 0x00, 0x00),
    (0x11110011, "puts", 0x00, 0x00),
    (0x11110012, "hexn", 0x00, 0x00),
    (0x11110013, "hexn", 0x00, 0x00),
    (0x11110016, "puts", 0x00, 0x00),
    (0x11110017, "hexn", 0x00, 0x00),
    (0x11110018, "puts", 0x00, 0x00),
    (0x11110019, "hexn", 0x00, 0x00),
    (0x1111001A, "hexn", 0x00, 0x00),
    (0x1111001B, "hexn", 0x00, 0x00),
    (0x1111001C, "hexn", 0x00, 0x00),
    (0x1111001D, "hexn", 0x00, 0x00),
]
JMP_PH = 0x11119999

BYTE_STRINGS = {
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


def run(cmd: list[str], *, text: bool = False):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        out = p.stdout.decode("utf-8", "replace") if not text else p.stdout
        raise SystemExit("command failed: %s\n%s" % (" ".join(cmd), out))
    return p.stdout.decode("utf-8", "replace") if text else p.stdout


def assemble(tag: str = "stub"):
    d = ROOT / ".tmp" / "m1_build"
    d.mkdir(parents=True, exist_ok=True)
    s = d / (tag + ".s")
    o = str(d / (tag + ".o"))
    elf = str(d / (tag + ".elf"))
    binp = d / (tag + ".bin")
    s.write_text(STUB_TMPL, encoding="utf-8", newline="\n")
    run([AS, "-o", o, str(s)])
    run([LD, "-Ttext", hex(CODE), "-o", elf, o])
    run([OBJCOPY, "-O", "binary", elf, str(binp)])
    dis = run([OBJDUMP, "-d", elf], text=True)
    (d / (tag + ".dis")).write_text(dis, encoding="utf-8", newline="\n")
    blob = bytearray(binp.read_bytes())
    syms = {}
    for line in dis.splitlines():
        m = re.match(r"^([0-9a-fA-F]{8}) <([^>]+)>:$", line.strip())
        if m:
            syms[m.group(2)] = int(m.group(1), 16)
    return blob, syms, dis


def patch_call(blob: bytearray, ph: int, target: int, frame: int, mask: int) -> int:
    """Patch the PC-relative disp32; the assembler owns mask/frame bytes."""
    pat = b"\xdd" + struct.pack("<I", ph)
    i = bytes(blob).find(pat)
    if i < 0 or bytes(blob).find(pat, i + 1) >= 0:
        raise SystemExit("call placeholder %#x not unique" % ph)
    site = i
    if bytes(blob[site + 5:site + 7]) != bytes([mask, frame]):
        raise SystemExit("call mask/frame %#x: got %s want %02x %02x"
                         % (CODE + site, blob[site + 5:site + 7].hex(), mask, frame))
    disp = target - (CODE + site)
    blob[site + 1:site + 5] = struct.pack("<i", disp)
    got = bytes(blob[site:site + 7])
    want = b"\xdd" + struct.pack("<i", disp) + bytes([mask, frame])
    if got != want:
        raise SystemExit("call self-check failed at %#x" % (CODE + site))
    return site


def patch_jmp(blob: bytearray, ph: int, target: int) -> list[int]:
    """Patch every `jmp <ph>` site (the stub has two: skip path and finish)."""
    pat = b"\xdc" + struct.pack("<I", ph)
    sites = []
    start = 0
    while True:
        i = bytes(blob).find(pat, start)
        if i < 0:
            break
        sites.append(i)
        start = i + 1
    if not sites:
        raise SystemExit("jmp placeholder %#x not found" % ph)
    for i in sites:
        disp = target - (CODE + i)
        blob[i + 1:i + 5] = struct.pack("<i", disp)
        if bytes(blob[i:i + 5]) != b"\xdc" + struct.pack("<i", disp):
            raise SystemExit("jmp self-check failed at %#x" % (CODE + i))
    return sites


def build_stub():
    blob, syms, dis = assemble()
    calls = []
    for ph, target, frame, mask in CALL_SPECS:
        tgt = syms[target] if isinstance(target, str) else target
        site = patch_call(blob, ph, tgt, frame, mask)
        calls.append([hex(CODE + site), hex(tgt), frame, mask])
    jumps = [hex(CODE + i) for i in patch_jmp(blob, JMP_PH, CHAIN)]

    if CODE + len(blob) > POCKET_HI:
        raise SystemExit("stub %d B ends at %#x > pocket end %#x"
                         % (len(blob), CODE + len(blob), POCKET_HI))
    return bytes(blob), syms, dis, calls, jumps


def jmp_bytes(site: int, target: int) -> bytes:
    return b"\xdc" + struct.pack("<i", target - site)


def main() -> int:
    if not SRC_BIN.exists():
        raise SystemExit("missing baseline %s" % SRC_BIN)
    raw = SRC_BIN.read_bytes()
    import hashlib
    base_sha = hashlib.sha256(raw).hexdigest()
    if base_sha != BASE_SHA256:
        raise SystemExit("baseline sha256 changed: %s" % base_sha)

    blocks = oc.parse(raw)
    b0 = blocks[0]
    payload = bytearray(b0.payload)
    hook_off = HOOK - BASE
    if bytes(payload[hook_off:hook_off + 5]) != HOOK_OLD:
        raise SystemExit("hook bytes differ from RELEASE_v1: %s"
                         % payload[hook_off:hook_off + 5].hex())

    stub, syms, dis, calls, jumps = build_stub()
    code_off = CODE - BASE
    if code_off + len(stub) > len(payload):
        raise SystemExit("stub outside payload")

    # guard/buffer/scratch area must still be FF in the file
    if bytes(payload[POCKET_LO - BASE:code_off]) != b"\xff" * (code_off - (POCKET_LO - BASE)):
        raise SystemExit("pocket head is not FF before write")

    payload[hook_off:hook_off + 5] = jmp_bytes(HOOK, CODE)
    payload[code_off:code_off + len(stub)] = stub

    nb0 = oc.replace_payload(b0, bytes(payload))
    out = (raw[:b0.offset] + nb0.header + oc.encode(nb0.payload) + nb0.trailer
           + raw[b0.offset + 64 + len(b0.payload):])

    # re-parse the finished container (independent check)
    chk = oc.parse(out)
    if chk[0].payload[hook_off:hook_off + 5] != jmp_bytes(HOOK, CODE):
        raise SystemExit("hook verification failed")
    if chk[0].payload[code_off:code_off + len(stub)] != stub:
        raise SystemExit("stub verification failed")

    OUT_BIN.write_bytes(out)
    sha = hashlib.sha256(out).hexdigest()

    # string offsets, for the doc and verify script
    string_offsets = {}
    for name, val in BYTE_STRINGS.items():
        i = stub.find(val)
        if i < 0:
            raise SystemExit("string %r missing from stub" % name)
        string_offsets[name] = [hex(CODE + i), len(val)]

    sim = {
        "verifier": "verify_m1.py",
        "sha256": sha,
        "closed": True,
        "scope": "M1: fileif media/fopen/fread/fwrite probe via existing SU hook",
        "base": "build/RELEASE_v1.BIN",
        "base_sha256": base_sha,
        "changes": [
            [hex(HOOK), 5, HOOK_OLD.hex(), jmp_bytes(HOOK, CODE).hex()],
            [hex(CODE), len(stub)],
        ],
        "stub": {
            "code": hex(CODE),
            "size": len(stub),
            "end": hex(CODE + len(stub)),
            "guard": hex(GUARD),
            "chain": hex(CHAIN),
            "exif": hex(EXIF),
            "text_format": "M1ms=%04X,I=%c,rd=%04X,d=%016llX,wr=%08X,p=%08X%08X%08X%08X%08X",
            "text_len": 95,
            "helpers": {"hexn": hex(syms["hexn"]), "puts": hex(syms["puts"])},
            "strings": string_offsets,
            "calls": calls,
            "jumps": jumps,
        },
    }
    OUT_SIM.write_text(json.dumps(sim, ensure_ascii=True, indent=2) + "\n",
                       encoding="utf-8", newline="\n")

    print("built %s" % OUT_BIN)
    print("  sha256   %s" % sha)
    print("  stub     %d B @ %#x .. %#x" % (len(stub), CODE, CODE + len(stub)))
    print("  hook     %#x  %s -> %s" % (HOOK, HOOK_OLD.hex(),
                                        jmp_bytes(HOOK, CODE).hex()))
    print("  sim      %s" % OUT_SIM)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
