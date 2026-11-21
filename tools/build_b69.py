#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b67.py — B69: B66 patch set + 成片色度选择器钩子（每档独立成片色度）

一句话：B63/B65/B66 把每档色度块写进 slot1（ctx+0x37D8），但成片色度根本不走
slot1 —— 它走色度选择器 0x604B4B24 的「路径 2」：0x604B4B38 判
`movbu (1071,a3),d0; cmp 2`，再判 `u16(12,a3) ∈ {0xA400,0xA800,0xA900}`；命中后
0x604B4CAD 固定 `mov 0x605D4829,a0`（库存块）并 0x604B4CFA `ret`。B69 把
0x604B4CAD 那条 6 B 指令换成 5 B 远跳 + 1 B nop，跳到新桩按 stash 的 k 取
BLK_TAB[k-1]；同时把 SU 桩里的 100 B slot1 拷贝与空槽判断整段删掉。

## 与 B66 的差别（只有两处）

1. 新钩子：0x604B4CAD 原 `FC DC 29 48 5D 60`（`mov 0x605D4829,a0`，6 B）
   → `DC <disp32>`（jmp，5 B）+ `CB`（nop，1 B）→ 新桩。
2. SU 桩（0x605DECD4）缩短为 69 B：只保留「读 stash → 算 k → k∈1..4 写 EXIF +
   选 tone 指针单元格」，**不写 slot1**（B66 的 100 B 拷贝、空槽判断、键变化
   守卫全部删除）。让出的尾部放选择器桩（38 B，4 对齐）。
   两桩共 108 B，都在 0x605DECD4..0x605DED44（112 B）内；0x605DED44 起是
   tone 槽 53，一字节不碰。

## 硬约束（build 时逐条断言）

- 只从 stock 起包；四张极端色度块与 B63 逐字节相同；块指针表 0x605DEDE4 不变
- tone 槽 22–25、四套矩阵、SD 桩、MX 桩、m0、bank2：与 B66 逐字节相同
- 0x605D4829 起的库存调色板块、0x604AA297 的 getter 钩、0x604B4B24 本体：原厂
- 与 B66 的差异只允许落在 SU 桩窗口（0x605DECD4..0x605DED44）与 6 B 新钩
- SU 桩/选择器桩内不得出现 slot1 写入代码（无 add 0x25a8、无 100 B 拷贝循环）

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
import build_b61 as b61                         # noqa: E402
import build_b63 as b63                         # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/B69_slot1.BIN'
B63_PKG = 'build/B63_slot1.BIN'
B66_PKG = 'build/B66_slot1.BIN'

# ---- 钩点 / 桩 ----
HOOK, HOOK_LEN = b63.HOOK, b63.HOOK_LEN          # SU 钩 0x60479A2B（5 B）
SU_RET = b63.SU_RET                              # 0x60479A32
SU_STUB = b63.SU_STUB                            # 0x605DECD4
SU_STUB_MAX = b63.SU_STUB_MAX                    # 112（到 0x605DED44）
SU_SLOT_END = 0x605DED44                         # tone 槽 53 起点，不可碰
OLD_SU_STUB, OLD_SU_LEN = b63.OLD_SU_STUB, b63.OLD_SU_LEN

SEL_INSERT, SEL_LEN = 0x604B4CAD, 6              # mov 0x605D4829,a0（6 B）
SEL_STOCK = bytes.fromhex('fcdc00485d60')
SEL_RET = 0x604B4CFA                             # 原厂 ret [d2,d3,a2,a3],0x18
NOP = 0xCB                                       # mn10300 nop

