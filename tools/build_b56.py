#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b56.py —— B56：用「改指针」做第一级替换（只离线）

## 为什么换到这一级

B54 的钩子改的是"拷进 ISP 窗口的那份表"（上传器 `0x60479A2B` 之后的拷贝路），实测是
**第二级映射**：输入已经是原厂显示值 0–255，我们写 0–1023 的曲线进去会被二次压缩 ⇒ 发白。

B56 改的是**上传器真正读的那个指针**：`0x60479A27 add 0x1230,a0; mov (a0),d0` 读的是
`*(ctx+0x1230)`。原厂每帧在 `0x604AA253 call 0x604B9E61` 里把它写成原厂表指针，紧接着
`0x604AA25A` 就是理想挂点：此刻

  - `a3` = ctx，`ctx+8 / +9 / +0xA`（key 的三个字节）**还没被清**（清发生在 `0x604AA261` 之后），
  - `a2` = P（snapshot 结果块），`a0` = `0x604B9E61` 的返回值（原厂表指针），
  - `d0` = 0、`a1` = 临时（下一个块开头自己会重写）。

钩子把 5 B（`mov a0,(8,a2)` 3 B + `clr d0` 1 B + `mov d0,(12,a2)` 前 1 B）换成 `DC disp32`，
桩先重放这 7 B，再按 key 算 k：k∈1..4 就把 `ctx+0x1230` 改成我们那档的表；越界**什么都不写**
（`0x604B9E61` 刚写进去的就是原厂指针，天然恢复，不会污染别的档）。

## 落点

  tone 表   槽 22–25（`0x605DAF44 + 0x200*i`）＝ 四套真 look 的 tone（retarget_tone 重采样）
            vivid=reala_ace / natural=astia / muted=eterna / portrait=bleach_bypass
  tone 指针表 `0x605DEDD4`（4×u32，按 k-1 索引）
  桩 A      `0x604AA25A`(5 B) -> `0x605DED44`（≤0x50）：重放 7 B、算 k、写 ctx+0x1230、尾回 `0x604AA261`
  MX 桩     `0x604B6AD6`(5 B) -> `0x601ADE04`（60 B 口袋）：按 key 选真矩阵，尾回 `0x604B6ADB`
  矩阵指针表 `0x605DEE3C`（4×u32，按 k-1 索引）
  矩阵数据  `0x605DEEE0`（4×18 B，< `0x605DEF44`）

## 硬约束（build 时逐条断言）

- 落点只能是池内 `0x605D8344..0x605DEF44` 或 60 B 口袋 `0x601ADE04..0x601ADE40`
- 不许 bank2（`0x605DEF4E` 起 2 KB diff==0）、不许 `0x60DA4E08`、不许 `0x60DE0000`
- 不写 m0 `0x605ECECA`；**不再用 SU 上传钩子 `0x60479A2B`、不再用 SD 钩子 `0x604B811D`**
- 不需要 stash：key 在 `0x604AA25A` 还活着

只离线：从 stock 起包、只写 build/、不刷机、不碰任何盘。
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
import build_beta_look as BL                    # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/B56_ptr1.BIN'

# ---- 钩子 ----
TRACE_INSERT, TRACE_LEN = 0x604AA25A, 5
TRACE_SPAN = 7           # 钩子实际盖住 7 B 指令（5 B jmp + 2 B 填充，桩里原样重放）
TRACE_STUB, TRACE_RET = 0x605DED44, 0x604AA261

MX_INSERT, MX_LEN = 0x604B6AD6, 5
MX_STUB, MX_RET = 0x601ADE04, 0x604B6ADB

# 本次**不碰**的两处（build 时断言与原厂一致）
NO_TOUCH = {'SD 钩子 0x604B811D': (0x604B811D, 7),
            'SU 钩子 0x60479A2B': (0x60479A2B, 5)}

# ---- 落点 ----
TONES = 0x605DAF44
SLOTS = [22, 23, 24, 25]
TONE_ADDRS = [TONES + (s - 22) * 0x200 for s in SLOTS]
TONE_PTRTAB = 0x605DEDD4
MX_PTRTAB = 0x605DEE3C
MX_DATA = 0x605DEEE0
MATRIX_M0 = 0x605ECECA

POOL = (0x605D8344, 0x605DEF44)
POCKET = (0x601ADE04, 0x601ADE40)
BANK2 = (0x605DEF4E, 0x605DF74E)
BANNED = [0x60DA4E08, 0x60DE0000]

EXIF_TEXT = 0x61894C90
EXIF_LEAD = 0x313D6B            # 文本字 = EXIF_LEAD + ((k-1) << 16) -> "k=N\0"

