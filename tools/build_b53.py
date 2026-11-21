#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b53.py —— B53：tone 表搬进 bank2，池区一个字节都不动（只离线）

B51 真机结果（父代理实测）：矩阵按档生效、EXIF 写 k 成功，但四档全"蒙白"
（比原厂亮约 +128 级、动态范围 197→138）。横比 B22/B24h/B48 后定位到 tone 表**位置**：
B22 用"池里 168 个指针全指一张表"=正常；B24h/B48 写槽 22–25 =正常；
B51 写槽 9/11/12/13 = 全档蒙白。槽 11/12/13 在索引 46 处原厂就分别是 941/947/990
（/1023）—— 它们属于运行期被重算的那几组（dir4–dir6，ART/i-e 复用），
我们写进去的 reala 曲线会被系统重算覆盖成"几乎全白"，与实测吻合。

B53 的改法：**tone 表整体搬出 tone 池**，写进 bank2 `0x605DEF4E + k*0x200`（k=1..4）。
bank2 只在 ctx+0x42F==2 的 3D 模式下被 0x604B8320 读，正常拍照没人碰，也不波及 i-e。

几何
  钩子 A 0x604B811D (7 B = 5 B jmp + 2 B 填充) -> SD 桩 0x605DF9C8（38 B）
      k = ((key>>24)&3)<<3 | (key&7) 写 32 位到 stash 0x605DF9C0；
      顺手把**完整 32 位 key**写进 EXIF 0x61894C94（四个字节二进制）；
      桩尾 mov d2,d1 / cmp 0x1010066,d2 / jmp 0x604B8124（让原 bcc 用同一组标志）
  钩子 B 0x60479A2B (5 B) -> SU 桩 0x605DF9F0
      重放 mov (a0),d0 / mov d2,a1 / add 0x400,d2；k=1..4 查 tone 指针表 0x605DF950
      （指向 bank2 四条）；同一个桩把 "k=N\\0" 写进 0x61894C90（k 不在 1..4 时不动）
      回 0x60479A3A（原厂拷贝循环）/ 0x60479A4E（空指针分支）
  钩子 C 0x604B6AD6 (5 B，原 call 0x604b6a95) -> MX 桩 0x605DFA38
      按 stash 的 k 查矩阵指针表 0x605DF964，返回 a0=矩阵地址；k 不在 1..4 回原厂 m0
      回 0x604B6ADB（原调用点之后；那里 cmp 0x0,a0 判空）
  **tone 池 0x605D8344..0x605DEF44 一个字节都不改**（B51 的桩/stash/表全在池里，这次全部搬出）

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
OUT = 'build/B53_bank2.BIN'

SD_INSERT, SD_LEN = 0x604B811D, 7
SD_STUB, SD_RET = 0x605DF9C8, 0x604B8124

SU_INSERT, SU_LEN = 0x60479A2B, 5
SU_STUB = 0x605DF9F0
SU_RET_LOAD, SU_RET_NULL = 0x60479A3A, 0x60479A4E

MX_INSERT, MX_LEN = 0x604B6AD6, 5
MX_STUB, MX_RET = 0x605DFA38, 0x604B6ADB

EXIF_TEXT = 0x61894C90      # UserComment 文本区：+0x00 "k=N\\0"、+0x04 key、+0x08 k(32 位)
EXIF_KEY = 0x61894C94
EXIF_K = 0x61894C98
PTRTAB = 0x605DF950         # tone 指针表 [_, bank2(k=1)..bank2(k=4)]
MX_PTRTAB = 0x605DF964      # 矩阵指针表 [m0, blk1..blk4]
MX_DATA = 0x605DF978        # 4 x 18 B 矩阵

BANK2 = 0x605DEF4E          # bank2 起点（3D 模式那张表的基址）
BSTRIDE = 0x200
BANK2_SLOTS = [BANK2 + k * BSTRIDE for k in (1, 2, 3, 4)]   # 0x605DF14E ... 0x605DF74E

POOL = (0x605D8344, 0x605DEF44)     # tone 池：一个字节都不许改
WINDOW = (BANK2, 0x605DFB4E)        # 本包所有落点（bank2 内，池外）
MATRIX_M0 = 0x605ECECA

