# -*- coding: utf-8 -*-
"""B26: per-mode tone + per-mode 3x3 matrix via two hooks.
tone hook  0x604AA242 -> 0x6066159E   (pick tone table by k)
matrix hook 0x604B6AD4 -> 0x601ADE04  (pick matrix slot m0..m3 by k)
k = ((b0&3)<<3)|(mode&7); looks: 1 reala, 2 astia, 3 eterna, 4 bleach.
Only k in 1..4 is touched; everything else keeps the stock path."""
from __future__ import annotations
import argparse, io, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_beta_look import load_spec, tone_bytes, retarget_tone  # noqa: E402

TONE = 0x605D8344
SLOT = 0x200
TONE_STUB = 0x6066159E
TONE_INSERT = 0x604AA242
TONE_RET = 0x604AA248
MX_STUB = 0x601ADE04
MX_INSERT = 0x604B6AD4
MX_RET = 0x604B6ADB
MX_BASE = 0x605ECECA
MX_STEP = 0x12
LOOKS = ['reala_ace', 'astia', 'eterna', 'bleach_bypass']


def relocate(stub: bytes, at: int, target: int) -> bytes:
    s = bytearray(stub)
    j = len(s) - 5
    s[j + 1:j + 5] = ((target - (at + j)) & 0xFFFFFFFF).to_bytes(4, 'little')
    return bytes(s)


def build(out: Path, tone_stub: bytes, mx_stub: bytes) -> int:
    patches = []
    for i, look in enumerate(LOOKS):
        tb = tone_bytes(retarget_tone(load_spec(look)['tone']))
        patches.append((TONE + (22 + i) * SLOT, tb, f'tone_{look}'))
        q = load_spec(look)['matrix']['quantized']['table_values']
        patches.append((MX_BASE + (i + 1) * MX_STEP,
                        b''.join(int(v).to_bytes(2, 'little', signed=True) for v in q), f'mx_{look}'))
    ident = [256, 0, 0, 0, 256, 0, 0, 0, 256]
    patches.append((MX_BASE, b''.join(int(v).to_bytes(2, 'little', signed=True) for v in ident), 'mx_identity'))
    slot_map = bytes([0, 1, 2, 3, 4] + [0] * 27)
    patches.append((0x605DB744, slot_map, 'slot_map'))
    patches.append((TONE_STUB, relocate(tone_stub, TONE_STUB, TONE_RET), 'stub_tone'))
    patches.append((TONE_INSERT, b'\xdc' + ((TONE_STUB - TONE_INSERT) & 0xFFFFFFFF).to_bytes(4, 'little') + b'\x00', 'jmp_tone'))
    patches.append((MX_STUB, relocate(mx_stub, MX_STUB, MX_RET), 'stub_mx'))
    patches.append((MX_INSERT, b'\xdc' + ((MX_STUB - MX_INSERT) & 0xFFFFFFFF).to_bytes(4, 'little') + b'\x00\x00', 'jmp_mx'))
    pf = ROOT / '.tmp' / 'b26.patch'
    with io.open(pf, 'w', encoding='ascii', newline='\n') as f:
        for ram, data, label in patches:
            f.write(f'ram 0x{ram:08X}={data.hex()}  # {label}\n')
    r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'mkpatch.py'),
                        str(ROOT / 'stock' / 'OLY_E_120_1400_0000_0000.BIN'),
                        '--out', str(out), '--patch-file', str(pf)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout[-500:] or r.stderr[-800:])
    return 0 if r.returncode == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='build/B26_four_looks_mx.BIN')
    ap.add_argument('--looks', default='', help='逗号分隔, 按 k=1..4 的顺序')
    a = ap.parse_args()
    if a.looks:
        global LOOKS
        LOOKS = [x.strip() for x in a.looks.split(',') if x.strip()]
        assert len(LOOKS) == 4, LOOKS
    return build((ROOT / a.out).resolve(), (ROOT / '.tmp' / 'b25.bin').read_bytes(),
                 (ROOT / '.tmp' / 'b26mx.bin').read_bytes())


if __name__ == '__main__':
    raise SystemExit(main())