# ---- 落点 ----
STASH = b63.STASH                                # 0x605DED70
SU_PTRTAB = b63.SU_PTRTAB                        # 0x605DEDD4
BLK_TAB = b63.BLK_TAB                            # 0x605DEDE4
CH_STOCK = b61.CH_STOCK                          # 0x605D4829 库存块
CH_BASE, CH_STRIDE = b63.CH_BASE, b63.CH_STRIDE
CH_ADDRS = list(b63.CH_ADDRS)
EXIF_TEXT = b63.EXIF_TEXT
EXIF_LEAD = b63.EXIF_LEAD
CH_HOOK = b63.CH_HOOK
POOL, BANK2 = b54.POOL, b54.BANK2
BANNED = b54.BANNED
KNOWN_ZONE = b63.KNOWN_ZONE
LOOKS = b61.LOOKS
CURVES = [(1.50, 'Vivid'), (1.00, 'Natural'), (0.40, 'Muted'), (0.70, 'Portrait')]
PH1, PH2 = 0x11111111, 0x22222222

# 拷贝循环样本（B66 的 slot1 写入）：B69 里必须一次都不出现
COPY_SEQ = bytes.fromhex('dbf04041f0514544d9')


def lines(spec):
    """把指令表拼成 GNU as 源（避免源文件里的字面 tab）。"""
    out = ['\t.am33', '\t.text', '\t.globl\t_start', '_start:']
    out += ['\t' + s for s in spec]
    return '\n'.join(out) + '\n'


# SU 桩：重放被覆盖的三条（mov (a0),d0 / mov d2,a1 / add 0x400,d2 的原位语义），
# 读 stash 得 key -> k -> 只在 k∈1..4 时写 EXIF（"k=N\0" + 完整 key）并把 a0 指到
# tone 指针表单元格；不碰 slot1。退出契约与 B54/B63/B66 相同。
SU_SPEC = [
    'mov\t(a0),d0',
    'mov\t0x%X,a2' % STASH,
    'mov\t(a2),d0',
    'mov\td0,d3',
    'mov\td0,d1',
    'and\t7,d0',
    'asr\t21,d1',
    'and\t0x18,d1',
    'or\td1,d0',
    'add\t-1,d0',
    'cmp\t3,d0',
    'bhi\tLgo',
    'mov\t0x%X,a2' % EXIF_TEXT,
    'mov\td3,(4,a2)',
    'mov\td0,d1',
    'asl\t0x10,d1',
    'add\t0x%X,d1' % EXIF_LEAD,
    'mov\td1,(a2)',
    'asl2\td0',                 # (k-1)*4
    'mov\t0x%X,a0' % SU_PTRTAB,
    'add\td0,a0',               # 指针表单元格（原厂 0x60479A38 会 mov (a0),a2）
    'Lgo:',
    'mov\td2,a1',               # a1 = dest+0x400
    'add\t0x400,d2',            # d2 = dest+0x800
    'mov\t1,d0',
    'jmp\t@@JRET@@',
]
SU_TMPL = lines(SU_SPEC)

# 选择器桩：读 stash 的 key & 7 -> k-1；0..3 -> a0 = BLK_TAB[k-1]；否则库存块。
# 出口一律 jmp 0x604B4CFA（原厂 ret）。
SEL_SPEC = [
    'mov\t0x%X,a2' % STASH,
    'mov\t(a2),d0',
    'and\t7,d0',
    'add\t-1,d0',
    'cmp\t3,d0',
    'bhi\tLstock',
    'asl2\td0',
    'mov\t0x%X,a0' % BLK_TAB,
    'mov\t(d0,a0),a0',
    'bra\tLret',
    'Lstock:',
    'mov\t0x%X,a0' % CH_STOCK,
    'Lret:',
    'jmp\t@@J1@@',
]
SEL_TMPL = lines(SEL_SPEC)


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def in_span(a, ln, span):
    return a >= span[0] and a + ln <= span[1]


def overlaps(a, ln, spans):
    return any(a < s[0] + s[1] and s[0] < a + ln for s in spans)


def _assemble_two_pass(tmpl, ph, name, base, target, tag):
    ph_blob = b46.assemble(tmpl.replace(tag, '0x%X' % ph), name + '_ph', base)
    j = b46.find_ph(ph_blob, ph)
    blob = b46.assemble(tmpl.replace(tag, '0x%x - (0x%x + 0x%x)' % (target, base, j)),
                        name, base)
    want = b'\xdc' + struct.pack('<i', target - (base + j))
    if blob[j:j + 5] != want:
        raise SystemExit('%s jmp 自检失败 @%#x: %s != %s'
                         % (name, base + j, blob[j:j + 5].hex(), want.hex()))
    return blob, j


