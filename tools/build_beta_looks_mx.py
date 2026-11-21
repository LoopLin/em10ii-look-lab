#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B34：四套 look 的 tone **和** 3x3 矩阵，在同一条 SU 钩子里按同一个 k 分档。

链路（一个 k，两件事）::

    SD 侧（0x604B8129 -> 0x605DED44）把 k 写进 stash（0x605DEDC0）
    SU 侧（0x60479A27 -> 0x605DEDD0）
        add 0x1230,a0            ; a0 = *(S)+0x1230 = tone 指针槽
        读 stash -> k, 守卫 1..4 ; 不满足 -> jmp 0x60479A2B（原厂路径，含空指针检查）
        slot_map[k] -> tone 槽 22..25 -> a2
        9 个 i16：矩阵数据[k] -> 0x605ECECA（m0，标准档唯一读到的矩阵槽）
        a1=d2, d2+=0x400, d1=(8,SP) -> jmp 0x60479A3A（原厂拷贝循环）

设计要点（为什么矩阵放在槽 53 的空位而不是 m1..m4）：

* `0x605ECECA + 0x12*k` 的 m1..m4 是**原厂在用的**矩阵（m1/m2/m4 值互不相同，
  由 `ctx+0x429` 选档），静态覆盖它们会改掉非标准档的画质；m6 是 identity。
* 槽 53（`0x605DED44..0x605DEF44`）里 36 B 给 SD 桩、1 B stash、SU 桩之后
  还有 ~300 B 空位；四套矩阵 4*0x12 = 72 B 放得下（`0x605DEE60`）。
  槽 53 的已知引用只有 `0x604B8B38`（0xB04/0xB05 图像效果读取），B32 已经接受。
* 矩阵数据块用**同一条 0x12 步长**，和 `0x605ECECA + 0x12*k` 口径一致，
  读文件的人可以把两块表逐档对照。

建包铁律（docs/METHOD-efficiency.md §建包铁律）：
    只从 stock/ 起包；tone/矩阵从 `.tmp/looks_matrix/<look>.json` 来；
    桩从 `.s` 汇编来（本脚本现汇编，并核对与已记录的字节一致）；
    出包只走 `tools/mkpatch.py --patch-file`（注释纯 ASCII）。

用法::

    python tools/build_beta_looks_mx.py                  # 出 build/B34_looks_mx.BIN
    python tools/build_beta_looks_mx.py --dry-run        # 只出 .s/.bin/patch，不建包
    python tools/build_beta_looks_mx.py --verify-only    # 只对已有包跑三道门
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import struct
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

import oly_container as oc  # noqa: E402
from build_beta_look import (  # noqa: E402
    load_spec, matrix_bytes, matrix_flat, retarget_tone, tone_bytes,
)

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/B34_looks_mx.BIN'
PATCH_FILE = '.tmp/b34.patch'

TONES = 0x605D8344           # tone 池（256 x u16，步长 0x200）
LOOK_SLOT0 = 22              # k=1 -> 槽 22
SLOT_MAP = 0x605DB744        # slot_map：u8[0..4] = 0,1,2,3,4（+27 B 0）
SLOT_MAP_LEN = 32
SD_STUB = 0x605DED44         # SD 桩：把 k 写进 stash
SD_SRC, SD_BIN_REF = '.tmp/b28_sd.s', '.tmp/b28_sd.bin'
STASH = 0x605DEDC0
SU_STUB = 0x605DEDD0         # SU 桩：选 tone 表 + 拷矩阵
SU_TMPL, SU_SRC = '.tmp/b34_su.tmpl.s', '.tmp/b34_su.s'
MX_DATA = 0x605DEE60         # 四套矩阵的存放处（槽 53 内空位）
MX_STRIDE = 0x12             # 与 0x605ECECA + 0x12*k 同口径
MATRIX_RAM = 0x605ECECA      # m0：SU 桩在运行期写它
HOOK_SU = 0x60479A27         # SU 插入点（add 0x1230,a0 的地址）
HOOK_SD = 0x604B8129         # SD 插入点
RET_TONE = 0x60479A3A        # 原厂：setlb（拷贝循环头）
RET_GUARD = 0x60479A2C       # 原厂 `mov d2,a1`；0x60479A2B 的 `mov (a0),d0` 被 DC 吃掉了，
#                              回落路径必须自己补这一条（见 .tmp/b34_su.tmpl.s 注释）

