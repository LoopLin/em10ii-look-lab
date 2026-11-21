#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b60.py —— B60：四个标准档各用自己的色度块（B59 + 第三个钩子）

## 一句话

B59 已经让四个标准档各拿到自己的 tone 表与 3×3 矩阵；B60 再补上最后一块：
**色度块**。`0x604B4B24`（色度指针函数）全固件只有一个调用点 `0x604AA297`，
在那里插 5 B 远跳进桩，桩先远调用原厂函数拿原返回值（a0），再看 stash 里的
完整 key 算 k：k∈1..4 → a0 = 色度指针表[k-1]（四张 100 B 色度块）；否则原样返回。

## 为什么要先调原厂

`0x604B4B24` 有三种返回路径（ctx+0x3c0 非空 / 标准档模式路径 / 自建表），
原返回值必须保住，否则 i-Enhance、ART、越界 key 全部走错。所以桩**只做替换**：
先 `call 0x604b4b24`（同样的 reglist/frame，参数寄存器原样），拿到 a0 后按 k 覆盖。

## 落点（全部在允许口袋里，互不重叠）

| 落点 | 地址 | 大小 | 说明 |
|---|---|---|---|
| 钩子 | `0x604AA297` | 5 B | 原 7 B `call` 的前 5 B 换 `DC disp32`；后 2 B `F0 18` 是 call 的 reglist/frame 后缀，成了死字节 |
| 桩 | `0x605DEE4C` | 49 B | 尾回 `0x604AA29E`（原 call 的下一条真实指令） |
| 色度指针表 | `0x605DEF28` | 16 B | 4×u32，按 k-1 索引 |
| 色度块 1..4 | `0x605DEB44 + 100*(k-1)` | 400 B | tone 槽 52 的前 400 B（代价见文档）|

## 硬约束（build 时逐条断言）

- 只从 `stock/OLY_E_120_1400_0000_0000.BIN` 起包，经 `tools/mkpatch.py` 落补丁
- B59 的每一处改动逐字节不变（B60 与 B59 的差异只允许落在上面四处新落点）
- bank2（`0x605DEF4E` 起 2 KB）零改动；不碰 `0x60DA4E08` / `0x60DE0000`
- 不写原厂 m0 `0x605ECECA`，不写 `ctx+0x1230`（桩只读 stash 与指针表）
- 桩内只破坏 d0/d1/a0/a1 —— 回跳点之后两条分支都会重写它们（见文档 2.4）

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
OUT = 'build/B60_chroma.BIN'

# ---- 新钩子 ----
CH_INSERT, CH_LEN = 0x604AA297, 5               # 原 7 B call 的前 5 B
CH_RET = 0x604AA29E                             # call 的下一条真实指令（cmp 0x0,a0）
CH_STUB = 0x605DEE4C                            # B59 未占（0x605DEE4C..0x605DEEE0 全空）
CH_PTRTAB = 0x605DEF28                          # 4×u32（< bank2 0x605DEF4E）
CH_BASE = 0x605DEB44                            # tone 槽 52 起点
CH_STRIDE = 100
CH_ADDRS = [CH_BASE + CH_STRIDE * i for i in range(4)]
CH_STOCK = 0x605D4829                           # 原厂标准档色度块（0x604B4B24 模式路径返回值）
STASH = b54.STASH                               # 0x605DED70（B59 写好的 32 位 key）

STUB_LIMIT = 0x605DEEE0                         # 再往后是 B59 的矩阵数据
LOOKS = ['reala_ace', 'astia', 'eterna', 'bleach_bypass']   # 与 .tmp/b58_tables.json 同序

PH_CALL, PH_JMP = 0x33333333, 0x11111111

# 桩：先远调用原厂（保留 a0）→ 读 stash 算 k → k∈1..4 用指针表 → 尾回 0x604AA29E
CH_TMPL = r"""	.am33
	.text
	.globl	_start
_start:
	call	@@CALL@@,[d2,d3,a2,a3],24
	mov	0x%X,a1
	mov	(a1),d0
	mov	d0,d1
	and	7,d0
	asr	21,d1
	and	0x18,d1
	or	d1,d0
	add	-1,d0
	cmp	3,d0
	bhi	Lkeep
	add	d0,d0
	add	d0,d0
	mov	0x%X,a1
	add	d0,a1
	mov	(a1),a0
Lkeep:
	jmp	@@JRET@@
""" % (STASH, CH_PTRTAB)