# 四档 = 四套真 look（顺序 = k=1..4）
LOOKS = [('vivid', 'reala_ace'), ('natural', 'astia'),
         ('muted', 'eterna'), ('portrait', 'bleach_bypass')]
T46_WANT = [689, 651, 642, 716]
SPEC_BETA = '.tmp/looks_beta'
SPEC_MATRIX = '.tmp/looks_matrix'

PH1 = 0x0BADF00D


# ---------------------------------------------------------------- 桩模板
TRACE_TMPL = r"""\t.am33
\t.text
\t.globl\t_start
_start:
\tmov\ta0,(8,a2)
\tclr\td0
\tmov\td0,(12,a2)
\tmovbu\t(8,a3),d1
\tmovhu\t(0xA,a3),d0
\tand\t3,d1
\tasl\t3,d1
\tand\t7,d0
\tor\td1,d0
\tadd\t-1,d0
\tcmp\t3,d0
\tbhi\tLend
\tmov\t0x%X,a1
\tmov\td0,d1
\tasl\t0x10,d1
\tadd\t0x%X,d1
\tmov\td1,(a1)
\tmov\t0x%X,a0
\tadd\td0,d0
\tadd\td0,d0
\tadd\td0,a0
\tmov\t(a0),a0
\tmov\t0x1230,a1
\tadd\ta3,a1
\tmov\ta0,(a1)
Lend:
\tclr\td0
\tjmp\t@@JRET@@
""" % (EXIF_TEXT, EXIF_LEAD, TONE_PTRTAB)

MX_TMPL = r"""\t.am33
\t.text
\t.globl\t_start
_start:
\tmov\td3,d0
\tmov\td3,d1
\tand\t7,d0
\tasr\t21,d1
\tand\t0x18,d1
\tor\td1,d0
\tadd\t-1,d0
\tcmp\t3,d0
\tbhi\tLstock
\tadd\td0,d0
\tadd\td0,d0
\tmov\t0x%X,a0
\tadd\td0,a0
\tmov\t(a0),a0
\tbra\tLend
Lstock:
\tmov\t0x%X,a0
Lend:
\tjmp\t@@JRET@@
""" % (MX_PTRTAB, MATRIX_M0)

TRACE_TMPL, MX_TMPL = (s.replace('\\t', '\t').replace('\\n', '\n') for s in (TRACE_TMPL, MX_TMPL))


# ---------------------------------------------------------------- 数据
def load_tones():
    """四档 tone：各自 look 的 tone 经 retarget_tone 重采样（B31 漏过这一步，别重复）。"""
    out = []
    for (label, look), t46 in zip(LOOKS, T46_WANT):
        with io.open('%s/%s.json' % (SPEC_BETA, look), encoding='utf-8') as f:
            spec = json.load(f)
        t = BL.retarget_tone(spec['tone'])
        if len(t) != 256:
            raise SystemExit('%s tone 长度 %d != 256' % (look, len(t)))
        if t[46] != t46:
            raise SystemExit('%s T46=%d != 期望 %d（retarget 漏了？）' % (look, t[46], t46))
        if any(b < a for a, b in zip(t, t[1:])):
            raise SystemExit('%s tone 非单调不减' % look)
        if max(t) > 1023:
            raise SystemExit('%s tone 越界 >1023' % look)
        out.append((label, look, t, BL.tone_bytes(t)))
    return out


def load_matrices():
    """四档矩阵：直接取 look 规格里的 quantized 矩阵，按硬件编码规则自检（行和 256 等）。"""
    out = []
    for label, look in LOOKS:
        with io.open('%s/%s.json' % (SPEC_MATRIX, look), encoding='utf-8') as f:
            spec = json.load(f)
        flat = BL.matrix_flat(spec)
        if [sum(flat[i * 3:i * 3 + 3]) for i in range(3)] != [256, 256, 256]:
            raise SystemExit('%s 矩阵行和 != 256' % look)
        if flat[1] & 1 or flat[7] & 1:
            raise SystemExit('%s m01/m21 未偶化' % look)
        for r in range(3):
            if flat[r * 3] != 256 - flat[r * 3 + 1] - flat[r * 3 + 2]:
                raise SystemExit('%s 第 %d 行对角 != 行和反推' % (look, r + 1))
        out.append((label, look, flat, BL.matrix_bytes(spec)))
    return out


def in_span(a, ln, span):
    return a >= span[0] and a + ln <= span[1]


