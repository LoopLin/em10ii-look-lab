#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b51.py —— B51 离线整链验证：派发 → k → stash → tone 表 / 矩阵表按档分派。

骨架照 tools/verify_b48.py，几何换成 B51（矩阵极端四档；本包无节奏/无色度通道）：
  钩子 A 0x604B811D -> SD 桩 0x605DED44（32 位 k 写 0x605DED70）
  钩子 B 0x60479A2B -> SU 桩 0x605DED80（k=1..4 查 0x605DEE3C 选 tone 槽 9/11/12/13）
  钩子 C 0x604B6AD6 -> MX 桩 0x605DEEC0（k=1..4 查 0x605DEEA8 选 4x18 B 矩阵）

每个用例（k=1..4 各一，k=0 与 k=7 走原厂）断言：
  C1 分派器回到 0x604B8124          C2 stash == 期望 k
  C3 tone 指针（a2）== 期望槽        C4 上传器拷进 dest 的表 == 该槽内容
  C5 矩阵指针（a0）== 期望 18 B 块    C6 该块 18 字节 == 期望矩阵字节
  C7 跑完后原厂 m0 0x605ECECA 逐字节未变
  C8 跑完后 ctx+0x1230 逐字节未变
退出码 0 = 全部闭合；顺手写 <包>.sim.json。
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import oly_container as oc                                  # noqa: E402
import verify_b32_chain as V                                 # noqa: E402
import build_b51 as B51                                      # noqa: E402

BASE = V.BASE
TONE, SLOT = V.TONE, V.SLOT

DISPATCH = 0x604B8098
SD_INSERT = 0x604B811D
SD_RAM = 0x605DED44
DISP_CONT = 0x604B8124

SU_INSERT = 0x60479A2B
SU_RAM = 0x605DED80
COPY_LOOP = 0x60479A3A

MX_INSERT = 0x604B6AD6
MX_RAM = 0x605DEEC0
MX_RET = 0x604B6ADB

STASH = 0x605DED70
PTRTAB = 0x605DEE3C
MX_DATA = 0x605DEE60
MX_PTRTAB = 0x605DEEA8
MX_STRIDE = 18
MATRIX_M0 = 0x605ECECA
SLOTS = [9, 11, 12, 13]
STOCK_LUT = TONE + 1 * SLOT          # 覆盖测试时给 ctx+0x1230 的"原厂表"

DEST = 0x9E00A400
O = 0x61E10000
S = 0x61E18000
T2 = 0x61E19000
CTX = 0x61E20000
SCRATCH = 0x61E28000
SP = 0x61E0F800

UPLOAD_HEAP = V.UPLOAD_HEAP
CHROMA_INSERT = 0x604B4B25           # 本包不该动它（色度通道不参与）
CHROMA_POOL = 0x605D9344

# EXIF：UserComment 文本区（X=0x618943FC 的 X+0x894）；libsim 里按"原厂全空格"喂
EXIF_TEXT = 0x61894C90
EXIF_REGION = '0x61894000:0x1000'
EXIF_POKE = b'        '
EXIF_STOCK = b'    '


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
        (CTX + 0x1230, (0xC0FFEE00).to_bytes(4, 'little')),
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
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': S + 0x1230, 'D2': DEST + 0x400}
    d = V.sim(image, SU_RAM, COPY_LOOP, limit=400, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,))
    return V.regs_of(d)['A2'], d


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
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': DEST, 'A1': O}
    d = V.sim(image, V.UPLOAD, V.UPLOAD_MERGE, limit=400000, pokes=pokes, regs=regs,
              extra_rwz=UPLOAD_HEAP + (EXIF_REGION,))
    return V.tone_of_dest(V.mem_bytes(d, DEST, V.DEST_SPAN)), d


def mx_pick(image, k):
    """钩子 C：只改 a0（返回矩阵指针）/d0（临时），d3 与 a2 必须原样。"""
    pokes = [(STASH, int(k).to_bytes(4, 'little'))]
    regs = {'A2': CTX, 'A0': SP + 8, 'D0': 0xDEADBEEF, 'D3': 0x01010064}
    d = V.sim(image, MX_RAM, MX_RET, limit=200, pokes=pokes, regs=regs)
    return V.regs_of(d), d


