#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b64.py — B64: identical patch set to B63, but the four chroma blocks
become verbatim copies of the stock block at 0x605D4829 (100 bytes).

B63 (build/B63_slot1.BIN) has correct live view on real hardware but the camera
reboots when a photo is saved. B63 differs from B59 only by four extreme chroma
blocks (y all 0x00/0xFF) plus the extended SU stub writing slot1. B64 keeps the
whole B63 patch and only replaces the four blocks with a byte-identical copy of
the stock block at 0x605D4829 (x/y/z/pad copied as-is), to distinguish a
write/save-path collision from an extreme-data failure in the save flow.

Difference vs B63: only the 100-byte content of blocks k=1..4 at
0x605DEB44+0x64*(k-1). Everything else is identical: hook 0x60479A2B, extended
SU stub 0x605DECD4, block pointer table 0x605DEDE4, B59 SD/SU/MX stubs and both
pointer tables, tone slots 22-25, four matrices, EXIF, bank2, m0. The B59 old SU
stub region 0x605DED80 was restored to stock bytes in B63; B64 starts from stock
so that region is stock already and is NOT written.

Hard constraints asserted at build time:
- built only from stock via tools/mkpatch.py; B59_real is never a build base
- each block is byte-identical to stock[0x605D4829:0x605D4829+100] and all four
  blocks are identical to each other
- bank2 / stock m0 / stock block 0x605D4829 / chroma hook 0x604AA297: untouched
- 0x605DED80 keeps stock bytes
- the finished pack differs from B63 only inside the four 100-byte block spans

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
STOCK = SRC                                      # alias for verifiers
OUT = 'build/B64_slot1.BIN'
B63_PKG = 'build/B63_slot1.BIN'

HOOK, HOOK_LEN = b63.HOOK, b63.HOOK_LEN          # 0x60479A2B / 5
SU_RET = b63.SU_RET                              # 0x60479A32
SU_STUB = b63.SU_STUB                            # 0x605DECD4
SU_STUB_MAX = b63.SU_STUB_MAX                    # 112
OLD_SU_STUB, OLD_SU_LEN = b63.OLD_SU_STUB, b63.OLD_SU_LEN   # 0x605DED80 / 0x4A（保持原厂）
STASH = b63.STASH                                # 0x605DED70
SU_PTRTAB = b63.SU_PTRTAB                        # 0x605DEDD4
BLK_TAB = b63.BLK_TAB                            # 0x605DEDE4
CH_STOCK = b61.CH_STOCK                          # 0x605D4829 库存块来源
CH_BASE, CH_STRIDE = b63.CH_BASE, b63.CH_STRIDE  # 0x605DEB44 / 0x64
CH_ADDRS = list(b63.CH_ADDRS)
EXIF_TEXT = b63.EXIF_TEXT
EXIF_LEAD = b63.EXIF_LEAD
CH_HOOK = b63.CH_HOOK                            # 0x604AA297 必须原厂
POOL, BANK2 = b54.POOL, b54.BANK2
BANNED = b54.BANNED
KNOWN_ZONE = b63.KNOWN_ZONE
LOOKS = b61.LOOKS


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def in_span(a, ln, span):
    return a >= span[0] and a + ln <= span[1]


def overlaps(a, ln, spans):
    return any(a < s[0] + s[1] and s[0] < a + ln for s in spans)


