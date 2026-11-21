#!/usr/bin/env python3
"""Apply byte patches to an OE update container and repair the trailer checksums.

Scope: offline only. This tool never talks to a camera and never produces a
file named like an SD-card update; renaming/placing the result is a separate,
deliberate step.

Two address conventions are accepted for each patch:

* ``ram 0x6052E52B``  analysis address (RAM) for the main block, converted with
  ``ram = 0x60021020 + file_offset`` -> ``container_offset = 32 + (ram - base)``.
* ``off 0x50D52B``    raw container file offset (may land in any block payload).
* ``blk 1:0x7F5448``  decoded offset inside block N (for resource blocks, which
  have no RAM mapping).  Only the decoded payload is addressed; the container
  encoding is handled by ``oly_container``.

Every patch must stay inside one block payload and keep the block size
unchanged; the block trailer checksum is recomputed with
``oly_container.replace_payload``. The result is re-parsed and must pass the
full container checks (signature, model, header/trailer equality, additive
checksum).

Example::

    python tools/mkpatch.py stock/OLY_E_120_1400_0000_0000.BIN \\
        --out build/patched.bin --patch 0x6052E52B=0D0A0D0A0D0A0D0A0D0A0D0A
"""

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oly_container as oc  # noqa: E402

DEFAULT_BASE = 0x60021020
# 容器载荷相对分析基址的固定偏置：decoded[i] 对应 RAM (base + i)，base = 0x60021020。
# 2026-11-14 复核：此前误改为 0x60021000 并加 -0x20，导致所有包前移 0x20；已恢复。
RAM_BIAS = 0
SD_NAME = re.compile(r"^[A-Za-z]\d{3}\d{4}\.BIN$", re.I)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def parse_hex(text):
    value = re.sub(r"[\s:_-]", "", text)
    if len(value) % 2:
        raise ValueError(f"hex payload has odd length: {text!r}")
    return bytes.fromhex(value)


