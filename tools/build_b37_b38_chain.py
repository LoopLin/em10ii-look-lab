#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b37_b38_chain.py —— B37 / B38：按 k 选 tone 表（B38 再加每档 3x3 矩阵）的离线闭环包。

链路（两个钩子、一个 k）::

    派发器 0x604B8098 返回之后，0x604AA228 里 0x604AA242 处 ctx+8/9/0x0A 仍然有效
        （清零点在 0x604AA261..0x604AA278，在插入点之后 0x1F 字节）。

    SD 钩子 0x604AA242（6 B：DC + disp32 + 1 个不可达填充字节）-> 0x605DED44
        k = ((u8(ctx+8) & 3) << 3) | (u16(ctx+0x0A) & 7)      ; k ∈ 0..31
        把 k 写进 stash 0x605DEDC0
        重放被吃掉的 clr d0 / mov d0,(4,a2) / mov a3,(20,SP)
        jmp 0x604AA248

    SU 钩子 0x60479960（上传器入口，5 B 整条：原 mov a0,(8,SP) / mov a1,(12,SP) / mov a1,a2）
        -> 0x605DEDD0
        读 stash；k ∈ 1..4 -> *(S+0x1230) = 0x605D8344 + (21+k)*0x200  （槽 22..25）
        k 不在 1..4 -> 一个字节都不写（原厂路径，tone 指针仍指原厂表）
        B38 另外把第 k 档的 9 个 i16 拷进 m0 = 0x605ECECA（Q8 色调矩阵）
        重放三条被吃掉的指令，jmp 0x60479965

为什么把 SU 侧从 0x60479A27 挪到上传器入口：

* 0x60479A27 会被 `0x60479A0A beq 0x60479A59` 绕过去 —— 当 *(S+0x420) == 0 时
  tone 走 0x60479A59 那条"只拷 tone"的路。两条路都从 *(S+0x1230) 取值，
  所以在入口把这个指针改掉，一处覆盖两路；`patch_check --reach` 也就真的能过
  （0x60479A27 处它必然报 "可以跳过"）。
* 0x604AA242 在派发器**返回之后**，不再受派发器 [0x01010066, 0x02000096] 的守卫
  约束（B32 的结构性冲突：真机的 k=1..4 要求 b0&3 == 0 ⇒ key ≥ 0x04xxxxxx，
  必然落到 0x604B8320 默认分支，永远到不了 0x604B8129）。

建包铁律（docs/METHOD-efficiency.md）：只从 stock/ 起包；桩用 mn10300-elf-as 现汇编
（两趟定 jmp 位移，不手写字节）；出包只走 tools/mkpatch.py --patch-file（注释纯 ASCII）。

用法::

    python tools/build_b37_b38_chain.py                  # 出 B37 + B38
    python tools/build_b37_b38_chain.py --variant b37     # 只出 B37
    python tools/build_b37_b38_chain.py --dry-run         # 只出 .s / patch，不建包
    python tools/build_b37_b38_chain.py --verify-only     # 只对已有包跑门
    python tools/build_b37_b38_chain.py --no-tools        # 跳过 audit / patch_check
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

TONES = 0x605D8344           # tone 池：256 x u16，槽步长 0x200
LOOK_SLOT0 = 22              # k=1 -> 槽 22（表号 = 21 + k）
LOOKS = ['reala_ace', 'astia', 'eterna', 'bleach_bypass']   # k = 1..4

HOOK_SD = 0x604AA242         # SD 插入点（0x604AA228 内，派发器返回之后）
SD_PATCH_LEN = 6             # DC + disp32 + 1 个填充字节
SD_RET = 0x604AA248          # 桩尾回到的原厂指令地址
SD_STUB = 0x605DED44         # SD 桩（槽 53 头部）
STASH = 0x605DEDC0           # 1 B：SD 桩写、SU 桩读

HOOK_SU = 0x60479960         # SU 插入点（上传器入口）
SU_RET = 0x60479965          # 桩尾回到的原厂指令地址
SU_STUB = 0x605DEDD0         # SU 桩

MX_DATA = 0x605DEE60         # B38：四套矩阵（46 = 4 * 0x12 B）
MX_STRIDE = 0x12
MATRIX_RAM = 0x605ECECA      # m0：SU 桩运行期写
SLOT_END = 0x605DEF44        # 槽 53 尾部（0x605DEF4E 处原厂会读一个 dword，不能压）