def rnd(v):
    """四舍五入（.5 远离 0），与 audit_beta_pack 的 round() 在容差 1 内一致。"""
    return int(v + 0.5) if v >= 0 else -int(-v + 0.5)


def clamp8(v):
    return 0 if v < 0 else (255 if v > 255 else int(rnd(v)))


def z_chain(x, y):
    """z[0]=0；dx==0 沿用上一个斜率；否则 round(dy*128/dx) & 0xFF（同 audit_beta_pack）。"""
    z = [0] * 17
    prev = 0
    for i in range(1, 17):
        dx = x[i] - x[i - 1] if i < 16 else 0
        want = prev if dx == 0 else (rnd((y[i] - y[i - 1]) * 128.0 / dx) & 0xFF)
        z[i] = want
        prev = want
    return z


def stock_block0():
    img = payload_of(SRC)
    return bytes(img[CH_STOCK - BASE:CH_STOCK - BASE + CH_STRIDE])


def chroma_block(look, stock):
    """40 B 表 + z 现算 → 100 B。x 沿用库存（并断言与库存逐字节相同）。"""
    with io.open('.tmp/looks_beta/%s.json' % look, encoding='utf-8') as f:
        spec = json.load(f)
    ch = spec['chroma']
    out = b''
    for tag, xo, xk, yk in (('Cb', 0x00, 'cb_x', 'cb_y'), ('Cr', 0x32, 'cr_x', 'cr_y')):
        x = [clamp8(v) for v in ch[xk]]
        y = [clamp8(v) for v in ch[yk]]
        if len(x) != 16 or len(y) != 16:
            raise SystemExit('%s %s/%s 长度不是 16' % (look, xk, yk))
        if any(a > b for a, b in zip(x, x[1:])):
            raise SystemExit('%s %s x 非单调' % (look, tag))
        want_x = list(stock[xo:xo + 16])
        if x != want_x:
            raise SystemExit('%s %s x=%r != 库存 %r' % (look, tag, x, want_x))
        z = z_chain(x, y)
        out += bytes(x) + bytes(y) + bytes(z) + b'\x00'
    if len(out) != CH_STRIDE:
        raise SystemExit('%s 色度块 %d B != 100' % (look, len(out)))
    if out[0x31] or out[0x63]:
        raise SystemExit('%s pad 非 0' % look)
    return out


def check_block(blk, name):
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


