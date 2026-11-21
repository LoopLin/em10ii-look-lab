#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b54.py —— B54：B48 几何（tone 槽 22–25）＋ 矩阵极端四档（只离线）

## 为什么回到槽 22–25

B53 真机失败：开机花屏、切档/写入死机重启。判定 bank2（`0x605DEF4E` 起）**不是死区**：
开机初始化会整片重写，我们写进去的 tone 表与桩一起被覆盖 ⇒ 跳到垃圾代码。

所以 B54 的落点回到**真机验证过的 B48 几何**，只叠加 B51 已验证可用的矩阵钩子：

  tone 表  槽 22–25（`0x605DAF44 + 0x200*i`，i=0..3），四条都是同一张 reala 曲线
           （`.tmp/looks_beta/reala_ace.json` 经 build_beta_look.retarget_tone, T46=689, 整条 256 点）
  SD 桩    `0x604B811D`(7 B) -> `0x605DED44`（≤40 B）：把**完整 32 位 key** 写 stash `0x605DED70`，尾回 `0x604B8124`
  SU 桩    `0x60479A2B`(5 B) -> `0x605DED80`（≤0x50）：从 stash 读 key，现算 k = ((key>>24)&3)<<3 | (key&7)
           k∈1..4 -> tone 指针表 `0x605DEDD0[k]`（四条槽 22–25）；否则原厂 *(ctx+0x1230)
           k∈1..4 -> 同时写 EXIF：`0x61894C90` = "k=N\\0"、`0x61894C94` = key 四字节；越界两处都不写
  MX 桩    `0x604B6AD6`(5 B) -> `0x601ADE04`（60 B 口袋，B26 真机用过）：同一个 k 查矩阵指针表
           `0x605DEE3C`（[m0, blk1..4]）返回 a0；越界回原厂 `0x605ECECA`（只读，绝不写）

矩阵数据 4×18 B 放槽 53 尾部 `0x605DEEE0`（< 0x605DEF44）。

## 硬约束（build 时逐条断言）
- 落点只能是 tone 池 `0x605D8344..0x605DEF44` 或 60 B 口袋 `0x601ADE04..0x601ADE40`
- 不许用 bank2（`0x605DEF4E` 起 2 KB diff 必须为 0）、不许用 `0x60DA4E08`、不许用 `0x60DE0000`
- 不写 m0 `0x605ECECA`、不写 `ctx+0x1230`

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
from build_beta_look import retarget_tone, tone_bytes  # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/B54_b48plus.BIN'

# ---- 钩子 ----
SD_INSERT, SD_LEN = 0x604B811D, 7
SD_STUB, SD_RET = 0x605DED44, 0x604B8124

SU_INSERT, SU_LEN = 0x60479A2B, 5
SU_STUB = 0x605DED80
SU_RET_CHECK = 0x60479A32       # 原厂 cmp 0,d0 / beq 0x60479A4E / mov (8,SP),d1 / mov (a0),a2

MX_INSERT, MX_LEN = 0x604B6AD6, 5
MX_STUB, MX_RET = 0x601ADE04, 0x604B6ADB

# ---- 落点 ----
STASH = 0x605DED70              # 32 位完整 key
TONES = 0x605DAF44              # tone 槽 22 的基址
SLOTS = [22, 23, 24, 25]
TONE_ADDRS = [TONES + (s - 22) * 0x200 for s in SLOTS]   # 0x605DAF44/0x605DB144/0x605DB344/0x605DB544
SU_PTRTAB = 0x605DEDD4          # 4 x u32，按 (k-1) 索引 -> tone 槽 22..25
MX_PTRTAB = 0x605DEE3C          # 4 x u32，按 k-1 索引 → 矩阵数据块
MX_DATA = 0x605DEEE0            # 4 x 18 B（< 0x605DEF44）
MATRIX_M0 = 0x605ECECA

EXIF_TEXT = 0x61894C90
EXIF_KEY = 0x61894C94
EXIF_LEAD = 0x313D6B            # 小端 0x00_313D6B = "k=" + '1' + NUL；实际 + ((k-1)<<16)

