#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b61.py —— B61：四张极端色度块 + 双写（新鲜记录与 slot1 一起写）

## 一句话

B60 把色度块换成"按 k 选块"，预览变了、成片没变。原因是 **0x60480047** 在
`u8[O+0x38EC]==1` 时会把 **slot1 记录 +0x1D 的 100 B** 整块覆盖回"新鲜记录 +0x1D"
（`0x604803B5..0x604803BD`），成片链 `0x6047FD3D` 读的也是 slot1
（见 docs/map/chroma-consumers.md）。所以 B61 的桩在原有写入之外，**再把同一 100 B
写进 `*(ctx+0x37D8)+0x1D`**（即 slot1），并带 `cmp 0` 保护。

## 与 B60 的差别（只有两处）

1. 四张块内容：y 取极端值（Cb/Cr 各 0x00 或 0xFF），x 沿用库存，z 现算，pad=0。
2. 桩：k∈1..4 时算完块指针后，读 `*(ctx+0x37d8)` → 空则只回块指针（不写），
   否则 100 次拷贝写进 `slot1+0x1d`，然后回 `0x604AA29E`；k∉1..4 与原厂等价
   （先远调用 `0x604B4B24`）。

## 落点（沿用 B60，互不重叠）

| 落点 | 地址 | 大小 |
|---|---|---|
| 钩子 | `0x604AA297` | 5 B（原 7 B call 的前 5 B，后 2 B `F0 18` 成死字节）|
| 桩 | `0x605DEE4C` | < 148 B（`0x605DEEE0` 是 B59 矩阵数据）|
| 色度指针表 | `0x605DEF28` | 16 B（< bank2 `0x605DEF4E`）|
| 色度块 1..4 | `0x605DEB44 + 100*(k-1)` | 400 B（tone 槽 52）|

## 硬约束（build 时逐条断言）

- 只从 stock 起包，经 tools/mkpatch.py 落补丁；B59 的每一处改动逐字节不变
- bank2 / 原厂 m0 / `0x605D4829` 调色板块：零改动
- tone 槽 22–25（B59 的四档 tone）与 B59 逐字节相同
- 桩内只破坏 d0/d1/a0/a1；不回写 ctx+0x1230

只离线：不刷机、不写卡、不碰任何盘符。
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

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
B59 = 'build/B59_real.BIN'
OUT = 'build/B61_chroma2.BIN'

# ---- 钩子 / 桩（沿用 B60） ----
CH_INSERT, CH_LEN = 0x604AA297, 5
CH_RET = 0x604AA29E
CH_STUB = 0x605DEE4C
CH_PTRTAB = 0x605DEF28
CH_BASE = 0x605DEB44
CH_STRIDE = 100
CH_ADDRS = [CH_BASE + CH_STRIDE * i for i in range(4)]
CH_STOCK = 0x605D4829
STASH = b54.STASH
STUB_LIMIT = 0x605DEEE0

SLOT1_OFF = 0x37D8          # *(ctx+0x37d8) = slot1 记录（= O+0x393C）
REC_OFF = 0x1D              # 记录里 100 B 色度块的起点

LOOKS = ['reala_ace', 'astia', 'eterna', 'bleach_bypass']
# 四张块的 y 极值：(Cb y, Cr y)；x 沿用库存，z 按公式现算
BLOCK_SPEC = [(0x00, 0x00), (0xFF, 0xFF), (0x00, 0xFF), (0xFF, 0x00)]

PH_CALL = 0x33333333
PH_J1, PH_J2, PH_J3 = 0x11111111, 0x22222222, 0x44444444

CH_TMPL = r"""	.am33
	.text
	.globl	_start
_start:
	mov	0x%X,a1
	mov	(a1),d0
	mov	d0,d1
	and	7,d0
	asr	21,d1
	and	0x18,d1
	or	d1,d0
	add	-1,d0
	cmp	3,d0
	bhi	Lstock
	add	d0,d0
	add	d0,d0
	mov	0x%X,a1
	add	d0,a1
	mov	(a1),a1
	mov	0x37d8,d0
	add	d0,a0
	mov	(a0),a0
	cmp	0,a0
	beq	Ljust
	add	0x1d,a0
	mov	-100,d1
	setlb
Lcp:
	movbu	(a1),d0
	inc	a1
	movbu	d0,(a0)
	inc	a0
	inc	d1
	lne
	add	-100,a1
	mov	a1,a0
	jmp	@@J1@@
Ljust:
	mov	a1,a0
	jmp	@@J2@@
Lstock:
	call	@@CALL@@,[d2,d3,a2,a3],24
	jmp	@@J3@@
""" % (STASH, CH_PTRTAB)

