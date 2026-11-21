#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b66.py — B66: B63 patch set rebuilt from stock, SU stub guarded.

B66 keeps B63's whole patch set (tone slots, four matrices, EXIF write, four
extreme chroma blocks, extended SU stub at 0x605DECD4) but the stub only
rewrites slot1+0x1D when the look key CHANGED since the previous upload.

Why: B63/B64 reboot the camera on the first shutter press even when the four
blocks carry benign stock bytes (B64), and even with the SD card removed, so
the reboot is caused by the act of writing slot1 during capture. The uploader
runs every preview frame too; live view is fine. This package tests the
hypothesis "the write at shutter time is the killer" by making the write
happen only on a key change (i.e. right after switching mode / at start-up),
never on a steady-state preview or capture frame.

Guard: the 4-byte key is kept in the EXIF text block at 0x61894C90+4 (the same
cell B54/B59 already used for the key). If the new key equals the stored key
the stub skips the EXIF rewrite AND the 100-byte slot1 copy, then falls through
to the original tone upload (a0 = ctx+0x1230, d0 = 1).

Stub size: 109 B <= SU_STUB_MAX (112); bytes 0x605DED41..0x605DED43 stay free.

Hard constraints asserted at build time:
- built only from stock; B59_real/B63/B64 are never build bases
- four blocks are byte-identical to B63's blocks (extreme y, x/z/pad as B63)
- bank2 / stock m0 / stock block 0x605D4829 / chroma hook 0x604AA297 untouched
- 0x605DED80 keeps stock bytes
- the finished pack differs from B63 ONLY inside the SU stub region
  0x605DECD4..0x605DED44 (the stub is 105 -> 109 B)

Offline only: no flashing, no SD card writes.
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
import build_b63 as b63                         # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
STOCK = SRC
OUT = 'build/B66_slot1.BIN'
B63_PKG = 'build/B63_slot1.BIN'

HOOK, HOOK_LEN = b63.HOOK, b63.HOOK_LEN
SU_RET = b63.SU_RET
SU_STUB = b63.SU_STUB
SU_STUB_MAX = b63.SU_STUB_MAX
OLD_SU_STUB, OLD_SU_LEN = b63.OLD_SU_STUB, b63.OLD_SU_LEN
STASH = b63.STASH
SU_PTRTAB = b63.SU_PTRTAB
BLK_TAB = b63.BLK_TAB
CH_STOCK = b61.CH_STOCK
CH_BASE, CH_STRIDE = b63.CH_BASE, b63.CH_STRIDE
CH_ADDRS = list(b63.CH_ADDRS)
EXIF_TEXT = b63.EXIF_TEXT
EXIF_LEAD = b63.EXIF_LEAD
CH_HOOK = b63.CH_HOOK
POOL, BANK2 = b54.POOL, b54.BANK2
BANNED = b54.BANNED
KNOWN_ZONE = b63.KNOWN_ZONE
LOOKS = b61.LOOKS
BLOCK_SPEC = b63.BLOCK_SPEC
PH1 = 0x11111111

# 键变化才写：EXIF_TEXT+4 存上一次的 4 字节键，相等则跳过写 EXIF 与拷 slot1。
SU_TMPL = r"""
	.am33
	.text
	.globl	_start
_start:
	mov	(a0),d0
	mov	d2,a1
	add	0x400,d2
	cmp	0,d0
	beq	Lgo
	mov	0x%X,a2
	mov	(a2),d0
	mov	d0,d3
	mov	d0,d1
	and	7,d0
	asr	21,d1
	and	0x18,d1
	or	d1,d0
	add	-1,d0
	cmp	3,d0
	bhi	Lgo
	mov	0x%X,a2
	mov	(4,a2),d1
	cmp	d1,d3
	beq	Lgo
	mov	a0,d1
	add	0x25a8,d1
	mov	d1,a1
	mov	(a1),a1
	cmp	0,a1
	beq	Lgo
	mov	d3,(4,a2)
	add	0x1d,a1
	add	d0,d0
	add	d0,d0
	mov	0x%X,a0
	add	d0,a0
	mov	a0,d3
	mov	(0x10,a0),a0
	mov	-100,d1
	setlb
Lcp:
	movbu	(a0),d0
	inc	a0
	movbu	d0,(a1)
	inc	a1
	inc	d1
	lne
	mov	d3,a0
Lgo:
	mov	d2,a1
	add	-0x400,a1
	mov	1,d0
	jmp	@@JRET@@
""" % (STASH, EXIF_TEXT, SU_PTRTAB)


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def in_span(a, ln, span):
    return a >= span[0] and a + ln <= span[1]