LOOKS = ['reala_ace', 'astia', 'eterna', 'bleach_bypass']   # k = 1..4
T46_WANT = [689, 651, 642, 716]
T0_18, T1_18 = 584, 804      # 槽 0/槽 1 的 T[46]，不许动

TC = '.tmp/olya/toolchains/mn10300-elf/bin'
AS = os.path.join(TC, 'mn10300-elf-as.exe')
LD = os.path.join(TC, 'mn10300-elf-ld.exe')
OBJCOPY = os.path.join(TC, 'mn10300-elf-objcopy.exe')


# --------------------------------------------------------------------------
# 汇编
# --------------------------------------------------------------------------
def run(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    if check and r.returncode:
        raise SystemExit('%s failed (%d)\n%s\n%s'
                         % (' '.join(cmd), r.returncode,
                            r.stdout[-2000:], r.stderr[-2000:]))
    return r


def assemble(src_text, tag, base):
    """写 .s -> as -> ld -Ttext base -> objcopy -O binary，返回字节。"""
    s = '.tmp/%s.s' % tag
    o = '.tmp/%s.o' % tag
    elf = '.tmp/%s.elf' % tag
    binp = '.tmp/%s.bin' % tag
    io.open(s, 'w', encoding='utf-8', newline='\n').write(src_text)
    run([AS, '-o', o, s])
    run([LD, '-Ttext', hex(base), '-o', elf, o])
    run([OBJCOPY, '-O', 'binary', elf, binp])
    with open(binp, 'rb') as f:
        return f.read()


def jmp_bytes(site, target):
    """`DC + disp32`，基准 = 指令起始地址（MN10300 的 jmp 就是 PC 相对）。"""
    return b'\xdc' + struct.pack('<i', target - site)


SD_RET = 0x604B812F         # 原厂 `bls`：被钩掉的 cmp（0x604B8129，6 B）之后那条
SD_TMPL = '.tmp/b34_sd.tmpl.s'


def assemble_with_jmp(tmpl_path, tag, base, placeholder, target):
    """两趟汇编：先量出 jmp 指令偏移，再把 `@@JRET@@` 换成常数位移。"""
    with io.open(tmpl_path, encoding='utf-8') as f:
        tmpl = f.read()
    b1 = assemble(tmpl.replace('@@JRET@@', placeholder), tag + '_p1', base)
    tok = struct.pack('<I', int(placeholder, 16))
    i = b1.find(tok)
    if i < 0:
        raise SystemExit('占位常量 %s 没编出来（%s）' % (placeholder, tmpl_path))
    off = i - 1                 # 占位字节从 DC 之后开始 -> 指令起始 = i-1
    text = tmpl.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (target, base, off))
    blob = assemble(text, tag, base)
    want = b'\xdc' + struct.pack('<i', target - (base + off))
    if blob[off:off + 5] != want:
        raise SystemExit('jmp 自检失败（%s）：%s != %s'
                         % (tmpl_path, blob[off:off + 5].hex(), want.hex()))
    return blob, off