def check_layout(items):
    s = sorted(items)
    for i, (a, ln, name) in enumerate(s):
        if not (in_span(a, ln, POOL) or in_span(a, ln, POCKET)):
            raise SystemExit('%s 0x%x..0x%x 不在允许落点（池或 60 B 口袋）' % (name, a, a + ln))
        for b in BANNED:
            if a <= b < a + ln:
                raise SystemExit('%s 压到禁地 0x%x' % (name, b))
        if a < BANK2[1] and a + ln > BANK2[0]:
            raise SystemExit('%s 压到 bank2（活区）' % name)
        if a % 4:
            raise SystemExit('%s 0x%x 未 4 对齐' % (name, a))
        if i and s[i - 1][0] + s[i - 1][1] > a:
            raise SystemExit('%s 与 %s 重叠' % (name, s[i - 1][2]))


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


# ---------------------------------------------------------------- 构建
def asm_two_pass(tmpl, tag, base, ret, limit, what):
    first = b46.assemble(tmpl.replace('@@JRET@@', '0x%X' % PH1), tag + '_ph', base)
    j = b46.find_ph(first, PH1)
    blob = b46.assemble(tmpl.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (ret, base, j)),
                        tag, base)
    if len(blob) > limit:
        raise SystemExit('%s %d B 太长（<=%d）' % (what, len(blob), limit))
    want = b'\xdc' + struct.pack('<i', ret - (base + j))
    if blob[j:j + 5] != want:
        raise SystemExit('%s jmp 自检失败 @%#x（%s != %s）'
                         % (what, base + j, blob[j:j + 5].hex(), want.hex()))
    return blob, j


def build():
    tr, tr_j = asm_two_pass(TRACE_TMPL, 'b56_tr', TRACE_STUB, TRACE_RET, 0x50, '桩 A')
    mx, mx_j = asm_two_pass(MX_TMPL, 'b56_mx', MX_STUB, MX_RET, 60, 'MX 桩')

    tones = load_tones()
    mats = load_matrices()
    toneptr = struct.pack('<4I', *TONE_ADDRS)
    mxptr = struct.pack('<4I', *[MX_DATA + i * 18 for i in range(4)])

    items = [(TRACE_STUB, len(tr), '桩 A'),
             (TONE_PTRTAB, len(toneptr), 'tone 指针表'),
             (MX_PTRTAB, len(mxptr), '矩阵指针表'),
             (MX_DATA, 72, '矩阵数据'),
             (MX_STUB, len(mx), 'MX 桩')]
    items += [(a, 0x200, 'tone 槽 %d' % s) for a, s in zip(TONE_ADDRS, SLOTS)]
    check_layout(items)

    ranges = [(TRACE_INSERT, TRACE_SPAN), (MX_INSERT, MX_LEN),
              (TRACE_STUB, len(tr)), (TONE_PTRTAB, len(toneptr)),
              (MX_PTRTAB, len(mxptr)), (MX_DATA, 72), (MX_STUB, len(mx))]
    ranges += [(a, 0x200) for a in TONE_ADDRS]
    ranges = sorted(ranges)

    lines = ['# B56 from stock: pointer-level tone switch (ctx+0x1230) + real matrices (offline)',
             'ram 0x%x=%s  # hook A 0x604AA25A -> stub A (replay 7B, write ctx+0x1230)' % (TRACE_INSERT, (b46.jmp_bytes(TRACE_INSERT, TRACE_STUB) + b'\x00\x00').hex()),
             'ram 0x%x=%s  # hook B 0x604B6AD6 -> MX stub' % (MX_INSERT, b46.jmp_bytes(MX_INSERT, MX_STUB).hex()),
             'ram 0x%x=%s  # stub A %d B (k from ctx+8/+0xA, ret 0x604AA261)' % (TRACE_STUB, tr.hex(), len(tr)),
             'ram 0x%x=%s  # MX stub %d B (k from d3=key, ret 0x604B6ADB)' % (MX_STUB, mx.hex(), len(mx)),
             'ram 0x%x=%s  # tone ptr table [slot22..25] indexed by k-1' % (TONE_PTRTAB, toneptr.hex()),
             'ram 0x%x=%s  # matrix ptr table [blk1..4] indexed by k-1' % (MX_PTRTAB, mxptr.hex())]
    for i, (label, look, t, blob) in enumerate(tones):
        lines.append('ram 0x%x=%s  # tone slot %d = %s (T46=%d, k=%d)'
                     % (TONE_ADDRS[i], blob.hex(), SLOTS[i], look, t[46], i + 1))
    for i, (label, look, flat, blob) in enumerate(mats):
        lines.append('ram 0x%x=%s  # matrix k=%d %s %r' % (MX_DATA + i * 18, blob.hex(), i + 1, look, flat))
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/b56.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(tr=tr, tr_j=tr_j, mx=mx, mx_j=mx_j, tones=tones, mats=mats,
                toneptr=toneptr, mxptr=mxptr, ranges=ranges, patch=pf)