def overlaps(a, ln, spans):
    return any(a < s[0] + s[1] and s[0] < a + ln for s in spans)


def assemble_stub():
    ph = b46.assemble(SU_TMPL.replace('@@JRET@@', '0x%X' % PH1), 'b65_su_ph', SU_STUB)
    j = b46.find_ph(ph, PH1)
    blob = b46.assemble(SU_TMPL.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (SU_RET, SU_STUB, j)),
                        'b65_su', SU_STUB)
    if blob[j:j + 5] != b'\xdc' + struct.pack('<i', SU_RET - (SU_STUB + j)):
        raise SystemExit('SU 桩 jmp 自检失败 @%#x' % (SU_STUB + j))
    if len(blob) > SU_STUB_MAX:
        raise SystemExit('SU 桩 %d B 超槽 52 口袋尾（<=%d）' % (len(blob), SU_STUB_MAX))
    seq = b'\xdb\xf0\x40\x41\xf0\x51\x45\x44\xd9'
    if blob.count(seq) != 1:
        raise SystemExit('SU 桩拷贝循环样本出现 %d 次（应为 1）' % blob.count(seq))
    if b'\x85\x9c' not in blob:
        raise SystemExit('SU 桩没有 mov -100,d1（100 B 计数）')
    return blob, j