def assemble_su():
    blob, j = _assemble_two_pass(SU_TMPL, PH1, 'b67_su', SU_STUB, SU_RET, '@@JRET@@')
    if len(blob) > SU_STUB_MAX:
        raise SystemExit('SU 桩 %d B 超 112 B 窗口' % len(blob))
    check_no_slot1(blob, 'SU 桩')
    return blob, j


def assemble_sel(base):
    blob, j = _assemble_two_pass(SEL_TMPL, PH1, 'b67_sel', base, SEL_RET, '@@J1@@')
    check_no_slot1(blob, '选择器桩')
    return blob, j


def check_no_slot1(blob, name):
    if b'\x25\xa8' in blob:          # add 0x25a8（slot1 路径）
        raise SystemExit('%s 里出现 add 0x25a8（slot1 地址计算）' % name)
    if COPY_SEQ in blob:             # setlb + 100 B 拷贝循环
        raise SystemExit('%s 里出现 100 B 拷贝循环' % name)
    if b'\x85\x9c' in blob:          # mov -100,d1
        raise SystemExit('%s 里出现 mov -100,d1（100 B 计数）' % name)


def align4(v):
    return (v + 3) & ~3


def curve_block(s, stock):
    """按饱和度系数 s 生成曲线块：x 用库存形状，y=128+(x-128)*s，z 现算。"""
    out = b''
    for xo in (0x00, 0x32):
        x = [int(v) for v in stock[xo:xo + 16]]
        y = [max(0, min(255, int(round(128 + (v - 128) * s)))) for v in x]
        z = b61.z_chain(x, y)
        out += bytes(x) + bytes(y) + bytes(z) + b'\x00'
    if len(out) != CH_STRIDE:
        raise SystemExit('色度块 %d B != 100' % len(out))
    return out


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

    stub, jmp_off = assemble_su()
    sel_stub = align4(SU_STUB + len(stub))
    sel, sel_j = assemble_sel(sel_stub)
    if sel_stub + len(sel) > SU_SLOT_END:
        raise SystemExit('两桩合计 %d B 超过 0x605DED44（SU %d + SEL %d）'
                         % (sel_stub + len(sel) - SU_STUB, len(stub), len(sel)))

    stock = payload_of(SRC)
    stock_blk = bytes(stock[CH_STOCK - BASE:CH_STOCK - BASE + CH_STRIDE])
    b61.check_block(stock_blk, '库存块')
    blocks = []
    for i, (s, look) in enumerate(CURVES):
        blk = curve_block(s, stock_blk)
        b61.check_block(blk, 'B69 k=%d (%s s=%.2f)' % (i + 1, look, s), stock=stock_blk)
        if blk[0x10 + 5] != 0x80 or blk[0x42 + 5] != 0x80:
            raise SystemExit('B69 k=%d 中性点 x=128 的 y 不是 0x80' % (i + 1))
        blocks.append(blk)
    if len(set(blocks)) != 4:
        raise SystemExit('四张色度块有重复')
    for i, (s, look) in enumerate(CURVES):
        if (s == 1.0) != (blocks[i] == stock_blk):
            raise SystemExit('B69 k=%d 与 stock 的关系不对' % (i + 1))

    blk_tab = struct.pack('<4I', *CH_ADDRS)

    new_spans = [(SU_STUB, len(stub)), (sel_stub, len(sel)),
                 (BLK_TAB, len(blk_tab))] + [(a, CH_STRIDE) for a in CH_ADDRS]
    b59_spans = [(b54.SD_INSERT, b54.SD_LEN), (b54.SU_INSERT, b54.SU_LEN),
                 (b54.MX_INSERT, b54.MX_LEN), (b54.SD_STUB, len(sd)),
                 (b54.SU_PTRTAB, 16), (b54.MX_PTRTAB, 16),
                 (b54.MX_DATA, 72), (b54.MX_STUB, len(mx))]
    b59_spans += [(a, 0x200) for a in b54.TONE_ADDRS]

    for a, ln, name in ([(SU_STUB, len(stub), 'SU 桩'), (sel_stub, len(sel), '选择器桩'),
                         (BLK_TAB, len(blk_tab), '块指针表')]
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
        raise SystemExit('SU 钩点 0x%X 不在 0x60479A20..0x60479A30' % HOOK)
    if not in_span(SEL_INSERT, SEL_LEN, (0x604B4CAD, 0x604B4CB3)):
        raise SystemExit('选择器钩点 0x%X 越界' % SEL_INSERT)
    if sel_stub < SU_STUB + len(stub):
        raise SystemExit('选择器桩压到 SU 桩')
    if overlaps(SU_STUB, len(stub), b59_spans) or overlaps(sel_stub, len(sel), b59_spans):
        raise SystemExit('桩压到 B59 已用区')
    if overlaps(BLK_TAB, len(blk_tab), b59_spans):
        raise SystemExit('块指针表压到 B59 已用区')
    for a in CH_ADDRS:
        if overlaps(a, CH_STRIDE, b59_spans):
            raise SystemExit('块 0x%X 压到 B59 已用区' % a)
    srt = sorted(new_spans)
    for i in range(1, len(srt)):
        if srt[i - 1][0] + srt[i - 1][1] > srt[i][0]:
            raise SystemExit('新落点互相重叠：%s / %s' % (srt[i - 1], srt[i]))
    if bytes(stock[SEL_INSERT - BASE:SEL_INSERT - BASE + SEL_LEN]) != SEL_STOCK:
        raise SystemExit('stock 0x%X 不是 %s' % (SEL_INSERT, SEL_STOCK.hex()))
    if bytes(stock[SU_SLOT_END - BASE:OLD_SU_STUB - BASE]) == b'':
        raise SystemExit('tone 槽 53 读不到')

    ranges = sorted(b59_spans + new_spans + [(SEL_INSERT, SEL_LEN)])

    sel_hook = b46.jmp_bytes(SEL_INSERT, sel_stub) + bytes([NOP])
    lines_ = ['# B69 build from stock: B66 patch set + capture chroma selector hook',
              'ram 0x%x=%s  # hook A 0x604B811D -> SD stub (B59)' % (b54.SD_INSERT, (b46.jmp_bytes(b54.SD_INSERT, b54.SD_STUB) + b'\x00\x00').hex()),
              'ram 0x%x=%s  # hook B 0x60479A2B -> SU stub 0x%X' % (HOOK, b46.jmp_bytes(HOOK, SU_STUB).hex(), SU_STUB),
              'ram 0x%x=%s  # hook C 0x604B6AD6 -> MX stub (B59)' % (b54.MX_INSERT, b46.jmp_bytes(b54.MX_INSERT, b54.MX_STUB).hex()),
              'ram 0x%x=%s  # hook D 0x604B4CAD -> chroma selector stub 0x%X (jmp+nop)' % (SEL_INSERT, sel_hook.hex(), sel_stub),
              'ram 0x%x=%s  # SD stub %d B' % (b54.SD_STUB, sd.hex(), len(sd)),
              'ram 0x%x=%s  # SU stub %d B (no slot1 write)' % (SU_STUB, stub.hex(), len(stub)),
              'ram 0x%x=%s  # chroma selector stub %d B (k -> BLK_TAB[k-1])' % (sel_stub, sel.hex(), len(sel)),
              'ram 0x%x=%s  # MX stub %d B' % (b54.MX_STUB, mx.hex(), len(mx)),
              'ram 0x%x=%s  # tone ptr table [slot22..25]' % (SU_PTRTAB, struct.pack('<4I', *b54.TONE_ADDRS).hex()),
              'ram 0x%x=%s  # chroma block ptr table [blk1..4]' % (BLK_TAB, blk_tab.hex()),
              'ram 0x%x=%s  # matrix ptr table' % (b54.MX_PTRTAB, struct.pack('<4I', *[b54.MX_DATA + 18 * i for i in range(4)]).hex())]
    for i, a in enumerate(b54.TONE_ADDRS):
        lines_.append('ram 0x%x=%s  # tone slot %d (%s)' % (a, tone[i].hex(), b54.SLOTS[i], LOOKS[i]))
    for i, blob in enumerate(mblobs):
        lines_.append('ram 0x%x=%s  # matrix k=%d %r'
                      % (b54.MX_DATA + 18 * i, blob.hex(), i + 1, mx_rows[i]))
    for i, blk in enumerate(blocks):
        lines_.append('ram 0x%x=%s  # chroma k=%d curve s=%.2f (%s)'
                      % (CH_ADDRS[i], blk.hex(), i + 1, CURVES[i][0], CURVES[i][1]))
    body = '\n'.join(lines_) + '\n'
    body.encode('ascii')
    pf = '.tmp/b69.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(sd=sd, mx=mx, stub=stub, jmp_off=jmp_off, sel=sel, sel_stub=sel_stub,
                sel_j=sel_j, sel_hook=sel_hook, tone=tone, mblobs=mblobs,
                blocks=blocks, stock_blk=stock_blk, blk_tab=blk_tab,
                ranges=ranges, new_spans=new_spans, b59_spans=b59_spans, patch=pf)


