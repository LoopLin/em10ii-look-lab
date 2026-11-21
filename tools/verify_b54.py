#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b54.py —— B54 离线整链验证（B48 几何 + 矩阵极端四档）

每个用例跑四次 libsim：
  1) 派发器 0x604B8098 -> 0x604B8124：SD 桩把**完整 32 位 key** 写进 stash 0x605DED70
  2) SU 桩 0x605DED80 -> 0x60479A3A：从 stash 读 key、现算 k、写 EXIF、选 tone 表
  3) 上传器 0x6047xxxx：把选中的表拷进 dest（验证真拷的是那张表）
  4) MX 桩 0x601ADE04 -> 0x604B6ADB：同一个 k 选矩阵指针

断言
  C1  派发器回到 0x604B8124，stash == 完整 32 位 key
  C2  tone 指针 == 槽 22..25 中该档那张（k∉1..4 时 == 原厂 *(ctx+0x1230)）
  C3  拷进 dest 的表 == 该表内容（T46=689）
  C4  EXIF：k∈1..4 时 +0x00 == "k=N\\0" 且 +0x04 == key；否则两处都不动
  C5  tone 槽 22..25 四条字节 == reala 重采样曲线
  C6  矩阵指针 == 矩阵数据第 k 块（k∉1..4 时 == 原厂 m0 0x605ECECA）
  C7  矩阵块内容 == 期望的 9 个 i16
  C8  原厂 m0 未变
  C9  ctx+0x1230 未变（不写长期状态）
  C10 bank2（0x605DEF4E 起 2 KB，活区）diff == 0
  C11 SU 桩保住 a1/a3/d2（拷贝循环的输入）与 MX 桩保住 d3/a2
