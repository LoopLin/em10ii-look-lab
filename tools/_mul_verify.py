#!/usr/bin/env python3
"""Verify 0x604B67C3 semantics: dest = (A x B) >> 8  (3x3 s16, row-major).

Runs the real decoder/emulator (tools/em10_run.py) with A at 0x61E00000,
B at 0x61E00020, dest at 0x61E00040 inside a zeroed rw region.
"""
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT / 'analysis' / '00_0100_40300020.decoded.bin'
BASE = 0x60021020
PRESETS = 0x605ECECA
FUNC = 0x604B67C3
A_ADDR, B_ADDR, D_ADDR = 0x61E00000, 0x61E00020, 0x61E00040


def img_read(addr, n):
    off = addr - BASE
    with open(IMAGE, 'rb') as f:
        f.seek(off)
        return f.read(n)


def mat(addr):
    return list(struct.unpack('<9h', img_read(addr, 18)))


def pack(m):
    return b''.join(struct.pack('<h', v) for v in m)


def mul_sim(a, b):
    out = []
    for r in range(3):
        for c in range(3):
            s = sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3))
            out.append(s >> 8)  # arithmetic shift == floor div 256
    return out


def run(a, b):
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tf:
        outp = tf.name
    cmd = [sys.executable, str(ROOT / 'tools' / 'em10_run.py'),
           '--pc', hex(FUNC), '--stop', '0x604B6A1F', '--limit', '20000',
           '--code', '0x604B6000:0x1000',
           '--rw', '0x61E00000:0x2000',
           '--reg', 'A0=' + hex(D_ADDR), '--reg', 'D0=' + hex(A_ADDR),
           '--reg', 'A1=' + hex(B_ADDR), '--reg', 'SP=0x61E00800',
           '--poke', hex(A_ADDR) + '=' + pack(a).hex(),
           '--poke', hex(B_ADDR) + '=' + pack(b).hex(),
           '--dump', hex(D_ADDR) + ':0x12',
           '--out', outp]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if p.returncode:
        print(p.stdout[-2000:], p.stderr[-2000:])
        raise SystemExit('emulator failed')
    res = json.loads(Path(outp).read_text())
    Path(outp).unlink()
    data = None
    for cap in res.get('memory', []):
        raw = cap.get('hex') or cap.get('data')
        b_ = bytes.fromhex(raw) if isinstance(raw, str) else bytes(raw or [])
        if cap.get('address') == 0x61E00000 and len(b_) >= 0x52:
            data = b_[0x40:0x52]
    return res, list(struct.unpack('<9h', data[:18])) if data else None


def check(name, a, b):
    res, got = run(a, b)
    exp = mul_sim(b, a)   # emulator: dest = M(a1) x M(d0)
    ok = got == exp
    print(f'--- {name}  status={res["status"]} completed={res.get("completed")}')
    print('  A   =', a)
    print('  B   =', b)
    print('  exp =', exp)
    print('  got =', got)
    print('  MATCH' if ok else '  *** MISMATCH ***')
    return ok


def main():
    m0 = mat(PRESETS)
    print('m0 @0x%08x = %s (row sums %s)' % (
        PRESETS, m0, [sum(m0[r * 3:r * 3 + 3]) for r in range(3)]))
    ident = [256, 0, 0, 0, 256, 0, 0, 0, 256]
    ok = []
    ok.append(check('m0 x I', m0, ident))
    ok.append(check('I x m0', ident, m0))
    b1 = [300, -20, 10, 30, 256, 0, 0, 0, 200]
    ok.append(check('m0 x B1', m0, b1))
    b2 = [100, 100, 56, 0, 256, 0, -40, 0, 296]
    ok.append(check('m0 x B2', m0, b2))
    b3 = [-10, 300, -34, 256, 0, 0, 0, 0, 256]
    ok.append(check('m0 x B3', m0, b3))
    ok.append(check('m6 x B2', mat(PRESETS + 6 * 0x12), b2))
    print('\nALL MATCH' if all(ok) else '\nSOME MISMATCH')


if __name__ == '__main__':
    main()
