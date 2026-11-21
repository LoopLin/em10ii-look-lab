# -*- coding: utf-8 -*-
"""B25: per-mode look via the plan-A hook (0x604AA242).
k = ((b0&3)<<3)|(mode&7); k=1..4 -> tone tables at slots 22..25 (vivid/natural/muted/portrait).
Other keys keep the stock pointer."""
from __future__ import annotations
import argparse, io, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_beta_look import load_spec, tone_bytes, retarget_tone, MATRIX_RAM  # noqa: E402

TONE = 0x605D8344
SLOT = 0x200
STUB_RAM = 0x6066159E
INSERT = 0x604AA242
RET = 0x604AA248
SLOT0 = 22
LOOKS = ['reala_ace', 'astia', 'eterna', 'bleach_bypass']


def build(out: Path, stub: bytes) -> int:
    s = bytearray(stub)
    jmp_at = len(s) - 5
    s[jmp_at + 1:jmp_at + 5] = ((RET - (STUB_RAM + jmp_at)) & 0xFFFFFFFF).to_bytes(4, 'little')
    patches = []
    for i, look in enumerate(LOOKS):
        tb = tone_bytes(retarget_tone(load_spec(look)['tone']))
        assert len(tb) == SLOT
        patches.append((TONE + (SLOT0 + i) * SLOT, tb, f'tone_{look}'))
    patches.append((STUB_RAM, bytes(s), 'stub'))
    patches.append((INSERT, b'\xdc' + ((STUB_RAM - INSERT) & 0xFFFFFFFF).to_bytes(4, 'little') + b'\x00', 'jmp'))
    pf = ROOT / '.tmp' / 'b25.patch'
    with io.open(pf, 'w', encoding='ascii', newline='\n') as f:
        for ram, data, label in patches:
            f.write(f'ram 0x{ram:08X}={data.hex()}  # {label}\n')
    r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'mkpatch.py'),
                        str(ROOT / 'stock' / 'OLY_120_1400_0000_0000.BIN'.replace('OLY_', 'OLY_E_')),
                        '--out', str(out), '--patch-file', str(pf)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout[-800:] or r.stderr[-1200:])
    return 0 if r.returncode == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='build/B25_four_looks.BIN')
    ap.add_argument('--stub', default='.tmp/b25.bin')
    a = ap.parse_args()
    return build((ROOT / a.out).resolve(), (ROOT / a.stub).read_bytes())


if __name__ == '__main__':
    raise SystemExit(main())