def main():
    info = build()
    print('桩 A  %d B @%#x（jmp@+0x%X）；MX 桩 %d B @%#x（jmp@+0x%X）'
          % (len(info['tr']), TRACE_STUB, info['tr_j'], len(info['mx']), MX_STUB, info['mx_j']))
    for a, s in zip(TONE_ADDRS, SLOTS):
        print('  tone 槽 %d @%#x' % (s, a))
    for i, (label, look, t, blob) in enumerate(info['tones']):
        print('  k=%d %-9s %-13s T46=%d  槽 %d' % (i + 1, label, look, t[46], SLOTS[i]))
    for i, (label, look, flat, blob) in enumerate(info['mats']):
        print('  k=%d %-9s %-13s %r 行和=%d/%d/%d'
              % (i + 1, label, look, flat, sum(flat[0:3]), sum(flat[3:6]), sum(flat[6:9])))

    if os.path.exists(OUT):
        os.remove(OUT)
    b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT, '--patch-file', info['patch']])

    stock = payload_of(SRC)
    built = payload_of(OUT)
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    outside = [i for i in diff if not any(a - BASE <= i < a - BASE + l for a, l in info['ranges'])]
    print('\n改动 %d 字节；区外 %d 字节 -> %s' % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    ok = not outside

    def at(addr, n):
        return bytes(built[addr - BASE: addr - BASE + n])

    def st_at(addr, n):
        return bytes(stock[addr - BASE: addr - BASE + n])

    # bank2 活区
    bad = [i for i in range(BANK2[0] - BASE, BANK2[1] - BASE) if built[i] != stock[i]]
    print('  bank2（活区，必须零改动）%#x..%#x 改动 %d 字节 -> %s'
          % (BANK2[0], BANK2[1], len(bad), 'OK' if not bad else 'FAIL'))
    ok &= not bad

    # tone 池 0..21 必须原样（我们只占 22–25）
    p0, p1 = POOL[0], TONE_ADDRS[0]
    badp = [i for i in range(p0 - BASE, p1 - BASE) if built[i] != stock[i]]
    print('  tone 池槽 0..21 %#x..%#x 改动 %d 字节 -> %s'
          % (p0, p1, len(badp), 'OK' if not badp else 'FAIL'))
    ok &= not badp

    for name, (a, ln) in NO_TOUCH.items():
        good = at(a, ln) == st_at(a, ln)
        ok &= good
        print('  %-20s 保持原厂=%s' % (name, good))

    for name, got, want in (('钩子 A', at(TRACE_INSERT, 7), b46.jmp_bytes(TRACE_INSERT, TRACE_STUB) + b'\x00\x00'),
                            ('钩子 B', at(MX_INSERT, 5), b46.jmp_bytes(MX_INSERT, MX_STUB)),
                            ('桩 A', at(TRACE_STUB, len(info['tr'])), info['tr']),
                            ('MX 桩', at(MX_STUB, len(info['mx'])), info['mx']),
                            ('tone 指针表', at(TONE_PTRTAB, len(info['toneptr'])), info['toneptr']),
                            ('矩阵指针表', at(MX_PTRTAB, len(info['mxptr'])), info['mxptr']),
                            ('矩阵数据', at(MX_DATA, 72), b''.join(m[3] for m in info['mats']))):
        good = got == want
        ok &= good
        print('  %-12s 一致=%s' % (name, good))

    for i, a in enumerate(TONE_ADDRS):
        good = at(a, 0x200) == info['tones'][i][3]
        ok &= good
        print('  tone 槽 %-3d  表一致=%s' % (SLOTS[i], good))

    m0_ok = at(MATRIX_M0, 18) == st_at(MATRIX_M0, 18)
    print('  原厂 m0 未动=%s' % m0_ok)
    ok &= m0_ok

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'trace_insert': TRACE_INSERT, 'trace_span': TRACE_SPAN, 'trace_stub': TRACE_STUB, 'trace_len': len(info['tr']),
           'trace_ret': TRACE_RET, 'mx_insert': MX_INSERT, 'mx_stub': MX_STUB,
           'mx_len': len(info['mx']), 'mx_ret': MX_RET,
           'tone_ptrtab': TONE_PTRTAB, 'mx_ptrtab': MX_PTRTAB, 'mx_data': MX_DATA,
           'tones': TONE_ADDRS, 'slots': SLOTS, 'looks': [l[1] for l in LOOKS],
           'exif_text': EXIF_TEXT, 'pool': POOL, 'pocket': POCKET, 'bank2': BANK2,
           'ranges': info['ranges']}
    with io.open('.tmp/b56_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