T0_18, T1_18 = 584, 804      # 槽 0 / 槽 1 的 T[46]，不许动
T46_WANT = [689, 651, 642, 716]   # reala / astia / eterna / bleach（B22/B34 记录）

TC = '.tmp/olya/toolchains/mn10300-elf/bin'
AS = os.path.join(TC, 'mn10300-elf-as.exe')
LD = os.path.join(TC, 'mn10300-elf-ld.exe')
OBJCOPY = os.path.join(TC, 'mn10300-elf-objcopy.exe')

OUT = {'b37': 'build/B37_looks_chain.BIN', 'b38': 'build/B38_looks_mx_chain.BIN'}
PATCH = {'b37': '.tmp/b37.patch', 'b38': '.tmp/b38.patch'}
TAG = {'b37': 'b37', 'b38': 'b38'}

# ---------------------------------------------------------------- 桩源码（ASCII 之外的注释也只在 .s 里）
SD_TMPL = r"""	.am33
	.text
	.globl	_start
_start:
	# entry 0x604AA242 (inside 0x604AA228, after the dispatcher returned):
	#   a3 = ctx, a2 = out struct, a0 = ctx.
	# ctx+8 / ctx+9 / ctx+0x0A still hold the dispatch key (zeroed at 0x604AA261).
	movbu	(8,a3),d0
	and	3,d0
	asl	3,d0
	movhu	(10,a3),d1
	and	7,d1
	or	d1,d0
	mov	0x605DEDC0,a1
	movbu	d0,(a1)
	clr	d0
	mov	d0,(4,a2)
	mov	a3,(20,sp)
	mov	a3,a0
	jmp	@@JRET@@
"""

SU_TMPL = r"""	.am33
	.text
	.globl	_start
_start:
	# entry 0x60479960 (uploader prologue; 5 bytes = mov a0,(8,SP)+mov a1,(12,SP)+mov a1,a2):
	#   a0 = dest, a1 = O.
	add	-0x10,sp
	mov	d0,(0,sp)
	mov	d1,(4,sp)
	mov	d2,(8,sp)
	mov	(12,a1),a3
	cmp	0,a3
	beq	Lrep
	mov	0x605DEDC0,a2
	movbu	(a2),d0
	cmp	1,d0
	bcs	Lrep
	cmp	4,d0
	bhi	Lrep
	add	21,d0
	asl	9,d0
	mov	0x605D8344,a2
	add	d0,a2
	add	0x1230,a3
	mov	a2,(a3)
@@MATRIX@@
Lrep:
	mov	(0,sp),d0
	mov	(4,sp),d1
	mov	(8,sp),d2
	add	0x10,sp
	mov	a0,(8,sp)
	mov	a1,(12,sp)
	mov	a1,a2
	jmp	@@JRET@@
"""

MX_SNIPPET = r"""	mov	0x605DEDC0,a2
	movbu	(a2),d0
	add	-1,d0
	mov	d0,d1
	asl	4,d1
	asl	1,d0
	add	d0,d1
	mov	0x605DEE60,a2
	add	d1,a2
	mov	0x605ECECA,a3
	mov	9,d2
Lmx:
	movhu	(a2),d0
	movhu	d0,(a3)
	add	2,a2
	add	2,a3
	add	-1,d2
	bne	Lmx
"""


# ---------------------------------------------------------------- 汇编
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
    d = '.tmp/b37'
    os.makedirs(d, exist_ok=True)
    s = os.path.join(d, '%s.s' % tag)
    o = os.path.join(d, '%s.o' % tag)
    elf = os.path.join(d, '%s.elf' % tag)
    binp = os.path.join(d, '%s.bin' % tag)
    io.open(s, 'w', encoding='utf-8', newline='\n').write(src_text)
    run([AS, '-o', o, s])
    run([LD, '-Ttext', hex(base), '-o', elf, o])
    run([OBJCOPY, '-O', 'binary', elf, binp])
    with open(binp, 'rb') as f:
        return f.read()


