#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b68.py — B67 离线验证：成片色度选择器钩子 + SU 桩（不再写 slot1）

B67 的目标是「成片每档独立色度」。静态结论（主线已确认）：成片色度不走
slot1，而走选择器 0x605DED1C 的路径 2 —— 0x604B4B38 判 ctx+0x42F==2，再判
u16(ctx+0xC) ∈ {0xA400,0xA800,0xA900}；命中后 0x604B4CAD 固定返回库存块
0x605D4829。B67 把那条 6 B 指令换成 jmp+nop 到新桩，按 stash(0x605DED70)
的 k 返回 BLK_TAB[k-1]。

用例：
  A 选择器：ctx 放 HEAP 0x61E00000，+0x3C0=0、+0x42F=2、+0xC=0xA400；
    stash=1..4 -> a0 == BLK_TAB[k-1]（=0x605DEB44+0x64*(k-1)）；
    stash=0/5/6/7 -> a0 == 0x605D4829。从 0x605DED1C 跑到 0x604B4CFA。
  B SU 桩：k=1..4 EXIF 文本是 "k=N\\0"、+4 是完整 key、a0 是 tone 指针表
    单元格；slot1 区（0x61E30000 填 0xEE）一个字节都没变。k 不在 1..4 时
    EXIF 不动、a0 仍是 ctx+0x1230。
  C 静态：0x604B4CAD 是 jmp（+nop），选择器桩在 SU 窗口内、两桩 ≤112 B，
    桩字节 == build_b67 的汇编结果，四张块与 B63 逐字节相同。
  D 与 B66 的差异只在 SU 桩窗口与 6 B 新钩。

用法：
  python tools/verify_b68.py                 # 默认 build/B67_slot1.BIN
  python tools/verify_b68.py build/B67_slot1.BIN
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import time

sys.path.insert(0, 'tools')

import oly_container as oc                 # noqa: E402
import verify_b32_chain as V              # noqa: E402
import verify_b59 as W                    # noqa: E402
import build_b46 as b46                   # noqa: E402
import build_b54 as B54                   # noqa: E402
import build_b61 as B61                   # noqa: E402
import build_b63 as B63                   # noqa: E402
import build_b68 as B68                   # noqa: E402

BASE = B68.BASE
STASH = B68.STASH
SU_STUB = B68.SU_STUB
SU_RET = B68.SU_RET
HOOK = B68.HOOK
CH_HOOK = B68.CH_HOOK
CH_ADDRS = B68.CH_ADDRS
CH_STRIDE = B68.CH_STRIDE
CH_STOCK = B68.CH_STOCK
SU_PTRTAB = B68.SU_PTRTAB
BLK_TAB = B68.BLK_TAB
OLD_SU_STUB, OLD_SU_LEN = B68.OLD_SU_STUB, B68.OLD_SU_LEN
EXIF_TEXT = B68.EXIF_TEXT
EXIF_KEY = B54.EXIF_KEY
EXIF_REGION = W.EXIF_REGION
EXIF_POKE = W.EXIF_POKE
MATRIX_M0 = B54.MATRIX_M0
BANK2 = B54.BANK2
TONE_ADDRS = B54.TONE_ADDRS

SEL_INSERT, SEL_LEN = B68.SEL_INSERT, B68.SEL_LEN
SEL_STOCK = B68.SEL_STOCK
SEL_RET = B68.SEL_RET
SEL_STUB_ENTRY = 0x605DED1C
SEL_ALT_RET = 0x604B4B24  # 原选择器入口（不应命中）
NO_TONE = 0x60479A4E

DEST = W.DEST
S = W.S
SP = W.SP
SLOT1 = 0x61E30000
SLOT1_SPAN = 0x200
SLOT_FILL = 0xEE
STOCK_LUT = W.STOCK_LUT
STUB_A3 = 0x61E1A000
SEL_CTX = 0x61E00000          # 选择器用的 ctx（HEAP 0x61e00000:0x40000 内）
SEL_CTX_DISP = 0x3C0
SEL_CTX_MODE = 0x42F
SEL_CTX_CODE = 0x0C