def table_of(image, slot):
    o = TONE + slot * SLOT - BASE
    return list(struct.unpack('<256H', bytes(image[o:o + SLOT])))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'build/B51_three.BIN'
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()

    want_blobs = []
    for _name, rows in B51.MX_CASES:
        flat = B51.enc_matrix(rows)
        want_blobs.append(b''.join(struct.pack('<h', v) for v in flat))

    # 静态前提：色度通道确实没进包（钩点还是原厂指令，池区没被写）
    pre1 = bytes(image[CHROMA_INSERT - BASE: CHROMA_INSERT - BASE + 3]) == b'\x25\xC0\x03'
    pre2 = bytes(image[CHROMA_POOL - BASE: CHROMA_POOL - BASE + 4]) != b'\x00\x00\x00\x00'
    m0_img = bytes(image[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18])
    print('静态前提：色度钩点 0x%x 仍是原厂 25 C0 03 = %s；池区 0x%x 有内容 = %s'
          % (CHROMA_INSERT, pre1, CHROMA_POOL, pre2))
    print('          原厂 m0 = %s'
          % list(struct.unpack('<9h', m0_img)))

    cases = [(0x00, 0x01, m) for m in (1, 2, 3, 4)] + [(0x00, 0x01, 0), (0x00, 0x01, 7)]
    allok = pre1 and pre2
    details = []
    for b0, b1, mode in cases:
        key = (b0 << 24) | (b1 << 16) | mode
        k_exp = (((key >> 24) & 3) << 3) | (key & 7)
        st, dd = stash_of(image, b0=b0, b1=b1, mode=mode)
        kk = st if st is not None else 0xFF
        in_range = 1 <= kk <= 4

        # --- tone 通道 ---
        a2, sd = su_pick(image, kk)
        exp_slot = SLOTS[kk - 1] if in_range else 1
        exp_a2 = TONE + exp_slot * SLOT
        tone, up = upload(image, kk)
        exp_tab = table_of(image, exp_slot)

        # --- 矩阵通道 ---
        regs, md = mx_pick(image, kk)
        a0 = regs['A0']
        exp_a0 = (MX_DATA + (kk - 1) * MX_STRIDE) if in_range else MATRIX_M0
        got_mx = V.mem_bytes(md, a0, MX_STRIDE) or b''
        exp_mx = want_blobs[kk - 1] if in_range else m0_img
        sentinel_ok = (regs['D0'] == 0xDEADBEEF and regs['D3'] == 0x01010064)

        # --- EXIF：k=1..4 写 "k=N\0"，其余保持原厂空格 ---
        exp_exif = (b'k=%d\x00' % kk) if in_range else EXIF_STOCK
        got_exif_sd = V.mem_bytes(sd, EXIF_TEXT, 4) or b''
        got_exif_up = V.mem_bytes(up, EXIF_TEXT, 4) or b''
        exif_ok = (got_exif_sd == exp_exif and got_exif_up == exp_exif)

        # --- 不变量：m0 与 ctx+0x1230 逐字节未变 ---
        # 每次 sim 里只 poke 了一处 ctx+0x1230，另一处应当保持区域零填 —— 两处都查。
        m0_ok = True
        ctx_ok = True
        for d in (dd, sd, up, md):
            b = V.mem_bytes(d, MATRIX_M0, 18)
            if b != m0_img:
                m0_ok = False
        ZERO4 = (0).to_bytes(4, 'little')
        for d, exp_c, exp_s in ((dd, (0xC0FFEE00).to_bytes(4, 'little'), ZERO4),
                                (sd, ZERO4, STOCK_LUT.to_bytes(4, 'little')),
                                (up, ZERO4, STOCK_LUT.to_bytes(4, 'little')),
                                (md, ZERO4, ZERO4)):
            if (V.mem_bytes(d, CTX + 0x1230, 4) or b'?') != exp_c:
                ctx_ok = False
            if (V.mem_bytes(d, S + 0x1230, 4) or b'?') != exp_s:
                ctx_ok = False

        c1 = V.regs_of(dd)['PC'] == DISP_CONT
        c2 = (st == k_exp) and (st == mode)
        c3 = (a2 == exp_a2)
        c4 = (tone == exp_tab)
        c5 = (a0 == exp_a0)
        c6 = (got_mx == exp_mx)
        ok = all([pre1, pre2, c1, c2, c3, c4, c5, c6, m0_ok, ctx_ok, sentinel_ok, exif_ok])
        allok &= ok
        print('== b0=%02X b1=%02X mode=%d  key=0x%08X k=%s' % (b0, b1, mode, key, st))
        print('   dispatcher=%s' % V.describe(dd))
        print('   tone: a2=0x%08X slot=%d（期望 %d） T46 拷出=%s 表内=%s'
              % (a2, (a2 - TONE) // SLOT if a2 >= TONE else -1, exp_slot,
                 tone[46] if tone else None, exp_tab[46]))
        print('   mtx : a0=0x%08X（期望 0x%08X） %s'
              % (a0, exp_a0, list(struct.unpack('<9h', got_mx)) if len(got_mx) == 18 else 'n/a'))
        print('   exif: 0x%08X = %r（期望 %r）' % (EXIF_TEXT, got_exif_sd, exp_exif))
        for name, v in (('C1 分派器回到 0x604B8124', c1), ('C2 stash == k', c2),
                        ('C3 tone 指针 == 期望槽', c3), ('C4 拷进 dest 的表 == 期望槽内容', c4),
                        ('C5 矩阵指针 == 期望块', c5), ('C6 块内容 == 期望矩阵', c6),
                        ('C7 原厂 m0 未变', m0_ok), ('C8 ctx+0x1230 未变', ctx_ok),
                        ('C9 MX 桩保住 d0/d3', sentinel_ok),
                        ('C10 EXIF 文本 == "k=N\\0"', exif_ok)):
            print('     %s  %s' % ('PASS' if v else 'FAIL', name))
        details.append({'mode': mode, 'key': key, 'k': st, 'slot': exp_slot,
                        'mx': '0x%08X' % a0, 'exif': got_exif_sd.hex(), 'ok': bool(ok)})

    rec = {'pkg': path, 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b51.py', 'cases': len(cases),
           'closed': bool(allok), 'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'checks': ['C1 分派器回到续跑点', 'C2 stash==k', 'C3 tone 指针==期望槽',
                      'C4 拷贝表==期望槽内容', 'C5 矩阵指针==期望块', 'C6 块内容==期望矩阵',
                      'C7 原厂 m0 未变', 'C8 ctx+0x1230 未变', 'C9 d0/d3 保住',
                      'C10 EXIF 文本槽 == k=N\\0（k=0/越界保持原样）'],
           'details': details}
    with open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print('\n-> ' + ('全部闭合' if allok else '有不闭合的用例'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, allok))
    sys.exit(0 if allok else 1)


if __name__ == '__main__':
    main()