def build_sd_stub():
    """SD 侧桩：把 key 压成 k 写进 stash，然后回到原厂 `bls`。

    B32/B33 在这里埋着一个雷：那边 36 B 的收尾 jmp 是 b24g 的占位
    `jmp -0x100000`（`.tmp/b24g_sd.s` 里写着"占位：构建时改写最后 4 字节"），
    实际落点 = 0x604DED63（另一段无关代码的中间），跳过去必崩。
    B34 把同一段指令体（前 31 B 逐字节相同）的收尾 jmp 正确落回 0x604B812F
    ——即 b24f_sd.s 当初写明的 `jmp 0x604B812F - (0x605DED44 + 0x1E)`。
    """
    stub, off = assemble_with_jmp(SD_TMPL, 'b34_sd', SD_STUB,
                                  '0x7EAD0003', SD_RET)
    with open(SD_BIN_REF, 'rb') as f:
        ref = f.read()
    if len(stub) != len(ref):
        raise SystemExit('SD 桩长度 %d != B32 记录 %d' % (len(stub), len(ref)))
    if stub[:off] != ref[:off]:
        raise SystemExit('SD 桩指令体与 B32 记录不一致')
    return stub, off, ref


def build_su_stub():
    """两趟汇编：第一趟量出两条 jmp 的偏移，第二趟用常数位移重汇编。

    坑：gas 对 `jmp <常量表达式>` 不做 PC 相对换算（把操作数当原样 disp32），
    而 `jmp <目标> - .` / `jmp <目标> - Llabel` 会报
    `Error: redefined symbol cannot be used on reloc`。所以只能量偏移、
    再写成 `0x目标 - (0x桩基址 + 0x偏移)` 这种纯常数表达式（b30_su.s 同法）。
    """
    with io.open(SU_TMPL, encoding='utf-8') as f:
        tmpl = f.read()
    ph3a, ph2b = '0x7EAD0001', '0x7EAD0002'
    p1 = tmpl.replace('@@J3A@@', ph3a).replace('@@J2B@@', ph2b)
    b1 = assemble(p1, 'b34_su_p1', SU_STUB)
    off = {}
    for tag, tok in (('@@J3A@@', '0100ad7e'), ('@@J2B@@', '0200ad7e')):
        i = b1.find(bytes.fromhex(tok))
        if i < 0:
            raise SystemExit('占位常量 %s 没编出来' % tag)
        off[tag] = i - 1        # 占位字节从 DC 之后开始 -> 指令起始 = i-1
    text = tmpl
    for tag, target in (('@@J3A@@', RET_TONE), ('@@J2B@@', RET_GUARD)):
        text = text.replace(tag, '0x%x - (0x%x + 0x%x)'
                            % (target, SU_STUB, off[tag]))
    io.open(SU_SRC, 'w', encoding='utf-8', newline='\n').write(text)
    stub = assemble(text, 'b34_su', SU_STUB)
    # 自检：两条 jmp 的机器码必须是 DC + (目标-指令起始)
    for tag, target in (('@@J3A@@', RET_TONE), ('@@J2B@@', RET_GUARD)):
        got = stub[off[tag]:off[tag] + 5]
        want = b'\xdc' + struct.pack('<i', target - (SU_STUB + off[tag]))
        if got != want:
            raise SystemExit('jmp 自检失败 %s: %s != %s'
                             % (tag, got.hex(), want.hex()))
    return stub, off


def disasm(data, base, limit=None):
    """用仓库自带的反汇编器把一段字节打印成清单（取指令边界）。"""
    import mn10300_dis as dis
    dec = dis.Decoder()
    out = []
    off = 0
    n = len(data) if limit is None else min(limit, len(data))
    for pc, nb, raw, mnem, ops, _rule in dec.disasm(data, off, n, base):
        out.append(dis.format_line(pc, raw, mnem, ops))
        if pc + nb >= base + n and n != len(data):
            break
    return out


# --------------------------------------------------------------------------
# 表
# --------------------------------------------------------------------------
def tables():
    """按 k=1..4 取 tone / 矩阵（tone 走 B22 同一套重采样，矩阵走同一套偶化）。"""
    tone, mx, flats = {}, {}, {}
    for i, look in enumerate(LOOKS, 1):
        spec = load_spec(look)
        tone[i] = tone_bytes(retarget_tone(spec['tone']))
        flats[i] = matrix_flat(spec)
        mx[i] = matrix_bytes(spec)
        if len(mx[i]) != 18:
            raise SystemExit('矩阵字节数不为 18')
    return tone, mx, flats


