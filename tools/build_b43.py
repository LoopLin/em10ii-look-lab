#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b41.py -- B41：修 B38 的真机缺陷（矩阵改成"只换指针"，一处共享状态都不写）

与 B38 的差别只有一处：
  B38  SU 桩把第 k 档的 9 个 i16 拷进 m0 = 0x605ECECA（原地改写，污染会残留）
  B41  不再碰 m0：把 call 0x604b6a95（矩阵选择器，5 B）换成远跳，桩按 stash 的 k
       返回我们自己那张表的地址；k 不在 1..4 就返回原厂 0x605ECECA

B42 = --variant b42：四档都指向同一张表（reala），用于回答"档间差异是谁造成的"。

用法：
  python tools/build_b41.py --variant b41
  python tools/build_b41.py --variant b42
  python tools/build_b41.py --variant b41 --dry-run      # 只出 .s / patch
"""
from __future__ import annotations

import argparse
import io
import os
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oly_container as oc  # noqa: E402
from build_beta_look import load_spec, tone_bytes, retarget_tone  # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'

TONES = 0x605D8344
LOOK_SLOT0 = 22
LOOKS = ['reala_ace', 'astia', 'eterna', 'bleach_bypass']
T46_WANT = [689, 651, 642, 716]
T0_18, T1_18 = 584, 804

HOOK_SD, SD_PATCH_LEN, SD_RET, SD_STUB = 0x604AA242, 6, 0x604AA248, 0x605DED44
HOOK_SU, SU_RET, SU_STUB = 0x60479960, 0x60479965, 0x605DEDD0
STASH = 0x605DEDC0
MX_DATA, MX_STRIDE = 0x605DEE60, 0x12
MX_HOOK, MX_RET, MX_STUB = 0x604B6AD6, 0x604B6ADB, 0x605DEEB0
MATRIX_M0 = 0x605ECECA
SLOT_END = 0x605DEF44
CODEMAP = 0x605DEE40          # 4 B：模式码（u16 x2）—— 先填 0x0066/0x0067/0x006B/0x0079
CODES = bytes([0x66, 0x00, 0x67, 0x00])

TC = '.tmp/olya/toolchains/mn10300-elf/bin'
AS = os.path.join(TC, 'mn10300-elf-as.exe')
LD = os.path.join(TC, 'mn10300-elf-ld.exe')
OBJCOPY = os.path.join(TC, 'mn10300-elf-objcopy.exe')

SD_TMPL = r"""	.am33
	.text
	.globl	_start
_start:
	# 0x604AA242：a3 = ctx，a2 = out，派发器的 key 还在 ctx+8/+9/+0xA
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
	# 0x60479960（上传器入口）：按 u16(ctx+0x30)（模式码）选表；不认识就一个字节都不写
	add	-0x10,sp
	mov	d0,(0,sp)
	mov	d1,(4,sp)
	mov	d2,(8,sp)
	mov	(12,a1),a3
	cmp	0,a3
	beq	Lrep
	movhu	(48,a3),d0
	mov	0x605DEE40,a2
	movbu	(a2),d1
	cmp	d0,d1
	beq	Llook1
	movbu	(1,a2),d1
	cmp	d0,d1
	beq	Llook2
	movbu	(2,a2),d1
	cmp	d0,d1
	beq	Llook3
	movbu	(3,a2),d1
	cmp	d0,d1
	beq	Llook4
	bra	Lrep
Llook1:
	mov	1,d1
	bra	Lset
Llook2:
	mov	2,d1
	bra	Lset
Llook3:
	mov	3,d1
	bra	Lset
Llook4:
	mov	4,d1
Lset:
	add	21,d1
	asl	9,d1
	mov	0x605D8344,a2
	add	d1,a2
	add	0x1230,a3
	mov	a2,(a3)
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

