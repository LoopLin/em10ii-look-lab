#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b53.py —— B53 离线整链验证：tone 表走 bank2、池区零改动、EXIF 写 k 与完整 key。

骨架照 tools/verify_b48.py / verify_b51.py，几何换成 B53：
  钩子 A 0x604B811D -> SD 桩 0x605DF9C8（key 写 EXIF 0x61894C94、k 写 0x61894C98；SU/MX 桩从 0x61894C98 读）
  钩子 B 0x60479A2B -> SU 桩 0x605DF9F0（k=1..4 查 0x605DF950 选 bank2 四条表；"k=N\\0" 写 0x61894C90）
  钩子 C 0x604B6AD6 -> MX 桩 0x605DFA38（k=1..4 查 0x605DF964 选 4x18 B 矩阵）

每个用例断言：
  C1 分派器回到 0x604B8124        C2 stash == k = ((key>>24)&3)<<3|(key&7)
  C3 tone 指针 a2 == bank2 槽（k=1..4）否则原厂表指针
  C4 上传器拷进 dest 的表 == 该表内容
  C5 矩阵指针 a0 == 期望 18 B 块  C6 块内容 == 期望矩阵
  C7 原厂 m0 未变                 C8 ctx+0x1230 / S+0x1230 未变
  C9 EXIF：+0x00 == "k=N\\0"（仅 k=1..4）、+0x04 == 完整 key 小端（任何 key 都写）
  C10 tone 池 0x605D8344..0x605DEF44 在每次 sim 后逐字节未变
  C11 MX 桩保住 d0 / d3
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
import build_b53 as B53                                      # noqa: E402

BASE = V.BASE
TONE, SLOT = V.TONE, V.SLOT

DISPATCH = 0x604B8098
DISP_CONT = 0x604B8124
SD_INSERT, SD_RAM = 0x604B811D, 0x605DF9C8
SU_INSERT, SU_RAM = 0x60479A2B, 0x605DF9F0
COPY_LOOP = 0x60479A3A
MX_INSERT, MX_RAM, MX_RET = 0x604B6AD6, 0x605DFA38, 0x604B6ADB

PTRTAB = 0x605DF950
MX_PTRTAB = 0x605DF964
MX_DATA = 0x605DF978
MX_STRIDE = 18
BANK2 = B53.BANK2_SLOTS
POOL = B53.POOL

MATRIX_M0 = 0x605ECECA
STOCK_LUT = TONE + 1 * SLOT
CH_INSERT = 0x604B4B25
CH_STOCK = b'\x25\xC0\x03'

EXIF_TEXT = 0x61894C90
EXIF_KEY = 0x61894C94        # SD 桩写完整 32 位 key
EXIF_K = 0x61894C98          # SD 桩写 k 的 32 位值；SU/MX 桩从这里读（不再有镜像 stash）
EXIF_REGION = '0x61894000:0x1000'
EXIF_POKE = b'        '

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


def k_of(key):
    return (((key >> 24) & 3) << 3) | (key & 7)


def stash_of(image, *, b0, b1, mode):
    pokes = [
        (CTX + 0x3C0, (0).to_bytes(4, 'little')),
        (CTX + 0x436, b'\x00\x00'),
        (CTX + 8, bytes([b0 & 0xFF])),
        (CTX + 9, bytes([b1 & 0xFF])),
        (CTX + 0x0A, struct.pack('<H', mode & 0xFFFF)),
        (CTX + 0x1230, (0xC0FFEE00).to_bytes(4, 'little')),
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': CTX, 'A1': SCRATCH}
    d = V.sim(image, DISPATCH, DISP_CONT, limit=300000, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,))
    rk = V.mem_bytes(d, EXIF_K, 4)
    rkey = V.mem_bytes(d, EXIF_KEY, 4)
    return ((int.from_bytes(rk, 'little') if rk else None),
            (bytes(rkey) if rkey else None), d)


