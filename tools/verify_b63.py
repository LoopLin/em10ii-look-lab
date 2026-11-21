#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b63.py —— B63 离线整链验证（B59 回归 + slot1 写入）

B63 只比 B59 多两件事，所以这里分成三段：

A. **静态**：钩点/桩/块指针表/四张块/B59 原样表（tone 指针表、tone 四槽、旧桩还原、
   色度 getter 钩仍是原厂）/bank2/m0。

B. **B59 回归**（沿用 verify_b59 的用例）：派发器 → stash == 完整 key；上传器 →
   dest 的 tone 表 == 该档表；EXIF `k=N\\0` + key（仅 k=1..4）。

C. **slot1 写入（新增）**：同一个上传器用例里把 `*(ctx+0x37D8)` 预置成一块堆内存，
   跑完之后

     C9   k=1..4：slot1+0x1D 的 100 B == 该档极端块
     C10  k!=1..4：slot1 整块不动（0xEE 填充保持）
     C11  *(ctx+0x37D8)==0：不崩、slot1 区一个字节不动
     C12  slot1 的其它字节（+0x00..0x1C 与 +0x81..）不动
     C13  寄存器保全：a1=dest+0x400、d2=dest+0x800、a3 未动、
          a0 = 指针表单元格（k=1..4）/ ctx+0x1230（否则）、d0 = 1（k=1..4）
     C14  ctx+0x1230 / m0 / bank2 在本桩执行中未被写

用法：
  python tools/verify_b63.py                 # 默认 build/B63_slot1.BIN
  python tools/verify_b63.py build/B63_slot1.BIN
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
import verify_b59 as W                    # noqa: E402
import build_b54 as B54                   # noqa: E402
import build_b61 as B61                   # noqa: E402
import build_b63 as B63                   # noqa: E402

BASE = B63.BASE
STASH = B63.STASH
SU_STUB = B63.SU_STUB
HOOK = B63.HOOK
SU_RET_LOOP = 0x60479A3A                  # 原厂拷贝循环起点（桩返回后的下一条死路）
NO_TONE = 0x60479A4E                      # d0==0 时原厂跳到这里
CH_HOOK = B63.CH_HOOK
CH_ADDRS = B63.CH_ADDRS
CH_STRIDE = B63.CH_STRIDE
SU_PTRTAB = B63.SU_PTRTAB
BLK_TAB = B63.BLK_TAB
OLD_SU_STUB, OLD_SU_LEN = B63.OLD_SU_STUB, B63.OLD_SU_LEN
EXIF_TEXT = B63.EXIF_TEXT
EXIF_KEY = B54.EXIF_KEY
EXIF_REGION = W.EXIF_REGION
EXIF_POKE = W.EXIF_POKE
MATRIX_M0 = B54.MATRIX_M0
BANK2 = B54.BANK2
TONE_ADDRS = B54.TONE_ADDRS

DEST = W.DEST
S = W.S
CTX = W.CTX
SP = W.SP
SLOT1 = 0x61E30000                        # HEAP 0x61e00000..0x61e40000 内
SLOT1_SPAN = 0x200
SLOT_FILL = 0xEE
STOCK_LUT = W.STOCK_LUT
STUB_A3 = 0x61E1A000

BASE_PKG = 'build/B59_real.BIN'


def payload_of(path):
    with open(path, 'rb') as f:
        return __import__('oly_container').parse(f.read())[0].payload


def k_of(key):
    return W.k_of(key)


def read_mem(d, addr, n):
    return W.read_mem(d, addr, n)


# ---------------------------------------------------------------- B：上传器（含 slot1）
def upload_run(image, key, *, slot1=SLOT1):
    """跑上传器 0x60479960 → 0x60479A77；ctx=*(O+0x0C)=S，slot1=*(S+0x37D8)。"""
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


def stash_run(image, *, b0, b1, mode):
    return W.stash_of(image, b0=b0, b1=b1, mode=mode)