def assemble_with_jmp(tmpl_text, tag, base, placeholder, target):
    """两趟汇编：先量出 jmp 指令偏移，再把占位常量换成纯常数位移表达式。

    gas 对 `jmp <绝对符号>` 会把操作数当原样 disp32（错），而 `jmp target - .`
    会报 redefined symbol，所以只能量偏移 + 常数表达式（B34 同法，见 B34 文档）。
    """
    ph = '0x%X' % placeholder
    b1 = assemble(tmpl_text.replace('@@JRET@@', ph), tag + '_p1', base)
    tok = struct.pack('<I', placeholder)
    i = b1.find(tok)
    if i < 0:
        raise SystemExit('占位常量 %s 没编出来（%s）' % (ph, tag))
    off = i - 1                      # DC 之后紧跟着 disp32 -> 指令起始 = i - 1
    if b1[off] != 0xDC:
        raise SystemExit('%s：偏移 %#x 处不是 DC' % (tag, off))
    text = tmpl_text.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (target, base, off))
    blob = assemble(text, tag, base)
    want = b'\xdc' + struct.pack('<i', target - (base + off))
    if blob[off:off + 5] != want:
        raise SystemExit('jmp 自检失败（%s）：%s != %s'
                         % (tag, blob[off:off + 5].hex(), want.hex()))
    return blob, off


def jmp_bytes(site, target):
    """`DC + disp32`；base = 指令起始地址（MN10300 的远跳是相对指令地址）。"""
    return b'\xdc' + struct.pack('<i', target - site)


def disasm(data, base):
    import mn10300_dis as dis
    dec = dis.Decoder()
    return [dis.format_line(pc, raw, mnem, ops)
            for pc, nb, raw, mnem, ops, _rule in dec.disasm(data, 0, len(data), base)]


# ---------------------------------------------------------------- 表
def tables():
    tone, mx, flats = {}, {}, {}
    for i, look in enumerate(LOOKS, 1):
        spec = load_spec(look)
        tone[i] = tone_bytes(retarget_tone(spec['tone']))
        flats[i] = matrix_flat(spec)
        mx[i] = matrix_bytes(spec)
        if len(mx[i]) != MX_STRIDE:
            raise SystemExit('矩阵字节数不为 %d' % MX_STRIDE)
    return tone, mx, flats


def slot_addr(k):
    return TONES + (LOOK_SLOT0 + k - 1) * 0x200


def declared_ranges(variant, sd_len, su_len):
    rng = [
        (HOOK_SD, SD_PATCH_LEN),
        (HOOK_SU, 5),
        (SD_STUB, sd_len),
        (STASH, 1),
        (SU_STUB, su_len),
    ]
    if variant == 'b38':
        rng.append((MX_DATA, MX_STRIDE * 4))
    for k in (1, 2, 3, 4):
        rng.append((slot_addr(k), 0x200))
    return sorted(rng)


# ---------------------------------------------------------------- patch / 建包
def make_patch(variant, tone, mx, sd_stub, su_stub):
    sd_hook = jmp_bytes(HOOK_SD, SD_STUB) + b'\x00'
    lines = ['# %s: per-k tone table (+ 3x3 matrix for b38) in one k, written at dispatch time'
             % variant.upper(),
             '# SD hook 0x%x (%d B) -> 0x%x ; SU hook 0x%x (5 B) -> 0x%x'
             % (HOOK_SD, SD_PATCH_LEN, SD_STUB, HOOK_SU, SU_STUB),
             '# k = ((u8(ctx+8)&3)<<3) | (u16(ctx+0xA)&7) ; k in 1..4 selects slot 21+k']
    lines.append('ram 0x%x=%s  # SD hook: DC disp32 + 1 filler byte (unreachable)'
                 % (HOOK_SD, sd_hook.hex()))
    lines.append('ram 0x%x=%s  # SU hook: DC disp32'
                 % (HOOK_SU, jmp_bytes(HOOK_SU, SU_STUB).hex()))
    for k in (1, 2, 3, 4):
        lines.append('ram 0x%x=%s  # tone k=%d %s (slot %d)'
                     % (slot_addr(k), tone[k].hex(), k, LOOKS[k - 1],
                        LOOK_SLOT0 + k - 1))
    lines.append('ram 0x%x=%s  # SD stub %d B -> jmp 0x%x'
                 % (SD_STUB, sd_stub.hex(), len(sd_stub), SD_RET))
    lines.append('ram 0x%x=ff  # stash init (0xff = SD stub never ran)' % STASH)
    lines.append('ram 0x%x=%s  # SU stub %d B -> jmp 0x%x'
                 % (SU_STUB, su_stub.hex(), len(su_stub), SU_RET))
    if variant == 'b38':
        lines.append('ram 0x%x=%s  # matrices k=1..4, stride 0x%x, rows sum 256'
                     % (MX_DATA, b''.join(mx[i] for i in (1, 2, 3, 4)).hex(),
                        MX_STRIDE))
    body = '\n'.join(lines) + '\n'
    try:
        body.encode('ascii')
    except UnicodeEncodeError:
        raise SystemExit('patch 文件里有非 ASCII 字符（mkpatch 的注释只许 ASCII）')
    io.open(PATCH[variant], 'w', encoding='utf-8', newline='\n').write(body)
    return body