def su_pick(image, k, key=None):
    pokes = [
        (S + 0x1230, STOCK_LUT.to_bytes(4, 'little')),
        (EXIF_K, int(k).to_bytes(4, 'little')),
        (EXIF_K + 4, EXIF_POKE[:4]),
        (SP + 8, DEST.to_bytes(4, 'little')),
        (EXIF_TEXT, EXIF_POKE),
    ]
    if key is not None:
        pokes.append((EXIF_KEY, int(key).to_bytes(4, 'little')))
    regs = {'A0': S + 0x1230, 'D2': DEST + 0x400}
    d = V.sim(image, SU_RAM, COPY_LOOP, limit=400, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,))
    return V.regs_of(d)['A2'], d


def upload(image, k, key=None):
    pokes = [
        (O + 0x0C, S.to_bytes(4, 'little')),
        (O + 0x64, (4).to_bytes(4, 'little')),
        (O + 0x34, (0).to_bytes(4, 'little')),
        (O + 0xBD, b'\x01'),
        (S + 0x41C, (0).to_bytes(4, 'little')),
        (S + 0x420, T2.to_bytes(4, 'little')),
        (S + 0x1230, STOCK_LUT.to_bytes(4, 'little')),
        (EXIF_K, int(k).to_bytes(4, 'little')),
        (EXIF_K + 4, EXIF_POKE[:4]),
        (EXIF_TEXT, EXIF_POKE),
    ]
    if key is not None:
        pokes.append((EXIF_KEY, int(key).to_bytes(4, 'little')))
    regs = {'A0': DEST, 'A1': O}
    d = V.sim(image, V.UPLOAD, V.UPLOAD_MERGE, limit=400000, pokes=pokes, regs=regs,
              extra_rwz=UPLOAD_HEAP + (EXIF_REGION,))
    return V.tone_of_dest(V.mem_bytes(d, DEST, V.DEST_SPAN)), d


def mx_pick(image, k):
    """钩子 C：只改 a0（返回矩阵指针）与临时 d0；d3 与 a2 必须原样。"""
    pokes = [(EXIF_K, int(k).to_bytes(4, 'little')),
             (EXIF_K + 4, EXIF_POKE[:4])]
    regs = {'A2': CTX, 'A0': SP + 8, 'D0': 0xDEADBEEF, 'D3': 0x01010064}
    d = V.sim(image, MX_RAM, MX_RET, limit=200, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,))
    return V.regs_of(d), d


def table_at(image, addr):
    o = addr - BASE
    return list(struct.unpack('<256H', bytes(image[o:o + SLOT])))


def read_mem(d, addr, n):
    """mem_bytes 要求 [addr, addr+n) 落在同一个输出段里；跨段时二分拼起来。
    段边界（0x605DE000 等）会切出 3 B 这种跨界小块，所以一路二分到 1 B。"""
    b = V.mem_bytes(d, addr, n)
    if b:
        return b
    if n <= 1:
        return b''
    h = n // 2
    lo = read_mem(d, addr, h)
    hi = read_mem(d, addr + h, n - h)
    if not lo or not hi:
        return b''
    return lo + hi