def assemble_stub():
    ph = (CH_TMPL.replace('@@CALL@@', '0x%X' % PH_CALL)
                 .replace('@@JRET@@', '0x%X' % PH_JMP))
    b1 = b46.assemble(ph, 'b60_ch_ph', CH_STUB)
    i = b1.find(struct.pack('<I', PH_CALL))
    if i < 1 or b1[i - 1] != 0xDD:
        raise SystemExit('桩里没找到 call 占位（前字节 0x%02x）' % (b1[i - 1] if i > 0 else 0))
    co = i - 1
    j = b46.find_ph(b1, PH_JMP)
    text = (CH_TMPL
            .replace('@@CALL@@', '0x%x - (0x%x + 0x%x)' % (0x604B4B24, CH_STUB, co))
            .replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (CH_RET, CH_STUB, j)))
    blob = b46.assemble(text, 'b60_ch', CH_STUB)
    want_call = b'\xDD' + struct.pack('<i', 0x604B4B24 - (CH_STUB + co)) + b'\xF0\x18'
    if blob[co:co + 7] != want_call:
        raise SystemExit('call 自检失败 @%#x: %s != %s'
                         % (CH_STUB + co, blob[co:co + 7].hex(), want_call.hex()))
    want_jmp = b'\xDC' + struct.pack('<i', CH_RET - (CH_STUB + j))
    if blob[j:j + 5] != want_jmp:
        raise SystemExit('jmp 自检失败 @%#x' % (CH_STUB + j))
    if len(blob) > STUB_LIMIT - CH_STUB:
        raise SystemExit('色度桩 %d B 越过 0x%X' % (len(blob), STUB_LIMIT))
    return blob, co, j


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def runs_of(a, b):
    """逐字节差异 → [(addr, len)] 连续段。"""
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
    b54_info = b54.build()          # B59 的三个桩/两张表一律现汇编现生成（不从旧 BIN 抠）
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
    blocks = [chroma_block(l, stock) for l in LOOKS]
    for i, b in enumerate(blocks):
        check_block(b, '色度块 k=%d (%s)' % (i + 1, LOOKS[i]))
    if len(set(blocks)) != 4:
        raise SystemExit('四张色度块有重复')

    stub, co, j = assemble_stub()
    chptr = struct.pack('<4I', *CH_ADDRS)

    new_spans = [(CH_INSERT, CH_LEN), (CH_STUB, len(stub)),
                 (CH_PTRTAB, len(chptr))] + [(a, CH_STRIDE) for a in CH_ADDRS]
    b59_spans = [(b54.SD_INSERT, b54.SD_LEN), (b54.SU_INSERT, b54.SU_LEN),
                 (b54.MX_INSERT, b54.MX_LEN), (b54.SD_STUB, len(sd)),
                 (STASH, 4), (b54.SU_STUB, len(su)), (b54.SU_PTRTAB, 16),
                 (b54.MX_PTRTAB, 16), (b54.MX_DATA, 72), (b54.MX_STUB, len(mx))]
    b59_spans += [(a, 0x200) for a in b54.TONE_ADDRS]

    # 1) 新落点不许压 B59 的任何改动，也不许压禁地/bank2，且必须落在允许口袋
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
    lines = ['# B60 build from stock: B59 (tone 22-25 + matrix 4) + chroma hook (per-mode 100 B blocks)',
             'ram 0x%x=%s  # hook A 0x604B811D -> SD stub (B59)' % (b54.SD_INSERT, (b46.jmp_bytes(b54.SD_INSERT, b54.SD_STUB) + b'\x00\x00').hex()),
             'ram 0x%x=%s  # hook B 0x60479A2B -> SU stub (B59)' % (b54.SU_INSERT, b46.jmp_bytes(b54.SU_INSERT, b54.SU_STUB).hex()),
             'ram 0x%x=%s  # hook C 0x604B6AD6 -> MX stub (B59)' % (b54.MX_INSERT, b46.jmp_bytes(b54.MX_INSERT, b54.MX_STUB).hex()),
             'ram 0x%x=%s  # hook D 0x604AA297 -> chroma stub (B60 new)' % (CH_INSERT, b46.jmp_bytes(CH_INSERT, CH_STUB).hex()),
             'ram 0x%x=%s  # SD stub %d B' % (b54.SD_STUB, sd.hex(), len(sd)),
             'ram 0x%x=%s  # SU stub %d B' % (b54.SU_STUB, su.hex(), len(su)),
             'ram 0x%x=%s  # MX stub %d B' % (b54.MX_STUB, mx.hex(), len(mx)),
             'ram 0x%x=%s  # chroma stub %d B (call stock 0x604B4B24 -> k -> ptrtab -> return 0x604AA29E)'
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
        lines.append('ram 0x%x=%s  # chroma k=%d %s (Cb: x/y/z/pad | Cr: x/y/z/pad)'
                     % (CH_ADDRS[i], blk.hex(), i + 1, LOOKS[i]))
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/b60.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(sd=sd, su=su, mx=mx, stub=stub, call_off=co, jmp_off=j,
                tone=tone, mblobs=mblobs, blocks=blocks, chptr=chptr,
                ranges=ranges, new_spans=new_spans, b59_spans=b59_spans, patch=pf)


def main():
    info = build()
    print('B60 = B59 + 色度钩子')
    print('  色度桩 %d B @0x%X（call @+%d, jmp @+%d -> 0x%X）'
          % (len(info['stub']), CH_STUB, info['call_off'], info['jmp_off'], CH_RET))
    print('  色度指针表 @0x%X = %s' % (CH_PTRTAB, ['0x%X' % v for v in CH_ADDRS]))
    for i, blk in enumerate(info['blocks']):
        cb = blk[0x10:0x20]
        cr = blk[0x42:0x52]
        print('  k=%d %-13s Cb y=%r Cr y=%r' % (i + 1, LOOKS[i], list(cb), list(cr)))

    if os.path.exists(OUT):
        os.remove(OUT)
    b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT, '--patch-file', info['patch']])

    stock = payload_of(SRC)
    built = payload_of(OUT)
    b59 = payload_of(B59)
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    outside = [i for i in diff if not any(a - BASE <= i < a - BASE + l for a, l in info['ranges'])]
    print('\n改动 %d 字节；区外 %d 字节 -> %s' % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    ok = not outside

    bad = [i for i in range(b54.BANK2[0] - BASE, b54.BANK2[1] - BASE) if built[i] != stock[i]]
    print('  bank2 0x%X..0x%X 改动 %d 字节 -> %s'
          % (b54.BANK2[0], b54.BANK2[1], len(bad), 'OK' if not bad else 'FAIL'))
    ok &= not bad

    # B60 与 B59 的差异只允许落在新落点（= B59 的每一处改动原样保留）
    d59 = [i for i in range(len(built)) if built[i] != b59[i]]
    stray = [i for i in d59 if not any(a - BASE <= i < a - BASE + l for a, l in info['new_spans'])]
    print('  vs B59 差异 %d 字节；落点之外 %d 字节 -> %s'
          % (len(d59), len(stray), 'OK' if not stray else 'FAIL'))
    ok &= not stray

    # B59 的落点在 B60 里必须与 B59 逐字节一致（B59 的东西一个字节都没动）
    for label, a, ln in (('SD 桩', b54.SD_STUB, len(info['sd'])),
                         ('SU 桩', b54.SU_STUB, len(info['su'])),
                         ('MX 桩', b54.MX_STUB, len(info['mx']))):
        same = bytes(built[a - BASE:a - BASE + ln]) == bytes(b59[a - BASE:a - BASE + ln])
        ok &= same
        print('  %s @0x%X == B59: %s' % (label, a, same))

    def at(addr, n, img):
        return bytes(img[addr - BASE:addr - BASE + n])

    for name, got, want in (('hook A', at(b54.SD_INSERT, 7, built), b46.jmp_bytes(b54.SD_INSERT, b54.SD_STUB) + b'\x00\x00'),
                            ('hook B', at(b54.SU_INSERT, 5, built), b46.jmp_bytes(b54.SU_INSERT, b54.SU_STUB)),
                            ('hook C', at(b54.MX_INSERT, 5, built), b46.jmp_bytes(b54.MX_INSERT, b54.MX_STUB)),
                            ('hook D', at(CH_INSERT, 5, built), b46.jmp_bytes(CH_INSERT, CH_STUB)),
                            ('SD 桩', at(b54.SD_STUB, len(info['sd']), built), info['sd']),
                            ('SU 桩', at(b54.SU_STUB, len(info['su']), built), info['su']),
                            ('MX 桩', at(b54.MX_STUB, len(info['mx']), built), info['mx']),
                            ('色度桩', at(CH_STUB, len(info['stub']), built), info['stub']),
                            ('色度指针表', at(CH_PTRTAB, 16, built), info['chptr'])):
        good = got == want
        ok &= good
        print('  %-10s 一致=%s' % (name, good))
    for i, a in enumerate(b54.TONE_ADDRS):
        good = at(a, 0x200, built) == info['tone'][i]
        ok &= good
        print('  tone 槽 %-3d 表一致=%s' % (b54.SLOTS[i], good))
    for i, a in enumerate(CH_ADDRS):
        good = at(a, CH_STRIDE, built) == info['blocks'][i]
        ok &= good
        print('  色度块 k=%d 内容一致=%s（也 == B59 同址原厂字节：%s）'
              % (i + 1, good, at(a, CH_STRIDE, b59) == at(a, CH_STRIDE, stock)))
    # 色度钩子改的 5 B 之后，原 call 的 reglist/frame 后缀（死字节）保持原厂
    tail_ok = at(CH_INSERT + 5, 2, built) == at(CH_INSERT + 5, 2, stock) == b'\xF0\x18'
    print('  钩点后 2 B 死字节保持原厂 %r: %s' % (b'\xF0\x18', tail_ok))
    ok &= tail_ok

    m0 = at(b54.MATRIX_M0, 18, built) == at(b54.MATRIX_M0, 18, stock)
    print('  原厂 m0 未动=%s' % m0)
    ok &= m0

    # 桩里不许出现对 ctx+0x1230 / m0 的写：只做静态粗查（sim 里再严格验一次）
    stub_bytes = info['stub']
    rwz_ok = not any(struct.pack('<I', a) in stub_bytes for a in (0x605ECECA,))
    print('  桩未引用 m0 地址=%s' % rwz_ok)
    ok &= rwz_ok

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'base_pkg': B59,
           'ch_hook': CH_INSERT, 'ch_ret': CH_RET, 'ch_stub': CH_STUB,
           'ch_stub_len': len(info['stub']), 'ch_ptrtab': CH_PTRTAB,
           'ch_blocks': CH_ADDRS, 'ch_stride': CH_STRIDE, 'ch_stock_block': CH_STOCK,
           'stash': STASH, 'looks': LOOKS,
           'tones': b54.TONE_ADDRS, 'slots': b54.SLOTS,
           'mx_data': b54.MX_DATA, 'ranges': info['ranges'],
           'new_spans': info['new_spans'], 'bank2': b54.BANK2}
    with io.open('.tmp/b60_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