def build(variant):
    tone, mx, flats = tables()
    sd_stub, sd_off = assemble_with_jmp(SD_TMPL, '%s_sd' % TAG[variant], SD_STUB,
                                        0x7EAD0031, SD_RET)
    su_tmpl = SU_TMPL.replace('@@MATRIX@@', MX_SNIPPET if variant == 'b38' else '')
    su_stub, su_off = assemble_with_jmp(su_tmpl, '%s_su' % TAG[variant], SU_STUB,
                                        0x7EAD0032, SU_RET)
    if SD_STUB + len(sd_stub) > STASH:
        raise SystemExit('SD 桩 %d B 压到 stash' % len(sd_stub))
    if SU_STUB + len(su_stub) > (MX_DATA if variant == 'b38' else SLOT_END):
        raise SystemExit('SU 桩 %d B 压到矩阵块/槽尾' % len(su_stub))
    if MX_DATA + MX_STRIDE * 4 > SLOT_END:
        raise SystemExit('矩阵块越出槽 53')
    make_patch(variant, tone, mx, sd_stub, su_stub)
    return dict(variant=variant, tone=tone, mx=mx, flats=flats, sd=sd_stub,
                su=su_stub, sd_off=sd_off, su_off=su_off)


def run_mkpatch(variant, out, force=False):
    if os.path.exists(out):
        if not force:
            raise SystemExit('拒绝覆盖已有文件：%s（--force 可覆盖）' % out)
        os.remove(out)
    r = run([sys.executable, 'tools/mkpatch.py', SRC, '--out', out,
             '--patch-file', PATCH[variant]])
    print(r.stdout.strip()[-1200:])


# ---------------------------------------------------------------- 验证
def payload_of(path):
    with open(path, 'rb') as f:
        blocks = oc.parse(f.read())
    return blocks[0].payload, blocks


def inside(addr, ranges):
    return any(a <= addr < a + l for a, l in ranges)


def diff_runs(a, b):
    if len(a) != len(b):
        raise SystemExit('payload 长度不同：%d / %d' % (len(a), len(b)))
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    runs = []
    for i in diff:
        if runs and i == runs[-1][0] + runs[-1][1]:
            runs[-1][1] += 1
        else:
            runs.append([i, 1])
    return runs, diff