def build():
    info = b54.build()                            # B59 的 SD / MX 桩（顺带算出 B54 自己的 SU 桩，不用）
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

    stub, jmp_off = b63.assemble_stub()           # 与 B63 逐字节相同的扩展 SU 桩
    stock = payload_of(SRC)
    stock_blk = bytes(stock[CH_STOCK - BASE:CH_STOCK - BASE + CH_STRIDE])
    b61.check_block(stock_blk, '库存块')           # x 单调 / z 自洽 / pad=0 由库存本身满足
    blocks = [bytes(stock_blk) for _ in range(4)]
    if len(set(blocks)) != 1:
        raise SystemExit('四张块不是同一份拷贝')
    for i, blk in enumerate(blocks):
        if blk != stock_blk:
            raise SystemExit('块 k=%d 与库存块不一致' % (i + 1))

    blk_tab = struct.pack('<4I', *CH_ADDRS)

    # ---- 落点 ----
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
    if overlaps(SU_STUB, len(stub), [s for s in b59_spans]):
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

    # ---- stock 前提 ----
    def sat(addr, n):
        return bytes(stock[addr - BASE:addr - BASE + n])

    if sat(CH_HOOK, 5) != bytes(stock[CH_HOOK - BASE:CH_HOOK - BASE + 5]):
        raise SystemExit('stock 色度钩区读不到')
    if sat(OLD_SU_STUB, OLD_SU_LEN) != bytes(stock[OLD_SU_STUB - BASE:OLD_SU_STUB - BASE + OLD_SU_LEN]):
        raise SystemExit('stock 旧 SU 桩区读不到')

    ranges = sorted(b59_spans + new_spans)
    lines = ['# B64 build from stock: B63 patch set, 4 chroma blocks = verbatim stock block copy',
             'ram 0x%x=%s  # hook A 0x604B811D -> SD stub (B59)' % (b54.SD_INSERT, (b46.jmp_bytes(b54.SD_INSERT, b54.SD_STUB) + b'\x00\x00').hex()),
             'ram 0x%x=%s  # hook B 0x60479A2B -> extended SU stub 0x%X' % (HOOK, b46.jmp_bytes(HOOK, SU_STUB).hex(), SU_STUB),
             'ram 0x%x=%s  # hook C 0x604B6AD6 -> MX stub (B59)' % (b54.MX_INSERT, b46.jmp_bytes(b54.MX_INSERT, b54.MX_STUB).hex()),
             'ram 0x%x=%s  # SD stub %d B' % (b54.SD_STUB, sd.hex(), len(sd)),
             'ram 0x%x=%s  # extended SU stub %d B (same bytes as B63: tone + EXIF + slot1 copy)' % (SU_STUB, stub.hex(), len(stub)),
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
        lines.append('ram 0x%x=%s  # chroma k=%d verbatim copy of stock block 0x%X'
                     % (CH_ADDRS[i], blk.hex(), i + 1, CH_STOCK))
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/b64.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(sd=sd, mx=mx, stub=stub, jmp_off=jmp_off, tone=tone, mblobs=mblobs,
                blocks=blocks, stock_blk=stock_blk, blk_tab=blk_tab,
                ranges=ranges, new_spans=new_spans, b59_spans=b59_spans, patch=pf)


def main():
    info = build()
    print('B64 = B63 补丁集（扩展 SU 桩 + slot1 写入），四张块 = 库存块拷贝')
    print('  扩展 SU 桩 %d B @0x%X（jmp 回 0x%X @+%d）；块指针表 @0x%X'
          % (len(info['stub']), SU_STUB, SU_RET, info['jmp_off'], BLK_TAB))
    print('  库存块 0x%X (100 B): %s' % (CH_STOCK, info['stock_blk'].hex()))
    print('  四张块 @%s 全部 == 库存块' % ['0x%X' % a for a in CH_ADDRS])

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

    # 与 B63 的差异只许落在四张块内
    b63_img = payload_of(B63_PKG)
    d63 = [i for i in range(len(built)) if built[i] != b63_img[i]]
    ch_spans = [(a - BASE, CH_STRIDE) for a in CH_ADDRS]
    stray = [i for i in d63 if not any(s <= i < s + l for s, l in ch_spans)]
    print('  vs B63 差异 %d 字节；四张块之外 %d 字节 -> %s'
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
    for i, a in enumerate(CH_ADDRS):
        got = at(built, a, CH_STRIDE)
        good = got == info['stock_blk']
        ok &= good
        print('  色度块 k=%d == 库存块逐字节: %s' % (i + 1, good))
    extra = [i for i in diff if any(CH_ADDRS[j] - BASE <= i < CH_ADDRS[j] - BASE + CH_STRIDE for j in range(4))]
    print('  四张块外的改动字节数（应为 B59 补丁集）= %d' % (len(diff) - len(extra)))

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

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'built_from': SRC, 'compare_to': B63_PKG,
           'hook': HOOK, 'su_stub': SU_STUB, 'su_stub_len': len(info['stub']),
           'su_ret': SU_RET, 'su_jmp_off': info['jmp_off'],
           'old_su_stub': OLD_SU_STUB, 'old_su_len': OLD_SU_LEN,
           'su_ptrtab': SU_PTRTAB, 'blk_tab': BLK_TAB,
           'blocks': CH_ADDRS, 'ch_stride': CH_STRIDE, 'ch_stock': CH_STOCK,
           'block_source': 'stock[0x%X:0x%X] 逐字节拷贝' % (CH_STOCK, CH_STOCK + CH_STRIDE),
           'block_bytes': info['stock_blk'].hex(),
           'chroma_hook_stock': CH_HOOK, 'looks': LOOKS,
           'tones': b54.TONE_ADDRS, 'slots': b54.SLOTS,
           'mx_data': b54.MX_DATA, 'stash': STASH,
           'b59_spans': info['b59_spans'], 'new_spans': info['new_spans'],
           'ranges': info['ranges'], 'bank2': BANK2, 'known_zone': KNOWN_ZONE}
    with io.open('.tmp/b64_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