MX_TMPL = r"""	.am33
	.text
	.globl	_start
_start:
	# 0x604B6AD6 原来 call 0x604b6a95（矩阵选择器），返回 a0=指针。
	# 调用点要求：a2=ctx、a3=out、d3=selector 保持，a0=矩阵指针，随后 0x604B6ADB 判空。
	add	-0x10,sp
	mov	d0,(0,sp)
	mov	d1,(4,sp)
	mov	d2,(8,sp)
	mov	a2,(12,sp)
	mov	0x605DEDC0,a2
	movbu	(a2),d0
	cmp	1,d0
	bcs	Lstock
	cmp	4,d0
	bhi	Lstock
	add	-1,d0
	mov	d0,d1
	asl	4,d1
	asl	1,d0
	add	d0,d1
	mov	0x605DEE60,a0
	add	d1,a0
	bra	Lend
Lstock:
	mov	0x605ECECA,a0
Lend:
	mov	(0,sp),d0
	mov	(4,sp),d1
	mov	(8,sp),d2
	mov	(12,sp),a2
	add	0x10,sp
	jmp	@@JRET@@
"""


def run(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    if check and r.returncode:
        raise SystemExit('%s failed (%d)\n%s\n%s' % (' '.join(cmd), r.returncode,
                                                    r.stdout[-1500:], r.stderr[-1500:]))
    return r


def assemble(text, tag, base):
    d = '.tmp/b41'
    os.makedirs(d, exist_ok=True)
    s, o = os.path.join(d, tag + '.s'), os.path.join(d, tag + '.o')
    elf, binp = os.path.join(d, tag + '.elf'), os.path.join(d, tag + '.bin')
    io.open(s, 'w', encoding='utf-8', newline='\n').write(text)
    run([AS, '-o', o, s])
    run([LD, '-Ttext', hex(base), '-o', elf, o])
    run([OBJCOPY, '-O', 'binary', elf, binp])
    with open(binp, 'rb') as f:
        return f.read()


def assemble_with_jmp(tmpl, tag, base, placeholder, target):
    ph = '0x%X' % placeholder
    b1 = assemble(tmpl.replace('@@JRET@@', ph), tag + '_p1', base)
    i = b1.find(struct.pack('<I', placeholder))
    if i < 0:
        raise SystemExit('占位常量没编出来（%s）' % tag)
    off = i - 1
    if b1[off] != 0xDC:
        raise SystemExit('%s：偏移 %#x 不是 DC' % (tag, off))
    text = tmpl.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (target, base, off))
    blob = assemble(text, tag, base)
    want = b'\xdc' + struct.pack('<i', target - (base + off))
    if blob[off:off + 5] != want:
        raise SystemExit('jmp 自检失败 %s：%s != %s' % (tag, blob[off:off + 5].hex(), want.hex()))
    return blob, off


def jmp_bytes(site, target):
    return b'\xdc' + struct.pack('<i', target - site)


def slot_addr(k):
    return TONES + (LOOK_SLOT0 + k - 1) * 0x200


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def build(variant):
    tone = {}
    same = (variant == 'b42')
    global CODES
    if variant == 'b43':
        CODES = bytes([0x66, 0x00, 0x67, 0x00, 0x6B, 0x00, 0x79, 0x00])
    if variant == 'b44':
        # 真机观察：vivid 开机就拿到 bleach 的槽 => vivid 的模式码 = 0x79
        CODES = bytes([0x79, 0x00, 0x66, 0x00, 0x67, 0x00, 0x6B, 0x00])
    for k in range(1, 5):
        look = LOOKS[0] if same else LOOKS[k - 1]
        tone[k] = tone_bytes(retarget_tone(load_spec(look)['tone']))
    sd, sd_off = b'', 0
    su, su_off = assemble_with_jmp(SU_TMPL, variant + '_su', SU_STUB, 0x7EAD0032, SU_RET)
    mx, mx_off = assemble_with_jmp(MX_TMPL, variant + '_mx', MX_STUB, 0x7EAD0033, MX_RET)
    if SU_STUB + len(su) > MX_DATA:
        raise SystemExit('SU 桩压到矩阵块')
    if MX_DATA + MX_STRIDE * 4 > MX_STUB:
        raise SystemExit('矩阵块压到 MX 桩')
    if MX_STUB + len(mx) > SLOT_END:
        raise SystemExit('MX 桩越出槽 53 尾部')
    ranges = [(HOOK_SU, 5), (MX_HOOK, 5), (SU_STUB, len(su)),
              (MX_DATA, MX_STRIDE * 4), (MX_STUB, len(mx)), (CODEMAP, len(CODES))]
    ranges += [(slot_addr(k), 0x200) for k in (1, 2, 3, 4)]
    lines = ['# %s build (one shot, from stock)' % variant.upper(),
             '# k=(b0&3)<<3 | (mode&7); SD hook 0x%x -> 0x%x ; SU hook 0x%x -> 0x%x ; MX hook 0x%x -> 0x%x'
             % (HOOK_SD, SD_STUB, HOOK_SU, SU_STUB, MX_HOOK, MX_STUB),
             'ram 0x%x=%s' % (HOOK_SU, jmp_bytes(HOOK_SU, SU_STUB).hex()),
             'ram 0x%x=%s' % (MX_HOOK, jmp_bytes(MX_HOOK, MX_STUB).hex())]
    for k in range(1, 5):
        look = LOOKS[0] if same else LOOKS[k - 1]
        lines.append('ram 0x%x=%s  # tone k=%d %s slot %d'
                     % (slot_addr(k), tone[k].hex(), k, look, LOOK_SLOT0 + k - 1))
    lines += ['ram 0x%x=%s  # mode codes for look1..4' % (CODEMAP, CODES.hex()),
              'ram 0x%x=%s  # SU stub %d B' % (SU_STUB, su.hex(), len(su)),
              'ram 0x%x=%s  # MX stub %d B' % (MX_STUB, mx.hex(), len(mx))]
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/%s.patch' % variant
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(tone=tone, sd=sd, su=su, mx=mx, ranges=sorted(ranges), patch=pf,
                sd_off=sd_off, su_off=su_off, mx_off=mx_off)


