# -*- coding: utf-8 -*-
"""verify_b48.py —— B48 离线整链验证：派发 → k → stash → 上传器按 k 选表 → 拷进 dest。

复用 tools/verify_b32_chain.py 的 libsim 驱动（同一份镜像、同一套内存建模），
只把"地址与几何"换成 B48 的：
  钩子 A 0x604B811D -> SD 桩 0x605DED44（写 32 位 k 到 0x605DED70）
  钩子 B 0x60479A2B -> SU 桩 0x605DED80（k=1..4 查 0x605DEE3C 选 slot 22..25）
判据：C1 进桩 / C2 stash==k / C3 回到 0x604B8124 / C4 选表对 / C5 a2 对 / C6 拷出的表对
退出码 0 = 全部闭合。
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import oly_container as oc                                  # noqa: E402
import verify_b32_chain as V                                 # noqa: E402

BASE = V.BASE
TONE, SLOT = V.TONE, V.SLOT

DISPATCH = 0x604B8098
SD_INSERT = 0x604B811D
SD_RAM = 0x605DED44
DISP_CONT = 0x604B8124

SU_INSERT = 0x60479A2B
SU_RAM = 0x605DED80
COPY_LOOP = 0x60479A3A

STASH = 0x605DED70
PTRTAB = 0x605DEE3C
STOCK_LUT = TONE + 1 * SLOT          # 覆盖测试时给 ctx+0x1230 的"原厂表"
DEST = 0x9E00A400
O = 0x61E10000
S = 0x61E18000
T2 = 0x61E19000
CTX = 0x61E20000
SCRATCH = 0x61E28000
SP = 0x61E0F800

UPLOAD_HEAP = V.UPLOAD_HEAP


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def stash_of(image, *, b0, b1, mode):
    pokes = [
        (CTX + 0x3C0, (0).to_bytes(4, 'little')),
        (CTX + 0x436, b'\x00\x00'),
        (CTX + 8, bytes([b0 & 0xFF])),
        (CTX + 9, bytes([b1 & 0xFF])),
        (CTX + 0x0A, struct.pack('<H', mode & 0xFFFF)),
    ]
    regs = {'A0': CTX, 'A1': SCRATCH}
    d = V.sim(image, DISPATCH, DISP_CONT, limit=300000, pokes=pokes, regs=regs)
    raw = V.mem_bytes(d, STASH, 4)
    return int.from_bytes(raw, 'little') if raw else None, d


def su_pick(image, k):
    pokes = [
        (S + 0x1230, STOCK_LUT.to_bytes(4, 'little')),
        (STASH, int(k).to_bytes(4, 'little')),
        (SP + 8, DEST.to_bytes(4, 'little')),
    ]
    regs = {'A0': S + 0x1230, 'D2': DEST + 0x400}
    d = V.sim(image, SU_RAM, COPY_LOOP, limit=400, pokes=pokes, regs=regs)
    a2 = V.regs_of(d)['A2']
    return a2, (a2 - TONE) // SLOT, d


def upload(image, k):
    pokes = [
        (O + 0x0C, S.to_bytes(4, 'little')),
        (O + 0x64, (4).to_bytes(4, 'little')),
        (O + 0x34, (0).to_bytes(4, 'little')),
        (O + 0xBD, b'\x01'),
        (S + 0x41C, (0).to_bytes(4, 'little')),
        (S + 0x420, T2.to_bytes(4, 'little')),
        (S + 0x1230, STOCK_LUT.to_bytes(4, 'little')),
        (STASH, int(k).to_bytes(4, 'little')),
    ]
    regs = {'A0': DEST, 'A1': O}
    d = V.sim(image, V.UPLOAD, V.UPLOAD_MERGE, limit=400000, pokes=pokes, regs=regs,
              extra_rwz=UPLOAD_HEAP)
    return V.tone_of_dest(V.mem_bytes(d, DEST, V.DEST_SPAN)), d


def table_of(image, slot):
    o = TONE + slot * SLOT - BASE
    return list(struct.unpack('<256H', bytes(image[o:o + SLOT])))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'build/B48_stash_extreme.BIN'
    image = payload_of(path)
    cases = [(0x00, 0x01, m) for m in (1, 2, 3, 4)] + [(0x00, 0x01, 0), (0x00, 0x01, 7)]
    allok = True
    for b0, b1, mode in cases:
        key = (b0 << 24) | (b1 << 16) | mode
        k_exp = (((key >> 24) & 3) << 3) | (key & 7)
        st, dd = stash_of(image, b0=b0, b1=b1, mode=mode)
        hit = V.regs_of(dd)['PC'] == DISP_CONT   # 到达续跑点即算回到原路
        a2, slot, sd = su_pick(image, st if st is not None else 0xFF)
        tone, up = upload(image, st if st is not None else 0xFF)

        exp_slot = (22 + st - 1) if (st is not None and 1 <= st <= 4) else 1
        exp_tab = table_of(image, exp_slot)
        c1 = hit
        c2 = (st == k_exp)
        c3 = hit
        c4 = (a2 == TONE + exp_slot * SLOT)
        c5 = c4
        c6 = (tone == exp_tab)
        ok = c1 and c2 and c4 and c6
        allok &= ok
        print(f'== b0={b0:02X} b1={b1:02X} mode={mode}  key=0x{key:08X} k_expect={k_exp}')
        print(f'   stash={st}  dispatcher={V.describe(dd)}')
        print(f'   SU: a2=0x{a2:08X} slot={slot}  期望 slot={exp_slot}  T[46] 拷出={tone[46] if tone else None} 表内={exp_tab[46]}')
        for name, v in (('C1 进 SD 桩并回 0x604B8124', c1), ('C2 stash == k', c2),
                        ('C4 a2 == 期望表', c4), ('C6 拷出的表 == 期望表', c6)):
            print(f'     {"PASS" if v else "FAIL"}  {name}')
    rec = {'pkg': path, 'sha256': hashlib.sha256(open(path,'rb').read()).hexdigest(),
           'verifier': 'tools/verify_b48.py', 'cases': len(cases),
           'closed': bool(allok), 'time': __import__('time').strftime('%Y-%m-%dT%H:%M:%S'),
           'checks': ['C1 进桩并回续跑点', 'C2 stash==k', 'C4 a2==期望表', 'C6 拷出的表==期望表']}
    with open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print('\n-> ' + ('全部闭合' if allok else '有不闭合的用例'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, allok))
    sys.exit(0 if allok else 1)


if __name__ == '__main__':
    main()