B63_PKG = B68.B63_PKG
B66_PKG = B68.B66_PKG
BASE_PKG = B68.SRC


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def read_mem(d, addr, n):
    return W.read_mem(d, addr, n)


def sel_run(image, stash, code=0xA400, mode=2, disp=0):
    """从 0x605DED1C 跑到 0x604B4CFA（或 0x604B4CFA），返回 (regs, d)。"""
    pokes = [
        (SEL_CTX + SEL_CTX_DISP, int(disp).to_bytes(4, 'little')),
        (SEL_CTX + SEL_CTX_MODE, bytes([mode & 0xFF])),
        (SEL_CTX + SEL_CTX_CODE, int(code).to_bytes(2, 'little')),
        (STASH, int(stash).to_bytes(4, 'little')),
    ]
    d = V.sim(image, SEL_STUB_ENTRY, 0xFFFFFFFF, limit=20000,
              bps=(SEL_RET, SEL_ALT_RET), regs={'A0': SEL_CTX}, pokes=pokes)
    return V.regs_of(d), d


def stub_run(image, key, *, slot1=SLOT1, tone_ptr=STOCK_LUT):
    pokes = [
        (SP + 8, DEST.to_bytes(4, 'little')),
        (S + 0x1230, int(tone_ptr).to_bytes(4, 'little')),
        (S + 0x37D8, int(slot1).to_bytes(4, 'little')),
        (SLOT1, bytes([SLOT_FILL]) * SLOT1_SPAN),
        (STASH, int(key).to_bytes(4, 'little')),
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': S + 0x1230, 'D2': DEST + 0x400, 'A3': STUB_A3}
    d = V.sim(image, SU_STUB, SU_RET, limit=4000, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,), bps=(NO_TONE,))
    return V.regs_of(d), d