def verify(path, info, variant, same=False):
    stock = payload_of(SRC)
    built = payload_of(path)
    print('\n== 门 1：差异是否全在声明区间 ==')
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    outside = [i for i in diff if not any(a - BASE <= i < a - BASE + l for a, l in info['ranges'])]
    print('  改动 %d 字节；区外 %d 字节 -> %s' % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    if outside:
        for i in outside[:8]:
            print('    区外 %#x: %02x -> %02x' % (BASE + i, stock[i], built[i]))
    print('\n== 门 2：回读 ==')
    ok = not outside
    for k in range(1, 5):
        got = built[slot_addr(k) - BASE: slot_addr(k) - BASE + 0x200]
        t46 = int.from_bytes(got[92:94], 'little')
        good = got == info['tone'][k]
        want46 = T46_WANT[0] if same else T46_WANT[k - 1]
        print('  k=%d slot %d T[46]=%d（期望 %d）逐字节=%s' % (k, LOOK_SLOT0 + k - 1, t46, want46, good))
        ok &= good
    m0 = built[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18]
    m0s = stock[MATRIX_M0 - BASE: MATRIX_M0 - BASE + 18]
    print('  原厂 m0 未改动 = %s' % (m0 == m0s))
    ok &= (m0 == m0s)
    for name, site, tgt, blob in (('SU', HOOK_SU, SU_STUB, info['su']),
                                  ('MX', MX_HOOK, MX_STUB, info['mx'])):
        got = built[site - BASE: site - BASE + 5]
        want = jmp_bytes(site, tgt)
        same_hook = got == want
        stub_ok = built[tgt - BASE: tgt - BASE + len(blob)] == blob
        print('  %s 钩子 %#x=%s 期望 %s -> %s ; 桩 %#x 一致=%s'
              % (name, site, got.hex(), want.hex(), 'OK' if same_hook else 'FAIL', tgt, stub_ok))
        ok &= same_hook and stub_ok
    print('  模式码表 = %s' % built[CODEMAP - BASE:CODEMAP - BASE + 4].hex())
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--variant', default='b41', choices=['b41', 'b42', 'b43', 'b44'])
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    out = {'b41': 'build/B41_fix_mxptr.BIN', 'b42': 'build/B42_sametable.BIN',
            'b43': 'build/B43_keyctx30.BIN', 'b44': 'build/B44_keyctx30_4.BIN'}[a.variant]
    info = build(a.variant)
    print('SU 桩 %d B / MX 桩 %d B' % (len(info['su']), len(info['mx'])))
    print('patch: %s' % info['patch'])
    if a.dry_run:
        return
    if os.path.exists(out):
        if not a.force:
            raise SystemExit('拒绝覆盖 %s（--force 覆盖）' % out)
        os.remove(out)
    r = run([sys.executable, 'tools/mkpatch.py', SRC, '--out', out, '--patch-file', info['patch']])
    print(r.stdout.strip()[-1000:])
    ok = verify(out, info, a.variant, same=(a.variant == 'b42'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