def verify(path, info):
    ok = True
    v = info['variant']
    stock, _ = payload_of(SRC)
    built, blocks = payload_of(path)
    ranges = declared_ranges(v, len(info['sd']), len(info['su']))

    print('\n== 门 1：容器 diff 全部落在声明区间（%s）==' % os.path.basename(path))
    blocks_stock = oc.parse(open(SRC, 'rb').read())
    diff_all, outside_all, runs_all = 0, [], []
    for bs, bb in zip(blocks_stock, blocks):
        if bs.payload == bb.payload:
            print('  块 %d payload %d B：未改动' % (bs.index, len(bb.payload)))
            continue
        runs, diff = diff_runs(bs.payload, bb.payload)
        outside = [i for i in diff if not inside(BASE + i, ranges)]
        print('  块 %d payload %d B：差异 %d 字节 / %d 段，区外 %d 字节'
              % (bs.index, len(bb.payload), len(diff), len(runs), len(outside)))
        diff_all += len(diff)
        runs_all += runs
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

    print('\n== 门 2：回读（tone / 钩子 / 桩 / stash%s）==' % (' / 矩阵' if v == 'b38' else ''))
    for k in (1, 2, 3, 4):
        got = built[slot_addr(k) - BASE:slot_addr(k) - BASE + 0x200]
        t46 = int.from_bytes(got[92:94], 'little')
        good = got == info['tone'][k] and t46 == T46_WANT[k - 1]
        print('  k=%d slot %d @%#x  T[46]=%d（期望 %d）逐字节一致=%s -> %s'
              % (k, LOOK_SLOT0 + k - 1, slot_addr(k), t46, T46_WANT[k - 1],
                 got == info['tone'][k], 'OK' if good else 'FAIL'))
        ok &= good
    t0 = int.from_bytes(built[TONES - BASE + 92:TONES - BASE + 94], 'little')
    t1 = int.from_bytes(built[TONES + 0x200 - BASE + 92:TONES + 0x200 - BASE + 94],
                        'little')
    print('  原厂 tone 槽 0 T[46]=%d（期望 %d）、槽 1 T[46]=%d（期望 %d）-> %s'
          % (t0, T0_18, t1, T1_18, 'OK' if (t0, t1) == (T0_18, T1_18) else 'FAIL'))
    ok &= (t0, t1) == (T0_18, T1_18)

    got_sd = built[HOOK_SD - BASE:HOOK_SD - BASE + SD_PATCH_LEN]
    want_sd = jmp_bytes(HOOK_SD, SD_STUB) + b'\x00'
    got_su = built[HOOK_SU - BASE:HOOK_SU - BASE + 5]
    want_su = jmp_bytes(HOOK_SU, SU_STUB)
    print('  SD 钩子 %#x = %s（期望 %s）-> %s'
          % (HOOK_SD, got_sd.hex(), want_sd.hex(), 'OK' if got_sd == want_sd else 'FAIL'))
    print('  SU 钩子 %#x = %s（期望 %s）-> %s'
          % (HOOK_SU, got_su.hex(), want_su.hex(), 'OK' if got_su == want_su else 'FAIL'))
    ok &= got_sd == want_sd and got_su == want_su
    for name, addr, blob in (('SD 桩', SD_STUB, info['sd']), ('SU 桩', SU_STUB, info['su'])):
        got = built[addr - BASE:addr - BASE + len(blob)]
        print('  %s @%#x %d B 逐字节一致 = %s' % (name, addr, len(blob), got == blob))
        ok &= got == blob
    stash = built[STASH - BASE]
    print('  stash @%#x = %#x（期望 0xff）-> %s'
          % (STASH, stash, 'OK' if stash == 0xff else 'FAIL'))
    ok &= stash == 0xff
    if v == 'b38':
        for k in (1, 2, 3, 4):
            a = MX_DATA + MX_STRIDE * (k - 1)
            got = built[a - BASE:a - BASE + MX_STRIDE]
            good = got == info['mx'][k]
            print('  矩阵 k=%d @%#x = %s -> %s'
                  % (k, a, info['flats'][k], 'OK' if good else 'FAIL'))
            ok &= good
        got_m0 = built[MATRIX_RAM - BASE:MATRIX_RAM - BASE + 18]
        print('  原厂 m0 未被静态改动 = %s'
              % (got_m0 == stock[MATRIX_RAM - BASE:MATRIX_RAM - BASE + 18]))

    print('\n== SD 桩反汇编（@%#x，%d B，jmp 指令 @+%#x）==' % (SD_STUB, len(info['sd']), info['sd_off']))
    for line in disasm(info['sd'], SD_STUB):
        print(line)
    print('\n== SU 桩反汇编（@%#x，%d B，jmp 指令 @+%#x）=='
          % (SU_STUB, len(info['su']), info['su_off']))
    for line in disasm(info['su'], SU_STUB):
        print(line)
    return ok


