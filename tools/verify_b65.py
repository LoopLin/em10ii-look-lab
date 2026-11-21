#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b65.py — B65 offline full-chain verification (B59 regression + guarded slot1 write)

B65 == B63 patch set rebuilt from stock; the extended SU stub writes slot1+0x1D
ONLY when the 4-byte look key differs from the key stored in the EXIF text block
(0x61894C90+4). So the new expectations are:

  C9   key changed (EXIF key != new key): slot1+0x1D == that block, EXIF updated
  C9b  key unchanged (EXIF key == new key): slot1 untouched, EXIF untouched,
       still exits with a0=ctx+0x1230, d0=1 (tone upload continues)
  C10  k not in 1..4: untouched
  C11  *(ctx+0x37D8)==0: no crash, no write
  C12  other slot1 bytes untouched
  C13  register contract
  C14  ctx+0x1230 / m0 / bank2 not written

Static: B65 == B63 except inside the SU stub region 0x605DECD4..0x605DED44;
four blocks byte-identical to B63; stub == tools/build_b65.py assemble_stub().

Usage:
  python tools/verify_b65.py                 # default build/B65_slot1.BIN
  python tools/verify_b65.py build/B65_slot1.BIN
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
import build_b63 as B63                   # noqa: E402
import build_b65 as B65                   # noqa: E402

BASE = B65.BASE
STASH = B65.STASH
SU_STUB = B65.SU_STUB
HOOK = B65.HOOK
SU_RET_LOOP = 0x60479A3A
NO_TONE = 0x60479A4E
CH_HOOK = B65.CH_HOOK
CH_ADDRS = B65.CH_ADDRS
CH_STRIDE = B65.CH_STRIDE
SU_PTRTAB = B65.SU_PTRTAB
BLK_TAB = B65.BLK_TAB
OLD_SU_STUB, OLD_SU_LEN = B65.OLD_SU_STUB, B65.OLD_SU_LEN
EXIF_TEXT = B65.EXIF_TEXT
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
SLOT1 = 0x61E30000
SLOT1_SPAN = 0x200
SLOT_FILL = 0xEE
STOCK_LUT = W.STOCK_LUT
STUB_A3 = 0x61E1A000

B63_PKG = B65.B63_PKG
BASE_PKG = B65.SRC


def payload_of(path):
    with open(path, 'rb') as f:
        return __import__('oly_container').parse(f.read())[0].payload


def k_of(key):
    return W.k_of(key)


def read_mem(d, addr, n):
    return W.read_mem(d, addr, n)


def exif_blob(key=None, k=1):
    if key is None:
        return EXIF_POKE
    return (b'k=%d\x00' % (k if 1 <= k <= 4 else 1)) + int(key).to_bytes(4, 'little')


def upload_run(image, key, *, slot1=SLOT1, exif_key=None):
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
        (EXIF_TEXT, exif_blob(exif_key, k_of(key))),
    ]
    regs = {'A0': DEST, 'A1': W.O}
    d = V.sim(image, V.UPLOAD, V.UPLOAD_MERGE, limit=400000, pokes=pokes, regs=regs,
              extra_rwz=V.UPLOAD_HEAP + (EXIF_REGION,))
    return V.regs_of(d), d