def main():
    info = build()
    print('B69 = B66 补丁集（从 stock 重建）+ 成片色度选择器钩子')
    print('  SU 桩 %d B @0x%X（jmp 回 0x%X @+%d）；选择器桩 %d B @0x%X；两桩合计 %d B / 112'
          % (len(info['stub']), SU_STUB, SU_RET, info['jmp_off'],
             len(info['sel']), info['sel_stub'],
             info['sel_stub'] + len(info['sel']) - SU_STUB))
    print('  新钩 @0x%X: %s（原厂 %s）' % (SEL_INSERT, info['sel_hook'].hex(), SEL_STOCK.hex()))

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

    # 与 B66 的差异只许落在 SU 桩窗口 + 6 B 新钩
    b66_img = payload_of(B66_PKG)
    d66 = [i for i in range(len(built)) if built[i] != b66_img[i]]
    win = (SU_STUB - BASE, SU_SLOT_END - BASE)
    hook_win = (SEL_INSERT - BASE, SEL_INSERT - BASE + SEL_LEN)
    blk_wins = [(a - BASE, a - BASE + CH_STRIDE) for a in CH_ADDRS]
    stray = [i for i in d66 if not (win[0] <= i < win[1] or hook_win[0] <= i < hook_win[1]
                                    or any(lo <= i < hi for lo, hi in blk_wins))]
    print('  vs B66 差异 %d 字节；预期两区之外 %d 字节 -> %s'
          % (len(d66), len(stray), 'OK' if not stray else 'FAIL'))
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

    # 四张曲线块 == 构建块；Natural(s=1.0) 必须逐字节等于库存块
    stock_blk = bytes(stock[CH_STOCK - BASE:CH_STOCK - BASE + CH_STRIDE])
    for i, a in enumerate(CH_ADDRS):
        want = curve_block(CURVES[i][0], stock_blk)
        same = at(built, a, CH_STRIDE) == want == info['blocks'][i]
        ok &= same
        print('  色度块 k=%d %-8s s=%.2f == 构建块: %s' % (i + 1, CURVES[i][1], CURVES[i][0], same))

    for name, got, want in (('hook A', at(built, b54.SD_INSERT, 7), b46.jmp_bytes(b54.SD_INSERT, b54.SD_STUB) + b'\x00\x00'),
                            ('hook B', at(built, HOOK, 5), b46.jmp_bytes(HOOK, SU_STUB)),
                            ('hook C', at(built, b54.MX_INSERT, 5), b46.jmp_bytes(b54.MX_INSERT, b54.MX_STUB)),
                            ('hook D', at(built, SEL_INSERT, 6), info['sel_hook']),
                            ('SD 桩', at(built, b54.SD_STUB, len(info['sd'])), info['sd']),
                            ('SU 桩', at(built, SU_STUB, len(info['stub'])), info['stub']),
                            ('选择器桩', at(built, info['sel_stub'], len(info['sel'])), info['sel']),
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
        good = at(built, b54.MX_DATA + 18 * i, 18) == blob
        ok &= good
        print('  矩阵 k=%d 一致=%s' % (i + 1, good))

    old_stub_ok = at(built, OLD_SU_STUB, OLD_SU_LEN) == at(stock, OLD_SU_STUB, OLD_SU_LEN)
    print('  0x%X 旧 SU 桩区 == 原厂: %s' % (OLD_SU_STUB, old_stub_ok))
    ok &= old_stub_ok

    tail_ok = at(built, HOOK + 5, 2) == at(stock, HOOK + 5, 2)
    print('  SU 钩点后 2 B 死字节保持原厂 %s: %s' % (at(stock, HOOK + 5, 2).hex(), tail_ok))
    ok &= tail_ok

    ret_ok = at(built, SEL_RET, 3) == at(stock, SEL_RET, 3)
    after_ok = at(built, SEL_INSERT + 6, 16) == at(stock, SEL_INSERT + 6, 16)
    print('  选择器 ret @0x%X 与后续字节保持原厂: %s / %s' % (SEL_RET, ret_ok, after_ok))
    ok &= ret_ok and after_ok

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

    # tone 槽 53 区间里只有 SD 桩（0x605DED44 起，B54/B59/B66 同址）是有意改动，
    # 桩之后的 0x605DED4F..0x605DED80 必须保持原厂。
    after_sd = b54.SD_STUB + len(info['sd'])
    slot53_ok = at(built, after_sd, OLD_SU_STUB - after_sd) == \
                at(stock, after_sd, OLD_SU_STUB - after_sd)
    print('  tone 槽 53 内 SD 桩之后 0x%X..0x%X == 原厂: %s' % (after_sd, OLD_SU_STUB, slot53_ok))
    ok &= slot53_ok

    free_from = info['sel_stub'] + len(info['sel'])
    free_ok = at(built, free_from, SU_SLOT_END - free_from) == at(stock, free_from, SU_SLOT_END - free_from)
    print('  两桩后剩余 %d B 保持原厂: %s' % (SU_SLOT_END - free_from, free_ok))
    ok &= free_ok

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'built_from': SRC, 'compare_to': B66_PKG,
           'hook': HOOK, 'su_stub': SU_STUB, 'su_stub_len': len(info['stub']),
           'su_ret': SU_RET, 'su_jmp_off': info['jmp_off'],
           'sel_hook': SEL_INSERT, 'sel_hook_len': SEL_LEN,
           'sel_stub': info['sel_stub'], 'sel_stub_len': len(info['sel']),
           'sel_ret': SEL_RET, 'sel_stock': SEL_STOCK.hex(),
           'old_su_stub': OLD_SU_STUB, 'old_su_len': OLD_SU_LEN,
           'su_ptrtab': SU_PTRTAB, 'blk_tab': BLK_TAB,
           'blocks': CH_ADDRS, 'ch_stride': CH_STRIDE, 'ch_stock': CH_STOCK,
           'block_curves': [{'k': i + 1, 'look': look, 's': s} for i, (s, look) in enumerate(CURVES)],
           'chroma_hook_stock': CH_HOOK, 'looks': LOOKS,
           'tones': b54.TONE_ADDRS, 'slots': b54.SLOTS,
           'mx_data': b54.MX_DATA, 'stash': STASH,
           'b59_spans': info['b59_spans'], 'new_spans': info['new_spans'],
           'ranges': info['ranges'], 'bank2': BANK2, 'known_zone': KNOWN_ZONE}
    with io.open('.tmp/b69_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
