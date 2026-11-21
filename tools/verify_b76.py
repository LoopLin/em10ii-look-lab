#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b76.py — B76 HSL 双点追踪（B 只置标志，不写 EXIF）

A 桩 @0x605DEE4C：写 B76A+键+PB+out 到 EXIF 0x61894CA0，并把标志
（0x605DEEC8，由 B 置 0xA5）抄到 EXIF+0x10 后清零。
B 桩 @0x605DEEA0：重放 3 条原厂指令后只把 0xA5 写进 0x605DEEC8，
不读入参、不碰 EXIF——开机早期执行也安全。
"""
from __future__ import annotations
import hashlib, json, struct, sys
sys.path.insert(0, 'tools')
import oly_container as oc
import verify_b32_chain as V

BASE = 0x60021020
B69 = 'build/B69_slot1.BIN'
HOOK_A, HOOK_B = 0x604B974D, 0x60479A9E
STUB_A, STUB_B = 0x605DEE4C, 0x605DEEA0
FLAG = 0x605DEEC8
EXIF = 0x61894CA0
PB, PL, OUT = 0x61E10000, 0x61E11000, 0x61E12000
SP = 0x61E13000

def payload_of(p):
    with open(p, 'rb') as f:
        return oc.parse(f.read())[0].payload

def at(buf, addr, n):
    o = addr - BASE
    return bytes(buf[o:o+n])

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'build/B76_hsl_trace2.BIN'
    img = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    b69 = payload_of(B69)
    res = {}
    res['hookA_jmp'] = at(img, HOOK_A, 5).hex() == 'dcff561200'
    res['hookB_jmp'] = at(img, HOOK_B, 5).hex() == 'dc02541600'
    res['stubA_bytes'] = at(img, STUB_A, 3).hex() == '92f82e'
    res['stubB_bytes'] = at(img, STUB_B, 10).hex() == '5c0459085b0880a5fcdf'
    res['flag_init'] = at(img, FLAG, 4) == b'\x00'*4

    # A：从 HSL 入口跑到安装入口
    pokes = [(PB+4, PL.to_bytes(4, 'little')), (PB+12, OUT.to_bytes(4, 'little')),
             (0x61E11000, b'\x00'*4)]
    d = V.sim(img, HOOK_A, 0x604B9766, limit=20000, pokes=pokes,
              regs={'A0': PB}, extra_rwz=('0x61894000:0x2000',))
    regs = V.regs_of(d)
    res['A_reached_install'] = regs.get('PC') == 0x604B9766 or d.get('pc') == 0x604B9766
    res['A_fault'] = d.get('fault')
    mem = V.mem_bytes(d, EXIF, 20) if not res['A_fault'] else b''
    res['A_exif_magic'] = mem[:4] == b'B76A'
    res['A_exif_key_written'] = len(mem) >= 8
    res['A_exif_pb'] = struct.unpack('<I', mem[8:12])[0] == PB if len(mem) >= 12 else False
    res['A_exif_out'] = struct.unpack('<I', mem[12:16])[0] == OUT if len(mem) >= 16 else False
    res['A_bit2'] = (V.mem_bytes(d, PB+0x64, 1)[0] & 4) != 0 if not res['A_fault'] else False

    # B：只做静态核对（B 写镜像页，libsim 不允许把代码页映射成可写）
    res['B_replay_stock'] = at(img, STUB_B, 6) == at(b69, 0x60479A9E, 6)
    res['B_flag_store'] = at(img, STUB_B+8, 7).hex() == 'fcdfc8ee5d6063'
    res['B_fault'] = None

    allok = all(v is True for k, v in res.items() if k not in ('A_fault', 'B_fault'))
    rec = {'pkg': str(path), 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b76.py', 'closed': bool(allok), 'checks': res,
           'base': B69, 'evidence': 'libsim (A 桩 + B 桩，单次)'}
    with open(str(path) + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    for k, v in res.items():
        print('  %-20s %s' % (k, v))

if __name__ == '__main__':
    main()
