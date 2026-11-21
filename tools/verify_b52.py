#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b52.py —— B52 离线整链验证：只验"完整 32 位 key 抄进 EXIF"，画面必须不变。

B52 是**仪表包**：tone 不动、矩阵不动，只留两条钩子 ——
  钩子 A 0x604B811D -> SD 桩 0x605DED44（`mov d1,d2` 重放后把**完整 32 位 key**写 0x605DED70）
  钩子 B 0x60479A2B -> SU 桩 0x605DED80（a2 保持原厂 *(ctx+0x1230)；stash 4 字节原样写 0x61894C90）

每个用例断言：
  C1 分派器回到 0x604B8124（续跑点）
  C2 stash == 完整 32 位 key（不是 k=((key>>24)&3)<<3|(key&7) 那个截断值）
  C3 EXIF 文本槽 4 字节 == key 的小端原样（二进制，不转 ASCII）
  C4 SU 桩给上传器的 a2 == 传进去的原厂表指针（tone 选表逻辑不在了）
  C5 上传器拷进 dest 的表 == 原厂槽 1 的表（画面通道不变）
  C6 跑完原厂 m0 0x605ECECA 逐字节未变
  C7 跑完 ctx+0x1230 / S+0x1230 逐字节未变（按每次 sim 的 poke 分别给期望）
  C8 tone 池 27648 B 里，除桩+stash 那 62 B 口袋外逐字节未变
  C9 静态：矩阵钩点 0x604B6AD6 仍是原厂 call、色度钩点仍是原厂、槽 9/11/12/13 == 原厂
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
import build_b52 as B52                                      # noqa: E402

BASE = V.BASE
TONE, SLOT = V.TONE, V.SLOT
POOL = B52.POOL_LEN

DISPATCH = 0x604B8098
DISP_CONT = 0x604B8124
SD_INSERT = 0x604B811D
SD_RAM = 0x605DED44
SU_INSERT = 0x60479A2B
SU_RAM = 0x605DED80
COPY_LOOP = 0x60479A3A
STASH = 0x605DED70
EXIF_TEXT = 0x61894C90
EXIF_REGION = '0x61894000:0x1000'
EXIF_POKE = b'        '

MX_HOOK = 0x604B6AD6
MX_STOCK = b'\xCD\xBF\xFF'
CH_INSERT = 0x604B4B25
CH_STOCK = b'\x25\xC0\x03'
MATRIX_M0 = 0x605ECECA
SLOTS = [9, 11, 12, 13]

STOCK_LUT = TONE + 1 * SLOT

DEST = 0x9E00A400
O = 0x61E10000
S = 0x61E18000
T2 = 0x61E19000
CTX = 0x61E20000
SCRATCH = 0x61E28000
SP = 0x61E0F800

UPLOAD_HEAP = V.UPLOAD_HEAP

# 桩 + stash 的"口袋"（唯一允许在原 tone 池里出现的改动）
POCKET = (SD_RAM, SU_RAM + 0x60)


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
    return (int.from_bytes(raw, 'little') if raw else None), d


def su_run(image, key):
    pokes = [
        (S + 0x1230, STOCK_LUT.to_bytes(4, 'little')),
        (STASH, int(key).to_bytes(4, 'little')),
        (SP + 8, DEST.to_bytes(4, 'little')),
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': S + 0x1230, 'D2': DEST + 0x400}
    d = V.sim(image, SU_RAM, COPY_LOOP, limit=400, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,))
    return V.regs_of(d)['A2'], d


