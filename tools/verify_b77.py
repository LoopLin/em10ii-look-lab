#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b77.py — B77：把真 HSL 表内容写进标准档实际使用的平表 0x605F0798

判据：
  A 静态：B69 的四个钩子原样；平表十张表与 0x605F5A64 的对应表逐字节相同；
    平表 +0xBD0 的 mask 仍为 0x07DF。
  B 模拟：0x604B97F8（选择器）在 flag=0、ctx+0x0C=0x0201、obj+0x30=0x0101006C
    条件下返回 A0=0x605F0798、D0=0x07DF，且 0x605F08E8 起的表内容 == 真表。
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, 'tools')
import oly_container as oc

PKG = Path(sys.argv[1] if len(sys.argv) > 1 else 'build/B77_hsl_content.BIN')
BASE = 0x60021020
TRUE = 0x605F5A64
FLAT = 0x605F0798
OFFS = [0x150, 0x2D0, 0x450, 0x510, 0x5D0, 0x690, 0x810, 0x990, 0xA50, 0xB10]
SIZE = [0x180, 0x180, 0x180, 0xC0, 0x180, 0xC0, 0xC0, 0xC0, 0xC0, 0xC0]
HOOKS = {
    0x604B811D: bytes.fromhex('dc276c12000000'),
    0x60479A2B: bytes.fromhex('dca9521600'),
    0x604B6AD6: bytes.fromhex('dc2e73cfff'),
    0x604B4CAD: bytes.fromhex('dc6fa01200cb'),
}
CHECKS = []


def check(name, ok, detail=''):
    CHECKS.append((name, bool(ok), detail))
    return ok


def payload_of(path: Path) -> bytes:
    return oc.parse(path.read_bytes())[0].payload


def main():
    raw = PKG.read_bytes()
    img = payload_of(PKG)
    sha = hashlib.sha256(raw).hexdigest()
    check('size', len(raw) == 69_599_232, str(len(raw)))
    check('container parse', True)

    def b(addr, n):
        o = addr - BASE
        return img[o:o + n]

    for addr, want in HOOKS.items():
        check('hook %08x' % addr, b(addr, len(want)) == want, b(addr, len(want)).hex())

    same = True
    for o, s in zip(OFFS, SIZE):
        if b(FLAT + o, s) != b(TRUE + o, s):
            same = False
            check('table %08x' % (FLAT + o), False)
        else:
            check('table %08x == true' % (FLAT + o), True)
    mask = struct.unpack('<H', b(FLAT + 0xBD0, 2))[0]
    check('flat mask 0x07DF', mask == 0x7DF, hex(mask))

    # 模拟：选择器平表支
    payload = PKG.with_suffix('.payload.bin')
    payload.write_bytes(img)
    out = Path('.tmp/b77_sel.json')
    obj, ctx, outobj = 0x61E00000, 0x61E00100, 0x61E00400

    def u32(v):
        return struct.pack('<I', v).hex()

    def u16(v):
        return struct.pack('<H', v).hex()

    cmd = [sys.executable, 'tools/em10_run.py', '--image', str(payload),
           '--pc', '0x604B97F8', '--stop', '0x61E80000',
           '--reg', 'A0=0x%x' % obj, '--reg', 'A2=0x%x' % obj,
           '--code', '0x604B97F8:0x80', '--code', '0x605ED254:0x40',
           '--code', '0x605F0798:0x1000', '--code', '0x605F5A64:0x1000',
           '--rw-any', '0x61E00000:0x1000', '--breakpoint', '0x604B985B',
           '--limit', '2000', '--dump', '0x605F08E8:0x20', '--out', str(out),
           '--poke', '0x%x=%s' % (obj + 4, u32(ctx)),
           '--poke', '0x%x=%s' % (obj + 0x0C, u32(outobj)),
           '--poke', '0x%x=%s' % (obj + 0x30, u32(0x0101006C)),
           '--poke', '0x%x=%s' % (ctx + 0x24A, '00'),
           '--poke', '0x%x=%s' % (ctx + 0x0C, u16(0x0201)),
           '--poke', '0x%x=%s' % (ctx + 0x24C, u16(0))]

    sel_ok = False
    dump_ok = False
    if out.exists():
        out.unlink()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if p.returncode == 0 and out.exists():
        res = json.loads(out.read_text())
        regs = res['registers']
        a0, d0 = regs[4], regs[0]
        check('sim status', res.get('status') == 'breakpoint', str(res.get('status')))
        check('sim A0=0x605F0798', a0 == FLAT, hex(a0))
        check('sim D0=0x07DF', d0 == 0x7DF, hex(d0))
        sel_ok = (a0 == FLAT and d0 == 0x7DF)
        for cap in res.get('memory', []):
            data = bytes.fromhex(cap.get('hex') or cap.get('data') or '')
            if cap.get('address') == 0x605F08E8:
                dump_ok = data == b(TRUE + 0x150, len(data))
                check('dump == true table0', dump_ok, data[:16].hex())
    else:
        check('sim run', False, (p.stdout or '')[-200:] + (p.stderr or '')[-200:])

    ok = all(c[1] for c in CHECKS)
    print('== verify_b77 %s' % PKG)
    for name, good, detail in CHECKS:
        print('   %s  %s  %s' % ('PASS' if good else 'FAIL', name, detail))
    print('   sha256 = %s' % sha)
    print('   结论：%s' % ('closed' if ok else 'NOT closed'))

    sim = {
        'pkg': str(PKG),
        'sha256': sha,
        'verifier': 'tools/verify_b77.py',
        'cases': len(CHECKS),
        'closed': ok,
        'checks': [{'name': n, 'ok': g, 'detail': d} for n, g, d in CHECKS],
    }
    sim_path = Path(str(PKG) + '.sim.json')
    sim_path.write_text(json.dumps(sim, ensure_ascii=False, indent=1), encoding='utf-8')
    print('   sim 记录 -> %s' % sim_path)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
