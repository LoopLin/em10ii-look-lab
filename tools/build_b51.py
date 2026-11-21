#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b51.py —— B51：三条通道按档分派（本版只验"通道能不能通"，不调观感）

本包要回答的问题只有一个：**按档分派的钩子链在成片上是否真的通**。
所以信号源放在最好判读的地方 —— 3x3 矩阵写成四组"一眼可辨"的极端值。

几何（全部沿用已验证事实，不重新论证）
  钩子 A 0x604B811D (7 B = 5 B jmp + 2 B 填充) -> SD 桩 0x605DED44
      k = ((key>>24)&3)<<3 | (key&7) 写成 32 位到 stash 0x605DED70
      桩尾 mov d2,d1 / cmp 0x1010066,d2 / jmp 0x604B8124（让原 bcc 用同一组标志）
  钩子 B 0x60479A2B (5 B) -> SU 桩 0x605DED80
      重放 mov (a0),d0 / mov d2,a1 / add 0x400,d2；k=1..4 查 tone 指针表 0x605DEE3C
      同一个桩顺手把 "k=N\0" 4 字节写进 EXIF UserComment 文本区 0x61894C90
      （k 不在 1..4 时什么都不写，保持原厂全空格）
      回 0x60479A3A（原厂拷贝循环）/ 0x60479A4E（空指针分支）
  钩子 C 0x604B6AD6 (5 B，原 call 0x604b6a95) -> MX 桩 0x605DEEC0
      按 stash 的 k 查矩阵指针表 0x605DEEA8，返回 a0=矩阵地址；k 不在 1..4 回原厂 m0
      回 0x604B6ADB（原调用点之后；那里 cmp 0x0,a0 判空）

  tone 四档**内容相同**（reala ace 重采样后的同一张曲线），写进槽 9/11/12/13 各一份。
  问"是不是拿到了该档的表"由指针断言回答，画面亮度保持一致、不干扰矩阵的判读。

  矩阵四组（4 x 18 B @ 0x605DEE60）—— 硬件规则：Q8、行和恒 256、m01/m21 偶化、
  m10+m12 同奇时一起偶化、对角由行和反推（编码器 0x604B6A22 只读六个非对角项）。
    k=1 互换 R/G    k=2 灰化(85/86/85)    k=3 只吃 R 通道（三路都由 R 驱动）
    k=4 单位阵（对照档，应与原厂几乎无差别）

  色度：本包**不做**（会与矩阵互相污染），不放任何色度代码/表。

只离线：从 stock 起包、只读 build/、不刷机、不碰任何盘。
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
OUT = 'build/B51_three.BIN'

SD_INSERT, SD_LEN = 0x604B811D, 7
SD_STUB, SD_RET = 0x605DED44, 0x604B8124

SU_INSERT, SU_LEN = 0x60479A2B, 5
SU_STUB = 0x605DED80
SU_RET_LOAD, SU_RET_NULL = 0x60479A3A, 0x60479A4E

MX_INSERT, MX_LEN = 0x604B6AD6, 5
MX_STUB, MX_RET = 0x605DEEC0, 0x604B6ADB

STASH = 0x605DED70          # 32 位 k
PTRTAB = 0x605DEE3C         # tone 指针表 [_, slot9, slot11, slot12, slot13]
MX_DATA = 0x605DEE60        # 4 x 18 B 矩阵
MX_PTRTAB = 0x605DEEA8      # 矩阵指针表，按 k 直接索引：[k0 原厂, blk1..blk4]

TONES = 0x605D8344
SLOT = 0x200
SLOTS = [9, 11, 12, 13]
MATRIX_M0 = 0x605ECECA
WINDOW = (0x605D8344, 0x605DEF44)   # 落点必须全在这个区间内

# EXIF: X = 0x618943FC，UserComment(0x9286) 的文本区 = X+0x894（8 B 字符集之后 117 B 空格）
# 写 "k=N\0" 4 字节；APP1 段长硬编码 0xA3FE，等长改写不动结构。
EXIF_TEXT = 0x61894C90
EXIF_LEAD = 0x303D6B                # 小端 0x00_303D6B = "k=" + (0x30+k) + NUL 的低三字节
EXIF_POKE = b'        '             # libsim 里该区的"原厂内容"：全空格

SPEC = '.tmp/looks_beta/reala_ace.json'
T46_WANT = 689