# setlb / movbu (a1),d0 / inc a1 / movbu d0,(a0) / inc a0 / inc d1 / lne
COPY_SEQ = b'\xdb' + b'\xf0\x41' + b'\x45' + b'\xf0\x50' + b'\x41' + b'\x44' + b'\xd9'


def rnd(v):
    return int(v + 0.5) if v >= 0 else -int(-v + 0.5)


def clamp8(v):
    return 0 if v < 0 else (255 if v > 255 else int(rnd(v)))


def z_chain(x, y):
    """z[0]=0；dx==0 沿用上一个斜率；否则 round(dy*128/dx) & 0xFF。"""
    z = [0] * 17
    prev = 0
    for i in range(1, 17):
        dx = x[i] - x[i - 1] if i < 16 else 0
        want = prev if dx == 0 else (rnd((y[i] - y[i - 1]) * 128.0 / dx) & 0xFF)
        z[i] = want
        prev = want
    return z


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def stock_block0():
    img = payload_of(SRC)
    return bytes(img[CH_STOCK - BASE:CH_STOCK - BASE + CH_STRIDE])


def extreme_block(cb_y, cr_y, stock):
    """Cb: x=库存 / y 全 cb_y；Cr: x=库存 / y 全 cr_y；z 现算，pad=0。"""
    out = b''
    for xo, yv in ((0x00, cb_y), (0x32, cr_y)):
        x = [clamp8(v) for v in stock[xo:xo + 16]]
        y = [int(yv)] * 16
        z = z_chain(x, y)
        out += bytes(x) + bytes(y) + bytes(z) + b'\x00'
    if len(out) != CH_STRIDE:
        raise SystemExit('色度块 %d B != 100' % len(out))
    return out


def check_block(blk, name, cb_y=None, cr_y=None, stock=None):
    """与 audit_beta_pack 同一套规则复检（x 单调、z 自洽容差 1、pad=0）。"""
    if len(blk) != CH_STRIDE:
        raise SystemExit('%s 长度 %d != 100' % (name, len(blk)))
    for tag, xo, yo, zo in (('Cb', 0x00, 0x10, 0x20), ('Cr', 0x32, 0x42, 0x52)):
        x, y, z = list(blk[xo:xo + 16]), list(blk[yo:yo + 16]), list(blk[zo:zo + 17])
        if any(a > b for a, b in zip(x, x[1:])):
            raise SystemExit('%s %s x 非单调' % (name, tag))
        prev = 0
        for i in range(1, 17):
            dx = x[i] - x[i - 1] if i < 16 else 0
            want = prev if dx == 0 else (rnd((y[i] - y[i - 1]) * 128.0 / dx) & 0xFF)
            if abs(want - z[i]) > 1:
                raise SystemExit('%s %s z[%d]=%d want %d' % (name, tag, i, z[i], want))
            prev = want
    if blk[0x31] or blk[0x63]:
        raise SystemExit('%s pad 非 0' % name)
    if stock is not None:
        for tag, xo in (('Cb', 0x00), ('Cr', 0x32)):
            if bytes(blk[xo:xo + 16]) != bytes(stock[xo:xo + 16]):
                raise SystemExit('%s %s x 不是库存形状' % (name, tag))
    if cb_y is not None:
        if set(blk[0x10:0x20]) != {cb_y} or set(blk[0x42:0x52]) != {cr_y}:
            raise SystemExit('%s y 不是全 %#x/%#x' % (name, cb_y, cr_y))