def build():
    info = b54.build()
    sd, mx = info['sd'], info['mx']

    tone_tabs = json.load(io.open('.tmp/b58_tables.json', encoding='utf-8'))
    mx_rows = json.load(io.open('.tmp/b59_mx.json'))
    if [t['look'] for t in tone_tabs] != LOOKS:
        raise SystemExit('b58_tables 顺序 %r != %r' % ([t['look'] for t in tone_tabs], LOOKS))
    tone = [b''.join(struct.pack('<H', v) for v in t['u16']) for t in tone_tabs]
    for i, t in enumerate(tone):
        if len(t) != 0x200:
            raise SystemExit('tone 表 %d 长度 %d' % (i, len(t)))
    mblobs = []
    for rows in mx_rows:
        flat = b54.enc_matrix([rows[0:3], rows[3:6], rows[6:9]])
        mblobs.append(b''.join(struct.pack('<h', v) for v in flat))

    stub, jmp_off = assemble_stub()
    stock = payload_of(SRC)
    stock_blk = bytes(stock[CH_STOCK - BASE:CH_STOCK - BASE + CH_STRIDE])
    b61.check_block(stock_blk, '库存块')
    blocks = []
    for i, (cb_y, cr_y) in enumerate(BLOCK_SPEC):
        blk = b61.extreme_block(cb_y, cr_y, stock_blk)
        b61.check_block(blk, 'B66 k=%d' % (i + 1), cb_y, cr_y, stock_blk)
        blocks.append(blk)

    blk_tab = struct.pack('<4I', *CH_ADDRS)

    new_spans = [(SU_STUB, len(stub)), (BLK_TAB, len(blk_tab))] \
                + [(a, CH_STRIDE) for a in CH_ADDRS]
    b59_spans = [(b54.SD_INSERT, b54.SD_LEN), (b54.SU_INSERT, b54.SU_LEN),
                 (b54.MX_INSERT, b54.MX_LEN), (b54.SD_STUB, len(sd)),
                 (b54.SU_PTRTAB, 16), (b54.MX_PTRTAB, 16),
                 (b54.MX_DATA, 72), (b54.MX_STUB, len(mx))]
    b59_spans += [(a, 0x200) for a in b54.TONE_ADDRS]

    for a, ln, name in ([(SU_STUB, len(stub), '扩展 SU 桩'), (BLK_TAB, len(blk_tab), '块指针表')]
                        + [(a, CH_STRIDE, '块 k=%d' % (i + 1)) for i, a in enumerate(CH_ADDRS)]):
        if not in_span(a, ln, POOL):
            raise SystemExit('%s 0x%X..0x%X 不在池内' % (name, a, a + ln))
        if not in_span(a, ln, KNOWN_ZONE):
            raise SystemExit('%s 0x%X..0x%X 不在已知落点 0x605DEB00..0x605DF000'
                             % (name, a, a + ln))
        for b in BANNED:
            if a <= b < a + ln:
                raise SystemExit('%s 压到禁地 0x%X' % (name, b))
        if a < BANK2[1] and a + ln > BANK2[0]:
            raise SystemExit('%s 压到 bank2' % name)
    if not in_span(HOOK, HOOK_LEN, (0x60479A20, 0x60479A30)):
        raise SystemExit('钩点 0x%X 不在 0x60479A20..0x60479A30' % HOOK)
    if SU_STUB + len(stub) > 0x605DED44:
        raise SystemExit('扩展桩压到 SD 桩 0x605DED44')
    if overlaps(SU_STUB, len(stub), b59_spans):
        raise SystemExit('扩展桩压到 B59 已用区')
    if overlaps(BLK_TAB, len(blk_tab), b59_spans):
        raise SystemExit('块指针表压到 B59 已用区')
    for a in CH_ADDRS:
        if overlaps(a, CH_STRIDE, b59_spans):
            raise SystemExit('块 0x%X 压到 B59 已用区' % a)
    srt = sorted(new_spans)
    for i in range(1, len(srt)):
        if srt[i - 1][0] + srt[i - 1][1] > srt[i][0]:
            raise SystemExit('新落点互相重叠：%s / %s' % (srt[i - 1], srt[i]))

    def sat(addr, n):
        return bytes(stock[addr - BASE:addr - BASE + n])

    if sat(CH_HOOK, 5) != bytes(stock[CH_HOOK - BASE:CH_HOOK - BASE + 5]):
        raise SystemExit('stock 色度钩区读不到')
    if sat(OLD_SU_STUB, OLD_SU_LEN) != bytes(stock[OLD_SU_STUB - BASE:OLD_SU_STUB - BASE + OLD_SU_LEN]):
        raise SystemExit('stock 旧 SU 桩区读不到')

    ranges = sorted(b59_spans + new_spans)
    lines = ['# B66 build from stock: B63 patch set, SU stub writes slot1 only on key change',
             'ram 0x%x=%s  # hook A 0x604B811D -> SD stub (B59)' % (b54.SD_INSERT, (b46.jmp_bytes(b54.SD_INSERT, b54.SD_STUB) + b'\x00\x00').hex()),
             'ram 0x%x=%s  # hook B 0x60479A2B -> extended SU stub 0x%X' % (HOOK, b46.jmp_bytes(HOOK, SU_STUB).hex(), SU_STUB),
             'ram 0x%x=%s  # hook C 0x604B6AD6 -> MX stub (B59)' % (b54.MX_INSERT, b46.jmp_bytes(b54.MX_INSERT, b54.MX_STUB).hex()),
             'ram 0x%x=%s  # SD stub %d B' % (b54.SD_STUB, sd.hex(), len(sd)),
             'ram 0x%x=%s  # extended SU stub %d B (key-change guard)' % (SU_STUB, stub.hex(), len(stub)),
             'ram 0x%x=%s  # MX stub %d B' % (b54.MX_STUB, mx.hex(), len(mx)),
             'ram 0x%x=%s  # tone ptr table [slot22..25]' % (SU_PTRTAB, struct.pack('<4I', *b54.TONE_ADDRS).hex()),
             'ram 0x%x=%s  # chroma block ptr table [blk1..4]' % (BLK_TAB, blk_tab.hex()),
             'ram 0x%x=%s  # matrix ptr table' % (b54.MX_PTRTAB, struct.pack('<4I', *[b54.MX_DATA + 18 * i for i in range(4)]).hex())]
    for i, a in enumerate(b54.TONE_ADDRS):
        lines.append('ram 0x%x=%s  # tone slot %d (%s)' % (a, tone[i].hex(), b54.SLOTS[i], LOOKS[i]))
    for i, blob in enumerate(mblobs):
        lines.append('ram 0x%x=%s  # matrix k=%d %r'
                     % (b54.MX_DATA + 18 * i, blob.hex(), i + 1, mx_rows[i]))
    for i, blk in enumerate(blocks):
        lines.append('ram 0x%x=%s  # chroma k=%d extreme (y 0x%02X/0x%02X)'
                     % (CH_ADDRS[i], blk.hex(), i + 1, BLOCK_SPEC[i][0], BLOCK_SPEC[i][1]))
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/b66.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(sd=sd, mx=mx, stub=stub, jmp_off=jmp_off, tone=tone, mblobs=mblobs,
                blocks=blocks, stock_blk=stock_blk, blk_tab=blk_tab,
                ranges=ranges, new_spans=new_spans, b59_spans=b59_spans, patch=pf)