def verify_tools(path, info):
    print('\n== 门 3：仓库自带工具 ==')
    log = []
    r = run([sys.executable, 'tools/audit_beta_pack.py', path], check=False)
    print(r.stdout.strip())
    log.append(r.stdout.strip())
    ok = r.returncode == 0 and '[OK' in r.stdout
    for entry, insert in ((hex(0x604AA228), hex(HOOK_SD)),
                          (hex(HOOK_SU), hex(HOOK_SU))):
        r = run([sys.executable, 'tools/patch_check.py', '--reach', entry, insert],
                check=False)
        print('\n$ patch_check --reach %s %s  (rc=%d)' % (entry, insert, r.returncode))
        print(r.stdout.strip())
        log.append('$ patch_check --reach %s %s (rc=%d)\n%s'
                   % (entry, insert, r.returncode, r.stdout.strip()))
        ok &= r.returncode == 0
    pay = '.tmp/%s_payload.bin' % info['variant']
    with open(pay, 'wb') as f:
        f.write(payload_of(path)[0])
    for site, dest in ((hex(HOOK_SD), hex(SD_STUB)), (hex(HOOK_SU), hex(SU_STUB))):
        r = run([sys.executable, 'tools/patch_check.py', '--jump', site, dest,
                 '--blob', pay], check=False)
        print('\n$ patch_check --jump %s %s --blob %s  (rc=%d)'
              % (site, dest, pay, r.returncode))
        print(r.stdout.strip())
        log.append('$ patch_check --jump %s %s (rc=%d)\n%s'
                   % (site, dest, r.returncode, r.stdout.strip()))
        ok &= r.returncode == 0
    gf = '.tmp/b37_b38_gate3_%s.txt' % info['variant']
    io.open(gf, 'w', encoding='utf-8', newline='\n').write('\n\n'.join(log) + '\n')
    print('\n  （完整输出另存 %s）' % gf)
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--variant', choices=['b37', 'b38', 'both'], default='both')
    ap.add_argument('--out', default=None, help='只出这一个包（配合 --variant b37|b38）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify-only', action='store_true')
    ap.add_argument('--no-tools', action='store_true')
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args(argv)

    variants = ['b37', 'b38'] if args.variant == 'both' else [args.variant]
    all_ok = True
    infos = {}
    for v in variants:
        print('\n' + '=' * 72)
        print('variant %s' % v)
        print('=' * 72)
        info = build(v)
        infos[v] = info
        print('  SD 桩 %d B（jmp @+%#x）  SU 桩 %d B（jmp @+%#x）'
              % (len(info['sd']), info['sd_off'], len(info['su']), info['su_off']))
        for k in (1, 2, 3, 4):
            t = info['tone'][k]
            print('  k=%d %-13s slot %2d @%#x  T[46]=%3d T[22]=%3d T[0]=%3d T[255]=%4d  m=%s'
                  % (k, LOOKS[k - 1], LOOK_SLOT0 + k - 1, slot_addr(k),
                     int.from_bytes(t[92:94], 'little'),
                     int.from_bytes(t[44:46], 'little'),
                     int.from_bytes(t[0:2], 'little'),
                     int.from_bytes(t[510:512], 'little'), info['flats'][k]))
        print('  patch: %s' % PATCH[v])
        if args.dry_run:
            continue
        out = args.out if (args.out and len(variants) == 1) else OUT[v]
        if not args.verify_only:
            run_mkpatch(v, out, force=args.force)
        ok = verify(out, info)
        if not args.no_tools:
            ok &= verify_tools(out, info)
        blob = open(out, 'rb').read()
        print('\n包：%s  %d B  sha256=%s'
              % (out, len(blob), hashlib.sha256(blob).hexdigest()[:16]))
        if os.path.getsize(out) != os.path.getsize(SRC):
            print('大小与源不一致！')
            ok = False
        print('VERDICT %s %s' % (v, 'PASS' if ok else 'FAIL'))
        all_ok &= ok

    if 'b37' in infos and 'b38' in infos and not args.dry_run:
        print('\n== B37 vs B38：差异只能在 SU 桩 + 矩阵块 ==')
        a, _ = payload_of(OUT['b37'])
        b, _ = payload_of(OUT['b38'])
        runs, diff = diff_runs(a, b)
        allowed = [(SU_STUB, max(len(infos['b37']['su']), len(infos['b38']['su']))),
                   (MX_DATA, MX_STRIDE * 4)]
        bad = [i for i in diff if not inside(BASE + i, allowed)]
        print('  差异 %d 字节 / %d 段；不在（SU 桩 ∪ 矩阵块）里的 %d 字节 -> %s'
              % (len(diff), len(runs), len(bad), 'OK' if not bad else 'FAIL'))
        for r0, l in runs:
            print('    %#x + %d' % (BASE + r0, l))
        all_ok &= not bad

    print('\nALL VERDICT', 'PASS' if all_ok else 'FAIL')
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