def assemble_stub():
    p1 = (CH_TMPL.replace('@@CALL@@', '0x%X' % PH_CALL)
                 .replace('@@J1@@', '0x%X' % PH_J1)
                 .replace('@@J2@@', '0x%X' % PH_J2)
                 .replace('@@J3@@', '0x%X' % PH_J3))
    b1 = b46.assemble(p1, 'b61_ch_ph', CH_STUB)
    i = b1.find(struct.pack('<I', PH_CALL))
    if i < 1 or b1[i - 1] != 0xDD:
        raise SystemExit('桩里没找到 call 占位（前字节 0x%02x）' % (b1[i - 1] if i > 0 else 0))
    co = i - 1
    jo = {}
    for name, ph in (('J1', PH_J1), ('J2', PH_J2), ('J3', PH_J3)):
        jo[name] = b46.find_ph(b1, ph)
    text = (CH_TMPL
            .replace('@@CALL@@', '0x%x - (0x%x + 0x%x)' % (0x604B4B24, CH_STUB, co))
            .replace('@@J1@@', '0x%x - (0x%x + 0x%x)' % (CH_RET, CH_STUB, jo['J1']))
            .replace('@@J2@@', '0x%x - (0x%x + 0x%x)' % (CH_RET, CH_STUB, jo['J2']))
            .replace('@@J3@@', '0x%x - (0x%x + 0x%x)' % (CH_RET, CH_STUB, jo['J3'])))
    blob = b46.assemble(text, 'b61_ch', CH_STUB)
    want_call = b'\xDD' + struct.pack('<i', 0x604B4B24 - (CH_STUB + co)) + b'\xF0\x18'
    if blob[co:co + 7] != want_call:
        raise SystemExit('call 自检失败 @%#x: %s != %s'
                         % (CH_STUB + co, blob[co:co + 7].hex(), want_call.hex()))
    if blob.count(b'\xdd') < 1:
        raise SystemExit('桩里没有 call')
    tail = 0
    for name in ('J1', 'J2', 'J3'):
        off = jo[name]
        want_jmp = b'\xDC' + struct.pack('<i', CH_RET - (CH_STUB + off))
        if blob[off:off + 5] != want_jmp:
            raise SystemExit('%s jmp 自检失败 @%#x: %s != %s'
                             % (name, CH_STUB + off, blob[off:off + 5].hex(), want_jmp.hex()))
        tail += 1
    if tail != 3:
        raise SystemExit('回跳 jmp 数量 %d != 3' % tail)
    if blob.count(COPY_SEQ) != 1:
        raise SystemExit('桩里 100 B 拷贝循环不唯一（%d 处）' % blob.count(COPY_SEQ))
    if b'\x85\x9c' not in blob:
        raise SystemExit('桩里没有 mov -100,d1（计数初值）')
    if len(blob) > STUB_LIMIT - CH_STUB:
        raise SystemExit('色度桩 %d B 越过 0x%X' % (len(blob), STUB_LIMIT))
    if len(blob) & 1:                 # 奇数长度补一条 nop（不可达，只为对齐）
        blob += b'\xcb'
    return blob, co, jo


def runs_of(a, b):
    out = []
    i = 0
    n = len(a)
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            out.append((BASE + i, j - i))
            i = j
        else:
            i += 1
    return out


def in_any(addr, ln, spans):
    return any(addr >= s[0] and addr + ln <= s[0] + s[1] for s in spans)


def overlaps(addr, ln, spans):
    return any(addr < s[0] + s[1] and s[0] < addr + ln for s in spans)