def main():
    info = build()
    print('B66 = B63 补丁集（从 stock 重建），SU 桩改为「键变化才写 slot1」')
    print('  扩展 SU 桩 %d B @0x%X（jmp 回 0x%X @+%d）；块指针表 @0x%X'
          % (len(info['stub']), SU_STUB, SU_RET, info['jmp_off'], BLK_TAB))
    print('  库存块 0x%X (100 B): %s' % (CH_STOCK, info['stock_blk'].hex()))

    if os.path.exists(OUT):
        os.remove(OUT)
    b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT, '--patch-file', info['patch']])

    stock = payload_of(SRC)
    built = payload_of(OUT)

    def at(img, addr, n):
        return bytes(img[addr - BASE:addr - BASE + n])

    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    outside = [i for i in diff if not any(a - BASE <= i < a - BASE + l for a, l in info['ranges'])]
    print('\n改动 %d 字节；区外 %d 字节 -> %s' % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    ok = not outside

    bad = [i for i in range(BANK2[0] - BASE, BANK2[1] - BASE) if built[i] != stock[i]]
    print('  bank2 0x%X..0x%X 改动 %d 字节 -> %s'
          % (BANK2[0], BANK2[1], len(bad), 'OK' if not bad else 'FAIL'))
    ok &= not bad

    # 与 B63 的差异只许落在扩展 SU 桩区（块与 B63 逐字节相同）
    b63_img = payload_of(B63_PKG)
    b63_stub = bytes(b63_img[SU_STUB - BASE:SU_STUB - BASE + 0x70])
    d63 = [i for i in range(len(built)) if built[i] != b63_img[i]]
    stub_span = (SU_STUB - BASE, 0x70)
    stray = [i for i in d63 if not (stub_span[0] <= i < stub_span[0] + stub_span[1])]
    print('  vs B63 差异 %d 字节；SU 桩区之外 %d 字节 -> %s'
          % (len(d63), len(stray), 'OK' if not stray else 'FAIL'))
    if stray:
        runs = []
        s = p = stray[0]
        for i in stray[1:]:
            if i - p > 1:
                runs.append((BASE + s, BASE + p, p - s + 1))
                s = i
            p = i
        runs.append((BASE + s, BASE + p, p - s + 1))
        for a, e, n in runs:
            print('     越界差异 0x%X..0x%X (%d B)' % (a, e, n))
    ok &= not stray

    for i, a in enumerate(CH_ADDRS):
        same = at(built, a, CH_STRIDE) == at(b63_img, a, CH_STRIDE) == info['blocks'][i]
        ok &= same
        print('  色度块 k=%d == B63 块 == 构建块: %s' % (i + 1, same))

    for name, got, want in (('hook A', at(built, b54.SD_INSERT, 7), b46.jmp_bytes(b54.SD_INSERT, b54.SD_STUB) + b'\x00\x00'),
                            ('hook B', at(built, HOOK, 5), b46.jmp_bytes(HOOK, SU_STUB)),
                            ('hook C', at(built, b54.MX_INSERT, 5), b46.jmp_bytes(b54.MX_INSERT, b54.MX_STUB)),
                            ('SD 桩', at(built, b54.SD_STUB, len(info['sd'])), info['sd']),
                            ('扩展 SU 桩', at(built, SU_STUB, len(info['stub'])), info['stub']),
                            ('MX 桩', at(built, b54.MX_STUB, len(info['mx'])), info['mx']),
                            ('tone 指针表', at(built, SU_PTRTAB, 16), struct.pack('<4I', *b54.TONE_ADDRS)),
                            ('块指针表', at(built, BLK_TAB, 16), info['blk_tab']),
                            ('矩阵指针表', at(built, b54.MX_PTRTAB, 16), struct.pack('<4I', *[b54.MX_DATA + 18 * i for i in range(4)]))):
        good = got == want
        ok &= good
        print('  %-10s 一致=%s' % (name, good))

    for i, a in enumerate(b54.TONE_ADDRS):
        good = at(built, a, 0x200) == info['tone'][i]
        ok &= good
        print('  tone 槽 %-3d 表一致=%s' % (b54.SLOTS[i], good))
    for i, blob in enumerate(info['mblobs']):
        a = b54.MX_DATA + 18 * i
        good = at(built, a, 18) == blob
        ok &= good
        print('  矩阵 k=%d 一致=%s' % (i + 1, good))

    old_stub_ok = at(built, OLD_SU_STUB, OLD_SU_LEN) == at(stock, OLD_SU_STUB, OLD_SU_LEN)
    print('  0x%X 旧 SU 桩区 == 原厂: %s' % (OLD_SU_STUB, old_stub_ok))
    ok &= old_stub_ok

    tail_ok = at(built, HOOK + 5, 2) == at(stock, HOOK + 5, 2)
    print('  钩点后 2 B 死字节保持原厂 %s: %s' % (at(stock, HOOK + 5, 2).hex(), tail_ok))
    ok &= tail_ok

    m0 = at(built, b54.MATRIX_M0, 18) == at(stock, b54.MATRIX_M0, 18)
    print('  原厂 m0 未动=%s' % m0)
    ok &= m0

    pal = at(built, CH_STOCK, CH_STRIDE) == at(stock, CH_STOCK, CH_STRIDE)
    print('  原厂色度块 0x%X 未动=%s' % (CH_STOCK, pal))
    ok &= pal

    ch_hook_stock = at(built, CH_HOOK, 5) == at(stock, CH_HOOK, 5)
    print('  色度 getter 钩 0x%X == 原厂: %s' % (CH_HOOK, ch_hook_stock))
    ok &= ch_hook_stock

    stash_ok = at(built, STASH, 4) == at(stock, STASH, 4)
    print('  stash 0x%X == 原厂: %s' % (STASH, stash_ok))
    ok &= stash_ok

    free_ok = at(built, SU_STUB + len(info['stub']), 0x605DED44 - SU_STUB - len(info['stub'])) == \
              at(stock, SU_STUB + len(info['stub']), 0x605DED44 - SU_STUB - len(info['stub']))
    print('  桩后剩余 %d B 保持原厂: %s' % (0x605DED44 - SU_STUB - len(info['stub']), free_ok))
    ok &= free_ok

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'built_from': SRC, 'compare_to': B63_PKG,
           'hook': HOOK, 'su_stub': SU_STUB, 'su_stub_len': len(info['stub']),
           'su_ret': SU_RET, 'su_jmp_off': info['jmp_off'],
           'old_su_stub': OLD_SU_STUB, 'old_su_len': OLD_SU_LEN,
           'su_ptrtab': SU_PTRTAB, 'blk_tab': BLK_TAB,
           'blocks': CH_ADDRS, 'ch_stride': CH_STRIDE, 'ch_stock': CH_STOCK,
           'block_spec': BLOCK_SPEC,
           'chroma_hook_stock': CH_HOOK, 'looks': LOOKS,
           'tones': b54.TONE_ADDRS, 'slots': b54.SLOTS,
           'mx_data': b54.MX_DATA, 'stash': STASH,
           'b59_spans': info['b59_spans'], 'new_spans': info['new_spans'],
           'ranges': info['ranges'], 'bank2': BANK2, 'known_zone': KNOWN_ZONE}
    with io.open('.tmp/b66_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