def declared_ranges(sd_len, su_len):
    """本包声明改动的全部区间（区外必须 0 字节）。"""
    rng = [
        (HOOK_SU, 5),
        (HOOK_SD, 5),
        (SLOT_MAP, SLOT_MAP_LEN),
        (SD_STUB, sd_len),
        (STASH, 1),
        (SU_STUB, su_len),
        (MX_DATA, MX_STRIDE * 4),
    ]
    for i in range(4):
        rng.append((TONES + (LOOK_SLOT0 + i) * 0x200, 0x200))
    return sorted(rng)


# --------------------------------------------------------------------------
# 建包
# --------------------------------------------------------------------------
def make_patch(tone, mx, sd_stub, su_stub):
    mxblob = b''.join(mx[i] for i in (1, 2, 3, 4))
    if len(mxblob) != MX_STRIDE * 4:
        raise SystemExit('矩阵块长度不对：%d' % len(mxblob))
    lines = ['# B34: per-k look = tone slot + 3x3 matrix in one SU hook',
             '# k is written by the SD stub into stash 0x605dedc0 (1..4).',
             '# hook sites: SU 0x60479a27 -> 0x605dedd0, SD 0x604b8129 -> 0x605ded44',
             'ram 0x%x=%s' % (HOOK_SU, jmp_bytes(HOOK_SU, SU_STUB).hex()),
             'ram 0x%x=%s' % (HOOK_SD, jmp_bytes(HOOK_SD, SD_STUB).hex())]
    for i in (1, 2, 3, 4):
        lines.append('ram 0x%x=%s  # tone k=%d %s (slot %d)'
                     % (TONES + (LOOK_SLOT0 + i - 1) * 0x200, tone[i].hex(),
                        i, LOOKS[i - 1], LOOK_SLOT0 + i - 1))
    lines.append('ram 0x%x=%s  # slot_map 0,1,2,3,4'
                 % (SLOT_MAP, (bytes([0, 1, 2, 3, 4]) + bytes(27)).hex()))
    lines.append('ram 0x%x=%s  # SD stub %d B' % (SD_STUB, sd_stub.hex(), len(sd_stub)))
    lines.append('ram 0x%x=ff  # stash init (0xff = stub not executed yet)' % STASH)
    lines.append('ram 0x%x=%s  # SU stub %d B' % (SU_STUB, su_stub.hex(), len(su_stub)))
    lines.append('ram 0x%x=%s  # matrices k=1..4, stride 0x%x'
                 % (MX_DATA, mxblob.hex(), MX_STRIDE))
    body = '\n'.join(lines) + '\n'
    try:
        body.encode('ascii')
    except UnicodeEncodeError:
        raise SystemExit('patch 文件里有非 ASCII 字符（mkpatch 注释只许 ASCII）')
    io.open(PATCH_FILE, 'w', encoding='utf-8', newline='\n').write(body)
    return mxblob