# ---------------------------------------------------------------- C：桩本身（寄存器契约）
def stub_run(image, key, *, slot1=SLOT1, tone_ptr=STOCK_LUT):
    """从扩展 SU 桩起点跑，停在原厂循环/无色调分支之前，检查退出契约。"""
    pokes = [
        (SP + 8, DEST.to_bytes(4, 'little')),
        (S + 0x1230, int(tone_ptr).to_bytes(4, 'little')),
        (S + 0x37D8, int(slot1).to_bytes(4, 'little')),
        (SLOT1, bytes([SLOT_FILL]) * SLOT1_SPAN),
        (STASH, int(key).to_bytes(4, 'little')),
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': S + 0x1230, 'D2': DEST + 0x400, 'A3': STUB_A3}
    d = V.sim(image, SU_STUB, SU_RET_LOOP, limit=4000, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,), bps=(NO_TONE,))
    return V.regs_of(d), d


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'build/B63_slot1.BIN'
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    stock = payload_of(B63.STOCK)
    base = payload_of(BASE_PKG)

    def at(addr, n, buf=None):
        buf = image if buf is None else buf
        return bytes(buf[addr - BASE:addr - BASE + n])

    # ---------------------------------------------------------- A 静态
    want_stub = None
    pre = {}
    pre['hook'] = at(HOOK, 5) == B63.b46.jmp_bytes(HOOK, SU_STUB)
    pre['ch_hook_stock'] = at(CH_HOOK, 5) == bytes(stock[CH_HOOK - BASE:CH_HOOK - BASE + 5])
    pre['su_ptrtab'] = struct.unpack('<4I', at(SU_PTRTAB, 16)) == tuple(TONE_ADDRS)
    pre['blk_tab'] = struct.unpack('<4I', at(BLK_TAB, 16)) == tuple(CH_ADDRS)
    pre['old_stub_stock'] = at(OLD_SU_STUB, OLD_SU_LEN) == bytes(
        stock[OLD_SU_STUB - BASE:OLD_SU_STUB - BASE + OLD_SU_LEN])
    stock_blk = bytes(stock[B61.CH_STOCK - BASE:B61.CH_STOCK - BASE + CH_STRIDE])
    blk_ok, blk_note = True, []
    for i, (a, (cb, cr)) in enumerate(zip(CH_ADDRS, B63.BLOCK_SPEC)):
        blk = at(a, CH_STRIDE)
        want = B61.extreme_block(cb, cr, stock_blk)
        try:
            B61.check_block(blk, 'B63 k=%d' % (i + 1), cb, cr, stock_blk)
        except SystemExit as e:
            blk_ok = False
            blk_note.append(str(e))
        blk_ok &= (blk == want)
    pre['blocks'] = blk_ok
    tone_ok = True
    tabs = json.load(open('.tmp/b58_tables.json', encoding='utf-8'))
    for a, t in zip(TONE_ADDRS, tabs):
        tone_ok &= at(a, 0x200) == struct.pack('<256H', *t['u16'])
    pre['tones'] = tone_ok
    mx_ok = True
    mxj = json.load(open('.tmp/b59_mx.json'))
    for i, rows in enumerate(mxj):
        flat = B54.enc_matrix([rows[0:3], rows[3:6], rows[6:9]])
        mx_ok &= at(B54.MX_DATA + i * 18, 18) == b''.join(struct.pack('<h', v) for v in flat)
    pre['matrices'] = mx_ok
    bank2_ok = at(BANK2[0], BANK2[1] - BANK2[0]) == bytes(base[BANK2[0] - BASE:BANK2[1] - BASE])
    pre['bank2'] = bank2_ok
    m0_ok = at(MATRIX_M0, 18) == bytes(stock[MATRIX_M0 - BASE:MATRIX_M0 - BASE + 18])
    pre['m0'] = m0_ok
    # 桩长（从 build 布局里取，静态记录）
    try:
        LAYOUT = json.load(open('.tmp/b63_layout.json', encoding='utf-8'))
    except Exception:
        LAYOUT = {}
    stub_len = LAYOUT.get('su_stub_len') or 0
    pre['stub_len'] = stub_len
    pre_ok = all(pre.values())
    print('静态：%s' % ' '.join('%s=%s' % (k, v) for k, v in pre.items()))
    if blk_note:
        print('  块复检失败：%s' % blk_note)

    # ---------------------------------------------------------- 用例
    cases = [(0x00, 0x01, m) for m in (1, 2, 3, 4)]              # k=1..4
    cases += [(0x00, 0x00, 0x00),                                # k=0（全零 key）
              (0x00, 0x01, 0x07),                                # k=7 越界
              (0x01, 0x01, 0x64)]                                # 标准档形态 key，k=12
    allok = pre_ok
    details = []
    for b0, b1, mode in cases:
        key = (b0 << 24) | (b1 << 16) | mode
        k_exp = k_of(key)
        in_range = 1 <= k_exp <= 4

        st, dd = stash_run(image, b0=b0, b1=b1, mode=mode)
        uregs, up = upload_run(image, key, slot1=SLOT1)
        sregs, sd = stub_run(image, key, slot1=SLOT1)
        zregs, zd = upload_run(image, key, slot1=0)
        nregs, nd = stub_run(image, key, slot1=0)

        # C6 stash
        c6 = (V.regs_of(dd)['PC'] == W.DISP_CONT and st == key)
        # C7 tone（B59 回归）
        exp_tab_addr = TONE_ADDRS[k_exp - 1] if in_range else STOCK_LUT
        tone = V.tone_of_dest(V.mem_bytes(up, DEST, V.DEST_SPAN))
        exp_tab = W.table_at(image, exp_tab_addr)
        c7 = tone == exp_tab
        # C8 EXIF
        exp_text = (b'k=%d\x00' % k_exp) if in_range else EXIF_POKE[:4]
        exp_keyb = key.to_bytes(4, 'little') if in_range else EXIF_POKE[:4]
        got_text = V.mem_bytes(sd, EXIF_TEXT, 4) or b''
        got_key = V.mem_bytes(sd, EXIF_KEY, 4) or b''
        got_text_u = read_mem(up, EXIF_TEXT, 4) or b''
        got_key_u = read_mem(up, EXIF_KEY, 4) or b''
        c8 = (got_text == exp_text and got_key == exp_keyb
              and got_text_u == exp_text and got_key_u == exp_keyb)

        # C9/C10 slot1 的 100 B
        blk_expect = at(CH_ADDRS[k_exp - 1], CH_STRIDE) if in_range else bytes([SLOT_FILL]) * CH_STRIDE
        got_blk = read_mem(up, SLOT1 + 0x1D, CH_STRIDE) or b''
        got_blk_stub = read_mem(sd, SLOT1 + 0x1D, CH_STRIDE) or b''
        blk_ok = (got_blk == blk_expect and got_blk_stub == blk_expect)
        c9 = blk_ok if in_range else True          # 不适用即真
        c10 = blk_ok if not in_range else True
        # C11 slot1==0：整块不动、不崩
        c11 = ((V.regs_of(zd)['PC'] == V.UPLOAD_MERGE)
               and (read_mem(zd, SLOT1, SLOT1_SPAN) or b'') == bytes([SLOT_FILL]) * SLOT1_SPAN
               and (V.regs_of(nd)['PC'] in (SU_RET_LOOP, NO_TONE))
               and (read_mem(nd, SLOT1, SLOT1_SPAN) or b'') == bytes([SLOT_FILL]) * SLOT1_SPAN)
        # C12 slot1 其它字节不动
        keep = read_mem(up, SLOT1, 0x1D) or b''
        keep2 = read_mem(up, SLOT1 + 0x1D + CH_STRIDE, SLOT1_SPAN - 0x1D - CH_STRIDE) or b''
        c12 = keep == bytes([SLOT_FILL]) * 0x1D and \
              keep2 == bytes([SLOT_FILL]) * (SLOT1_SPAN - 0x1D - CH_STRIDE)
        # C13 寄存器保全（桩自身退出契约）
        exp_a0 = (SU_PTRTAB + (k_exp - 1) * 4) if in_range else (S + 0x1230)
        c13 = (sregs['A1'] == DEST + 0x400 and sregs['D2'] == DEST + 0x800
               and sregs['A3'] == STUB_A3 and sregs['A0'] == exp_a0
               and (sregs['D0'] == 1 if in_range else True))
        # C14 本轮没写 ctx+0x1230 / m0 / bank2
        stock_lut_b = STOCK_LUT.to_bytes(4, 'little')
        c14 = all((read_mem(d, S + 0x1230, 4) or b'?') == stock_lut_b for d in (sd, nd)) and \
            all((read_mem(d, MATRIX_M0, 18) or b'?') == at(MATRIX_M0, 18) for d in (up, sd, zd, nd)) and \
            all(W.span_diff(d, at(BANK2[0], BANK2[1] - BANK2[0]), BANK2) == [] for d in (up, sd, zd, nd))

        ok = all([c6, c7, c8, c9, c10, c11, c12, c13, c14])
        allok &= ok
        print('== key=0x%08X  stash=0x%08X  k=%d（1..4=%s）' % (key, st or 0, k_exp, in_range))
        print('   tone dest T46=%s / 期望 %s ; EXIF text=%r key=%s'
              % (tone[46] if tone else None, exp_tab[46], got_text, got_key.hex()))
        print('   slot1+0x1D 前 8 B = %s（期望 %s）'
              % (got_blk[:8].hex(), blk_expect[:8].hex()))
        print('   a0=0x%08X（期望 0x%08X） a1=0x%08X d2=0x%08X a3=0x%08X d0=%d'
              % (sregs['A0'], exp_a0, sregs['A1'], sregs['D2'], sregs['A3'], sregs['D0']))
        for name, v in (('C6 派发器→stash==完整 key', c6),
                        ('C7 dest tone 表==该档表', c7),
                        ('C8 EXIF k=N\\0 + key（仅 1..4）', c8),
                        ('C9 k=1..4：slot1+0x1D 100 B == 该档块', c9),
                        ('C10 k!=1..4：slot1 不动', c10),
                        ('C11 *(ctx+0x37D8)==0：不崩、不写', c11),
                        ('C12 slot1 其它字节不动', c12),
                        ('C13 寄存器保全（a1/d2/a3/a0/d0）', c13),
                        ('C14 ctx+0x1230 / m0 / bank2 未被写', c14)):
            print('     %s  %s' % ('PASS' if v else 'FAIL', name))
        details.append({'key': '0x%08X' % key, 'k': k_exp, 'in_range': in_range,
                        'tone_ok': bool(c7), 'exif_text': got_text.hex(),
                        'slot1_first8': got_blk[:8].hex(),
                        'slot1_expect8': blk_expect[:8].hex(),
                        'a0': '0x%08X' % sregs['A0'], 'd0': sregs['D0'],
                        'ok': bool(ok)})

    checks = ['A1 钩点 0x60479A2B → 0x%X；色度 getter 钩 0x604AA297 == 原厂' % SU_STUB,
              'A2 tone 指针表 == 槽 22..25（B59 原样）；块指针表 == 四张块',
              'A3 四张块：x 库存形状、y 全极值、z 自洽、pad=0',
              'A4 旧 SU 桩 0x%X..+%d 已还原成原厂字节' % (OLD_SU_STUB, OLD_SU_LEN),
              'A5 tone 四槽 == reala；矩阵四块 == B59；bank2/m0 == 原厂',
              'C6 派发器→stash==完整 key', 'C7 dest tone 表==该档表',
              'C8 EXIF k=N 与 key（仅 1..4）',
              'C9 k=1..4：slot1+0x1D 100 B == 该档极端块',
              'C10 k!=1..4：slot1 整块不动',
              'C11 *(ctx+0x37D8)==0：不崩、不写',
              'C12 slot1 其它字节不动',
              'C13 寄存器保全（a1/dest+0x400、d2/dest+0x800、a3、a0、d0）',
              'C14 ctx+0x1230 / m0 / bank2 未被写']
    rec = {'pkg': path, 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b63.py', 'cases': len(cases),
           'closed': bool(allok), 'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'base': BASE_PKG, 'hook': '0x%08X' % HOOK, 'su_stub': '0x%08X' % SU_STUB,
           'slot1_off': 0x37D8, 'rec_off': 0x1D,
           'chroma_getter_hook': 'stock (0x604AA297 untouched)',
           'checks': checks,
           'static': {k: bool(v) for k, v in pre.items()},
           'details': details}
    with open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print('\n-> ' + ('全部闭合' if allok else '有不闭合的用例'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, allok))
    return 0 if allok else 1


if __name__ == '__main__':
    sys.exit(main())