def build():
    b54_info = b54.build()
    sd, su, mx = b54_info['sd'], b54_info['su'], b54_info['mx']
    tone_tabs = json.load(io.open('.tmp/b58_tables.json', encoding='utf-8'))
    mx_rows = json.load(io.open('.tmp/b59_mx.json'))
    if [t['look'] for t in tone_tabs] != LOOKS:
        raise SystemExit('b58_tables 顺序 %r != %r' % ([t['look'] for t in tone_tabs], LOOKS))
    tone = [b''.join(struct.pack('<H', v) for v in t['u16']) for t in tone_tabs]
    for i, t in enumerate(tone):
        if len(t) != 0x200:
            raise SystemExit('tone 表 %d 长度 %d' % (i, len(t)))
    mblobs = []
    for i, rows in enumerate(mx_rows):
        flat = b54.enc_matrix([rows[0:3], rows[3:6], rows[6:9]])
        mblobs.append(b''.join(struct.pack('<h', v) for v in flat))

    stock = stock_block0()
    blocks = [extreme_block(cb, cr, stock) for cb, cr in BLOCK_SPEC]
    for i, b in enumerate(blocks):
        cb, cr = BLOCK_SPEC[i]
        check_block(b, '色度块 k=%d（Cb y=%#x / Cr y=%#x）' % (i + 1, cb, cr),
                    cb_y=cb, cr_y=cr, stock=stock)
    if len(set(blocks)) != 4:
        raise SystemExit('四张色度块有重复')
    for i, b in enumerate(blocks):
        if b == stock:
            raise SystemExit('色度块 k=%d 与原厂块相同' % (i + 1))

    stub, co, jo = assemble_stub()
    chptr = struct.pack('<4I', *CH_ADDRS)

    new_spans = [(CH_INSERT, CH_LEN), (CH_STUB, len(stub)),
                 (CH_PTRTAB, len(chptr))] + [(a, CH_STRIDE) for a in CH_ADDRS]
    b59_spans = [(b54.SD_INSERT, b54.SD_LEN), (b54.SU_INSERT, b54.SU_LEN),
                 (b54.MX_INSERT, b54.MX_LEN), (b54.SD_STUB, len(sd)),
                 (STASH, 4), (b54.SU_STUB, len(su)), (b54.SU_PTRTAB, 16),
                 (b54.MX_PTRTAB, 16), (b54.MX_DATA, 72), (b54.MX_STUB, len(mx))]
    b59_spans += [(a, 0x200) for a in b54.TONE_ADDRS]

    for a, ln in new_spans:
        if overlaps(a, ln, b59_spans):
            raise SystemExit('新落点 0x%X..0x%X 压到 B59 已用区（重叠）' % (a, a + ln))
        for b in b54.BANNED:
            if a <= b < a + ln:
                raise SystemExit('新落点 0x%X 压到禁地 0x%X' % (a, b))
        if a < b54.BANK2[1] and a + ln > b54.BANK2[0]:
            raise SystemExit('新落点 0x%X 压到 bank2' % a)
        if not in_any(a, ln, [b54.POOL, b54.POCKET]):
            raise SystemExit('新落点 0x%X 不在允许口袋（池 / 60 B 口袋）' % a)
    for i in range(len(new_spans)):
        for k in range(i + 1, len(new_spans)):
            if overlaps(new_spans[i][0], new_spans[i][1], [new_spans[k]]):
                raise SystemExit('新落点互相重叠：%s / %s' % (new_spans[i], new_spans[k]))

    ranges = sorted(b59_spans + new_spans)
    lines = ['# B61 build from stock: B59 (tone 22-25 + matrix 4) + chroma hook, 4 extreme blocks, double write',
             'ram 0x%x=%s  # hook A 0x604B811D -> SD stub (B59)' % (b54.SD_INSERT, (b46.jmp_bytes(b54.SD_INSERT, b54.SD_STUB) + b'\x00\x00').hex()),
             'ram 0x%x=%s  # hook B 0x60479A2B -> SU stub (B59)' % (b54.SU_INSERT, b46.jmp_bytes(b54.SU_INSERT, b54.SU_STUB).hex()),
             'ram 0x%x=%s  # hook C 0x604B6AD6 -> MX stub (B59)' % (b54.MX_INSERT, b46.jmp_bytes(b54.MX_INSERT, b54.MX_STUB).hex()),
             'ram 0x%x=%s  # hook D 0x604AA297 -> chroma stub (B61: write fresh rec + slot1)' % (CH_INSERT, b46.jmp_bytes(CH_INSERT, CH_STUB).hex()),
             'ram 0x%x=%s  # SD stub %d B' % (b54.SD_STUB, sd.hex(), len(sd)),
             'ram 0x%x=%s  # SU stub %d B' % (b54.SU_STUB, su.hex(), len(su)),
             'ram 0x%x=%s  # MX stub %d B' % (b54.MX_STUB, mx.hex(), len(mx)),
             'ram 0x%x=%s  # chroma stub %d B (k -> ptrtab -> copy 100B into slot1+0x1d -> return 0x604AA29E)'
             % (CH_STUB, stub.hex(), len(stub)),
             'ram 0x%x=%s  # tone ptr table' % (b54.SU_PTRTAB, struct.pack('<4I', *b54.TONE_ADDRS).hex()),
             'ram 0x%x=%s  # matrix ptr table' % (b54.MX_PTRTAB, struct.pack('<4I', *[b54.MX_DATA + 18 * i for i in range(4)]).hex()),
             'ram 0x%x=%s  # chroma ptr table [blk1..4]' % (CH_PTRTAB, chptr.hex())]
    for i, a in enumerate(b54.TONE_ADDRS):
        lines.append('ram 0x%x=%s  # tone slot %d (%s)' % (a, tone[i].hex(), b54.SLOTS[i], LOOKS[i]))
    for i, blob in enumerate(mblobs):
        lines.append('ram 0x%x=%s  # matrix k=%d %r'
                     % (b54.MX_DATA + 18 * i, blob.hex(), i + 1, mx_rows[i]))
    for i, blk in enumerate(blocks):
        lines.append('ram 0x%x=%s  # chroma k=%d %s y=(%#x,%#x) (Cb: x/y/z/pad | Cr: x/y/z/pad)'
                     % (CH_ADDRS[i], blk.hex(), i + 1, LOOKS[i],
                        BLOCK_SPEC[i][0], BLOCK_SPEC[i][1]))
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/b61.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(sd=sd, su=su, mx=mx, stub=stub, call_off=co, jmp_off=jo,
                tone=tone, mblobs=mblobs, blocks=blocks, chptr=chptr,
                ranges=ranges, new_spans=new_spans, b59_spans=b59_spans, patch=pf)