def build():
    tone, mx, flats = tables()
    print('looks: ' + ', '.join('%d=%s' % (i, LOOKS[i - 1]) for i in (1, 2, 3, 4)))
    for i in (1, 2, 3, 4):
        t = tone[i]
        print('  k=%d %-13s T[46]=%3d T[22]=%3d T[0]=%3d T[255]=%4d  m0=%s'
              % (i, LOOKS[i - 1], int.from_bytes(t[92:94], 'little'),
                 int.from_bytes(t[44:46], 'little'),
                 int.from_bytes(t[0:2], 'little'),
                 int.from_bytes(t[510:512], 'little'), flats[i]))

    # SD 桩：从 .s 现汇编（两趟定 jmp），指令体必须与 B32 记录的 31 B 对得上
    sd_stub, sd_off, sd_ref = build_sd_stub()
    b32_target = SD_STUB + sd_off + struct.unpack('<i', sd_ref[sd_off + 1:sd_off + 5])[0]
    print('  SD 桩 %d B（jmp @+%#x）；B32 记录的收尾 jmp 落点 = %#x（占位未改写），'
          '本包 -> %#x' % (len(sd_stub), sd_off, b32_target, SD_RET))
    print('    指令体前 %d B 与 %s 逐字节一致' % (sd_off, SD_BIN_REF))

    su_stub, jmp_off = build_su_stub()
    print('  SU 桩 %d B（jmp 偏移 %s），上限 150 B / 槽尾余量 %d B'
          % (len(su_stub), {k: hex(v) for k, v in jmp_off.items()},
             0x605DEF44 - SU_STUB))
    if SU_STUB + len(su_stub) > MX_DATA:
        raise SystemExit('SU 桩 %d B 会压到矩阵块 0x%x' % (len(su_stub), MX_DATA))
    if len(su_stub) > 150:
        raise SystemExit('SU 桩超过 150 B')
    if MX_DATA + MX_STRIDE * 4 > 0x605DEF44:
        raise SystemExit('矩阵块越出槽 53')

    mxblob = make_patch(tone, mx, sd_stub, su_stub)
    print('  patch:', PATCH_FILE)
    for a, l in declared_ranges(len(sd_stub), len(su_stub)):
        print('    声明区间 %#x + %d' % (a, l))
    return dict(tone=tone, mx=mx, mxblob=mxblob, sd=sd_stub, su=su_stub,
                su_off=jmp_off, flats=flats)


def run_mkpatch(out):
    if os.path.exists(out):
        raise SystemExit('拒绝覆盖已有文件：%s' % out)
    r = run([sys.executable, 'tools/mkpatch.py', SRC, '--out', out,
             '--patch-file', PATCH_FILE])
    print(r.stdout.strip()[-1500:])


# --------------------------------------------------------------------------
# 验证
# --------------------------------------------------------------------------
def read_payload(path):
    with open(path, 'rb') as f:
        blocks = oc.parse(f.read())
    return blocks


def payload_of(path):
    blocks = read_payload(path)
    return blocks[0].payload, blocks


def diff_runs(a, b):
    """逐字节 diff，返回 (runs, outside) —— runs 把相邻差异并成段。"""
    runs = []
    i = 0
    n = min(len(a), len(b))
    diff = [i for i in range(n) if a[i] != b[i]]
    if len(a) != len(b):
        raise SystemExit('两个 payload 长度不同：%d / %d' % (len(a), len(b)))
    for i in diff:
        if runs and i - (runs[-1][0] + runs[-1][1]) <= 0:
            runs[-1][1] += 1
        else:
            runs.append([i, 1])
    return runs, diff


def inside(addr, ranges):
    return any(a <= addr < a + l for a, l in ranges)