def pool_diff(d, ref):
    got = read_mem(d, POOL[0], POOL[1] - POOL[0])
    if not got or len(got) != len(ref):
        return None
    return [i for i in range(len(ref)) if got[i] != ref[i]]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'build/B53_bank2.BIN'
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    stock = payload_of(B53.SRC)
    pool_ref = bytes(image[POOL[0] - BASE: POOL[1] - BASE])

    # ---- 静态前提 ----
    pre_pool = pool_ref == bytes(stock[POOL[0] - BASE: POOL[1] - BASE])
    pre_mx_hook = bytes(image[MX_INSERT - BASE: MX_INSERT - BASE + 5]) == B53.b46.jmp_bytes(MX_INSERT, MX_RAM)
    pre_ch = bytes(image[CH_INSERT - BASE: CH_INSERT - BASE + 3]) == CH_STOCK
    m0_img = bytes(image[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18])
    pre_m0 = m0_img == bytes(stock[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18])
    tone_vals, tone_blob = B53.load_tone()
    pre_bank2 = all(bytes(image[a - BASE: a - BASE + SLOT]) == tone_blob for a in BANK2)
    pre_ptr = [int.from_bytes(bytes(image[PTRTAB - BASE + 4 * i: PTRTAB - BASE + 4 * i + 4]), 'little')
               for i in range(5)]
    pre_ptr_ok = pre_ptr[1:] == list(BANK2)
    print('静态前提：池区==原厂%s MX钩子=jmp%s 色度钩点原厂%s m0==原厂%s bank2 四条==reala%s 指针表%s'
          % (pre_pool, pre_mx_hook, pre_ch, pre_m0, pre_bank2, pre_ptr_ok))
    print('          tone 指针表 = %s' % ['0x%X' % v for v in pre_ptr])
    pre_ok = pre_pool and pre_mx_hook and pre_ch and pre_m0 and pre_bank2 and pre_ptr_ok

    want_blobs = []
    for _name, rows in B53.MX_CASES:
        flat = B53.enc_matrix(rows)
        want_blobs.append(b''.join(struct.pack('<h', v) for v in flat))

    cases = ([(0x00, 0x01, m) for m in (1, 2, 3, 4)]
             + [(0x00, 0x01, 0), (0x00, 0x01, 7),
                (0x01, 0x01, 0x64), (0x01, 0x01, 0x66)])
    allok = pre_ok
    details = []
    for b0, b1, mode in cases:
        key = (b0 << 24) | (b1 << 16) | mode
        k_exp = k_of(key)
        in_range = 1 <= k_exp <= 4

        st, key_got, dd = stash_of(image, b0=b0, b1=b1, mode=mode)
        kk = st if st is not None else 0xFF
        range_st = 1 <= kk <= 4

        a2, sd = su_pick(image, kk, key=key)
        exp_a2 = BANK2[kk - 1] if range_st else STOCK_LUT
        tone, up = upload(image, kk, key=key)
        exp_tab = table_at(image, exp_a2)

        regs, md = mx_pick(image, kk)
        a0 = regs['A0']
        exp_a0 = (MX_DATA + (kk - 1) * MX_STRIDE) if range_st else MATRIX_M0
        got_mx = V.mem_bytes(md, a0, MX_STRIDE) or b''
        exp_mx = want_blobs[kk - 1] if range_st else m0_img
        sentinel_ok = (regs['D0'] == 0xDEADBEEF and regs['D3'] == 0x01010064)

        exp_text = (b'k=%d\x00' % kk) if range_st else EXIF_POKE[:4]
        exp_keyb = key.to_bytes(4, 'little')
        got_text_sd = V.mem_bytes(sd, EXIF_TEXT, 4) or b''
        got_text_up = V.mem_bytes(up, EXIF_TEXT, 4) or b''
        got_key_sd = V.mem_bytes(sd, EXIF_KEY, 4) or b''
        got_key_up = V.mem_bytes(up, EXIF_KEY, 4) or b''
        zero_k = (0).to_bytes(4, 'little')
        got_k = [V.mem_bytes(d, EXIF_K, 4) or b'?' for d in (sd, up, md)]
        k_ok = all(g == kk.to_bytes(4, 'little') for g in got_k)
        exif_ok = (got_text_sd == exp_text and got_text_up == exp_text
                   and got_key_sd == exp_keyb and got_key_up == exp_keyb and k_ok)

        m0_ok = True
        for d in (dd, sd, up, md):
            if (V.mem_bytes(d, MATRIX_M0, 18) or b'') != m0_img:
                m0_ok = False
        ZERO4 = (0).to_bytes(4, 'little')
        ctx_ok = True
        for d, exp_c, exp_s in ((dd, (0xC0FFEE00).to_bytes(4, 'little'), ZERO4),
                                (sd, ZERO4, STOCK_LUT.to_bytes(4, 'little')),
                                (up, ZERO4, STOCK_LUT.to_bytes(4, 'little')),
                                (md, ZERO4, ZERO4)):
            if (V.mem_bytes(d, CTX + 0x1230, 4) or b'?') != exp_c:
                ctx_ok = False
            if (V.mem_bytes(d, S + 0x1230, 4) or b'?') != exp_s:
                ctx_ok = False

        pool_ok = True
        for d in (dd, sd, up, md):
            pd = pool_diff(d, pool_ref)
            if pd is None or pd:
                pool_ok = False
                break

        c1 = V.regs_of(dd)['PC'] == DISP_CONT
        c2 = (st == k_exp and key_got == key.to_bytes(4, 'little'))
        c3 = (a2 == exp_a2)
        c4 = (tone == exp_tab)
        c5 = (a0 == exp_a0)
        c6 = (got_mx == exp_mx)
        ok = all([pre_ok, c1, c2, c3, c4, c5, c6, m0_ok, ctx_ok, exif_ok, pool_ok, sentinel_ok])
        allok &= ok
        print('== b0=%02X b1=%02X mode=0x%04X  key=0x%08X  k=%s（期望 %d，1..4=%s）'
              % (b0, b1, mode, key, st, k_exp, range_st))
        print('   dispatcher=%s' % V.describe(dd))
        print('   tone: a2=0x%08X（期望 0x%08X） dest T46=%s 表内 T46=%s'
              % (a2, exp_a2, tone[46] if tone else None, exp_tab[46]))
        print('   mtx : a0=0x%08X（期望 0x%08X） %s'
              % (a0, exp_a0, list(struct.unpack('<9h', got_mx)) if len(got_mx) == 18 else 'n/a'))
        print('   exif: text=%r key=%s（期望 %r / %s）'
              % (got_text_sd, got_key_sd.hex(), exp_text, exp_keyb.hex()))
        for name, v in (('C1 分派器回到 0x604B8124', c1), ('C2 EXIF k 槽 == k（且 key 槽 == 完整 key）', c2),
                        ('C3 tone 指针 == bank2 槽（或原厂）', c3),
                        ('C4 拷进 dest 的表 == 该表内容', c4),
                        ('C5 矩阵指针 == 期望块', c5), ('C6 块内容 == 期望矩阵', c6),
                        ('C7 原厂 m0 未变', m0_ok), ('C8 ctx+0x1230 未变', ctx_ok),
                        ('C9 EXIF text=k=N\\0、key=完整 32 位、k 槽 == k', exif_ok),
                        ('C10 tone 池零改动', pool_ok), ('C11 MX 桩保住 d0/d3', sentinel_ok)):
            print('     %s  %s' % ('PASS' if v else 'FAIL', name))
        details.append({'b0': b0, 'b1': b1, 'mode': mode, 'key': '0x%08X' % key,
                        'k': st, 'k_exp': k_exp, 'bank2': '0x%08X' % exp_a2 if range_st else None,
                        'mx': '0x%08X' % a0, 'exif_text': got_text_sd.hex(),
                        'exif_key': got_key_sd.hex(), 'ok': bool(ok)})

    rec = {'pkg': path, 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b53.py', 'cases': len(cases),
           'closed': bool(allok), 'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'checks': ['C1 分派器回到续跑点', 'C2 EXIF k 槽==k', 'C3 tone 指针==bank2 槽',
                      'C4 dest 表==该表内容', 'C5 矩阵指针==期望块', 'C6 块内容==期望矩阵',
                      'C7 原厂 m0 未变', 'C8 ctx+0x1230 未变',
                      'C9 EXIF 写 k=N\\0 + 完整 key + k 槽', 'C10 tone 池零改动',
                      'C11 d0/d3 保住'],
           'static': {'pool_untouched': pre_pool, 'mx_hook': pre_mx_hook,
                      'chroma_hook_stock': pre_ch, 'm0_stock': pre_m0,
                      'bank2_tables': pre_bank2, 'tone_ptrtab': ['0x%X' % v for v in pre_ptr]},
           'details': details}
    with open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print('\n-> ' + ('全部闭合' if allok else '有不闭合的用例'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, allok))
    sys.exit(0 if allok else 1)


if __name__ == '__main__':
    main()