# EXIF: X = 0x618943FC；UserComment(0x9286) 文本区 = X+0x894（125 B）
#   +0x00 = "k=N\0"    （SU 桩，仅 k=1..4）
#   +0x04 = 完整 32 位 key 小端（SD 桩，任何 key 都写）
#   +0x08 = k 的 32 位值（SD 桩；SU/MX 桩从这里读——不再需要镜像里的 stash）
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
\tasr\t21,d0
\tand\t0x18,d0
\tmov\td2,d3
\tand\t7,d3
\tor\td3,d0
\tmov\t0x%X,a0
\tmov\td2,(a0)
\tmov\td0,(4,a0)
\tcmp\t0x1010066,d2
\tjmp\t-0x100000
""" % EXIF_KEY

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
""" % (EXIF_K, EXIF_TEXT, PTRTAB)

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
""" % (EXIF_K, MX_PTRTAB, MATRIX_M0)

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


def check_layout(items):
    """三条硬规定：不得碰 tone 池、全部落在 bank2 窗口、两两不重叠并 4 对齐。"""
    lo, hi = WINDOW
    plo, phi = POOL
    s = sorted(items)
    for i, (a, ln, name) in enumerate(s):
        if a < hi and a + ln > plo and a < phi:
            if not (a + ln <= plo or a >= phi):
                raise SystemExit('%s 0x%x..0x%x 压到 tone 池 %#x..%#x' % (name, a, a + ln, plo, phi))
        if a < lo or a + ln > hi:
            raise SystemExit('%s 0x%x..0x%x 越界（窗口 %#x..%#x）' % (name, a, a + ln, lo, hi))
        if i and s[i - 1][0] + s[i - 1][1] > a:
            raise SystemExit('%s 与 %s 重叠' % (name, s[i - 1][2]))


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def build():
    sd = bytearray(b46.assemble(SD_TMPL, 'b53_sd', SD_STUB))
    off = len(sd) - 5
    sd[off + 1:off + 5] = ((SD_RET - (SD_STUB + off)) & 0xFFFFFFFF).to_bytes(4, 'little')
    sd = bytes(sd)
    if len(sd) > 40:
        raise SystemExit('SD 桩 %d B 太长（<=40）' % len(sd))
    if sd[off:off + 5] != b'\xdc' + struct.pack('<i', SD_RET - (SD_STUB + off)):
        raise SystemExit('SD 桩 jmp 自检失败')

    su1 = b46.assemble(SU_TMPL.replace('@@J1@@', '0x%X' % PH1)
                       .replace('@@J2@@', '0x%X' % PH2), 'b53_su_ph', SU_STUB)
    i1 = b46.find_ph(su1, PH1)
    i2 = b46.find_ph(su1, PH2)
    su = b46.assemble(SU_TMPL
                      .replace('@@J1@@', '0x%x - (0x%x + 0x%x)' % (SU_RET_LOAD, SU_STUB, i1))
                      .replace('@@J2@@', '0x%x - (0x%x + 0x%x)' % (SU_RET_NULL, SU_STUB, i2)),
                      'b53_su', SU_STUB)
    if len(su) > 0x50:
        raise SystemExit('SU 桩 %d B 太长（<=0x50）' % len(su))
    for off_, want in ((i1, SU_RET_LOAD), (i2, SU_RET_NULL)):
        got = su[off_:off_ + 5]
        exp = b'\xdc' + struct.pack('<i', want - (SU_STUB + off_))
        if got != exp:
            raise SystemExit('SU 桩 jmp 自检失败 @%#x' % (SU_STUB + off_))

    mx1 = b46.assemble(MX_TMPL.replace('@@JRET@@', '0x%X' % PH1), 'b53_mx_ph', MX_STUB)
    j = b46.find_ph(mx1, PH1)
    mx = b46.assemble(MX_TMPL.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (MX_RET, MX_STUB, j)),
                      'b53_mx', MX_STUB)
    if len(mx) > 0x40:
        raise SystemExit('MX 桩 %d B 太长（<=0x40）' % len(mx))
    if mx[j:j + 5] != b'\xdc' + struct.pack('<i', MX_RET - (MX_STUB + j)):
        raise SystemExit('MX 桩 jmp 自检失败 @%#x' % (MX_STUB + j))

    tone_vals, tone = load_tone()
    blobs = mx_blobs()
    ptr = struct.pack('<5I', 0, *BANK2_SLOTS)
    mxptr = struct.pack('<5I', MATRIX_M0, *[MX_DATA + i * 18 for i in range(4)])

    items = [(SD_STUB, len(sd), 'SD 桩'),
             (SU_STUB, len(su), 'SU 桩'),
             (MX_STUB, len(mx), 'MX 桩'),
             (PTRTAB, len(ptr), 'tone 指针表'),
             (MX_PTRTAB, len(mxptr), '矩阵指针表'),
             (MX_DATA, 72, '矩阵数据')]
    items += [(BANK2_SLOTS[i], 0x200, 'bank2 k=%d' % (i + 1)) for i in range(4)]
    check_layout(items)
    for a, ln, name in ((PTRTAB, len(ptr), 'tone 指针表'), (MX_PTRTAB, len(mxptr), '矩阵指针表'),
                        (MX_DATA, 72, '矩阵数据'),
                        (SD_STUB, len(sd), 'SD 桩'), (SU_STUB, len(su), 'SU 桩'),
                        (MX_STUB, len(mx), 'MX 桩')):
        if a % 4:
            raise SystemExit('%s 0x%x 未 4 对齐' % (name, a))

    ranges = [(SD_INSERT, SD_LEN), (SU_INSERT, SU_LEN), (MX_INSERT, MX_LEN),
              (SD_STUB, len(sd)), (SU_STUB, len(su)), (MX_STUB, len(mx)),
              (PTRTAB, len(ptr)), (MX_DATA, 72), (MX_PTRTAB, len(mxptr))]
    ranges += [(a, 0x200) for a in BANK2_SLOTS]
    ranges = sorted(ranges)

    lines = ['# B53 build from stock: tone tables moved to bank2, tone pool untouched (offline)',
             'ram 0x%x=%s  # hook A 0x604B811D -> SD stub' % (SD_INSERT, (b46.jmp_bytes(SD_INSERT, SD_STUB) + b'\x00\x00').hex()),
             'ram 0x%x=%s  # hook B 0x60479A2B -> SU stub' % (SU_INSERT, b46.jmp_bytes(SU_INSERT, SU_STUB).hex()),
             'ram 0x%x=%s  # hook C 0x604B6AD6 -> MX stub' % (MX_INSERT, b46.jmp_bytes(MX_INSERT, MX_STUB).hex()),
             'ram 0x%x=%s  # SD stub %d B (stash k + EXIF key)' % (SD_STUB, sd.hex(), len(sd)),
             'ram 0x%x=%s  # SU stub %d B (tone ptr table -> bank2, EXIF k text)' % (SU_STUB, su.hex(), len(su)),
             'ram 0x%x=%s  # MX stub %d B' % (MX_STUB, mx.hex(), len(mx)),
             'ram 0x%x=%s  # tone ptr table [_, bank2 k=1..4]' % (PTRTAB, ptr.hex()),
             'ram 0x%x=%s  # matrix ptr table indexed by k [m0,blk1..4]' % (MX_PTRTAB, mxptr.hex())]
    for i, a in enumerate(BANK2_SLOTS):
        lines.append('ram 0x%x=%s  # bank2 tone k=%d (reala retargeted)' % (a, tone.hex(), i + 1))
    for i, (name, flat, blob) in enumerate(blobs):
        lines.append('ram 0x%x=%s  # matrix k=%d %s %r' % (MX_DATA + i * 18, blob.hex(), i + 1, name, flat))
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/b53.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(sd=sd, su=su, mx=mx, tone=tone, tone_vals=tone_vals, blobs=blobs,
                ptr=ptr, mxptr=mxptr, ranges=ranges, patch=pf)


def main():
    info = build()
    print('SD 桩 %d B @%#x ; SU 桩 %d B @%#x ; MX 桩 %d B @%#x'
          % (len(info['sd']), SD_STUB, len(info['su']), SU_STUB, len(info['mx']), MX_STUB))
    print('bank2 tone 四条：%s（同一张 reala 曲线，T46=%d）'
          % (['0x%X' % a for a in BANK2_SLOTS], info['tone_vals'][46]))
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

    # 硬规定：tone 池一个字节都不许改
    plo, phi = POOL
    pool_bad = [i for i in range(plo - BASE, phi - BASE) if built[i] != stock[i]]
    print('  tone 池 0x%X..0x%X 改动 %d 字节 -> %s'
          % (plo, phi, len(pool_bad), 'OK' if not pool_bad else 'FAIL'))
    ok &= not pool_bad

    def at(addr, n):
        return bytes(built[addr - BASE: addr - BASE + n])

    for name, got, want in (('hook A', at(SD_INSERT, 7), b46.jmp_bytes(SD_INSERT, SD_STUB) + b'\x00\x00'),
                            ('hook B', at(SU_INSERT, 5), b46.jmp_bytes(SU_INSERT, SU_STUB)),
                            ('hook C', at(MX_INSERT, 5), b46.jmp_bytes(MX_INSERT, MX_STUB)),
                            ('SD 桩', at(SD_STUB, len(info['sd'])), info['sd']),
                            ('SU 桩', at(SU_STUB, len(info['su'])), info['su']),
                            ('MX 桩', at(MX_STUB, len(info['mx'])), info['mx']),
                            ('tone 指针表', at(PTRTAB, len(info['ptr'])), info['ptr']),
                            ('矩阵指针表', at(MX_PTRTAB, len(info['mxptr'])), info['mxptr'])):
        good = got == want
        ok &= good
        print('  %-12s 一致=%s' % (name, good))
    for i, a in enumerate(BANK2_SLOTS):
        good = at(a, 0x200) == info['tone']
        ok &= good
        print('  bank2 k=%d   表一致=%s' % (i + 1, good))
    m0_ok = at(MATRIX_M0, 18) == bytes(stock[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18])
    print('  原厂 m0 未动=%s' % m0_ok)
    ok &= m0_ok

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'sd_stub': SD_STUB, 'sd_len': len(info['sd']), 'su_stub': SU_STUB, 'su_len': len(info['su']),
           'mx_stub': MX_STUB, 'mx_len': len(info['mx']), 'ptrtab': PTRTAB,
           'mx_ptrtab': MX_PTRTAB, 'mx_data': MX_DATA, 'bank2_slots': BANK2_SLOTS,
           'exif_text': EXIF_TEXT, 'exif_key': EXIF_KEY, 'exif_k': EXIF_K,
           'pool': POOL, 'ranges': info['ranges']}
    with io.open('.tmp/b53_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