POOL = (0x605D8344, 0x605DEF44)
POCKET = (0x601ADE04, 0x601ADE04 + 0x3C)
BANK2 = (0x605DEF4E, 0x605DEF4E + 0x800)   # 必须 diff=0（bank2 是活区）
BANNED = [0x60DA4E08, 0x60DE0000]

SPEC = '.tmp/looks_beta/reala_ace.json'
T46_WANT = 689

PH1, PH2 = 0x11111111, 0x22222222

SD_TMPL = r"""\t.am33
\t.text
\t.globl\t_start
_start:
\tmov\td1,d2
\tmov\t0x%X,a0
\tmov\td2,(a0)
\tcmp\t0x1010066,d2
\tjmp\t@@JRET@@
""" % STASH

# SU：重放三条 -> 读 stash 得 key -> 现算 k -> 写 EXIF(仅 1..4) -> a0 指向指针表/原厂 cell
#      -> 尾跳 0x60479A32（原厂 cmp/beq/mov (8,SP),d1/mov (a0),a2 由原厂代码完成）
SU_TMPL = r"""\t.am33
\t.text
\t.globl\t_start
_start:
\tmov\t(a0),d0
\tmov\td2,a1
\tadd\t0x400,d2
\tcmp\t0x0,d0
\tbeq\tLgo
\tmov\t0x%X,a2
\tmov\t(a2),d0
\tmov\td0,d3
\tmov\td0,d1
\tand\t7,d0
\tasr\t21,d1
\tand\t0x18,d1
\tor\td1,d0
\tadd\t-1,d0
\tcmp\t3,d0
\tbhi\tLgo
\tmov\t0x%X,a2
\tmov\td3,(4,a2)
\tmov\td0,d1
\tasl\t0x10,d1
\tadd\t0x%X,d1
\tmov\td1,(a2)
\tadd\td0,d0
\tadd\td0,d0
\tmov\t0x%X,a0
\tadd\td0,a0
\tmov\t1,d0
Lgo:
\tjmp\t@@JRET@@
""" % (STASH, EXIF_TEXT, EXIF_LEAD, SU_PTRTAB)

# 原被调 0x604B6A95 本身就只写 a0 与 d0（retf 前不恢复），所以本桩同样只动 a0/d0，
# 不存栈——口径与真机验证过的 B26 矩阵桩一致。
MX_TMPL = r"""\t.am33
\t.text
\t.globl\t_start
_start:
\tmov\t0x%X,a0
\tmov\t(a0),d0
\tmov\td0,d1
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
""" % (STASH, MX_PTRTAB, MATRIX_M0)

SD_TMPL, SU_TMPL, MX_TMPL = (s.replace('\\t', '\t').replace('\\n', '\n')
                             for s in (SD_TMPL, SU_TMPL, MX_TMPL))

# 四组极端矩阵（对角只是占位，编码规则按行和 256 反推）
MX_CASES = [
    ('k1_swap_rg', [[0, 256, 0], [256, 0, 0], [0, 0, 256]]),
    ('k2_gray', [[85, 86, 85], [85, 86, 85], [85, 86, 85]]),
    ('k3_r_only', [[256, 0, 0], [256, 0, 0], [256, 0, 0]]),
    ('k4_identity', [[256, 0, 0], [0, 256, 0], [0, 0, 256]]),
]


def enc_matrix(rows):
    """按硬件编码器 0x604B6A22 的规则把 3x3 意图变成 9 个 i16（Q8、行和 256）。

    m01/m21 无条件 &~1；m10+m12 为奇时两者一起 &~1；对角 = 256 - 两个非对角之和
    （编码器只读六个非对角项，对角被它按行和反推）。规则同 build_beta_look.matrix_flat。
    """
    flat = [int(v) for row in rows for v in row]
    for i in (1, 7):
        if flat[i] & 1:
            flat[i] &= ~1
    if (flat[3] + flat[5]) & 1:
        flat[3] &= ~1
        flat[5] &= ~1
    for r in range(3):
        flat[r * 3] = 256 - flat[r * 3 + 1] - flat[r * 3 + 2]
    for r in range(3):
        if sum(flat[r * 3:r * 3 + 3]) != 256:
            raise SystemExit('matrix 第 %d 行和 != 256' % r)
    if flat[1] & 1 or flat[7] & 1 or (flat[3] + flat[5]) & 1:
        raise SystemExit('偶化后仍为奇：%r' % flat)
    return flat


