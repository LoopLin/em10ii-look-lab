#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b95.py -- read B95 back and prove the four looks landed.

Static part: the four tone LUTs, the four 3x3 matrices and the four 100 B chroma
blocks are read out of the image and compared byte for byte with
build/looks_final/*.json; the pointer tables, hooks, both stubs and the B90 i-e
SD stub are checked against build/B90_ie_stock.BIN and stock; the diff set is
checked to be exactly the look-data spans.

Dynamic part (libsim, closed): the SU stub is run for k=1..4 and must write EXIF
"k=N\\0"+key, point a0 at the k-th tone slot and (through the full upload path)
leave that tone table in the destination; the chroma selector stub is run for
stash 1..4 and must return the k-th block address, whose 100 B are then decoded
by the consumer segment without faulting.

Writes <pkg>.sim.json; exit 0 only when every check closes.
"""
from __future__ import annotations

import hashlib
import io
import json
import struct
import sys
import time

import oly_container as oc                 # noqa: E402
import verify_b32_chain as V               # noqa: E402
import verify_b59 as W                     # noqa: E402
import build_b46 as b46                    # noqa: E402
import build_b54 as B54                    # noqa: E402
import build_b61 as B61                    # noqa: E402
import build_b69 as B69                    # noqa: E402

BASE = B54.BASE
STASH = B54.STASH
SU_STUB, SU_RET = B69.SU_STUB, B69.SU_RET
SU_PTRTAB = B54.SU_PTRTAB
MX_DATA = B54.MX_DATA
MX_PTRTAB = B54.MX_PTRTAB
BLK_TAB = B69.BLK_TAB
CH_ADDRS = B69.CH_ADDRS
CH_STRIDE = B69.CH_STRIDE
CH_STOCK = B69.CH_STOCK
TONE_ADDRS = B54.TONE_ADDRS
SEL_INSERT, SEL_LEN = B69.SEL_INSERT, B69.SEL_LEN
SEL_STOCK = B69.SEL_STOCK
SEL_RET = B69.SEL_RET
SEL_STUB_ENTRY = 0x605DED1C
SEL_ALT_RET = 0x604B4B24
SEL_CTX = 0x61E00000
SEL_CTX_DISP, SEL_CTX_MODE, SEL_CTX_CODE = 0x3C0, 0x42F, 0x0C
EXIF_TEXT = B69.EXIF_TEXT
EXIF_REGION = W.EXIF_REGION
EXIF_POKE = W.EXIF_POKE
NO_TONE = 0x60479A4E
DEST, S, SP = W.DEST, W.S, W.SP
STOCK_LUT = W.STOCK_LUT
STUB_A3 = 0x61E1A000
DECODE_ENTRY, DECODE_STOP = 0x604B4E42, 0x604B4ED6
DECODE_OBJ, DECODE_FILL = 0x61E08000, 0xEE

SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
BASE_PKG = 'build/B90_ie_stock.BIN'
OUT = 'build/B95_looks.BIN'
LAYOUT = '.tmp/b95_layout.json'

LOOKS = [('ektachrome_100_vs', 'Vivid'), ('elite_color_200', 'Natural'),
         ('classic_chrome', 'Muted'), ('pro_neg_hi', 'Portrait')]

HARD_SPANS = [(B54.SD_INSERT, B54.SD_LEN), (B54.SU_INSERT, B54.SU_LEN),
              (B54.MX_INSERT, B54.MX_LEN), (SEL_INSERT, SEL_LEN),
              (B54.SD_STUB, 19), (SU_STUB, 69), (SEL_STUB_ENTRY, 38),
              (B54.MX_STUB, 50), (SU_PTRTAB, 16), (BLK_TAB, 16), (MX_PTRTAB, 16)]


def diff_idx(a, b):
    """Indices where two equal-length payloads differ (numpy, 69 MB in ~0.1 s)."""
    import numpy as np
    x = np.frombuffer(a, dtype=np.uint8)
    y = np.frombuffer(b, dtype=np.uint8)
    return np.nonzero(x != y)[0].tolist()


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def canon(flat):
    """Hardware encoder fixed point (diagonal of row 1 is m11 = flat[4])."""
    f = [int(v) for v in flat]
    f[1] &= ~1
    f[7] &= ~1
    if (f[3] + f[5]) & 1:
        f[3] &= ~1
        f[5] &= ~1
    f[0] = 256 - f[1] - f[2]
    f[4] = 256 - f[3] - f[5]
    f[6] = 256 - f[7] - f[8]
    return f


def pack_matrix(f):
    return b''.join(struct.pack('<h', v) for v in f)


def sel_run(image, stash, code=0xA400, mode=2, disp=0):
    pokes = [(SEL_CTX + SEL_CTX_DISP, int(disp).to_bytes(4, 'little')),
             (SEL_CTX + SEL_CTX_MODE, bytes([mode & 0xFF])),
             (SEL_CTX + SEL_CTX_CODE, int(code).to_bytes(2, 'little')),
             (STASH, int(stash).to_bytes(4, 'little'))]
    d = V.sim(image, SEL_STUB_ENTRY, 0xFFFFFFFF, limit=20000,
              bps=(SEL_RET, SEL_ALT_RET), regs={'A0': SEL_CTX}, pokes=pokes)
    return V.regs_of(d), d


def stub_run(image, key, tone_ptr=STOCK_LUT):
    pokes = [(SP + 8, DEST.to_bytes(4, 'little')),
             (S + 0x1230, int(tone_ptr).to_bytes(4, 'little')),
             (STASH, int(key).to_bytes(4, 'little')),
             (EXIF_TEXT, EXIF_POKE)]
    regs = {'A0': S + 0x1230, 'D2': DEST + 0x400, 'A3': STUB_A3}
    d = V.sim(image, SU_STUB, SU_RET, limit=4000, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,), bps=(NO_TONE,))
    return V.regs_of(d), d


def upload_run(image, key):
    pokes = [(W.O + 0x0C, S.to_bytes(4, 'little')),
             (W.O + 0x64, (4).to_bytes(4, 'little')),
             (W.O + 0x34, (0).to_bytes(4, 'little')),
             (W.O + 0xBD, b'\x01'),
             (S + 0x41C, (0).to_bytes(4, 'little')),
             (S + 0x420, W.T2.to_bytes(4, 'little')),
             (S + 0x1230, STOCK_LUT.to_bytes(4, 'little')),
             (STASH, int(key).to_bytes(4, 'little')),
             (EXIF_TEXT, EXIF_POKE)]
    regs = {'A0': DEST, 'A1': W.O}
    d = V.sim(image, V.UPLOAD, V.UPLOAD_MERGE, limit=400000, pokes=pokes, regs=regs,
              extra_rwz=V.UPLOAD_HEAP + (EXIF_REGION,))
    return V.regs_of(d), d


def decode_run(image, blk):
    pokes = [(SP + 8, DECODE_OBJ.to_bytes(4, 'little')),
             (DECODE_OBJ, bytes([DECODE_FILL]) * 0x200)]
    d = V.sim(image, DECODE_ENTRY, DECODE_STOP, limit=20000, pokes=pokes,
              regs={'A0': int(blk)})
    return V.regs_of(d), d


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else OUT
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    with open(BASE_PKG, 'rb') as f:
        b90_whole = f.read()
    stock = payload_of(SRC)
    b90 = payload_of(BASE_PKG)

    def at(addr, n, buf=None):
        buf = image if buf is None else buf
        return bytes(buf[addr - BASE:addr - BASE + n])

    allok = True
    st = {}

    # ---------------- 1. look data from the JSONs ----------------
    looks = []
    for name, mode in LOOKS:
        with io.open('build/looks_final/%s.json' % name, encoding='utf-8') as f:
            d = json.load(f)
        looks.append(dict(name=name, mode=mode, json=d))

    data_ok = True
    print('1 look data (byte readback vs build/looks_final/*.json)')
    for i, l in enumerate(looks):
        d = l['json']
        tone = [int(v) for v in d['tone256']]
        mjson = [int(v) for v in d['matrix_q8']]
        mflat = canon(mjson)
        # tone
        want_tone = struct.pack('<256H', *tone)
        got_tone = at(TONE_ADDRS[i], 0x200)
        tone_ok = got_tone == want_tone
        # matrix
        want_mx = pack_matrix(mflat)
        got_mx = at(MX_DATA + 18 * i, 18)
        mx_ok = got_mx == want_mx
        got_flat = list(struct.unpack('<9h', got_mx))
        rows_ok = all(sum(got_flat[3 * r:3 * r + 3]) == 256 for r in range(3))
        legal_ok = (got_flat[1] % 2 == 0 and got_flat[7] % 2 == 0
                    and (got_flat[3] + got_flat[5]) % 2 == 0
                    and canon(got_flat) == got_flat)
        # chroma block
        xs = {}
        for tag, key in (('cb', 'chroma_cb'), ('cr', 'chroma_cr')):
            if len(d[key]) != 16:
                raise SystemExit('%s: %s != 16 pairs' % (l['name'], key))
            xs[tag] = [int(p[0]) for p in d[key]], [int(p[1]) for p in d[key]]
        blk = at(CH_ADDRS[i], CH_STRIDE)
        want_blk = (bytes(xs['cb'][0]) + bytes(xs['cb'][1]) + bytes(B61.z_chain(*xs['cb'])) + b'\x00'
                    + bytes(xs['cr'][0]) + bytes(xs['cr'][1]) + bytes(B61.z_chain(*xs['cr'])) + b'\x00')
        blk_ok = blk == want_blk
        xy_ok = (list(blk[0x00:0x10]) == xs['cb'][0] and list(blk[0x10:0x20]) == xs['cb'][1]
                 and list(blk[0x32:0x42]) == xs['cr'][0] and list(blk[0x42:0x52]) == xs['cr'][1])
        pad_ok = blk[0x31] == 0 and blk[0x63] == 0
        try:
            B61.check_block(blk, 'k=%d %s' % (i + 1, l['name']))
            cb_ok = True
        except SystemExit as e:
            cb_ok = False
            print('   check_block k=%d raised: %s' % (i + 1, e))
        adj = [j for j in range(9) if mjson[j] != mflat[j]]
        k_ok = all((tone_ok, mx_ok, rows_ok, legal_ok, blk_ok, xy_ok, pad_ok, cb_ok))
        data_ok &= k_ok
        allok &= k_ok
        print('   k=%d %-8s tone=%s matrix=%s rows=%s legal=%s block=%s xy=%s pad=%s check=%s%s'
              % (i + 1, l['mode'], tone_ok, mx_ok, rows_ok, legal_ok, blk_ok, xy_ok,
                 pad_ok, cb_ok,
                 '' if not adj else ' (stored %r, json %r, delta<=1 LSB)'
                 % (mflat, mjson)))
        if any(abs(mflat[j] - mjson[j]) > 1 for j in range(9)):
            print('   FAIL: stored matrix differs from JSON by more than 1 LSB')
            data_ok = False
            allok = False
        l['mflat'] = mflat
        l['tone'] = tone
        l['blk'] = blk
    st['look_data_from_json'] = bool(data_ok)

    # ---------------- 2. pointer tables / stubs / baseline ----------------
    print('2 wiring and baseline')
    st['su_ptrtab'] = struct.unpack('<4I', at(SU_PTRTAB, 16)) == tuple(TONE_ADDRS)
    st['blk_tab'] = struct.unpack('<4I', at(BLK_TAB, 16)) == tuple(CH_ADDRS)
    st['mx_ptrtab'] = struct.unpack('<4I', at(MX_PTRTAB, 16)) == tuple(
        MX_DATA + 18 * i for i in range(4))
    su_stub, _ = B69.assemble_su()
    sel_addr = B69.align4(SU_STUB + len(su_stub))
    sel_stub, _ = B69.assemble_sel(sel_addr)
    st['hook_su'] = at(B54.SU_INSERT, 5) == b46.jmp_bytes(B54.SU_INSERT, SU_STUB)
    st['hook_d'] = at(SEL_INSERT, SEL_LEN) == b46.jmp_bytes(SEL_INSERT, sel_addr) + bytes([B69.NOP])
    st['hook_c'] = at(B54.MX_INSERT, 5) == b46.jmp_bytes(B54.MX_INSERT, B54.MX_STUB)
    st['hook_a'] = at(B54.SD_INSERT, 5) == b46.jmp_bytes(B54.SD_INSERT, B54.SD_STUB)
    st['su_stub_bytes'] = at(SU_STUB, len(su_stub)) == su_stub
    st['sel_stub_bytes'] = at(sel_addr, len(sel_stub)) == sel_stub
    st['stub_window'] = sel_addr + len(sel_stub) <= B69.SU_SLOT_END
    st['sel_ret_stock'] = at(SEL_RET, 3) == at(SEL_RET, 3, stock)
    hard_ok = all(st[k] for k in ('hook_su', 'hook_d', 'hook_c', 'hook_a',
                                  'su_stub_bytes', 'sel_stub_bytes',
                                  'stub_window', 'sel_ret_stock'))

    eq90 = all(at(a, n) == at(a, n, b90) for a, n in HARD_SPANS)
    st['baseline_pieces_eq_b90'] = eq90
    st['sd_stub_eq_b90'] = at(B54.SD_STUB, 19) == at(B54.SD_STUB, 19, b90)
    allok &= hard_ok and eq90 and st['sd_stub_eq_b90']

    inv = [(B61.CH_STOCK, CH_STRIDE, 'stock chroma block'),
           (B54.MATRIX_M0, 18, 'stock m0'),
           (STASH, 4, 'stash'),
           (B69.OLD_SU_STUB, B69.OLD_SU_LEN, 'retired B59 SU stub slot')]
    for a, n, nm in inv:
        ok = at(a, n) == at(a, n, stock)
        st['stock:' + nm] = ok
        allok &= ok
    bank2 = at(B54.BANK2[0], B54.BANK2[1] - B54.BANK2[0])
    st['bank2_stock'] = bank2 == at(B54.BANK2[0], B54.BANK2[1] - B54.BANK2[0], stock)
    allok &= st['bank2_stock']

    # ---------------- 3. diff sets ----------------
    print('3 diff sets')
    data_spans = ([(a, 0x200) for a in TONE_ADDRS] + [(MX_DATA + 18 * i, 18) for i in range(4)]
                  + [(a, CH_STRIDE) for a in CH_ADDRS])
    lay = json.load(io.open(LAYOUT, encoding='utf-8'))
    ranges = [tuple(r) for r in lay['ranges']]

    ds = diff_idx(image, stock)
    stray_s = [i for i in ds if not any(a - BASE <= i < a - BASE + n for a, n in ranges)]
    d90 = diff_idx(image, b90)
    stray_90 = [i for i in d90 if not any(a - BASE <= i < a - BASE + n for a, n in data_spans)]
    st['diff_vs_stock_inside_patch_ranges'] = not stray_s
    st['diff_vs_b90_only_look_data'] = not stray_90
    st['diff_vs_stock_bytes'] = len(ds)
    st['diff_vs_b90_bytes'] = len(d90)
    allok &= not stray_s and not stray_90
    print('   vs stock %d B (all inside the %d patch ranges: %s)'
          % (len(ds), len(ranges), not stray_s))
    print('   vs B90   %d B (all inside tone/matrix/block: %s)'
          % (len(d90), not stray_90))
    per_span = []
    for a, n in data_spans:
        c = sum(1 for i in range(a - BASE, a - BASE + n) if image[i] != b90[i])
        if c:
            per_span.append((a, n, c))
    for a, n, c in per_span:
        print('     x 0x%08X..0x%08X  %3d B span, %3d changed' % (a, a + n - 1, n, c))

    static_ok = all(v is True for v in st.values() if isinstance(v, bool))
    print('   static %s' % ('PASS' if static_ok else 'FAIL'))

    # ---------------- 4. SU stub + upload path (libsim) ----------------
    print('4 SU stub 0x%X -> 0x%X (libsim)' % (SU_STUB, SU_RET))
    resB = {}
    for k in (0, 1, 2, 3, 4, 5, 7):
        regs, d = stub_run(image, k)
        in_range = 1 <= k <= 4
        exp_a0 = SU_PTRTAB + (k - 1) * 4 if in_range else S + 0x1230
        exp_exif = (b'k=%d\x00' % k) + k.to_bytes(4, 'little') if in_range else EXIF_POKE
        got_exif = W.read_mem(d, EXIF_TEXT, 8) or b''
        ptr_ok = True
        if in_range:
            p = struct.unpack('<I', at(exp_a0, 4))[0]
            ptr_ok = p == TONE_ADDRS[k - 1]
        ok = (d.get('status') in ('breakpoint', 'returned') and regs['PC'] == SU_RET
              and regs['A0'] == exp_a0 and regs['D0'] == 1
              and regs['A1'] == DEST + 0x400 and regs['D2'] == DEST + 0x800
              and regs['A3'] == STUB_A3 and got_exif == exp_exif)
        resB['su_k=%d' % k] = ok
        allok &= ok
        print('   key=%#010x a0=0x%08X (want 0x%08X) d0=%d EXIF=%r slot_ptr=%s %s'
              % (k, regs['A0'], exp_a0, regs['D0'], got_exif, ptr_ok,
                 'PASS' if ok else 'FAIL'))

    print('4b full upload path k=1..4: tone table in DEST')
    resU = {}
    for k in (1, 2, 3, 4):
        regs, d = upload_run(image, k)
        tone = V.tone_of_dest(V.mem_bytes(d, DEST, V.DEST_SPAN))
        exif = W.read_mem(d, EXIF_TEXT, 8) or b''
        want = looks[k - 1]['tone']
        ok = (regs['PC'] == V.UPLOAD_MERGE and tone == want
              and exif == (b'k=%d\x00' % k) + k.to_bytes(4, 'little'))
        resU['upload_k=%d' % k] = ok
        allok &= ok
        print('   k=%d tone T46=%s (want %d) EXIF=%r %s'
              % (k, tone[46] if tone else None, want[46], exif, 'PASS' if ok else 'FAIL'))

    # ---------------- 5. chroma selector (libsim) ----------------
    print('5 chroma selector 0x%X -> 0x%X (libsim)' % (SEL_STUB_ENTRY, SEL_RET))
    resA = {}
    for stash in (0, 1, 2, 3, 4, 5, 7):
        regs, d = sel_run(image, stash)
        if 1 <= stash <= 4:
            exp = CH_ADDRS[stash - 1]
        else:
            exp = CH_STOCK
        landed = d.get('status') in ('breakpoint', 'returned') and regs['PC'] == SEL_RET
        body_ok = True
        if 1 <= stash <= 4:
            body_ok = at(exp, CH_STRIDE) == looks[stash - 1]['blk']
        ok = landed and regs['A0'] == exp and body_ok
        resA['sel_%d' % stash] = ok
        allok &= ok
        print('   stash=%d -> a0=0x%08X (want 0x%08X) body_matches_json=%s %s'
              % (stash, regs['A0'], exp, body_ok, 'PASS' if ok else 'FAIL'))
    for code in (0xA400, 0xA800, 0xA900):
        regs, d = sel_run(image, 2, code=code)
        ok = (d.get('status') in ('breakpoint', 'returned') and regs['PC'] == SEL_RET
              and regs['A0'] == CH_ADDRS[1])
        resA['code_%#x' % code] = ok
        allok &= ok
        print('   ctx+0xC=%#06x stash=2 -> a0=0x%08X %s'
              % (code, regs['A0'], 'PASS' if ok else 'FAIL'))

    # ---------------- 6. consumer segment ----------------
    print('6 consumer 0x%X (block -> obj+0xA4/0xE8)' % DECODE_ENTRY)
    resE, extents = {}, {}
    for k in (1, 2, 3, 4):
        blk_addr = CH_ADDRS[k - 1]
        blkb = at(blk_addr, CH_STRIDE)
        regs, d = decode_run(image, blk_addr)
        region = W.read_mem(d, DECODE_OBJ, 0x200) or b''
        wrote = tuple(i for i in range(0x200)
                      if i < len(region) and region[i] != DECODE_FILL)
        extents[k] = wrote
        fault = regs['PC'] != DECODE_STOP
        head_ok = region[0xA4:0xA8] == (blkb[0x20] << 16).to_bytes(4, 'little')
        second_ok = region[0xE8:0xEC] == (blkb[0x52] << 16).to_bytes(4, 'little')
        ok = not fault and head_ok and second_ok
        resE['decode_k=%d' % k] = ok
        allok &= ok
        print('   k=%d pc=0x%08X head=%s second=%s %s'
              % (k, regs['PC'], head_ok, second_ok, 'PASS' if ok else 'FAIL'))
    same = len(set(extents.values())) == 1
    st['decode_same_extent'] = same
    allok &= same
    print('   four blocks touch the same obj offsets: %s' % same)

    checks = [
        '1 four tone LUTs == build/looks_final/*.json tone256 (256 x u16 LE)',
        '1 four matrices == hardware-encoder fixed point of matrix_q8, row sums 256',
        '1 four chroma blocks == JSON x/y with z=z_chain, pad=0, check_block OK',
        '2 tone/block/matrix pointer tables point at those four sets',
        '2 hooks + SU/SEL/MX stubs + B90 i-e SD stub identical to B90',
        '2 stock chroma block, stock m0, stash, retired B59 stub slot, bank2 untouched',
        '3 diff vs stock inside patch ranges; diff vs B90 only tone/matrix/block',
        '4 SU stub: k=1..4 -> EXIF "k=N\\0"+key, a0=k-th tone pointer, d0=1',
        '4 SU stub: k=0/5/7 -> EXIF untouched, a0=ctx+0x1230',
        '4b upload path: DEST tone table == JSON tone for k=1..4',
        '5 selector: stash=1..4 -> a0=BLK_TAB[k-1] and bytes == JSON block',
        '5 selector: stash=0/5/7 -> a0=stock 0x605D4829',
        '5 selector: ctx+0xC in {0xA400,0xA800,0xA900} same path',
        '6 consumer segment: four blocks decode without fault',
    ]
    rec = {'pkg': path, 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b95.py', 'closed': bool(allok),
           'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'src': SRC, 'baseline': BASE_PKG,
           'baseline_sha256': hashlib.sha256(b90_whole).hexdigest(),
           'looks': [{'k': i + 1, 'mode': l['mode'], 'look': l['name'],
                      'json_matrix_q8': [int(v) for v in l['json']['matrix_q8']],
                      'stored_matrix_q8': l['mflat'],
                      'tone_addr': '0x%08X' % TONE_ADDRS[i],
                      'matrix_addr': '0x%08X' % (MX_DATA + 18 * i),
                      'block_addr': '0x%08X' % CH_ADDRS[i]}
                     for i, l in enumerate(looks)],
           'static': st, 'checks': checks,
           'diff_vs_stock_bytes': len(ds), 'diff_vs_b90_bytes': len(d90),
           'changed_spans_vs_b90': [{'from': '0x%08X' % a, 'to': '0x%08X' % (a + n - 1),
                                     'span': n, 'changed': c} for a, n, c in per_span],
           'dynamic': dict(resB, **resU, **resA, **resE)}
    with open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print('\n-> ' + ('all closed' if allok else 'SOME CHECKS FAILED'))
    print('   sim record: %s.sim.json  closed=%s' % (path, allok))
    return 0 if allok else 1


if __name__ == '__main__':
    sys.exit(main())