def upload_run(image, key, *, slot1=SLOT1):
    pokes = [
        (W.O + 0x0C, S.to_bytes(4, 'little')),
        (W.O + 0x64, (4).to_bytes(4, 'little')),
        (W.O + 0x34, (0).to_bytes(4, 'little')),
        (W.O + 0xBD, b'\x01'),
        (S + 0x41C, (0).to_bytes(4, 'little')),
        (S + 0x420, W.T2.to_bytes(4, 'little')),
        (S + 0x1230, STOCK_LUT.to_bytes(4, 'little')),
        (S + 0x37D8, int(slot1).to_bytes(4, 'little')),
        (SLOT1, bytes([SLOT_FILL]) * SLOT1_SPAN),
        (STASH, int(key).to_bytes(4, 'little')),
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': DEST, 'A1': W.O}
    d = V.sim(image, V.UPLOAD, V.UPLOAD_MERGE, limit=400000, pokes=pokes, regs=regs,
              extra_rwz=V.UPLOAD_HEAP + (EXIF_REGION,))
    return V.regs_of(d), d


# 选择器返回的块在 0x604B4E42 起的「消费段」里被解码进 obj+0xA4 / obj+0xE8 两张表。
# 段内只读 block+0x00..0x63，写 obj+0xA4..obj+0x127（固定 16+16 项，与块内容无关）。
DECODE_ENTRY = 0x604B4E42
DECODE_STOP = 0x604B4ED6
DECODE_OBJ = 0x61E08000
DECODE_FILL = 0xEE


def decode_run(image, blk):
    """用极端块跑消费段：obj 填 0xEE，看是否 fault、写到哪里、值对不对。"""
    pokes = [
        (SP + 8, DECODE_OBJ.to_bytes(4, 'little')),
        (DECODE_OBJ, bytes([DECODE_FILL]) * 0x200),
    ]
    d = V.sim(image, DECODE_ENTRY, DECODE_STOP, limit=20000, pokes=pokes,
              regs={'A0': int(blk)})
    return V.regs_of(d), d
def main():
    path = sys.argv[1] if len(sys.argv) > 1 else B68.OUT
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    stock = payload_of(BASE_PKG)
    b63_img = payload_of(B63_PKG)
    b66_img = payload_of(B66_PKG)

    def at(addr, n, buf=None):
        buf = image if buf is None else buf
        return bytes(buf[addr - BASE:addr - BASE + n])

    # ---------- C 静态 ----------
    su_stub, su_j = B68.assemble_su()
    sel_stub = B68.align4(SU_STUB + len(su_stub))
    sel_stub_b, sel_j = B68.assemble_sel(sel_stub)
    want_hook = b46.jmp_bytes(SEL_INSERT, sel_stub) + bytes([B68.NOP])
    got_hook = at(SEL_INSERT, SEL_LEN)
    hook_ok = got_hook == want_hook
    hook_target = SEL_INSERT + struct.unpack('<i', got_hook[1:5])[0]
    hook_target_ok = hook_target == sel_stub

    st = {}
    st['hook_d_jmp'] = hook_ok
    st['hook_d_target'] = hook_target_ok
    st['hook_d_nop'] = got_hook[5] == B68.NOP
    st['sel_ret_stock'] = at(SEL_RET, 3) == at(SEL_RET, 3, stock)
    st['sel_after_stock'] = at(SEL_INSERT + SEL_LEN, 16) == at(SEL_INSERT + SEL_LEN, 16, stock)
    st['su_stub_bytes'] = at(SU_STUB, len(su_stub)) == su_stub
    st['sel_stub_bytes'] = at(sel_stub, len(sel_stub_b)) == sel_stub_b
    st['sel_stub_addr'] = sel_stub
    st['stub_total_len'] = sel_stub + len(sel_stub_b) - SU_STUB
    st['stub_in_window'] = sel_stub + len(sel_stub_b) <= B68.SU_SLOT_END
    st['stub_len_le_112'] = st['stub_total_len'] <= 112
    st['sel_before_sel_stub'] = sel_stub >= SU_STUB + len(su_stub)
    try:
        B68.check_no_slot1(su_stub, 'SU 桩')
        B68.check_no_slot1(sel_stub_b, '选择器桩')
        st['no_slot1_code'] = True
    except SystemExit:
        st['no_slot1_code'] = False

    blk_ok = True
    blk_from_spec = True
    stock_blk = at(CH_STOCK, CH_STRIDE, stock)
    for i, a in enumerate(CH_ADDRS):
        blk_ok &= at(a, CH_STRIDE) == at(a, CH_STRIDE, b63_img)
        spec = B68.BLOCK_SPEC[i]
        blk_from_spec &= at(a, CH_STRIDE) == B61.extreme_block(spec[0], spec[1], stock_blk)
    st['blocks_eq_b63'] = blk_ok
    st['blocks_eq_spec'] = blk_from_spec
    st['blk_tab'] = struct.unpack('<4I', at(BLK_TAB, 16)) == tuple(CH_ADDRS)
    st['su_ptrtab'] = struct.unpack('<4I', at(SU_PTRTAB, 16)) == tuple(TONE_ADDRS)
    st['hook_b'] = at(HOOK, 5) == b46.jmp_bytes(HOOK, SU_STUB)
    st['ch_hook_stock'] = at(CH_HOOK, 5) == at(CH_HOOK, 5, stock)
    st['stock_block_stock'] = at(CH_STOCK, CH_STRIDE) == stock_blk
    st['m0_stock'] = at(MATRIX_M0, 18) == at(MATRIX_M0, 18, stock)
    st['bank2_stock'] = at(BANK2[0], BANK2[1] - BANK2[0]) == at(BANK2[0], BANK2[1] - BANK2[0], stock)
    st['old_su_stub_stock'] = at(OLD_SU_STUB, OLD_SU_LEN) == at(OLD_SU_STUB, OLD_SU_LEN, stock)
    st['stash_stock'] = at(STASH, 4) == at(STASH, 4, stock)
    b54_info = B54.build()
    sd_len = len(b54_info['sd'])
    after_sd = B54.SD_STUB + sd_len
    st['tone_slot53_after_sd_stock'] = at(after_sd, OLD_SU_STUB - after_sd) == \
        at(after_sd, OLD_SU_STUB - after_sd, stock)
    st['sd_stub_bytes'] = at(B54.SD_STUB, sd_len) == b54_info['sd']

    tone_ok = True
    tabs = json.load(open('.tmp/b58_tables.json', encoding='utf-8'))
    for a, t in zip(TONE_ADDRS, tabs):
        tone_ok &= at(a, 0x200) == struct.pack('<256H', *t['u16'])
    st['tones'] = tone_ok
    mx_ok = True
    mxj = json.load(open('.tmp/b59_mx.json'))
    for i, rows in enumerate(mxj):
        flat = B54.enc_matrix([rows[0:3], rows[3:6], rows[6:9]])
        mx_ok &= at(B54.MX_DATA + i * 18, 18) == b''.join(struct.pack('<h', v) for v in flat)
    st['matrices'] = mx_ok

    # D 与 B66 的差异集合
    win = (SU_STUB - BASE, B68.SU_SLOT_END - BASE)
    hwin = (SEL_INSERT - BASE, SEL_INSERT - BASE + SEL_LEN)
    d66 = [i for i in range(len(image)) if image[i] != b66_img[i]]
    stray66 = [i for i in d66 if not (win[0] <= i < win[1] or hwin[0] <= i < hwin[1])]
    st['b66_diff_bytes'] = len(d66)
    st['b66_diff_only_expected'] = not stray66
    # 与 stock 的差异集合
    ds = [i for i in range(len(image)) if image[i] != stock[i]]
    st['stock_diff_bytes'] = len(ds)

    static_ok = all(v is True for k, v in st.items()
                    if isinstance(v, bool))
    print('C 静态：' + ' '.join('%s=%s' % (k, v) for k, v in st.items() if isinstance(v, bool)))
    print('   选择器桩 @0x%X（%d B）；两桩合计 %d B；与 B66 差异 %d B'
          % (sel_stub, len(sel_stub_b), st['stub_total_len'], len(d66)))

    allok = static_ok
    details = []

    # ---------- A 选择器 ----------
    print('\nA 选择器 0x%X -> 0x%X（ctx 0x%X）' % (SEL_STUB_ENTRY, SEL_RET, SEL_CTX))
    resA = {}
    for stash in (0, 1, 2, 3, 4, 5, 6, 7):
        regs, d = sel_run(image, stash)
        pc = regs['PC']
        landed = d.get('status') == 'breakpoint' and pc == SEL_RET
        if 1 <= stash <= 4:
            exp = struct.unpack('<I', at(BLK_TAB + (stash - 1) * 4, 4))[0]
            exp_src = 'BLK_TAB[%d]' % (stash - 1)
        else:
            exp = CH_STOCK
            exp_src = 'stock 0x605D4829'
        ok = landed and regs['A0'] == exp
        resA[stash] = ok
        allok &= ok
        print('   stash=%d -> a0=0x%08X（期望 0x%08X %s）PC=0x%08X %s %s'
              % (stash, regs['A0'], exp, exp_src, pc, d.get('status'),
                 'PASS' if ok else 'FAIL'))
    # 三个判别量都要走同一条路径
    for code in (0xA400, 0xA800, 0xA900):
        regs, d = sel_run(image, 2, code=code)
        ok = (d.get('status') == 'breakpoint' and regs['PC'] == SEL_RET
              and regs['A0'] == struct.unpack('<I', at(BLK_TAB + 4, 4))[0])
        resA['code_0x%X' % code] = ok
        allok &= ok
        print('   ctx+0xC=0x%04X stash=2 -> a0=0x%08X %s'
              % (code, regs['A0'], 'PASS' if ok else 'FAIL'))

    # ---------- B SU 桩 ----------
    print('\nB SU 桩 0x%X（stop @0x%X），slot1 区 0x%X 填 0x%02X'
          % (SU_STUB, SU_RET, SLOT1, SLOT_FILL))
    resB = {}
    for k in (1, 2, 3, 4, 5, 7, 0):
        key = k          # stash 就是 key；k_of(key)= (key&7)
        regs, d = stub_run(image, key)
        in_range = 1 <= k <= 4
        exp_a0 = SU_PTRTAB + (k - 1) * 4 if in_range else S + 0x1230
        exp_exif = (b'k=%d\x00' % k) + key.to_bytes(4, 'little') if in_range else EXIF_POKE
        got_exif = read_mem(d, EXIF_TEXT, 8) or b''
        slot = read_mem(d, SLOT1, SLOT1_SPAN) or b''
        b_ok = (regs['PC'] == SU_RET and regs['A0'] == exp_a0 and regs['D0'] == 1
                and regs['A1'] == DEST + 0x400 and regs['D2'] == DEST + 0x800
                and regs['A3'] == STUB_A3
                and got_exif == exp_exif
                and slot == bytes([SLOT_FILL]) * SLOT1_SPAN)
        resB[k] = b_ok
        allok &= b_ok
        print('   k=%d a0=0x%08X（期望 0x%08X）EXIF=%r slot1 未动=%s %s'
              % (k, regs['A0'], exp_a0, got_exif,
                 slot == bytes([SLOT_FILL]) * SLOT1_SPAN, 'PASS' if b_ok else 'FAIL'))

    # B2 完整上传路径也一个字节都不写 slot1（k=1/2）
    for k in (1, 2):
        regs, d = upload_run(image, k)
        slot = read_mem(d, SLOT1, SLOT1_SPAN) or b''
        exif = read_mem(d, EXIF_TEXT, 8) or b''
        tone = V.tone_of_dest(V.mem_bytes(d, DEST, V.DEST_SPAN))
        exp_tone = W.table_at(image, TONE_ADDRS[k - 1])
        b2_ok = (regs['PC'] == V.UPLOAD_MERGE and slot == bytes([SLOT_FILL]) * SLOT1_SPAN
                 and exif == (b'k=%d\x00' % k) + k.to_bytes(4, 'little')
                 and tone == exp_tone)
        resB['upload_k=%d' % k] = b2_ok
        allok &= b2_ok
        print('   upload k=%d tone T46=%s slot1 未动=%s %s'
              % (k, tone[46] if tone else None,
                 slot == bytes([SLOT_FILL]) * SLOT1_SPAN, 'PASS' if b2_ok else 'FAIL'))

    # ---------- E 极端块的消费段（obj+0xA4 六轴表解码） ----------
    print('\nE 消费段 0x%X（块 -> obj+0xA4/0xE8 表），obj=0x%X 填 0x%02X'
          % (DECODE_ENTRY, DECODE_OBJ, DECODE_FILL))
    resE = {}
    extents = {}
    for k in (1, 2, 3, 4):
        blk = CH_ADDRS[k - 1]
        blkb = image[blk - BASE:blk - BASE + CH_STRIDE]
        regs, d = decode_run(image, blk)
        region = read_mem(d, DECODE_OBJ, 0x200) or b''
        wrote = [i for i in range(0x200)
                 if i < len(region) and region[i] != DECODE_FILL]
        extents[k] = tuple(wrote)
        fault = d.get('status') not in ('stopped', 'returned', 'breakpoint') \
            or regs['PC'] != DECODE_STOP
        # 段末还会清 obj+0 的 bit1（mov (a0),d0 / and 0xfffffffd / mov d0,(a0)）
        flag_ok = region[0:4] == ((DECODE_FILL * 0x01010101 & 0xFFFFFFFD).to_bytes(4, 'little'))
        tables_ok = all(0xA4 <= i < 0x12C for i in wrote if i >= 4)
        head_ok = region[0xA4:0xA8] == (blkb[0x20] << 16).to_bytes(4, 'little')
        second_ok = region[0xE8:0xEC] == (blkb[0x52] << 16).to_bytes(4, 'little')
        e_ok = bool(not fault and flag_ok and tables_ok and head_ok and second_ok)
        resE[k] = e_ok
        allok &= e_ok
        print('   k=%d 跑完=%s(%s pc=%X) 清标志位=%s 表区限界=%s(0x%X..0x%X, %d B) '
              '头[0x20]<<16=%s 二头[0x52]<<16=%s %s'
              % (k, not fault, d.get('status'), regs['PC'], flag_ok, tables_ok,
                 min(wrote) if wrote else -1, (max(wrote) + 1) if wrote else -1,
                 len(wrote), head_ok, second_ok, 'PASS' if e_ok else 'FAIL'))
    same_extent = len({v for v in extents.values()}) == 1
    allok &= same_extent
    print('   四块写入偏移集合一致（与块内容无关）=%s' % same_extent)
    details = [{'case': 'A', 'stash': k, 'ok': bool(v)} for k, v in resA.items()]
    details += [{'case': 'B', 'k': k, 'ok': bool(v)} for k, v in resB.items()]
    details += [{'case': 'E', 'k': k, 'ok': bool(v)} for k, v in resE.items()]

    checks = [
        'A 选择器：stash=1..4 -> a0 == BLK_TAB[k-1]（0x605DEB44+0x64*(k-1)）',
        'A 选择器：stash=0/5/6/7 -> a0 == 0x605D4829（库存块）',
        'A 选择器：ctx+0xC ∈ {0xA400,0xA800,0xA900} 都走同一路径',
        'B SU 桩：k=1..4 写 EXIF "k=N\\0"+key，a0=tone 指针表单元格',
        'B SU 桩：slot1 区（0x61E30000 填 0xEE）零改动',
        'B SU 桩：k 不在 1..4 时 EXIF 不动、a0=ctx+0x1230、d0=1',
        'B2 完整上传路径：slot1 零改动、tone 表正确',
        'C 0x604B4CAD = jmp 到选择器桩 + nop（原厂 mov 被替换）',
        'C 两桩都在 0x605DECD4..0x605DED44 内且 <=112 B',
        'C 四张块 == B63 == 按 B67 规格重算',
        'D 与 B66 的差异只在 SU 桩窗口与 6 B 新钩',
        'E 极端块跑消费段 0x604B4E42：无 fault，写入只落在 obj+0xA4..0x12C（四块一致）',
    ]
    rec = {'pkg': path, 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b68.py', 'closed': bool(allok),
           'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'base': BASE_PKG, 'compare_to': B66_PKG,
           'hook_d': '0x%08X' % SEL_INSERT, 'sel_stub': '0x%08X' % sel_stub,
           'sel_stub_len': len(sel_stub_b), 'sel_ret': '0x%08X' % SEL_RET,
           'su_stub': '0x%08X' % SU_STUB, 'su_stub_len': len(su_stub),
           'su_ret': '0x%08X' % SU_RET, 'stub_total_len': st['stub_total_len'],
           'slot1_off': 0x37D8, 'rec_off': 0x1D,
           'slot1_write': 'removed (B67 SU stub does not touch slot1)',
           'checks': checks, 'static': {k: v for k, v in st.items()},
           'details': details}
    with open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print('\n-> ' + ('全部闭合' if allok else '有不闭合的用例'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, allok))
    return 0 if allok else 1


if __name__ == '__main__':
    sys.exit(main())