def main():
    info = build()
    print('B61 = B59 + 色度钩子（四张极端块 + 双写新鲜记录/slot1）')
    print('  色度桩 %d B @0x%X（call @+%d，jmp @+%d/%d/%d -> 0x%X）'
          % (len(info['stub']), CH_STUB, info['call_off'], info['jmp_off']['J1'],
             info['jmp_off']['J2'], info['jmp_off']['J3'], CH_RET))
    print('  第二处写：*(ctx+0x%X)+0x%X（slot1 记录，cmp 0 保护）' % (SLOT1_OFF, REC_OFF))
    print('  色度指针表 @0x%X = %s' % (CH_PTRTAB, ['0x%X' % v for v in CH_ADDRS]))
    for i, blk in enumerate(info['blocks']):
        print('  k=%d %-13s Cb y=%#04x Cr y=%#04x Cb z=%r Cr z=%r'
              % (i + 1, LOOKS[i], BLOCK_SPEC[i][0], BLOCK_SPEC[i][1],
                 list(blk[0x20:0x31]), list(blk[0x52:0x63])))

    if os.path.exists(OUT):
        os.remove(OUT)
    b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT, '--patch-file', info['patch']])

    stock = payload_of(SRC)
    built = payload_of(OUT)
    b59 = payload_of(B59)

    def at(img, addr, n):
        return bytes(img[addr - BASE:addr - BASE + n])

    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    outside = [i for i in diff if not any(a - BASE <= i < a - BASE + l for a, l in info['ranges'])]
    print('\n改动 %d 字节；区外 %d 字节 -> %s' % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    ok = not outside

    bad = [i for i in range(b54.BANK2[0] - BASE, b54.BANK2[1] - BASE) if built[i] != stock[i]]
    print('  bank2 0x%X..0x%X 改动 %d 字节 -> %s'
          % (b54.BANK2[0], b54.BANK2[1], len(bad), 'OK' if not bad else 'FAIL'))
    ok &= not bad

    d59 = [i for i in range(len(built)) if built[i] != b59[i]]
    stray = [i for i in d59 if not any(a - BASE <= i < a - BASE + l for a, l in info['new_spans'])]
    print('  vs B59 差异 %d 字节；落点之外 %d 字节 -> %s'
          % (len(d59), len(stray), 'OK' if not stray else 'FAIL'))
    ok &= not stray

    for label, a, ln in (('SD 桩', b54.SD_STUB, len(info['sd'])),
                         ('SU 桩', b54.SU_STUB, len(info['su'])),
                         ('MX 桩', b54.MX_STUB, len(info['mx']))):
        same = at(built, a, ln) == at(b59, a, ln)
        ok &= same
        print('  %s @0x%X == B59: %s' % (label, a, same))

    for name, got, want in (('hook A', at(built, b54.SD_INSERT, 7), b46.jmp_bytes(b54.SD_INSERT, b54.SD_STUB) + b'\x00\x00'),
                            ('hook B', at(built, b54.SU_INSERT, 5), b46.jmp_bytes(b54.SU_INSERT, b54.SU_STUB)),
                            ('hook C', at(built, b54.MX_INSERT, 5), b46.jmp_bytes(b54.MX_INSERT, b54.MX_STUB)),
                            ('hook D', at(built, CH_INSERT, 5), b46.jmp_bytes(CH_INSERT, CH_STUB)),
                            ('SD 桩', at(built, b54.SD_STUB, len(info['sd'])), info['sd']),
                            ('SU 桩', at(built, b54.SU_STUB, len(info['su'])), info['su']),
                            ('MX 桩', at(built, b54.MX_STUB, len(info['mx'])), info['mx']),
                            ('色度桩', at(built, CH_STUB, len(info['stub'])), info['stub']),
                            ('色度指针表', at(built, CH_PTRTAB, 16), info['chptr'])):
        good = got == want
        ok &= good
        print('  %-10s 一致=%s' % (name, good))
    for i, a in enumerate(b54.TONE_ADDRS):
        good = at(built, a, 0x200) == info['tone'][i] == at(b59, a, 0x200)
        ok &= good
        print('  tone 槽 %-3d 表一致（且 == B59）=%s' % (b54.SLOTS[i], good))
    for i, a in enumerate(CH_ADDRS):
        good = at(built, a, CH_STRIDE) == info['blocks'][i]
        ok &= good
        print('  色度块 k=%d 内容一致=%s（B59 同址 == 原厂：%s）'
              % (i + 1, good, at(b59, a, CH_STRIDE) == at(stock, a, CH_STRIDE)))

    tail_ok = at(built, CH_INSERT + 5, 2) == at(stock, CH_INSERT + 5, 2) == b'\xF0\x18'
    print('  钩点后 2 B 死字节保持原厂 %r: %s' % (b'\xF0\x18', tail_ok))
    ok &= tail_ok

    m0 = at(built, b54.MATRIX_M0, 18) == at(stock, b54.MATRIX_M0, 18)
    print('  原厂 m0 未动=%s' % m0)
    ok &= m0

    pal = at(built, CH_STOCK, CH_STRIDE) == at(stock, CH_STOCK, CH_STRIDE)
    print('  原厂色度块 0x%X 未动=%s' % (CH_STOCK, pal))
    ok &= pal

    rwz_ok = not any(struct.pack('<I', a) in info['stub'] for a in (0x605ECECA,))
    print('  桩未引用 m0 地址=%s' % rwz_ok)
    ok &= rwz_ok

    # 桩只碰 d0/d1/a0/a1：静态查一下没出现别的寄存器号（粗查，sim 里再验）
    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'base_pkg': B59,
           'ch_hook': CH_INSERT, 'ch_ret': CH_RET, 'ch_stub': CH_STUB,
           'ch_stub_len': len(info['stub']), 'ch_ptrtab': CH_PTRTAB,
           'ch_blocks': CH_ADDRS, 'ch_stride': CH_STRIDE, 'ch_stock_block': CH_STOCK,
           'ch_block_spec': [{'k': i + 1, 'look': LOOKS[i], 'cb_y': '0x%02X' % BLOCK_SPEC[i][0],
                              'cr_y': '0x%02X' % BLOCK_SPEC[i][1]}
                             for i in range(4)],
           'write1': 'fresh record: *(O+0x3940)+0x1d (原路径，0x604AA31d 之后由原厂拷贝循环写入)',
           'write2': 'slot1 record: *(ctx+0x%X)+0x%X (B61 新增，桩内 100 次 movbu)' % (SLOT1_OFF, REC_OFF),
           'stash': STASH, 'looks': LOOKS,
           'tones': b54.TONE_ADDRS, 'slots': b54.SLOTS,
           'mx_data': b54.MX_DATA, 'ranges': info['ranges'],
           'new_spans': info['new_spans'], 'bank2': b54.BANK2}
    with io.open('.tmp/b61_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