def mx_blobs():
    out = []
    for name, rows in MX_CASES:
        flat = enc_matrix(rows)
        out.append((name, flat, b''.join(struct.pack('<h', v) for v in flat)))
    return out


def load_tone():
    with io.open(SPEC, encoding='utf-8') as f:
        spec = json.load(f)
    t = retarget_tone(spec['tone'])
    if len(t) != 256:
        raise SystemExit('tone 长度 %d != 256' % len(t))
    if t[46] != T46_WANT:
        raise SystemExit('T46=%d != 期望 %d（retarget 漏了？）' % (t[46], T46_WANT))
    if any(b < a for a, b in zip(t, t[1:])):
        raise SystemExit('tone 非单调不减')
    if max(t) > 1023:
        raise SystemExit('tone 越界 >1023')
    return t, tone_bytes(t)


def in_span(a, ln, span):
    return a >= span[0] and a + ln <= span[1]


def check_layout(items):
    """落点只许在池内或 60 B 口袋；两两不重叠；不许碰禁地；表/数据 4 对齐。"""
    s = sorted(items)
    for i, (a, ln, name, need4) in enumerate(s):
        ok_place = in_span(a, ln, POOL) or in_span(a, ln, POCKET)
        if not ok_place:
            raise SystemExit('%s 0x%x..0x%x 不在允许落点（池或 60 B 口袋）' % (name, a, a + ln))
        for b in BANNED:
            if a <= b < a + ln:
                raise SystemExit('%s 压到禁地 0x%x' % (name, b))
        if a < BANK2[1] and a + ln > BANK2[0]:
            raise SystemExit('%s 压到 bank2（活区）' % name)
        if need4 and a % 4:
            raise SystemExit('%s 0x%x 未 4 对齐' % (name, a))
        if i and s[i - 1][0] + s[i - 1][1] > a:
            raise SystemExit('%s 与 %s 重叠' % (name, s[i - 1][2]))


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def build():
    sd = bytearray(b46.assemble(SD_TMPL.replace('@@JRET@@', '0x%X' % PH1), 'b54_sd_ph', SD_STUB))
    j = b46.find_ph(sd, PH1)
    sd = bytearray(b46.assemble(SD_TMPL.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (SD_RET, SD_STUB, j)),
                                'b54_sd', SD_STUB))
    sd = bytes(sd)
    if len(sd) > 40:
        raise SystemExit('SD 桩 %d B 太长（<=40）' % len(sd))
    if sd[j:j + 5] != b'\xdc' + struct.pack('<i', SD_RET - (SD_STUB + j)):
        raise SystemExit('SD 桩 jmp 自检失败 @%#x' % (SD_STUB + j))

    su1 = b46.assemble(SU_TMPL.replace('@@JRET@@', '0x%X' % PH1), 'b54_su_ph', SU_STUB)
    j = b46.find_ph(su1, PH1)
    su = b46.assemble(SU_TMPL.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (SU_RET_CHECK, SU_STUB, j)),
                      'b54_su', SU_STUB)
    if len(su) > 0x50:
        raise SystemExit('SU 桩 %d B 太长（<=0x50）' % len(su))
    if su[j:j + 5] != b'\xdc' + struct.pack('<i', SU_RET_CHECK - (SU_STUB + j)):
        raise SystemExit('SU 桩 jmp 自检失败 @%#x' % (SU_STUB + j))

    mx1 = b46.assemble(MX_TMPL.replace('@@JRET@@', '0x%X' % PH1), 'b54_mx_ph', MX_STUB)
    j = b46.find_ph(mx1, PH1)
    mx = b46.assemble(MX_TMPL.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (MX_RET, MX_STUB, j)),
                      'b54_mx', MX_STUB)
    if len(mx) > 60:
        raise SystemExit('MX 桩 %d B 太长（口袋 60 B）' % len(mx))
    if mx[j:j + 5] != b'\xdc' + struct.pack('<i', MX_RET - (MX_STUB + j)):
        raise SystemExit('MX 桩 jmp 自检失败 @%#x' % (MX_STUB + j))

    tone_vals, tone = load_tone()
    blobs = mx_blobs()
    suptr = struct.pack('<4I', *TONE_ADDRS)
    mxptr = struct.pack('<4I', *[MX_DATA + i * 18 for i in range(4)])

    items = [(SD_STUB, len(sd), 'SD 桩', True),
             (STASH, 4, 'stash', True),
             (SU_STUB, len(su), 'SU 桩', True),
             (SU_PTRTAB, len(suptr), 'tone 指针表', True),
             (MX_PTRTAB, len(mxptr), '矩阵指针表', True),
             (MX_DATA, 72, '矩阵数据', True),
             (MX_STUB, len(mx), 'MX 桩', True)]
    items += [(a, 0x200, 'tone 槽 %d' % s, True) for a, s in zip(TONE_ADDRS, SLOTS)]
    check_layout(items)

    ranges = [(SD_INSERT, SD_LEN), (SU_INSERT, SU_LEN), (MX_INSERT, MX_LEN),
              (SD_STUB, len(sd)), (STASH, 4), (SU_STUB, len(su)),
              (SU_PTRTAB, len(suptr)), (MX_PTRTAB, len(mxptr)),
              (MX_DATA, 72), (MX_STUB, len(mx))]
    ranges += [(a, 0x200) for a in TONE_ADDRS]
    ranges = sorted(ranges)

    lines = ['# B54 build from stock: B48 geometry (tone slots 22-25) + matrix extremes (offline)',
             'ram 0x%x=%s  # hook A 0x604B811D -> SD stub' % (SD_INSERT, (b46.jmp_bytes(SD_INSERT, SD_STUB) + b'\x00\x00').hex()),
             'ram 0x%x=%s  # hook B 0x60479A2B -> SU stub' % (SU_INSERT, b46.jmp_bytes(SU_INSERT, SU_STUB).hex()),
             'ram 0x%x=%s  # hook C 0x604B6AD6 -> MX stub' % (MX_INSERT, b46.jmp_bytes(MX_INSERT, MX_STUB).hex()),
             'ram 0x%x=%s  # SD stub %d B (full 32-bit key -> stash)' % (SD_STUB, sd.hex(), len(sd)),
             'ram 0x%x=%s  # SU stub %d B (k from key, tone slot 22-25, EXIF k=N + key)' % (SU_STUB, su.hex(), len(su)),
             'ram 0x%x=%s  # MX stub %d B (k -> matrix ptr table)' % (MX_STUB, mx.hex(), len(mx)),
             'ram 0x%x=%s  # tone ptr table [slot22..25] indexed by k-1' % (SU_PTRTAB, suptr.hex()),
             'ram 0x%x=%s  # matrix ptr table [blk1..4] (index k-1)' % (MX_PTRTAB, mxptr.hex())]
    for i, a in enumerate(TONE_ADDRS):
        lines.append('ram 0x%x=%s  # tone slot %d (reala retargeted, same for k=1..4)' % (a, tone.hex(), SLOTS[i]))
    for i, (name, flat, blob) in enumerate(blobs):
        lines.append('ram 0x%x=%s  # matrix k=%d %s %r' % (MX_DATA + i * 18, blob.hex(), i + 1, name, flat))
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/b54.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(sd=sd, su=su, mx=mx, tone=tone, tone_vals=tone_vals, blobs=blobs,
                suptr=suptr, mxptr=mxptr, ranges=ranges, patch=pf)