"""
from __future__ import annotations

import hashlib
import io
import json
import struct
import sys
import time

sys.path.insert(0, 'tools')

import verify_b32_chain as V              # noqa: E402
import build_b54 as B54                   # noqa: E402

BASE = 0x60021020
SLOT = 0x200
TONE = 0x605D8344

DISPATCH = 0x604B8098
DISP_CONT = 0x604B8124

SD_INSERT, SD_RAM = 0x604B811D, 0x605DED44
SU_INSERT, SU_RAM = 0x60479A2B, 0x605DED80
COPY_LOOP = 0x60479A3A
MX_INSERT, MX_RAM, MX_RET = 0x604B6AD6, 0x601ADE04, 0x604B6ADB

STASH = 0x605DED70
SU_PTRTAB = 0x605DEDD4
MX_PTRTAB = 0x605DEE3C
MX_DATA = 0x605DEEE0
MX_STRIDE = 18
TONE_ADDRS = B54.TONE_ADDRS
BANK2 = B54.BANK2

MATRIX_M0 = 0x605ECECA
STOCK_LUT = TONE + 1 * SLOT          # 原厂 *(ctx+0x1230) 指向的表（sim 里 poke 这个值）
CH_INSERT = 0x604B4B25
CH_STOCK = b'\x25\xC0\x03'

EXIF_TEXT = 0x61894C90
EXIF_KEY = 0x61894C94
EXIF_REGION = '0x61894000:0x1000'
EXIF_POKE = b'        '

DEST = 0x9E00A400
O = 0x61E10000
S = 0x61E18000
T2 = 0x61E19000
CTX = 0x61E20000
SCRATCH = 0x61E28000
SP = 0x61E0F800


def payload_of(path):
    import oly_container as oc
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def k_of(key):
    return ((key >> 24) & 3) * 8 + (key & 7)


def stash_of(image, *, b0, b1, mode):
    """跑派发器：SD 桩把完整 32 位 key 写进 stash。"""
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
    raw = V.mem_bytes(d, STASH, 4)
    return (int.from_bytes(raw, 'little') if raw else None), d


def su_pick(image, key):
    pokes = [
        (S + 0x1230, STOCK_LUT.to_bytes(4, 'little')),
        (STASH, int(key).to_bytes(4, 'little')),
        (SP + 8, DEST.to_bytes(4, 'little')),
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': S + 0x1230, 'D2': DEST + 0x400, 'A3': 0x61E1A000}
    d = V.sim(image, SU_RAM, COPY_LOOP, limit=400, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,))
    return V.regs_of(d), d


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
              extra_rwz=V.UPLOAD_HEAP + (EXIF_REGION,))
    return V.tone_of_dest(V.mem_bytes(d, DEST, V.DEST_SPAN)), d


def mx_pick(image, key):
    pokes = [(STASH, int(key).to_bytes(4, 'little'))]
    regs = {'A2': CTX, 'A3': SP + 8, 'D3': key, 'D1': 0x01010064}
    d = V.sim(image, MX_RAM, MX_RET, limit=300, pokes=pokes, regs=regs)
    return V.regs_of(d), d


def table_at(image, addr):
    o = addr - BASE
    return list(struct.unpack('<256H', bytes(image[o:o + SLOT])))


def read_mem(d, addr, n):
    """mem_bytes 要求 [addr, addr+n) 落在一个输出段里；跨段时二分拼起来（一路到 1 B）。"""
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


def span_diff(d, ref, span):
    got = read_mem(d, span[0], span[1] - span[0])
    if not got or len(got) != len(ref):
        return None
    return [i for i in range(len(ref)) if got[i] != ref[i]]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'build/B54_b48plus.BIN'
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    stock = payload_of(B54.SRC)

    bank2_ref = bytes(image[BANK2[0] - BASE: BANK2[1] - BASE])
    pre_bank2 = bank2_ref == bytes(stock[BANK2[0] - BASE: BANK2[1] - BASE])
    pre_mx_hook = bytes(image[MX_INSERT - BASE: MX_INSERT - BASE + 5]) == B54.b46.jmp_bytes(MX_INSERT, MX_RAM)
    pre_ch = bytes(image[CH_INSERT - BASE: CH_INSERT - BASE + 3]) == CH_STOCK
    m0_img = bytes(image[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18])
    pre_m0 = m0_img == bytes(stock[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18])
    tone_vals, tone_blob = B54.load_tone()
    pre_tones = all(bytes(image[a - BASE: a - BASE + SLOT]) == tone_blob for a in TONE_ADDRS)
    suptr = [int.from_bytes(bytes(image[SU_PTRTAB - BASE + 4 * i: SU_PTRTAB - BASE + 4 * i + 4]), 'little')
             for i in range(4)]
    pre_suptr = suptr == list(TONE_ADDRS)
    mxptr = [int.from_bytes(bytes(image[MX_PTRTAB - BASE + 4 * i: MX_PTRTAB - BASE + 4 * i + 4]), 'little')
             for i in range(4)]
    pre_mxptr = mxptr == [MX_DATA + i * 18 for i in range(4)]

    print('静态前提：bank2==原厂%s MX钩子=jmp%s 色度钩点原厂%s m0==原厂%s tone 四条==reala%s tone 指针表%s 矩阵指针表%s'
          % (pre_bank2, pre_mx_hook, pre_ch, pre_m0, pre_tones, pre_suptr, pre_mxptr))
    print('          tone 指针表 = %s' % ['0x%X' % v for v in suptr])
    pre_ok = pre_bank2 and pre_mx_hook and pre_ch and pre_m0 and pre_tones and pre_suptr and pre_mxptr

    want_blobs = []
    for _name, rows in B54.MX_CASES:
        flat = B54.enc_matrix(rows)
        want_blobs.append(b''.join(struct.pack('<h', v) for v in flat))

    cases = ([(0x00, 0x01, m) for m in (1, 2, 3, 4)]
             + [(0x00, 0x01, 0), (0x00, 0x01, 7), (0x01, 0x01, 0x64)])
    allok = pre_ok
    details = []
    for b0, b1, mode in cases:
        key = (b0 << 24) | (b1 << 16) | mode
        k_exp = k_of(key)
        in_range = 1 <= k_exp <= 4

        st, dd = stash_of(image, b0=b0, b1=b1, mode=mode)
        sregs, sd = su_pick(image, key)
        a2 = sregs['A2']
        exp_a2 = TONE_ADDRS[k_exp - 1] if in_range else STOCK_LUT
        tone, up = upload(image, key)
        exp_tab = table_at(image, exp_a2)

        mregs, md = mx_pick(image, key)
        a0 = mregs['A0']
        exp_a0 = (MX_DATA + (k_exp - 1) * MX_STRIDE) if in_range else MATRIX_M0
        got_mx = V.mem_bytes(md, a0, MX_STRIDE) or b''
        exp_mx = want_blobs[k_exp - 1] if in_range else m0_img

        exp_text = (b'k=%d\x00' % k_exp) if in_range else EXIF_POKE[:4]
        exp_keyb = key.to_bytes(4, 'little') if in_range else EXIF_POKE[:4]
        got_text = V.mem_bytes(sd, EXIF_TEXT, 4) or b''
        got_key = V.mem_bytes(sd, EXIF_KEY, 4) or b''
        exif_ok = (got_text == exp_text and got_key == exp_keyb)

        c1 = (V.regs_of(dd)['PC'] == DISP_CONT and st == key)
        c2 = (a2 == exp_a2)
        c3 = (tone == exp_tab)
        c6 = (a0 == exp_a0)
        c7 = (got_mx == exp_mx)
        # SU 桩必须保住拷贝循环的输入
        c11 = (sregs['A1'] == DEST + 0x400 and sregs['D2'] == DEST + 0x800
               and sregs['A3'] == 0x61E1A000
               and mregs['D3'] == key and mregs['A2'] == CTX)

        m0_ok = all((V.mem_bytes(d, MATRIX_M0, 18) or b'') == m0_img for d in (dd, sd, up, md))
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

        bank2_ok = True
        for d in (dd, sd, up, md):
            bd = span_diff(d, bank2_ref, BANK2)
            if bd is None or bd:
                bank2_ok = False
                break

        ok = all([pre_ok, c1, c2, c3, c6, c7, m0_ok, ctx_ok, exif_ok, bank2_ok, c11])
        allok &= ok
        print('== b0=%02X b1=%02X mode=0x%04X  key=0x%08X  stash=0x%08X  k=%d（在 1..4=%s）'
              % (b0, b1, mode, key, st or 0, k_exp, in_range))
        print('   dispatcher=%s' % V.describe(dd))
        print('   tone: a2=0x%08X（期望 0x%08X） dest T46=%s 表内 T46=%s'
              % (a2, exp_a2, tone[46] if tone else None, exp_tab[46]))
        print('   mtx : a0=0x%08X（期望 0x%08X） %s'
              % (a0, exp_a0, list(struct.unpack('<9h', got_mx)) if len(got_mx) == 18 else 'n/a'))
        print('   exif: text=%r key=%s（期望 %r / %s）' % (got_text, got_key.hex(), exp_text, exp_keyb.hex()))
        for name, v in (('C1 派发器回到 0x604B8124 且 stash==完整 key', c1),
                        ('C2 tone 指针 == 槽 22..25 之一（或原厂）', c2),
                        ('C3 拷进 dest 的表 == 该表内容', c3),
                        ('C4 EXIF k=N\\0 + key（仅 k=1..4）', exif_ok),
                        ('C5 tone 槽 22..25 == reala 曲线', pre_tones),
                        ('C6 矩阵指针 == 期望块', c6),
                        ('C7 块内容 == 期望矩阵', c7),
                        ('C8 原厂 m0 未变', m0_ok),
                        ('C9 ctx+0x1230 未变', ctx_ok),
                        ('C10 bank2 2 KB diff == 0', bank2_ok),
                        ('C11 SU 保 a1/a3/d2、MX 保 d3/a2', c11)):
            print('     %s  %s' % ('PASS' if v else 'FAIL', name))
        details.append({'b0': b0, 'b1': b1, 'mode': mode, 'key': '0x%08X' % key,
                        'k': st, 'k_exp': k_exp,
                        'tone': '0x%08X' % exp_a2 if in_range else None,
                        'mx': '0x%08X' % a0, 'exif_text': got_text.hex(),
                        'exif_key': got_key.hex(), 'ok': bool(ok)})

    rec = {'pkg': path, 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b54.py', 'cases': len(cases),
           'closed': bool(allok), 'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'checks': ['C1 派发器回 0x604B8124 且 stash==完整 key', 'C2 tone 指针==槽 22..25',
                      'C3 dest 表==该表内容', 'C4 EXIF k=N 与 key（仅 1..4）',
                      'C5 tone 槽字节==reala', 'C6 矩阵指针==期望块', 'C7 块内容==期望矩阵',
                      'C8 原厂 m0 未变', 'C9 ctx+0x1230 未变', 'C10 bank2 diff==0',
                      'C11 SU/MX 寄存器保全'],
           'static': {'bank2_untouched': pre_bank2, 'mx_hook': pre_mx_hook,
                      'chroma_hook_stock': pre_ch, 'm0_stock': pre_m0,
                      'tones': pre_tones, 'tone_ptrtab': ['0x%X' % v for v in suptr],
                      'matrix_ptrtab': ['0x%X' % v for v in mxptr]},
           'details': details}
    with open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print('\n-> ' + ('全部闭合' if allok else '有不闭合的用例'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, allok))
    sys.exit(0 if allok else 1)


if __name__ == '__main__':
    main()
