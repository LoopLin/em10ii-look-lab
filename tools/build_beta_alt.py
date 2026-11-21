# -*- coding: utf-8 -*-
"""B24i: plan-A hook (no stash) -- at 0x604AA242 write the tone pointer for the
current key directly, using ctx+8/+9/+0x0A which are still valid right after the
dispatch call returns.  Tables live at tone slots 22..53 (k = key index)."""
from __future__ import annotations
import argparse, io, json, struct, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_beta_stash import probe_table, TONE, SLOT, MATRIX_RAM  # noqa: E402

STUB_RAM = 0x6066159E
INSERT = 0x604AA242          # clr d0 / mov d0,(4,a2) / mov a3,(20,SP)
RET = 0x604AA248
TABLE_BASE_K = 22            # first slot we own


def build(out: Path, stub: bytes) -> int:
    s = bytearray(stub)
    jmp_at = len(s) - 5                     # opcode byte offset of the final jmp
    disp = (RET - (STUB_RAM + jmp_at)) & 0xFFFFFFFF
    s[jmp_at + 1:jmp_at + 5] = disp.to_bytes(4, 'little')
    patches = []
    for k in range(32):
        patches.append((TONE + (TABLE_BASE_K + k) * SLOT, probe_table(k), f'tone{k}'))
    patches.append((STUB_RAM, bytes(s), 'stub'))
    patches.append((INSERT, b'\xdc' + ((STUB_RAM - INSERT) & 0xFFFFFFFF).to_bytes(4, 'little') + b'\x00', 'jmp'))
    pf = ROOT / '.tmp' / 'b24i.patch'
    with io.open(pf, 'w', encoding='ascii', newline='\n') as f:
        for ram, data, label in patches:
            f.write(f'ram 0x{ram:08X}={data.hex()}  # {label}\n')
    r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'mkpatch.py'),
                        str(ROOT / 'stock' / 'OLY_E_120_1400_0000_0000.BIN'),
                        '--out', str(out), '--patch-file', str(pf)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout[-1500:] or r.stderr[-2000:])
    return 0 if r.returncode == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='build/B24i_alt.BIN')
    ap.add_argument('--stub', default='.tmp/b24i.bin')
    a = ap.parse_args()
    return build((ROOT / a.out).resolve(), (ROOT / a.stub).read_bytes())


if __name__ == '__main__':
    raise SystemExit(main())