def verify(path, info, gate=True):
    ok = True
    stock, _ = payload_of(SRC)
    built, blocks = payload_of(path)
    su, sd = info['su'], info['sd']
    ranges = declared_ranges(len(sd), len(su))

    print('\n== 门 1：容器 diff 全部落在声明区间 ==')
    blocks_stock = read_payload(SRC)
    runs_all, diff_all, outside_all = [], 0, []
    for bs, bb in zip(blocks_stock, blocks):
        if bs.payload == bb.payload:
            print('  块 %d payload %d B：未改动' % (bs.index, len(bb.payload)))
            continue
        runs, diff = diff_runs(bs.payload, bb.payload)
        outside = [i for i in diff if not inside(BASE + i, ranges)]
        print('  块 %d payload %d B：差异 %d 字节 / %d 段，区外 %d 字节'
              % (bs.index, len(bb.payload), len(diff), len(runs), len(outside)))
        runs_all += runs
        diff_all += len(diff)
        outside_all += outside
    if len(blocks_stock) != len(blocks):
        print('  块数不同：%d vs %d' % (len(blocks_stock), len(blocks)))
        ok = False
    print('  合计差异 %d 字节 / %d 段；区外 %d 字节 -> %s'
          % (diff_all, len(runs_all), len(outside_all),
             'OK' if not outside_all else 'FAIL'))
    for a, l in ranges:
        n = sum(1 for i in range(a - BASE, a - BASE + l)
                if 0 <= i < len(built) and stock[i] != built[i])
        print('    声明区间 %#x + %d：改动 %d 字节' % (a, l, n))
    if outside_all:
        ok = False
        for i in outside_all[:10]:
            print('    区外 %#x: %02x -> %02x' % (BASE + i, stock[i], built[i]))

    print('\n== 门 2：回读（tone / 矩阵 / slot_map / 钩子 / 桩）==')
    for i in (1, 2, 3, 4):
        a = TONES + (LOOK_SLOT0 + i - 1) * 0x200
        got = built[a - BASE:a - BASE + 0x200]
        t46 = int.from_bytes(got[92:94], 'little')
        good = got == info['tone'][i] and t46 == T46_WANT[i - 1]
        print('  k=%d slot %d  T[46]=%d（期望 %d）逐字节一致=%s -> %s'
              % (i, LOOK_SLOT0 + i - 1, t46, T46_WANT[i - 1],
                 got == info['tone'][i], 'OK' if good else 'FAIL'))
        ok &= good
    t0 = int.from_bytes(built[TONES - BASE + 92:TONES - BASE + 94], 'little')
    t1a = TONES + 0x200
    t1 = int.from_bytes(built[t1a - BASE + 92:t1a - BASE + 94], 'little')
    print('  原厂 tone 槽 0 T[46]=%d（期望 %d）、槽 1 T[46]=%d（期望 %d）-> %s'
          % (t0, T0_18, t1, T1_18, 'OK' if (t0, t1) == (T0_18, T1_18) else 'FAIL'))
    ok &= (t0, t1) == (T0_18, T1_18)

    got_map = built[SLOT_MAP - BASE:SLOT_MAP - BASE + SLOT_MAP_LEN]
    print('  slot_map = %s（期望 0001020304 + 27*00）-> %s'
          % (got_map.hex(), 'OK' if got_map == bytes([0, 1, 2, 3, 4]) + bytes(27) else 'FAIL'))
    ok &= got_map == bytes([0, 1, 2, 3, 4]) + bytes(27)

    for i in (1, 2, 3, 4):
        a = MX_DATA + MX_STRIDE * (i - 1)
        got = built[a - BASE:a - BASE + MX_STRIDE]
        good = got == info['mx'][i]
        print('  矩阵 k=%d @%#x = %s -> %s'
              % (i, a, info['flats'][i], 'OK' if good else 'FAIL'))
        ok &= good
    got_m0 = built[MATRIX_RAM - BASE:MATRIX_RAM - BASE + 18]
    stock_m0 = stock[MATRIX_RAM - BASE:MATRIX_RAM - BASE + 18]
    print('  原厂 m0 未被静态改动 = %s（%s）' % (got_m0 == stock_m0,
                                          '、'.join(str(int.from_bytes(got_m0[k:k + 2], 'little', signed=True))
                                                   for k in range(0, 18, 2))))

    got_hook_su = built[HOOK_SU - BASE:HOOK_SU - BASE + 5]
    got_hook_sd = built[HOOK_SD - BASE:HOOK_SD - BASE + 5]
    good = (got_hook_su == jmp_bytes(HOOK_SU, SU_STUB)
            and got_hook_sd == jmp_bytes(HOOK_SD, SD_STUB))
    print('  钩子 SU %#x -> %#x = %s；SD %#x -> %#x = %s -> %s'
          % (HOOK_SU, SU_STUB, got_hook_su.hex(), HOOK_SD, SD_STUB,
             got_hook_sd.hex(), 'OK' if good else 'FAIL'))
    ok &= good
    for name, addr, blob in (('SD 桩', SD_STUB, sd), ('SU 桩', SU_STUB, su)):
        got = built[addr - BASE:addr - BASE + len(blob)]
        print('  %s @%#x %d B 逐字节一致 = %s' % (name, addr, len(blob), got == blob))
        ok &= got == blob
    stash = built[STASH - BASE]
    print('  stash @%#x = %#x（期望 0xff）-> %s'
          % (STASH, stash, 'OK' if stash == 0xff else 'FAIL'))
    ok &= stash == 0xff

    print('\n== SU 桩反汇编（@%#x，%d B）==' % (SU_STUB, len(su)))
    for line in disasm(su, SU_STUB):
        print(line)
    print('\n== SD 桩反汇编（@%#x，%d B）==' % (SD_STUB, len(sd)))
    for line in disasm(sd, SD_STUB):
        print(line)
    return ok