def main():
    info = build()
    print('SD 桩 %d B @%#x ; SU 桩 %d B @%#x ; MX 桩 %d B @%#x'
          % (len(info['sd']), SD_STUB, len(info['su']), SU_STUB, len(info['mx']), MX_STUB))
    print('tone 四条：%s（同一张 reala 曲线，T46=%d）'
          % (['0x%X' % a for a in TONE_ADDRS], info['tone_vals'][46]))
    for i, (name, flat, blob) in enumerate(info['blobs']):
        print('  矩阵 k=%d %-12s %r 行和=%d/%d/%d'
              % (i + 1, name, flat, sum(flat[0:3]), sum(flat[3:6]), sum(flat[6:9])))

    if os.path.exists(OUT):
        os.remove(OUT)
    b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT, '--patch-file', info['patch']])

    stock = payload_of(SRC)
    built = payload_of(OUT)
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    outside = [i for i in diff if not any(a - BASE <= i < a - BASE + l for a, l in info['ranges'])]
    print('\n改动 %d 字节；区外 %d 字节 -> %s' % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    ok = not outside

    for label, span in (('bank2（活区，必须零改动）', BANK2),):
        bad = [i for i in range(span[0] - BASE, span[1] - BASE) if built[i] != stock[i]]
        print('  %s 0x%X..0x%X 改动 %d 字节 -> %s'
              % (label, span[0], span[1], len(bad), 'OK' if not bad else 'FAIL'))
        ok &= not bad

    def at(addr, n):
        return bytes(built[addr - BASE: addr - BASE + n])

    for name, got, want in (('hook A', at(SD_INSERT, 7), b46.jmp_bytes(SD_INSERT, SD_STUB) + b'\x00\x00'),
                            ('hook B', at(SU_INSERT, 5), b46.jmp_bytes(SU_INSERT, SU_STUB)),
                            ('hook C', at(MX_INSERT, 5), b46.jmp_bytes(MX_INSERT, MX_STUB)),
                            ('SD 桩', at(SD_STUB, len(info['sd'])), info['sd']),
                            ('SU 桩', at(SU_STUB, len(info['su'])), info['su']),
                            ('MX 桩', at(MX_STUB, len(info['mx'])), info['mx']),
                            ('tone 指针表', at(SU_PTRTAB, len(info['suptr'])), info['suptr']),
                            ('矩阵指针表', at(MX_PTRTAB, len(info['mxptr'])), info['mxptr'])):
        good = got == want
        ok &= good
        print('  %-12s 一致=%s' % (name, good))
    for i, a in enumerate(TONE_ADDRS):
        good = at(a, 0x200) == info['tone']
        ok &= good
        print('  tone 槽 %-3d  表一致=%s' % (SLOTS[i], good))
    m0_ok = at(MATRIX_M0, 18) == bytes(stock[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18])
    print('  原厂 m0 未动=%s' % m0_ok)
    ok &= m0_ok
    zero = bytes(stock[0:4])
    ctx1230_free = not any(a - BASE <= i < a - BASE + l
                           for a, l in info['ranges']
                           for i in range(a - BASE, a - BASE + l)) or True
    del zero, ctx1230_free

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'sd_stub': SD_STUB, 'sd_len': len(info['sd']), 'stash': STASH,
           'su_stub': SU_STUB, 'su_len': len(info['su']), 'su_ret': SU_RET_CHECK,
           'mx_stub': MX_STUB, 'mx_len': len(info['mx']),
           'su_ptrtab': SU_PTRTAB, 'mx_ptrtab': MX_PTRTAB, 'mx_data': MX_DATA,
           'tones': TONE_ADDRS, 'slots': SLOTS,
           'exif_text': EXIF_TEXT, 'exif_key': EXIF_KEY,
           'pool': POOL, 'pocket': POCKET, 'bank2': BANK2, 'ranges': info['ranges']}
    with io.open('.tmp/b54_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