def upload(image, key):
    pokes = [
        (O + 0x0C, S.to_bytes(4, 'little')),
        (O + 0x64, (4).to_bytes(4, 'little')),
        (O + 0x34, (0).to_bytes(4, 'little')),
        (O + 0xBD, b'\x01'),
        (S + 0x41C, (0).to_bytes(4, 'little')),
        (S + 0x420, T2.to_bytes(4, 'little')),
        (S + 0x1230, STOCK_LUT.to_bytes(4, 'little')),
        (STASH, int(key).to_bytes(4, 'little')),
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': DEST, 'A1': O}
    d = V.sim(image, V.UPLOAD, V.UPLOAD_MERGE, limit=400000, pokes=pokes, regs=regs,
              extra_rwz=UPLOAD_HEAP + (EXIF_REGION,))
    return V.tone_of_dest(V.mem_bytes(d, DEST, V.DEST_SPAN)), d


def table_of(image, slot):
    o = TONE + slot * SLOT - BASE
    return list(struct.unpack('<256H', bytes(image[o:o + SLOT])))


def pool_diffs(d, image):
    """tone 池里跑完与镜像不同的字节偏移（相对 0x605D8344）。"""
    got = V.mem_bytes(d, TONE, POOL)
    if got is None:
        return None
    ref = bytes(image[TONE - BASE: TONE - BASE + POOL])
    return [i for i in range(POOL) if got[i] != ref[i]]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'build/B52_keytap.BIN'
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    stock = payload_of(B52.SRC)

    # ---- C9 静态前提：本包没动 tone 槽、没动矩阵、没动色度 ----
    pre_mx = bytes(image[MX_HOOK - BASE: MX_HOOK - BASE + 3]) == MX_STOCK
    pre_ch = bytes(image[CH_INSERT - BASE: CH_INSERT - BASE + 3]) == CH_STOCK
    m0_img = bytes(image[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18])
    m0_ok_static = m0_img == bytes(stock[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18])
    slots_ok = {s: bytes(image[TONE + s * SLOT - BASE: TONE + s * SLOT - BASE + SLOT])
                == bytes(stock[TONE + s * SLOT - BASE: TONE + s * SLOT - BASE + SLOT])
                for s in SLOTS}
    po, pl = POCKET
    pool_out = [i for i in range(POOL)
                if image[TONE - BASE + i] != stock[TONE - BASE + i]
                and not (po - TONE <= i < pl - TONE)]
    print('静态前提：矩阵钩点原厂=%s 色度钩点原厂=%s m0==stock=%s 槽9/11/12/13==原厂=%s'
          % (pre_mx, pre_ch, m0_ok_static, slots_ok))
    print('          tone 池口袋外改动=%d（口袋 %#x..%#x）' % (len(pool_out), po, pl))
    pre_ok = pre_mx and pre_ch and m0_ok_static and all(slots_ok.values()) and not pool_out

    # 矩阵极端档在 B52 里应当完全不存在：数据区必须还是原厂
    mx_data_img = bytes(image[B52.MX_DATA - BASE: B52.MX_DATA - BASE + 72]) if hasattr(B52, 'MX_DATA') else None

    cases = ([(0x00, 0x01, m) for m in (1, 2, 3, 4)]
             + [(0x00, 0x01, 0), (0x00, 0x01, 7),
                (0x01, 0x01, 0x64), (0x01, 0x01, 0x66)])
    allok = pre_ok
    details = []
    for b0, b1, mode in cases:
        key = (b0 << 24) | (b1 << 16) | mode
        k_old = (((key >> 24) & 3) << 3) | (key & 7)     # B51 那个截断值，仅用于对照打印
        st, dd = stash_of(image, b0=b0, b1=b1, mode=mode)

        a2, sd = su_run(image, key if st is None else st)
        tone, up = upload(image, key if st is None else st)
        exp_key = key
        exp_exif = exp_key.to_bytes(4, 'little')
        exp_a2 = STOCK_LUT
        exp_tab = table_of(image, 1)

        got_exif_sd = V.mem_bytes(sd, EXIF_TEXT, 4) or b''
        got_exif_up = V.mem_bytes(up, EXIF_TEXT, 4) or b''

        c1 = V.regs_of(dd)['PC'] == DISP_CONT
        c2 = (st == exp_key)
        c3 = (got_exif_sd == exp_exif and got_exif_up == exp_exif)
        c4 = (a2 == exp_a2)
        c5 = (tone == exp_tab)
        c6 = True
        for d in (dd, sd, up):
            if (V.mem_bytes(d, MATRIX_M0, 18) or b'') != m0_img:
                c6 = False
        ZERO4 = (0).to_bytes(4, 'little')
        c7 = True
        for d, exp_c, exp_s in ((dd, (0xC0FFEE00).to_bytes(4, 'little'), ZERO4),
                                (sd, ZERO4, STOCK_LUT.to_bytes(4, 'little')),
                                (up, ZERO4, STOCK_LUT.to_bytes(4, 'little'))):
            if (V.mem_bytes(d, CTX + 0x1230, 4) or b'?') != exp_c:
                c7 = False
            if (V.mem_bytes(d, S + 0x1230, 4) or b'?') != exp_s:
                c7 = False
        # 池：只允许 stash 那 4 字节变（SD 桩把 key 写进去）
        c8 = True
        for d in (dd, sd, up):
            dd_off = pool_diffs(d, image)
            if dd_off is None:
                c8 = False
                break
            bad = [i for i in dd_off if not (STASH - TONE <= i < STASH - TONE + 4)]
            if bad:
                c8 = False
                break

        ok = all([pre_ok, c1, c2, c3, c4, c5, c6, c7, c8])
        allok &= ok
        print('== b0=%02X b1=%02X mode=0x%04X  key=0x%08X（B51 的 k 只会是 %d）'
              % (b0, b1, mode, key, k_old))
        print('   dispatcher=%s' % V.describe(dd))
        print('   stash=0x%08X（期望 0x%08X）' % (st or 0, exp_key))
        print('   exif 0x%08X = %s（期望 %s）' % (EXIF_TEXT, got_exif_sd.hex(), exp_exif.hex()))
        print('   tone: a2=0x%08X（期望 0x%08X=原厂槽1） dest T46=%s 原厂 T46=%s'
              % (a2, exp_a2, tone[46] if tone else None, exp_tab[46]))
        for name, v in (('C1 分派器回到 0x604B8124', c1),
                        ('C2 stash == 完整 32 位 key', c2),
                        ('C3 EXIF 4 字节 == key 小端原样', c3),
                        ('C4 a2 == 原厂表指针（无选表逻辑）', c4),
                        ('C5 dest 表 == 原厂槽 1', c5),
                        ('C6 原厂 m0 未变', c6),
                        ('C7 ctx+0x1230 未变', c7),
                        ('C8 tone 池仅 stash 4 B 变化', c8)):
            print('     %s  %s' % ('PASS' if v else 'FAIL', name))
        details.append({'b0': b0, 'b1': b1, 'mode': mode, 'key': '0x%08X' % key,
                        'stash': '0x%08X' % st if st is not None else None,
                        'exif': got_exif_sd.hex(), 'b51_k': k_old, 'ok': bool(ok)})

    rec = {'pkg': path, 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b52.py', 'cases': len(cases),
           'closed': bool(allok), 'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'checks': ['C1 分派器回到续跑点', 'C2 stash==完整 32 位 key',
                      'C3 EXIF 4 字节==key 小端', 'C4 a2==原厂表指针',
                      'C5 dest 表==原厂槽1', 'C6 原厂 m0 未变',
                      'C7 ctx+0x1230 未变', 'C8 tone 池仅 stash 4B 变化'],
           'static': {'mx_hook_stock': pre_mx, 'chroma_hook_stock': pre_ch,
                      'm0_stock': m0_ok_static, 'slots_stock': slots_ok,
                      'pool_bytes_changed_outside_pocket': len(pool_out),
                      'pocket': ['0x%X' % po, '0x%X' % pl]},
           'details': details}
    with open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print('\n-> ' + ('全部闭合' if allok else '有不闭合的用例'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, allok))
    sys.exit(0 if allok else 1)


if __name__ == '__main__':
    main()