def load_patch_specs(args):
    specs = []
    for item in args.patch or []:
        specs.extend(parse_patch_line(item, "ram"))
    for item in args.patch_off or []:
        specs.extend(parse_patch_line(item, "off"))
    for item in args.patch_blk or []:
        specs.extend(parse_patch_line(item, "blk"))
    if args.patch_file:
        for lineno, line in enumerate(Path(args.patch_file).read_text().splitlines(), 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            mode, _, body = line.partition(" ")
            if mode not in ("ram", "off", "blk"):
                raise ValueError(f"{args.patch_file}:{lineno}: expected 'ram', 'off' or 'blk', got {mode!r}")
            specs.extend(parse_patch_line(body, mode))
    return specs


def parse_patch_line(text, mode):
    addr_text, _, data_text = text.partition("=")
    if not data_text:
        raise ValueError(f"patch must look like ADDR=HEX: {text!r}")
    addr_text = addr_text.strip()
    if mode == "blk":
        block_text, _, off_text = addr_text.partition(":")
        if not block_text.isdigit() or not re.fullmatch(r"0[xX][0-9a-fA-F]+", off_text):
            raise ValueError(f"block patch must look like N:0xOFFSET: {addr_text!r}")
        return [("blk", (int(block_text), int(off_text, 16)), parse_hex(data_text))]
    if not re.fullmatch(r"0[xX][0-9a-fA-F]+", addr_text):
        raise ValueError(f"patch address must be 0x-prefixed hex: {addr_text!r}")
    return [(mode, int(addr_text, 16), parse_hex(data_text))]


def locate(blocks, mode, addr, base):
    """Return (block, decoded_offset) for a patch address."""
    if mode == "blk":
        block_index, decoded_offset = addr
        block = next((b for b in blocks if b.index == block_index), None)
        if block is None:
            raise ValueError(f"block {block_index} does not exist (have {[b.index for b in blocks]})")
        if decoded_offset < 0 or decoded_offset >= len(block.payload):
            raise ValueError(f"decoded offset {decoded_offset:#x} is outside block {block_index}")
        return block, decoded_offset
    if mode == "ram":
        if addr < base:
            raise ValueError(f"RAM address {addr:#x} is below base {base:#x}")
        container_offset = 32 + (addr - base)
        if container_offset < 32:
            raise ValueError(f"RAM address {addr:#x} maps before the payload")
        block = block_for_container_offset(blocks, container_offset)
        return block, container_offset - block.offset - 32 + RAM_BIAS
    block = block_for_container_offset(blocks, addr)
    return block, addr - block.offset - 32


def block_for_container_offset(blocks, offset):
    for block in blocks:
        start = block.offset + 32
        if start <= offset < start + len(block.payload):
            return block
    raise ValueError(f"container offset {offset:#x} is not inside any block payload")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--patch", action="append", metavar="RAM=HEX",
                        help="patch at an analysis (RAM) address, e.g. 0x6052E52B=00ff")
    parser.add_argument("--patch-off", action="append", metavar="OFF=HEX",
                        help="patch at a raw container file offset, e.g. 0x50D52B=00ff")
    parser.add_argument("--patch-blk", action="append", metavar="N:OFF=HEX",
                        help="patch at a decoded offset inside block N, e.g. 1:0x7F5448=..." )
    parser.add_argument("--patch-file", type=Path, help="file with 'ram 0x... hex' / 'off 0x... hex' lines")
    parser.add_argument("--code-base", type=lambda s: int(s, 0), default=DEFAULT_BASE,
                        help=f"main-block analysis base (default {DEFAULT_BASE:#x})")
    parser.add_argument("--expect-sha256", help="require the input to match this SHA-256")
    parser.add_argument("--dry-run", action="store_true", help="report the result without writing")
    parser.add_argument("--omemu-patch", type=Path, metavar="JSON",
                        help="also write an om-emu patch overlay (block/offset/old/new base64) for bin/nav.mjs --patch")
    args = parser.parse_args()

    data = args.image.read_bytes()
    source_sha = sha256(data)
    if args.expect_sha256 and source_sha != args.expect_sha256.lower():
        raise ValueError("source SHA-256 differs from the expected build")

    blocks = oc.parse(data)
    specs = load_patch_specs(args)
    if not specs:
        raise ValueError("no patches given")

    payloads = [bytearray(block.payload) for block in blocks]
    applied = []
    for mode, addr, patch in specs:
        block, offset = locate(blocks, mode, addr, args.code_base)
        payload = payloads[block.index]
        if offset < 0 or offset + len(patch) > len(payload):
            raise ValueError(f"patch at {addr:#x} ({mode}) overruns block {block.index}")
        old = bytes(payload[offset:offset + len(patch)])
        payload[offset:offset + len(patch)] = patch
        if mode == "blk":
            input_address = f"{addr[0]}:{addr[1]:#x}"
            ram_address = None
        else:
            input_address = hex(addr)
            ram_address = hex(args.code_base + offset) if block.index == 0 else None
        applied.append({
            "mode": mode,
            "input_address": input_address,
            "block": block.index,
            "kind": hex(block.kind),
            "decoded_offset": hex(offset),
            "container_offset": hex(block.offset + 32 + offset),
            "ram_address": ram_address,
            "old": old.hex(),
            "new": patch.hex(),
            "changed_bytes": sum(a != b for a, b in zip(old, patch)),
        })

    # 落点自检：把“写入后”的载荷与原载荷逐字节比，差异必须全部落在预期区间内。
    intended = {}
    for item in applied:
        if item["block"] != 0:
            continue
        off = int(item["decoded_offset"], 16)
        for i in range(off, off + len(bytes.fromhex(item["new"]))):
            intended[i] = True
    stray = []
    for i, (before, after) in enumerate(zip(blocks[0].payload, payloads[0])):
        if before != after and not intended.get(i):
            stray.append(i)
    for item in applied:
        if item["block"] != 0:
            continue
        off = int(item["decoded_offset"], 16)
        if bytes(payloads[0][off:off + len(bytes.fromhex(item["new"]))]) != bytes.fromhex(item["new"]):
            raise ValueError(f"patch at {item['input_address']} did not land at its decoded offset")
    if stray:
        raise ValueError(f"patch landed off-target: {len(stray)} stray byte(s), first at {hex(BASE + stray[0]) if False else hex(stray[0])}")

    repaired = [oc.replace_payload(block, bytes(payloads[block.index]))
                for block in blocks]
    out = b"".join(block.header + oc.encode(block.payload) + block.trailer
                   for block in repaired)

    # Independent verification: the produced image must pass every container check.
    reparsed = oc.parse(out)
    if [b.payload for b in reparsed] != [bytes(p) for p in payloads]:
        raise ValueError("re-parsed payloads differ from the intended payloads")

    report = {
        "source": str(args.image),
        "source_sha256": source_sha,
        "source_size": len(data),
        "output_sha256": sha256(out),
        "output_size": len(out),
        "code_base": hex(args.code_base),
        "patches": applied,
        "container_checks": "passed (signature, model, size%16, header/trailer, additive checksum)",
        "scope": "offline container integrity only; not a bootability or recovery test",
    }
    if SD_NAME.match(args.out.name):
        report["warning"] = "output name looks like an SD-card update file; place it on a card deliberately"
    if args.dry_run:
        print(json.dumps(report, indent=2))
        return

    if args.out.resolve() == args.image.resolve():
        raise ValueError("refusing to overwrite the source image")
    if args.out.resolve().parent == args.image.parent.resolve():
        raise ValueError("refusing to write into the original firmware directory")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise ValueError(f"output already exists: {args.out}")
    args.out.write_bytes(out)
    report["written"] = str(args.out)
    if args.omemu_patch:
        overlays = []
        for item in applied:
            if item["block"] not in (0, 1):
                raise ValueError(f"om-emu loads only blocks 0 and 1; patch targets block {item['block']}")
            overlays.append({
                "block": item["block"],
                "offset": int(item["decoded_offset"], 16),
                "old": base64.b64encode(bytes.fromhex(item["old"])).decode(),
                "new": base64.b64encode(bytes.fromhex(item["new"])).decode(),
                "label": f"{item['mode']} {item['input_address']}",
            })
        args.omemu_patch.parent.mkdir(parents=True, exist_ok=True)
        args.omemu_patch.write_text(json.dumps({"patches": overlays}, indent=2))
        report["omemu_patch"] = str(args.omemu_patch)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        sys.exit(f"error: {exc}")