def verify_tools(path):
    """aufit 是硬门（必须 [OK]）；patch_check --reach 是**参考门**，结果照实打印。

    --reach 的模型是"从入口到插入点必须有一条不绕过插入点的线性路径"，
    适用于无条件钩子；B34 的两条桩都是条件钩子（key 分档 / tone 指针非零），
    所以它照例会报几条"可以跳过"。这些不是本包的失败，而是要写进文档的判读。
    """
    print('\n== 门 3：仓库自带工具 ==')
    log = []
    r = run([sys.executable, 'tools/audit_beta_pack.py', path])
    print(r.stdout.strip())
    log.append(r.stdout.strip())
    good = '[OK' in r.stdout
    for entry, insert in (('0x604B8098', '0x604B8129'), ('0x60479960', '0x60479A27')):
        r = run([sys.executable, 'tools/patch_check.py', '--reach', entry, insert],
                check=False)
        out = r.stdout.strip()
        print(out)
        log.append(out)
        n_ok = out.count('[ok  ]')
        n_fail = out.count('[FAIL]')
        print('    判读：%s → 入口 %s 到插入点 %s：%d 条不绕过 / %d 条可跳过（退出码 %d）'
              % ('通过' if r.returncode == 0 else '非零', entry, insert,
                 n_ok, n_fail, r.returncode))
        log.append('判读：%d 条不绕过 / %d 条可跳过（退出码 %d）'
                   % (n_ok, n_fail, r.returncode))
    io.open('.tmp/b34_gate3.txt', 'w', encoding='utf-8', newline='\n').write(
        '\n\n'.join(log) + '\n')
    print('    （完整输出另存 .tmp/b34_gate3.txt）')
    return good


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--dry-run', action='store_true', help='只出桩/patch，不建包')
    ap.add_argument('--verify-only', action='store_true', help='只验已有包')
    ap.add_argument('--no-tools', action='store_true', help='跳过 audit/patch_check')
    args = ap.parse_args(argv)

    if args.verify_only:
        info = build()
        ok = verify(args.out, info)
        if not args.no_tools:
            ok &= verify_tools(args.out)
        print('\nVERDICT', 'PASS' if ok else 'FAIL')
        return 0 if ok else 1

    info = build()
    if args.dry_run:
        print('\n(dry-run：桩与 patch 已出，未建包)')
        return 0
    run_mkpatch(args.out)
    ok = verify(args.out, info)
    if not args.no_tools:
        ok &= verify_tools(args.out)
    with open(args.out, 'rb') as f:
        blob = f.read()
    print('\n包：%s  %d B  sha256=%s'
          % (args.out, len(blob), hashlib.sha256(blob).hexdigest()[:16]))
    if os.path.getsize(args.out) != os.path.getsize(SRC):
        print('大小与源不一致！')
        ok = False
    print('VERDICT', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
