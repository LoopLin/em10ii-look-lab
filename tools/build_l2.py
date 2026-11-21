#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_l2.py -- L2 (Lemon 1.0.1): L1 looks + ART key probe (tone LUT + 3x3 matrix + chroma block per mode)

What this package is
--------------------
B90's patch set (= B69's patch set + the 6 B i-e SD stub) is the baseline and is
rebuilt from stock, never from a previous image.  On top of that baseline only
the *data* the four look JSONs describe is replaced:

  k1 Vivid     = Ektachrome 100 VS   build/looks_final/ektachrome_100_vs.json
  k2 Natural   = Elite Color 200     build/looks_final/elite_color_200.json
  k3 Muted     = Classic Chrome      build/looks_final/classic_chrome.json
  k4 Portrait  = Pro Neg Hi          build/looks_final/pro_neg_hi.json

  tones     4 x 0x200 B at b54.TONE_ADDRS   (slots 22..25, B59 layout, <256H)
  matrices  4 x 18 B    at b54.MX_DATA      (Q8 intent tables, encoder-legal)
  blocks    4 x 100 B   at b61.CH_ADDRS     (Cb/Cr x,y + z chain + pad=0)

Everything else (SU/SEL/MX/SD hooks and stubs, pointer tables, EXIF write, the
i-e stash clear) comes from .tmp/b69.patch + the B90 SD stub, byte for byte.

Matrix encoding (hardware 0x604B6A22, see docs/color/matrix-reader.md 1.6)
-------------------------------------------------------------------------
The encoder reads the six off-diagonal entries, clears the low bit of m01 and
m21, and (when m10+m12 is odd) clears the low bit of both m10 and m12; the three
diagonal entries are then derived from the row sums (m00 = 256-m01-m02,
m11 = 256-m10-m12, m22 = 256-m21-m22).  A table is therefore *encoder-legal*
only if the derived diagonal is what we intend, i.e. the stored table must be a
fixed point of that map.  Three of the four looks already are (their rows sum to
256 with even m01/m21/m10+m12).  pro_neg_hi is not: its row 1 is (18,201,37),
m10+m12 = 55 is odd, so the hardware would turn it into (18,202,36).  We store
that fixed point, which differs from the JSON by 1 LSB on m11 and m12 (0.4% of
one row entry; hue change is unmeasurable).  Every stored table is asserted to
be a fixed point and to have exact 256 row sums.

Only offline: no card, no camera, nothing outside build/ tools/ docs/.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.chdir(ROOT)

import oly_container as oc                      # noqa: E402
import build_b46 as b46                         # noqa: E402
import build_b54 as b54                         # noqa: E402
import build_b61 as b61                         # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/L2.BIN'
BASE_PKG = 'build/B90_ie_stock.BIN'
BASE_SHA = 'a8c40b907f438e8af02c4c52f21d888843dcb763472e9cfea46d26be5a87838d'
B69_PATCH = '.tmp/b69.patch'
B90_PATCH = '.tmp/b90.patch'
PATCH = '.tmp/l2.patch'
OVERLAY = 'build/L2.omemu.json'
LAYOUT = '.tmp/l2_layout.json'

SD_STUB, SD_RET = b54.SD_STUB, b54.SD_RET
SD_TEXT = '''
\tmov\td1,d2
\tmov\t0x605DED70,a0
\tmov\td2,(a0)
\tcmp\t2,d2
\tbne\tLskip
\tclr\td0
\tmov\td0,(a0)
Lskip:
\tjmp\t0x604B8124
'''

LOOKS = [
    ('ektachrome_100_vs', 'Vivid', 'Kodak Ektachrome 100 VS'),
    ('elite_color_200', 'Natural', 'Kodak Elite Color 200'),
    ('classic_chrome', 'Muted', 'Fuji Classic Chrome'),
    ('pro_neg_hi', 'Portrait', 'Fuji Pro Neg Hi'),
]


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def at(img, addr, n):
    return bytes(img[addr - BASE:addr - BASE + n])


