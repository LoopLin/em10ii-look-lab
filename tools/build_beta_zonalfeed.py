# -*- coding: utf-8 -*-
"""B28 zonal-feed: B26 + (mask 0xFFF & ten pointers to neutral tables) on every
uploader call.

Chain
  stock OLY_E_120_1400_0000_0000.BIN
    --(tools/build_beta_hook4mx.py: tone hook 0x604AA242, matrix hook 0x604B6AD4)-->
  .tmp/B26ref.BIN   (must be byte-identical to build/B26_four_looks_mx.BIN)
    --(this script via tools/mkpatch.py)-->
  build/B28_zonalfeed.BIN

What this script adds on top of B26 (three regions, nothing else):
  0x60479960   5 B   jmp 0x605DED44   (replaces `43 08 47 0C 96`, the uploader's
                                        `mov a0,(8,SP)/mov a1,(12,SP)/mov a1,a2`;
                                        resume at 0x60479965)
  0x605DED44 120 B   stub: out=*(O+0x0C); out+0x3C4=0x0FFF; ten pointers
                     out+0x1434..0x1458 -> stock HSL dataset banks; replay + jmp
  0x605F5BB4 0xA80 B ten neutral tables written over the stock HSL dataset's
                     bank area (dataset+0x150..0xBD0).  u32 banks 0x3F003F00,
                     u16 banks 0x3F00; the 12 descriptors at +0x000..0x150 and
                     the mask/flag at +0xBD0/+0xBD2 stay stock.

Usage:
    python tools/build_beta_zonalfeed.py              # build build/B28_zonalfeed.BIN
    python tools/build_beta_zonalfeed.py --patch-only # only write .tmp/b28.patch
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import oly_container  # noqa: E402

BASE = 0x60021020
STOCK = ROOT / 'stock' / 'OLY_E_120_1400_0000_0000.BIN'
B26_REF = ROOT / '.tmp' / 'B26ref.BIN'
B26_SHIPPED = ROOT / 'build' / 'B26_four_looks_mx.BIN'
OUT = ROOT / 'build' / 'B28_zonalfeed.BIN'
DECODED = ROOT / '.tmp' / 'B28.decoded.bin'

ZF_INSERT = 0x60479960          # uploader entry (5 hookable bytes)
ZF_RESUME = 0x60479965
ZF_STUB = 0x605DED44            # tone slot 53, 0x200 B, one base-only reference
ZF_STUB_SRC = ROOT / 'tools' / 'hooks' / 'zf_stub.s'
ZF_STUB_BIN = ROOT / '.tmp' / 'zf_stub.bin'
ZF_STUB_ELF = ROOT / '.tmp' / 'zf_stub.elf'
ZF_STUB_OBJ = ROOT / '.tmp' / 'zf_stub.o'
TC = ROOT / '.tmp' / 'olya' / 'toolchains' / 'mn10300-elf' / 'bin'

HSL = 0x605F5A64                # stock HSL dataset (0xBD4 B)
# pointer slot i -> dataset offset, and the width of that bank
SLOT_OFF = [0x150, 0x690, 0x2D0, 0x450, 0x810, 0x990, 0x510, 0xA50, 0x5D0, 0xB10]
U32_SLOTS = {0, 1, 2, 4}
NEUTRAL_U16 = 0x3F00
NEUTRAL_U32 = 0x3F003F00
TABLE_RAM = HSL + 0x150         # 0x605F5BB4: start of the contiguous bank area
TABLE_LEN = 0xA80               # 4*0x180 + 6*0xC0


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def ensure_b26() -> bytes:
    """Rebuild B26 from stock and cross-check it against the shipped image."""
    if not B26_REF.exists() or B26_REF.stat().st_mtime < STOCK.stat().st_mtime:
        r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'build_beta_hook4mx.py'),
                            '--out', str(B26_REF)],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode:
            sys.stderr.write(r.stdout[-800:] + r.stderr[-800:])
            raise SystemExit('B26 rebuild failed')
    data = B26_REF.read_bytes()
    if B26_SHIPPED.exists():
        ref = B26_SHIPPED.read_bytes()
        if ref != data:
            raise SystemExit('B26 rebuild differs from build/B26_four_looks_mx.BIN '
                             f'(rebuild {sha256(data)[:16]} vs shipped {sha256(ref)[:16]})')
    return data


def assemble_stub(force: bool = False) -> bytes:
    need = force or not ZF_STUB_BIN.exists() or \
        ZF_STUB_BIN.stat().st_mtime < ZF_STUB_SRC.stat().st_mtime
    if need:
        env = dict(os.environ)
        env.update(TMP='.tmp/gtmp', TEMP='.tmp/gtmp', TMPDIR='.tmp/gtmp')
        (ROOT / '.tmp' / 'gtmp').mkdir(parents=True, exist_ok=True)
        runs = [
            [str(TC / 'mn10300-elf-as.exe'), '-alh=' + str(ROOT / '.tmp' / 'zf_stub.lst'),
             '-o', str(ZF_STUB_OBJ), str(ZF_STUB_SRC)],
            [str(TC / 'mn10300-elf-ld.exe'), '-Ttext', hex(ZF_STUB), '-e', 'zf_entry',
             '-o', str(ZF_STUB_ELF), str(ZF_STUB_OBJ)],
            [str(TC / 'mn10300-elf-objcopy.exe'), '-O', 'binary', str(ZF_STUB_ELF),
             str(ZF_STUB_BIN)],
        ]
        for cmd in runs:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
            if r.returncode:
                sys.stderr.write((r.stdout or '') + (r.stderr or ''))
                raise SystemExit('assembler failed: ' + cmd[0])
    stub = ZF_STUB_BIN.read_bytes()
    if not stub.endswith(b'\xdc') and stub[-5] != 0xDC:
        raise SystemExit('stub does not end with a jmp')
    j = len(stub) - 5                       # offset of the 0xDC byte
    stub = bytearray(stub)
    stub[j + 1:j + 5] = ((ZF_RESUME - (ZF_STUB + j)) & 0xFFFFFFFF).to_bytes(4, 'little')
    return bytes(stub)


def tables() -> bytes:
    """10 neutral tables, memory order of the stock dataset bank area."""
    order = sorted(range(10), key=lambda i: SLOT_OFF[i])
    out = bytearray()
    for i in order:
        if i in U32_SLOTS:
            out += struct.pack('<I', NEUTRAL_U32) * 96
        else:
            out += struct.pack('<H', NEUTRAL_U16) * 96
    if len(out) != TABLE_LEN:
        raise SystemExit(f'table length {len(out):#x} != {TABLE_LEN:#x}')
    return bytes(out)


def write_patch(stub: bytes, tab: bytes) -> Path:
    patches = [
        (ZF_INSERT, b'\xdc' + ((ZF_STUB - ZF_INSERT) & 0xFFFFFFFF).to_bytes(4, 'little'),
         'jmp_zf'),
        (ZF_STUB, stub, 'stub_zf'),
        (TABLE_RAM, tab, 'zf_tables_neutral'),
    ]
    pf = ROOT / '.tmp' / 'b28.patch'
    with io.open(pf, 'w', encoding='ascii', newline='\n') as f:
        for ram, data, label in patches:
            f.write(f'ram 0x{ram:08X}={data.hex()}  # {label}\n')
    return pf


def payload_of(path: Path) -> bytes:
    """Payload of block 0 -- the 0x60021020 code+data block (3 blocks total)."""
    blocks = oly_container.parse(path.read_bytes())
    if len(blocks) != 3:
        raise SystemExit(f'{path.name}: expected 3 blocks, got {len(blocks)}')
    return blocks[0].payload


def report(b28: Path, stub: bytes, tab: bytes) -> int:
    b28_bytes = b28.read_bytes()
    stock_payload = payload_of(STOCK)
    b26_payload = payload_of(B26_REF)
    b28_payload = payload_of(b28)
    DECODED.write_bytes(b28_payload)
    regions = [(ZF_INSERT, 5), (ZF_STUB, len(stub)), (TABLE_RAM, len(tab))]
    changed = [(TABLE_RAM, len(tab)),
               (ZF_STUB, len(stub)),
               (ZF_INSERT, 5)]
    total = sum(n for _, n in changed)
    # bytes outside the three patched regions must equal B26
    ok = True
    inside = bytearray(len(b28_payload))
    for ram, n in regions:
        inside[ram - BASE:ram - BASE + n] = b'\x01' * n
    diff = 0
    for i, (a, b) in enumerate(zip(b28_payload, b26_payload)):
        if a != b and not inside[i]:
            diff += 1
            ok = False
    print(f'file      : {b28}  {len(b28_bytes):,} B')
    print(f'sha256    : {sha256(b28_bytes)}')
    print(f'payload   : {len(b28_payload):,} B  sha256 {sha256(b28_payload)}')
    print(f'patched   : {total} B  ' + '  '.join(f'{n}B@{ram:#010x}' for ram, n in changed))
    print(f'outside regions vs B26: {diff} byte(s) differ -> {"OK (0)" if ok else "MISMATCH"}')
    print(f'stub      : {len(stub)} B @ {ZF_STUB:#x}..{ZF_STUB + len(stub):#x}')
    print(f'tables    : {len(tab)} B @ {TABLE_RAM:#x}..{TABLE_RAM + len(tab):#x}')
    print('pointers  :')
    for i, off in enumerate(SLOT_OFF):
        kind = 'u32' if i in U32_SLOTS else 'u16'
        print(f'  slot{i} {kind} -> {HSL + off:#010x}  (dataset+{off:#05x})')
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(OUT))
    ap.add_argument('--patch-only', action='store_true')
    ap.add_argument('--force-asm', action='store_true')
    a = ap.parse_args()
    ensure_b26()
    stub = assemble_stub(a.force_asm)
    tab = tables()
    pf = write_patch(stub, tab)
    print(f'patch file: {pf}  ({pf.stat().st_size} B)')
    if a.patch_only:
        return 0
    out = Path(a.out).resolve()
    if out.exists():
        out.unlink()          # mkpatch refuses to overwrite an existing output
    r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'mkpatch.py'),
                        str(B26_REF), '--out', str(out), '--patch-file', str(pf)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    print((r.stdout or '')[-400:] or (r.stderr or '')[-800:])
    if r.returncode:
        raise SystemExit('mkpatch failed')
    return report(out, stub, tab)


if __name__ == '__main__':
    raise SystemExit(main())