PH1, PH2 = 0x11111111, 0x22222222

SD_TMPL = r"""\t.am33
\t.text
\t.globl\t_start
_start:
\tmov\td1,d2
\tmov\td2,d0
\tasr\t24,d0
\tand\t3,d0
\tasl\t3,d0
\tand\t7,d1
\tor\td1,d0
\tmov\t0x%X,a0
\tmov\td0,(a0)
\tmov\td2,d1
\tcmp\t0x1010066,d2
\tjmp\t-0x100000
""" % STASH

SU_TMPL = r"""\t.am33
\t.text
\t.globl\t_start
_start:
\tmov\t(a0),d0
\tmov\td2,a1
\tadd\t0x400,d2
\tcmp\t0x0,d0
\tbeq\tLnull
\tmov\t0x%X,a0
\tmov\t(a0),d3
\tcmp\t0x0,d3
\tbeq\tLstock
\tcmp\t0x4,d3
\tbhi\tLstock
\tmov\t0x%X,a2
\tmov\td3,d0
\tasl\t0x10,d0
\tadd\t0x303D6B,d0
\tmov\td0,(a2)
\tmov\t0x%X,a2
\tadd\td3,d3
\tadd\td3,d3
\tadd\td3,a2
\tmov\t(a2),a2
\tbra\tLgo
Lstock:
\tmov\td0,a2
Lgo:
\tmov\t(8,SP),d1
\tjmp\t@@J1@@
Lnull:
\tjmp\t@@J2@@
""" % (STASH, EXIF_TEXT, PTRTAB)

MX_TMPL = r"""\t.am33
\t.text
\t.globl\t_start
_start:
\tadd\t-0x8,sp
\tmov\td0,(0,sp)
\tmov\ta2,(4,sp)
\tmov\t0x%X,a2
\tmov\t(a2),d0
\tcmp\t1,d0
\tbcs\tLstock
\tcmp\t4,d0
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
\tmov\t(0,sp),d0
\tmov\t(4,sp),a2
\tadd\t0x8,sp
\tjmp\t@@JRET@@
""" % (STASH, MX_PTRTAB, MATRIX_M0)

SD_TMPL, SU_TMPL, MX_TMPL = (s.replace('\\t', '\t').replace('\\n', '\n')
                              for s in (SD_TMPL, SU_TMPL, MX_TMPL))

# 四组极端矩阵的"意图"（对角值只是占位，编码规则会按行和 256 反推）
MX_CASES = [
    ('k1_swap_rg', [[0, 256, 0], [256, 0, 0], [0, 0, 256]]),
    ('k2_gray', [[85, 86, 85], [85, 86, 85], [85, 86, 85]]),
    ('k3_r_only', [[256, 0, 0], [256, 0, 0], [256, 0, 0]]),
    ('k4_identity', [[256, 0, 0], [0, 256, 0], [0, 0, 256]]),
]