def stub_run(image, key, *, slot1=SLOT1, tone_ptr=STOCK_LUT, exif_key=None):
    pokes = [
        (SP + 8, DEST.to_bytes(4, 'little')),
        (S + 0x1230, int(tone_ptr).to_bytes(4, 'little')),
        (S + 0x37D8, int(slot1).to_bytes(4, 'little')),
        (SLOT1, bytes([SLOT_FILL]) * SLOT1_SPAN),
        (STASH, int(key).to_bytes(4, 'little')),
        (EXIF_TEXT, exif_blob(exif_key, k_of(key))),
    ]
    regs = {'A0': S + 0x1230, 'D2': DEST + 0x400, 'A3': STUB_A3}
    d = V.sim(image, SU_STUB, SU_RET_LOOP, limit=4000, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,), bps=(NO_TONE,))
    return V.regs_of(d), d


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'build/B65_slot1.BIN'
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    stock = payload_of(BASE_PKG)
    b63_img = payload_of(B63_PKG)

    def at(addr, n, buf=None):
        buf = image if buf is None else buf
        return bytes(buf[addr - BASE:addr - BASE + n])

    pre = {}
    pre['hook'] = at(HOOK, 5) == B65.b46.jmp_bytes(HOOK, SU_STUB)
    pre['ch_hook_stock'] = at(CH_HOOK, 5) == bytes(stock[CH_HOOK - BASE:CH_HOOK - BASE + 5])
    pre['su_ptrtab'] = struct.unpack('<4I', at(SU_PTRTAB, 16)) == tuple(TONE_ADDRS)
    pre['blk_tab'] = struct.unpack('<4I', at(BLK_TAB, 16)) == tuple(CH_ADDRS)
    pre['old_stub_stock'] = at(OLD_SU_STUB, OLD_SU_LEN) == bytes(
        stock[OLD_SU_STUB - BASE:OLD_SU_STUB - BASE + OLD_SU_LEN])
    blk_ok = True
    for i, a in enumerate(CH_ADDRS):
        blk_ok &= at(a, CH_STRIDE) == at(a, CH_STRIDE, b63_img)
    pre['blocks_eq_b63'] = blk_ok
    stub_b, _ = B65.assemble_stub()
    pre['stub_bytes'] = at(SU_STUB, len(stub_b)) == stub_b
    pre['stub_len'] = len(stub_b)
    stub_off = SU_STUB - BASE
    diff63 = [i for i in range(len(image)) if image[i] != b63_img[i]]
    stray = [i for i in diff63 if not (stub_off <= i < stub_off + 0x70)]
    pre['b63_diff_only_in_stub'] = not stray
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
    pre['bank2'] = at(BANK2[0], BANK2[1] - BANK2[0]) == bytes(stock[BANK2[0] - BASE:BANK2[1] - BASE])
    pre['m0'] = at(MATRIX_M0, 18) == bytes(stock[MATRIX_M0 - BASE:MATRIX_M0 - BASE + 18])
    pre_ok = all(bool(v) for v in pre.values())
    print('静态：%s' % ' '.join('%s=%s' % (k, v) for k, v in pre.items()))

    cases = [(0x00, 0x01, m) for m in (1, 2, 3, 4)]
    cases += [(0x00, 0x00, 0x00), (0x00, 0x01, 0x07), (0x01, 0x01, 0x64)]
    allok = pre_ok
    details = []
    for b0, b1, mode in cases:
        key = (b0 << 24) | (b1 << 16) | mode
        k_exp = k_of(key)
        in_range = 1 <= k_exp <= 4
        blk_expect = at(CH_ADDRS[k_exp - 1], CH_STRIDE) if in_range else bytes([SLOT_FILL]) * CH_STRIDE
        exp_text = (b'k=%d\x00' % k_exp) if in_range else EXIF_POKE[:4]
        exp_keyb = key.to_bytes(4, 'little') if in_range else EXIF_POKE[:4]

        st, dd = W.stash_of(image, b0=b0, b1=b1, mode=mode)

        # 写入路径（EXIF 里是旧键/垃圾键）
        uregs, up = upload_run(image, key, slot1=SLOT1)
        sregs, sd = stub_run(image, key, slot1=SLOT1)
        got_blk = read_mem(up, SLOT1 + 0x1D, CH_STRIDE) or b''
        got_blk_stub = read_mem(sd, SLOT1 + 0x1D, CH_STRIDE) or b''
        got_text_u = read_mem(up, EXIF_TEXT, 4) or b''
        got_key_u = read_mem(up, EXIF_KEY, 4) or b''
        c6 = (V.regs_of(dd)['PC'] == W.DISP_CONT and st == key)
        exp_tab_addr = TONE_ADDRS[k_exp - 1] if in_range else STOCK_LUT
        tone = V.tone_of_dest(V.mem_bytes(up, DEST, V.DEST_SPAN))
        c7 = tone == W.table_at(image, exp_tab_addr)
        c8 = (got_text_u == exp_text and got_key_u == exp_keyb)
        write_ok = (got_blk == blk_expect and got_blk_stub == blk_expect)
        c9 = write_ok if in_range else True
        c10 = (got_blk == bytes([SLOT_FILL]) * CH_STRIDE
               and got_blk_stub == bytes([SLOT_FILL]) * CH_STRIDE) if not in_range else True

        # 跳过路径（EXIF 里已是同一个键）
        uregs2, up2 = upload_run(image, key, slot1=SLOT1, exif_key=key)
        sregs2, sd2 = stub_run(image, key, slot1=SLOT1, exif_key=key)
        skip_blk = read_mem(up2, SLOT1 + 0x1D, CH_STRIDE) or b''
        skip_blk_stub = read_mem(sd2, SLOT1 + 0x1D, CH_STRIDE) or b''
        skip_text = read_mem(up2, EXIF_TEXT, 4) or b''
        skip_key = read_mem(up2, EXIF_KEY, 4) or b''
        c9b = (skip_blk == bytes([SLOT_FILL]) * CH_STRIDE
               and skip_blk_stub == bytes([SLOT_FILL]) * CH_STRIDE
               and skip_text == (b'k=%d\x00' % (k_exp if in_range else 1))
               and skip_key == key.to_bytes(4, 'little'))

        # slot1==0
        zregs, zd = upload_run(image, key, slot1=0)
        nregs, nd = stub_run(image, key, slot1=0)
        c11 = ((V.regs_of(zd)['PC'] == V.UPLOAD_MERGE)
               and (read_mem(zd, SLOT1, SLOT1_SPAN) or b'') == bytes([SLOT_FILL]) * SLOT1_SPAN
               and (V.regs_of(nd)['PC'] in (SU_RET_LOOP, NO_TONE))
               and (read_mem(nd, SLOT1, SLOT1_SPAN) or b'') == bytes([SLOT_FILL]) * SLOT1_SPAN)
        keep = read_mem(up, SLOT1, 0x1D) or b''
        keep2 = read_mem(up, SLOT1 + 0x1D + CH_STRIDE, SLOT1_SPAN - 0x1D - CH_STRIDE) or b''
        c12 = keep == bytes([SLOT_FILL]) * 0x1D and \
              keep2 == bytes([SLOT_FILL]) * (SLOT1_SPAN - 0x1D - CH_STRIDE)
        exp_a0 = (SU_PTRTAB + (k_exp - 1) * 4) if in_range else (S + 0x1230)
        c13 = (sregs['A1'] == DEST + 0x400 and sregs['D2'] == DEST + 0x800
               and sregs['A3'] == STUB_A3 and sregs['A0'] == exp_a0
               and (sregs['D0'] == 1 if in_range else True))
        c13b = True if not in_range else (
            sregs2['A1'] == DEST + 0x400 and sregs2['D2'] == DEST + 0x800
            and sregs2['A3'] == STUB_A3 and sregs2['A0'] == S + 0x1230
            and sregs2['D0'] == 1)
        stock_lut_b = STOCK_LUT.to_bytes(4, 'little')
        c14 = all((read_mem(d, S + 0x1230, 4) or b'?') == stock_lut_b for d in (sd, nd, sd2)) and \
            all((read_mem(d, MATRIX_M0, 18) or b'?') == at(MATRIX_M0, 18) for d in (up, sd, zd, nd, up2, sd2)) and \
            all(W.span_diff(d, at(BANK2[0], BANK2[1] - BANK2[0]), BANK2) == [] for d in (up, sd, zd, nd, up2, sd2))

        ok = all([c6, c7, c8, c9, c10, c11, c12, c13, c13b, c9b, c14])
        allok &= ok
        print('== key=0x%08X  k=%d（1..4=%s）' % (key, k_exp, in_range))
        print('   tone dest T46=%s / 期望 %s ; EXIF text=%r key=%s'
              % (tone[46] if tone else None, W.table_at(image, exp_tab_addr)[46], got_text_u, got_key_u.hex()))
        print('   slot1+0x1D 前 8 B = %s（期望 %s）；同键跳过=%s'
              % (got_blk[:8].hex(), blk_expect[:8].hex(), skip_blk == bytes([SLOT_FILL]) * CH_STRIDE))
        print('   a0=0x%08X（期望 0x%08X） a1=0x%08X d2=0x%08X a3=0x%08X d0=%d'
              % (sregs['A0'], exp_a0, sregs['A1'], sregs['D2'], sregs['A3'], sregs['D0']))
        for name, v in (('C6 派发器→stash==完整 key', c6),
                        ('C7 dest tone 表==该档表', c7),
                        ('C8 EXIF k=N\\0 + key（仅 1..4）', c8),
                        ('C9 键变化：slot1+0x1D 100 B == 该档块', c9),
                        ('C9b 同键：slot1/EXIF 都不动，tone 仍上传', c9b),
                        ('C10 k!=1..4：slot1 不动', c10),
                        ('C11 slot1==0: no crash, no write', c11),
                        ('C12 slot1 其它字节不动', c12),
                        ('C13 写入路径寄存器契约', c13),
                        ('C13b 跳过路径寄存器契约（a0=ctx+0x1230,d0=1）', c13b),
                        ('C14 ctx+0x1230 / m0 / bank2 未写', c14)):
            print('     %s  %s' % ('PASS' if v else 'FAIL', name))
        details.append({'key': '0x%08X' % key, 'k': k_exp, 'in_range': in_range,
                        'tone_ok': bool(c7), 'write_first8': got_blk[:8].hex(),
                        'skip_untouched': bool(c9b), 'ok': bool(ok)})

    checks = ['A1 hook 0x60479A2B -> 0x%X; chroma getter hook 0x604AA297 == stock' % SU_STUB,
              'A2 tone ptr table == slots 22..25; block ptr table == four blocks',
              'A3 four blocks byte-identical to B63 (extreme y)',
              'A4 stub bytes == tools/build_b65.py assemble_stub() (%d B)' % pre['stub_len'],
              'A5 B65 vs B63 diff only inside SU stub region 0x%X..0x605DED44' % SU_STUB,
              'A6 old SU stub region == stock; tone/matrices/bank2/m0 == stock',
              'C6 派发器→stash==完整 key', 'C7 dest tone 表==该档表',
              'C8 EXIF k=N 与 key（仅 1..4）',
              'C9 key changed: slot1+0x1D 100 B == that block',
              'C9b key unchanged: slot1/EXIF untouched, tone still uploaded',
              'C10 k!=1..4: slot1 untouched',
              'C11 *(ctx+0x37D8)==0: no crash, no write',
              'C12 other slot1 bytes untouched',
              'C13/C13b register contract (write / skip paths)',
              'C14 ctx+0x1230 / m0 / bank2 not written']
    rec = {'pkg': path, 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b65.py', 'cases': len(cases),
           'closed': bool(allok), 'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'base': BASE_PKG, 'hook': '0x%08X' % HOOK, 'su_stub': '0x%08X' % SU_STUB,
           'su_stub_len': pre['stub_len'], 'slot1_off': 0x37D8, 'rec_off': 0x1D,
           'guard': 'EXIF 0x%X+4 key compare' % EXIF_TEXT,
           'chroma_getter_hook': 'stock (0x604AA297 untouched)',
           'block_source': 'B63 extreme blocks (y 0x00/0xFF, x/z/pad as B63)',
           'b63_diff_only_in_stub': bool(pre['b63_diff_only_in_stub']),
           'checks': checks,
           'static': {k: bool(v) for k, v in pre.items()},
           'details': details}
    with open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print('\\n-> ' + ('全部闭合' if allok else '有不闭合的用例'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, allok))
    return 0 if allok else 1


if __name__ == '__main__':
    sys.exit(main())