# ---------------------------------------------------------------- matrices

def enc_matrix_hw(flat):
    """Hardware 0x604B6A22 fixed point for one row-major Q8 3x3 (9 ints).

    Same operations as build_b54.enc_matrix except that the *diagonal* of row 1
    (flat[4] = m11) is the derived entry, exactly like the encoder: row 0 and
    row 2 have their derived entry at flat[0]/flat[6] because m00/m20 are the
    diagonals there, so flat[3] (m10) is never derived.
    """
    f = [int(v) for v in flat]
    f[1] &= ~1                                   # m01
    f[7] &= ~1                                   # m21
    if (f[3] + f[5]) & 1:                        # m10 + m12
        f[3] &= ~1
        f[5] &= ~1
    f[0] = 256 - f[1] - f[2]                     # m00
    f[4] = 256 - f[3] - f[5]                     # m11
    f[6] = 256 - f[7] - f[8]                     # m22
    for r in range(3):
        if sum(f[3 * r:3 * r + 3]) != 256:
            raise SystemExit('matrix row %d sums to %d != 256: %r'
                             % (r, sum(f[3 * r:3 * r + 3]), f))
    if f[1] & 1 or f[7] & 1 or (f[3] + f[5]) & 1:
        raise SystemExit('matrix still has odd entries after canonicalization: %r' % f)
    # must be a fixed point of the encoder (writing it must reproduce it)
    if enc_matrix_hw_raw(f) != f:
        raise SystemExit('matrix is not an encoder fixed point: %r' % f)
    return f


def enc_matrix_hw_raw(flat):
    """One pass of the encoder map without the assertions (for the fixed point)."""
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


def matrix_blob(flat):
    return b''.join(struct.pack('<h', v) for v in flat)


# ---------------------------------------------------------------- chroma

def pair_list(pairs, name):
    if len(pairs) != 16:
        raise SystemExit('%s: %d pairs != 16' % (name, len(pairs)))
    xs = [int(p[0]) for p in pairs]
    ys = [int(p[1]) for p in pairs]
    for v in xs + ys:
        if not 0 <= v <= 255:
            raise SystemExit('%s: value %d outside 0..255' % (name, v))
    return xs, ys


def build_block(cb, cr):
    """Cb and Cr: x, y, z=z_chain(x,y), pad=0 (layout of build_b61)."""
    out = b''
    for pairs, tag in ((cb, 'Cb'), (cr, 'Cr')):
        x, y = pair_list(pairs, tag)
        out += bytes(x) + bytes(y) + bytes(b61.z_chain(x, y)) + b'\x00'
    if len(out) != b61.CH_STRIDE:
        raise SystemExit('chroma block %d B != 100' % len(out))
    b61.check_block(out, 'B95 block')
    return out


# ---------------------------------------------------------------- baseline

def sd_stub():
    blob = b46.assemble(SD_TEXT, 'b90_sd', SD_STUB)
    site = SD_STUB + len(blob) - 5
    want = b46.jmp_bytes(site, SD_RET)
    if blob[-5:] != want:
        blob = blob[:-5] + want
    return blob


def read_patch(path):
    text = io.open(path, encoding='utf-8').read()
    return [ln for ln in text.replace('\r\n', '\n').split('\n')]


def baseline_lines():
    """Rebuild B90's patch text from .tmp/b69.patch + the i-e SD stub."""
    out = []
    for ln in read_patch(B69_PATCH):
        if ln.startswith('ram 0x605ded44=') or ln.startswith('ram 0x605DED44='):
            continue
        out.append(ln)
    out.append('ram 0x%x=%s' % (SD_STUB, sd_stub().hex()))
    # ignore the trailing empty element of split('\n') on both sides
    ref = [ln for ln in read_patch(B90_PATCH) if ln != '']
    got = [ln for ln in out if ln != '']
    if got != ref:
        diff = [(i, a, b) for i, (a, b) in enumerate(zip(got, ref)) if a != b]
        raise SystemExit('rebuilt baseline patch != .tmp/b90.patch: first diffs %r'
                         % (diff[:3] or ('length', len(got), len(ref))))
    return got