def enc_matrix(rows):
    """按硬件编码器的规则把 3x3 意图变成 9 个 i16（Q8、行和 256）。

    规则（docs/color/look-spec-matrix.md §1.1，编码器 0x604B6A22 实测）：
      m01/m21 无条件 &~1；m10+m12 为奇时两者一起 &~1；对角 = 256 - 两个非对角之和。
    对角输入值被忽略（编码器根本不读它），所以这里先偶化非对角、再反推对角。
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
    return t, tone_bytes(t)


def check_layout(items):
    """落点两两不重叠、4 对齐（表/数据）、全部在 WINDOW 内。"""
    lo, hi = WINDOW
    s = sorted(items)
    for i, (a, ln, name) in enumerate(s):
        if a < lo or a + ln > hi:
            raise SystemExit('%s 0x%x..0x%x 越界（窗口 %#x..%#x）' % (name, a, a + ln, lo, hi))
        if i and s[i - 1][0] + s[i - 1][1] > a:
            raise SystemExit('%s 与 %s 重叠' % (name, s[i - 1][2]))


def build():
    # ---- 桩 --------------------------------------------------------------
    sd = bytearray(b46.assemble(SD_TMPL, 'b51_sd', SD_STUB))
    off = len(sd) - 5
    sd[off + 1:off + 5] = ((SD_RET - (SD_STUB + off)) & 0xFFFFFFFF).to_bytes(4, 'little')
    sd = bytes(sd)
    if len(sd) > 40:
        raise SystemExit('SD 桩 %d B 太长（<=40）' % len(sd))

    su1 = b46.assemble(SU_TMPL.replace('@@J1@@', '0x%X' % PH1)
                       .replace('@@J2@@', '0x%X' % PH2), 'b51_su_ph', SU_STUB)
    i1 = b46.find_ph(su1, PH1)
    i2 = b46.find_ph(su1, PH2)
    su = b46.assemble(SU_TMPL
                      .replace('@@J1@@', '0x%x - (0x%x + 0x%x)' % (SU_RET_LOAD, SU_STUB, i1))
                      .replace('@@J2@@', '0x%x - (0x%x + 0x%x)' % (SU_RET_NULL, SU_STUB, i2)),
                      'b51_su', SU_STUB)
    if len(su) > 0x50:
        raise SystemExit('SU 桩 %d B 太长（<=0x50）' % len(su))
    for off_, want in ((i1, SU_RET_LOAD), (i2, SU_RET_NULL)):
        got = su[off_:off_ + 5]
        exp = b'\xdc' + struct.pack('<i', want - (SU_STUB + off_))
        if got != exp:
            raise SystemExit('SU 桩 jmp 自检失败 @%#x' % (SU_STUB + off_))

    mx1 = b46.assemble(MX_TMPL.replace('@@JRET@@', '0x%X' % PH1), 'b51_mx_ph', MX_STUB)
    j = b46.find_ph(mx1, PH1)
    mx = b46.assemble(MX_TMPL.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (MX_RET, MX_STUB, j)),
                      'b51_mx', MX_STUB)
    if len(mx) > 0x40:
        raise SystemExit('MX 桩 %d B 太长（<=0x40）' % len(mx))
    exp = b'\xdc' + struct.pack('<i', MX_RET - (MX_STUB + j))
    if mx[j:j + 5] != exp:
        raise SystemExit('MX 桩 jmp 自检失败 @%#x' % (MX_STUB + j))

    # ---- 数据 ------------------------------------------------------------
    tone_vals, tone = load_tone()
    blobs = mx_blobs()
    ptr = struct.pack('<5I', 0, *[TONES + s * SLOT for s in SLOTS])
    mxptr = struct.pack('<5I', MATRIX_M0, *[MX_DATA + i * 18 for i in range(4)])

    check_layout([(SD_STUB, len(sd), 'SD 桩'),
                  (STASH, 4, 'stash'),
                  (SU_STUB, len(su), 'SU 桩'),
                  (PTRTAB, len(ptr), 'tone 指针表'),
                  (MX_DATA, 72, '矩阵数据'),
                  (MX_PTRTAB, len(mxptr), '矩阵指针表'),
                  (MX_STUB, len(mx), 'MX 桩')]
                 + [(TONES + s * SLOT, SLOT, 'tone 槽 %d' % s) for s in SLOTS])
    for a, ln, name in ((PTRTAB, len(ptr), 'tone 指针表'), (MX_DATA, 72, '矩阵数据'),
                        (MX_PTRTAB, len(mxptr), '矩阵指针表'), (MX_STUB, len(mx), 'MX 桩')):
        if a % 4:
            raise SystemExit('%s 0x%x 未 4 对齐' % (name, a))

    ranges = [(SD_INSERT, SD_LEN), (SU_INSERT, SU_LEN), (MX_INSERT, MX_LEN),
              (SD_STUB, len(sd)), (SU_STUB, len(su)), (MX_STUB, len(mx)),
              (STASH, 4), (PTRTAB, len(ptr)), (MX_DATA, 72), (MX_PTRTAB, len(mxptr))]
    ranges += [(TONES + s * SLOT, SLOT) for s in SLOTS]
    ranges = sorted(ranges)

    lines = ['# B51 build from stock: 3-channel per-mode dispatch (offline only)',
             'ram 0x%x=%s  # hook A 0x604B811D -> SD stub' % (SD_INSERT, (b46.jmp_bytes(SD_INSERT, SD_STUB) + b'\x00\x00').hex()),
             'ram 0x%x=%s  # hook B 0x60479A2B -> SU stub' % (SU_INSERT, b46.jmp_bytes(SU_INSERT, SU_STUB).hex()),
             'ram 0x%x=%s  # hook C 0x604B6AD6 -> MX stub' % (MX_INSERT, b46.jmp_bytes(MX_INSERT, MX_STUB).hex()),
             'ram 0x%x=%s  # SD stub %d B' % (SD_STUB, sd.hex(), len(sd)),
             'ram 0x%x=%s  # SU stub %d B' % (SU_STUB, su.hex(), len(su)),
             'ram 0x%x=%s  # MX stub %d B' % (MX_STUB, mx.hex(), len(mx)),
             'ram 0x%x=00000000  # stash k init' % STASH,
             'ram 0x%x=%s  # tone ptr table [_,9,11,12,13]' % (PTRTAB, ptr.hex()),
             'ram 0x%x=%s  # matrix ptr table indexed by k [m0,blk1..4]' % (MX_PTRTAB, mxptr.hex())]
    for s in SLOTS:
        lines.append('ram 0x%x=%s  # tone slot %d (same curve in all 4)' % (TONES + s * SLOT, tone.hex(), s))
    for i, (name, flat, blob) in enumerate(blobs):
        lines.append('ram 0x%x=%s  # matrix k=%d %s %r' % (MX_DATA + i * 18, blob.hex(), i + 1, name, flat))
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/b51.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)

    return dict(sd=sd, su=su, mx=mx, tone=tone, tone_vals=tone_vals, blobs=blobs,
                ptr=ptr, mxptr=mxptr, ranges=ranges, patch=pf)


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def main():
    info = build()
    print('SD 桩 %d B @%#x ; SU 桩 %d B @%#x ; MX 桩 %d B @%#x'
          % (len(info['sd']), SD_STUB, len(info['su']), SU_STUB, len(info['mx']), MX_STUB))
    print('tone 槽 %s 各 0x200（同一张 reala 曲线，T46=%d）' % (SLOTS, info['tone_vals'][46]))
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

    def at(addr, blob):
        n = blob if isinstance(blob, int) else len(blob)
        return built[addr - BASE: addr - BASE + n]

    checks = [
        ('hook A', at(SD_INSERT, 7), b46.jmp_bytes(SD_INSERT, SD_STUB) + b'\x00\x00'),
        ('hook B', at(SU_INSERT, 5), b46.jmp_bytes(SU_INSERT, SU_STUB)),
        ('hook C', at(MX_INSERT, 5), b46.jmp_bytes(MX_INSERT, MX_STUB)),
        ('SD 桩', at(SD_STUB, len(info['sd'])), info['sd']),
        ('SU 桩', at(SU_STUB, len(info['su'])), info['su']),
        ('MX 桩', at(MX_STUB, len(info['mx'])), info['mx']),
        ('tone 指针表', at(PTRTAB, len(info['ptr'])), info['ptr']),
        ('矩阵指针表', at(MX_PTRTAB, len(info['mxptr'])), info['mxptr']),
        ('stash 初值', at(STASH, 4), b'\x00\x00\x00\x00'),
    ]
    for i, (name, flat, blob) in enumerate(info['blobs']):
        checks.append(('矩阵 blk%d' % i, at(MX_DATA + i * 18, 18), blob))
    for s in SLOTS:
        checks.append(('tone 槽 %d' % s, at(TONES + s * SLOT, SLOT), info['tone']))
    for name, got, want in checks:
        good = got == want
        ok &= good
        print('  %-12s 一致=%s' % (name, good))

    m0 = at(MATRIX_M0, 18)
    m0_same = m0 == stock[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18]
    print('  原厂 m0 未动=%s' % m0_same)
    ok &= m0_same

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'sd_stub': SD_STUB, 'sd_len': len(info['sd']), 'su_stub': SU_STUB,
           'su_len': len(info['su']), 'mx_stub': MX_STUB, 'mx_len': len(info['mx']),
           'stash': STASH, 'ptrtab': PTRTAB, 'mx_data': MX_DATA, 'mx_ptrtab': MX_PTRTAB,
           'slots': SLOTS, 'tone_t46': info['tone_vals'][46],
           'matrices': [{'k': i + 1, 'name': n, 'flat': f} for i, (n, f, _b) in enumerate(info['blobs'])],
           'ranges': info['ranges']}
    with io.open('.tmp/b51_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('  布局表 .tmp/b51_layout.json')
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