def split_line(ln):
    head, _, rest = ln.partition('=')
    hexpart, _, _comment = rest.partition('  #')
    addr = int(head.split()[1], 16)
    return addr, bytes.fromhex(hexpart.strip()), ln


def replace_data(ref, replacements):
    """Replace ram lines by address; return (lines, old map)."""
    by_addr = {}
    for ln in ref:
        if ln.startswith('ram '):
            a, blob, _ = split_line(ln)
            if a in by_addr:
                raise SystemExit('duplicate ram line 0x%X' % a)
            by_addr[a] = blob
    missing = [a for a in replacements if a not in by_addr]
    if missing:
        raise SystemExit('patch has no line for %s' % ['0x%X' % a for a in missing])
    out, olds = [], {}
    for ln in ref:
        if ln.startswith('ram '):
            a, blob, _ = split_line(ln)
            if a in replacements:
                new, comment = replacements[a]
                olds[a] = blob
                out.append('ram 0x%x=%s  # %s' % (a, new.hex(), comment))
                continue
        out.append(ln)
    return out, olds


def build():
    stock = payload_of(SRC)
    b90 = payload_of(BASE_PKG)
    with open(BASE_PKG, 'rb') as f:
        if hashlib.sha256(f.read()).hexdigest() != BASE_SHA:
            raise SystemExit('%s sha256 != %s' % (BASE_PKG, BASE_SHA))

    ref = baseline_lines()

    looks = []
    for name, mode, label in LOOKS:
        with io.open('build/looks_final/%s.json' % name, encoding='utf-8') as f:
            d = json.load(f)
        tone = [int(v) for v in d['tone256']]
        if len(tone) != 256 or min(tone) < 0 or max(tone) > 1023:
            raise SystemExit('%s: tone256 shape/range' % name)
        if any(b < a for a, b in zip(tone, tone[1:])):
            raise SystemExit('%s: tone256 is not monotonic' % name)
        mjson = [int(v) for v in d['matrix_q8']]
        if len(mjson) != 9:
            raise SystemExit('%s: matrix_q8 has %d entries' % (name, len(mjson)))
        mflat = enc_matrix_hw(mjson)
        blk = build_block(d['chroma_cb'], d['chroma_cr'])
        looks.append(dict(name=name, mode=mode, label=label, json=d, tone=tone,
                          mjson=mjson, mflat=mflat, blk=blk))

    # four distinct blocks / matrices; nothing degenerate
    if len({l['blk'] for l in looks}) != 4:
        raise SystemExit('two looks share the same chroma block')
    if len({tuple(l['mflat']) for l in looks}) != 4:
        raise SystemExit('two looks share the same matrix')

    repl = {}
    for i, l in enumerate(looks):
        repl[b54.TONE_ADDRS[i]] = (struct.pack('<256H', *l['tone']),
                                   'tone slot %d k=%d %s (%s)'
                                   % (b54.SLOTS[i], i + 1, l['mode'], l['name']))
    for i, l in enumerate(looks):
        repl[b54.MX_DATA + 18 * i] = (matrix_blob(l['mflat']),
                                      'matrix k=%d %s %r' % (i + 1, l['mode'], l['mflat']))
    for i, l in enumerate(looks):
        repl[b61.CH_ADDRS[i]] = (l['blk'], 'chroma k=%d %s 16-pt x/y + z'
                                 % (i + 1, l['mode']))

    lines, olds = replace_data(ref, repl)
    # ---- ART key probe: hook the parameter-group selector, record key ----
    PROBE_HOOK, PROBE_STUB, PROBE_RET, PROBE_CELL = 0x604B8F1A, 0x6066159E, 0x604B8F1F, 0x61894CC0
    stock_hook = at(stock, PROBE_HOOK, 5)
    if stock_hook != bytes.fromhex('939a08f17e'):
        raise SystemExit('probe hook site is not the expected 5 B: %s' % stock_hook.hex())
    if at(stock, PROBE_STUB, 66) != b'\xff' * 66:
        raise SystemExit('probe pocket 0x%X is not free in stock' % PROBE_STUB)
    probe_stub = b46.assemble(
        '\n mov a0,a3\n mov 0x8,a2\n add a3,a2\n mov (a2),d0\n'
        ' mov 0x%08x,a1\n mov d0,(a1)\n jmp 0x%08x-(0x%08x+13)\n'
        % (PROBE_CELL, PROBE_RET, PROBE_STUB), 'l2_probe', PROBE_STUB)
    if len(probe_stub) != 18:
        raise SystemExit('probe stub is %d B, expected 18' % len(probe_stub))
    probe_hook = b46.jmp_bytes(PROBE_HOOK, PROBE_STUB)
    lines = [ln for ln in lines if ln != '']
    lines += ['ram 0x%x=%s  # ART probe hook -> 0x%X' % (PROBE_HOOK, probe_hook.hex(), PROBE_STUB),
              'ram 0x%x=%s  # ART probe stub: key=u32[ctx+8] -> EXIF+0x30' % (PROBE_STUB, probe_stub.hex())]

    body = '\n'.join([ln for ln in lines if ln != '']) + '\n'
    body.encode('ascii')
    io.open(PATCH, 'w', encoding='utf-8', newline='\n').write(body)

    if os.path.exists(OUT):
        os.remove(OUT)
    b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT,
             '--patch-file', PATCH, '--omemu-patch', OVERLAY])

    built = payload_of(OUT)

    # expected changed ranges = the patch lines actually applied
    ranges = []
    for ln in lines:
        if ln.startswith('ram '):
            a, blob, _ = split_line(ln)
            ranges.append((a, len(blob)))
    ranges.sort()
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    stray = [i for i in diff
             if not any(a - BASE <= i < a - BASE + ln for a, ln in ranges)]
    if stray:
        raise SystemExit('diff outside patch ranges: %d bytes, first 0x%X'
                         % (len(stray), BASE + stray[0]))

    # data spans vs baseline B90: everything else must be byte-identical
    data_spans = ([(a, 0x200) for a in b54.TONE_ADDRS]
                  + [(b54.MX_DATA + 18 * i, 18) for i in range(4)]
                  + [(a, b61.CH_STRIDE) for a in b61.CH_ADDRS])
    hard_spans = [(b54.SD_INSERT, b54.SD_LEN), (b54.SU_INSERT, b54.SU_LEN),
                  (b54.MX_INSERT, b54.MX_LEN), (0x604B4CAD, 6),
                  (SD_STUB, len(sd_stub())), (0x605DECD4, 69),
                  (0x605DED1C, 38), (b54.MX_STUB, 50),
                  (b54.SU_PTRTAB, 16), (0x605DEDE4, 16), (b54.MX_PTRTAB, 16)]
    d90 = [i for i in range(len(built)) if built[i] != b90[i]]
    stray90 = [i for i in d90
               if not any(a - BASE <= i < a - BASE + ln for a, ln in data_spans)]
    if stray90:
        raise SystemExit('diff vs B90 outside tone/matrix/block: %d bytes, first 0x%X'
                         % (len(stray90), BASE + stray90[0]))

    # baseline pieces still identical to B90
    for a, ln in hard_spans:
        if at(built, a, ln) != at(b90, a, ln):
            raise SystemExit('baseline piece 0x%X..0x%X changed vs B90' % (a, a + ln))

    # stock invariants
    inv = [(b61.CH_STOCK, b61.CH_STRIDE, 'stock chroma block'),
           (b54.MATRIX_M0, 18, 'stock m0'),
           (b54.STASH, 4, 'stash 0x605DED70'),
           (0x605DED80, 5, 'retired B59 SU stub site')]
    for a, ln, nm in inv:
        if at(built, a, ln) != at(stock, a, ln):
            raise SystemExit('%s changed vs stock' % nm)
    bank2 = [i for i in range(b54.BANK2[0] - BASE, b54.BANK2[1] - BASE)
             if built[i] != stock[i]]
    if bank2:
        raise SystemExit('bank2 changed: %d bytes' % len(bank2))

    # read back the data
    for i, l in enumerate(looks):
        if at(built, b54.TONE_ADDRS[i], 0x200) != struct.pack('<256H', *l['tone']):
            raise SystemExit('tone slot %d readback mismatch' % b54.SLOTS[i])
        if at(built, b54.MX_DATA + 18 * i, 18) != matrix_blob(l['mflat']):
            raise SystemExit('matrix k=%d readback mismatch' % (i + 1))
        if at(built, b61.CH_ADDRS[i], b61.CH_STRIDE) != l['blk']:
            raise SystemExit('block k=%d readback mismatch' % (i + 1))

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(),
           'len': len(whole), 'built_from': SRC, 'baseline': BASE_PKG,
           'baseline_sha256': BASE_SHA, 'patch': PATCH, 'overlay': OVERLAY,
           'looks': [{'k': i + 1, 'mode': l['mode'], 'look': l['name'],
                      'label': l['label'],
                      'json_matrix_q8': l['mjson'],
                      'stored_matrix_q8': l['mflat'],
                      'matrix_adjusted': l['mjson'] != l['mflat'],
                      'matrix_adjusted_idx': [j for j in range(9)
                                              if l['mjson'][j] != l['mflat'][j]],
                      'tone_addr': b54.TONE_ADDRS[i],
                      'matrix_addr': b54.MX_DATA + 18 * i,
                      'block_addr': b61.CH_ADDRS[i],
                      'tone_min': min(l['tone']), 'tone_max': max(l['tone']),
                      'tone_t46': l['tone'][46]}
                     for i, l in enumerate(looks)],
           'ranges': ranges, 'data_spans': data_spans,
           'diff_vs_stock': len(diff), 'diff_vs_b90': len(d90),
           'blocks': list(b61.CH_ADDRS), 'ch_stride': b61.CH_STRIDE,
           'tones': list(b54.TONE_ADDRS), 'slots': list(b54.SLOTS),
           'mx_data': b54.MX_DATA, 'su_ptrtab': b54.SU_PTRTAB,
           'blk_tab': 0x605DEDE4, 'mx_ptrtab': b54.MX_PTRTAB,
           'sd_stub': SD_STUB, 'su_stub': 0x605DECD4, 'sel_stub': 0x605DED1C,
           'mx_stub': b54.MX_STUB, 'bank2': list(b54.BANK2)}
    with io.open(LAYOUT, 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    return lay, looks, built, stock, b90, diff, d90


def main():
    lay, looks, built, stock, b90, diff, d90 = build()
    print('B95 = B90 patch set (rebuilt from stock) + four look data sets')
    print('  patch lines: %d (baseline rebuilt == .tmp/b90.patch)'
          % len([ln for ln in read_patch(PATCH) if ln.startswith('ram ')]))
    for i, l in enumerate(looks):
        adj = '' if l['mjson'] == l['mflat'] else '  [encoder %r -> %r]' % (
            l['mjson'], l['mflat'])
        print('  k=%d %-8s %-20s tone[46]=%3d matrix=%r%s'
              % (i + 1, l['mode'], l['name'], l['tone'][46], l['mflat'], adj))
    print('  diff vs stock: %d B, all inside %d patch ranges'
          % (len(diff), len(lay['ranges'])))
    print('  diff vs B90  : %d B, all inside tone/matrix/block' % len(d90))
    for a, n in lay['data_spans']:
        c = sum(1 for i in range(a - BASE, a - BASE + n) if built[i] != b90[i])
        if c:
            print('    span 0x%08X..0x%08X  %3d B, %3d changed' % (a, a + n - 1, n, c))
    print('  BIN %d B sha256=%s' % (lay['len'], lay['sha256']))
    print('  -> PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
