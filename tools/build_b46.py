#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b46.py -- B46 "pointer pick, pool-facing stub"：在 tone 上传器取指针处按指纹换表，零内存写入。

设计（docs/firmware/beta/B45-pointer-pick.md）：
  插入点 0x60479A23  5 B  DC <disp32>  ->  桩 0x605DED44
    盖掉 add 0x400,d2 (4 B) 与 add 0x1230,a0 的首字节；0x60479A27 剩下的 D0 30 12 成为死字节。
  桩（0x60DA4E08，84 B）：原样重放被绕过的 4 条指令（两条 add、mov (a0),d0、mov d2,a1、
    第二次 add 0x400,d2），按 *(ctx+0x1230) 的三个原厂指纹换成槽 22/24/25，未知模式原样；
    然后 jmp 0x60479A3A（正常拷贝）或 jmp 0x60479A4E（空指针分支）。
  **全程只读**：不写 ctx+0x1230、不写 0x605ECECA、不写任何 stash。
  表：reala_ace -> 槽 22、astia -> 槽 23（本包不引用，留给以后换映射）、
      eterna -> 槽 24、bleach_bypass -> 槽 25（基址 0x605D8344 + k*0x200）。

用法：
  python tools/build_b46.py                 # 出 build/B46_ptrpick_pool.BIN 并自检
  python tools/build_b46.py --dry-run       # 只出 .s / patch，不落 BIN
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oly_container as oc  # noqa: E402
from build_beta_look import retarget_tone, tone_bytes  # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/B46_ptrpick_pool.BIN'

HOOK = 0x60479A23            # 5 B 远跳
HOOK_LEN = 5
RET_LOAD = 0x60479A3A        # 正常路径：setlb 前（a2 已就位）
RET_NULL = 0x60479A4E        # *(ctx+0x1230) == 0 的另一条拷贝

STUB = 0x605DED44            # tone 槽 53 头部（真机多次用过的口袋；代价：ART 0xB04/0xB05 曲线头 84 B 被占）
STUB_LIMIT = 0x605DED44 + 400  # 槽 53 整段 512 B 之内

TONES = 0x605D8344
LOOK_SLOT0 = 22
SPEC_DIR = '.tmp/looks_beta'
LOOKS = ['reala_ace', 'astia', 'eterna', 'bleach_bypass']
T46_WANT = [689, 651, 642, 716]

FPR_VIVID_NATURAL = 0x605D8544   # 槽 1
FPR_MUTED = 0x605D8944           # 槽 3
FPR_PORTRAIT = 0x605D9144        # 槽 7
MATRIX_M0 = 0x605ECECA

TC = '.tmp/olya/toolchains/mn10300-elf/bin'
AS = os.path.join(TC, 'mn10300-elf-as.exe')
LD = os.path.join(TC, 'mn10300-elf-ld.exe')
OBJCOPY = os.path.join(TC, 'mn10300-elf-objcopy.exe')
OBJDUMP = os.path.join(TC, 'mn10300-elf-objdump.exe')

PH1, PH2 = 0x11111111, 0x22222222

# 桩：先按占位常量汇编，再换成 "目标 - (桩址 + 偏移)" 的表达式重汇编（build_b43 同法）。
STUB_TMPL = r"""	.am33
	.text
	.globl	_start
_start:
	add	0x400,d2		# 被 5 B 钩子盖掉的那条
	add	0x1230,a0		# a0 = ctx + 0x1230（复原；首个字节同样被盖）
	mov	(a0),d0			# d0 = 原厂 tone 指针（只读）
	mov	d2,a1			# 0x60479A2C，被绕过：a1 = d2
	add	0x400,d2		# 0x60479A2E，被绕过：d2 += 0x400
	cmp	0x0,d0
	beq	Lnull			# 空指针 -> 原厂 0x60479A4E
	mov	0x605D8544,d1		# vivid / natural
	cmp	d1,d0
	beq	M1
	mov	0x605D8944,d1		# muted
	cmp	d1,d0
	beq	M2
	mov	0x605D9144,d1		# portrait
	cmp	d1,d0
	beq	M3
	mov	(a0),a2			# 未知模式：原样（重新读 = stock 语义）
	bra	Lload
M1:
	mov	0x605DAF44,a2		# 槽 22 reala_ace
	bra	Lload
M2:
	mov	0x605DB344,a2		# 槽 24 eterna
	bra	Lload
M3:
	mov	0x605DB544,a2		# 槽 25 bleach_bypass
Lload:
	mov	(8,SP),d1		# 0x60479A36，被绕过
	jmp	@@J1@@
Lnull:
	jmp	@@J2@@
"""


def run(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    if check and r.returncode:
        raise SystemExit('%s failed (%d)\n%s\n%s' % (' '.join(cmd), r.returncode,
                                                    r.stdout[-1500:], r.stderr[-1500:]))
    return r


def assemble(text, tag, base):
    d = '.tmp/b45'
    os.makedirs(d, exist_ok=True)
    s, o = os.path.join(d, tag + '.s'), os.path.join(d, tag + '.o')
    elf, binp = os.path.join(d, tag + '.elf'), os.path.join(d, tag + '.bin')
    io.open(s, 'w', encoding='utf-8', newline='\n').write(text)
    run([AS, '-o', o, s])
    run([LD, '-Ttext', hex(base), '-o', elf, o])
    run([OBJCOPY, '-O', 'binary', elf, binp])
    with open(binp, 'rb') as f:
        data = f.read()
    with open(os.path.join(d, tag + '.dis'), 'w', encoding='utf-8') as f:
        f.write(run([OBJDUMP, '-d', elf]).stdout)
    return data


def find_ph(blob, value):
    i = blob.find(struct.pack('<I', value))
    if i < 0:
        raise SystemExit('占位常量 %#x 没编出来' % value)
    if blob[i - 1] != 0xDC:
        raise SystemExit('占位 %#x 前一个字节是 %#x，不是 DC' % (value, blob[i - 1]))
    return i - 1          # -> jmp 指令起始偏移


def assemble_stub():
    b1 = assemble(STUB_TMPL.replace('@@J1@@', '0x%X' % PH1)
                           .replace('@@J2@@', '0x%X' % PH2), 'stub_p1', STUB)
    off1 = find_ph(b1, PH1)
    off2 = find_ph(b1, PH2)
    text = (STUB_TMPL
            .replace('@@J1@@', '0x%x - (0x%x + 0x%x)' % (RET_LOAD, STUB, off1))
            .replace('@@J2@@', '0x%x - (0x%x + 0x%x)' % (RET_NULL, STUB, off2)))
    blob = assemble(text, 'stub', STUB)
    for off, target in ((off1, RET_LOAD), (off2, RET_NULL)):
        want = b'\xdc' + struct.pack('<i', target - (STUB + off))
        got = blob[off:off + 5]
        if got != want:
            raise SystemExit('jmp 自检失败 @%#x: %s != %s' % (STUB + off, got.hex(), want.hex()))
    return blob, off1, off2


def jmp_bytes(site, target):
    return b'\xdc' + struct.pack('<i', target - site)


def slot_addr(k):
    return TONES + (LOOK_SLOT0 + k - 1) * 0x200


def load_spec(look):
    with io.open(os.path.join(SPEC_DIR, look + '.json'), encoding='utf-8') as f:
        return json.load(f)


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def build():
    stub, off1, off2 = assemble_stub()
    if STUB + len(stub) > STUB_LIMIT:
        raise SystemExit('桩 %d B 越过装载器 used_end %#x' % (len(stub), STUB_LIMIT))
    if len(stub) & 1:
        raise SystemExit('桩长度奇数：%d' % len(stub))

    tone = {}
    for k in range(1, 5):
        spec = load_spec(LOOKS[k - 1])
        tone[k] = tone_bytes(retarget_tone(spec['tone']))

    ranges = [(HOOK, HOOK_LEN), (STUB, len(stub))]
    ranges += [(slot_addr(k), 0x200) for k in (1, 2, 3, 4)]

    lines = ['# B45 pointer-pick build: one shot from stock, no persistent writes',
             '# hook 0x%x (%d B) -> stub 0x%x (%d B); ret %#x / %#x'
             % (HOOK, HOOK_LEN, STUB, len(stub), RET_LOAD, RET_NULL),
             '# stub jmp offsets: @%#x -> %#x, @%#x -> %#x'
             % (STUB + off1, RET_LOAD, STUB + off2, RET_NULL),
             'ram 0x%x=%s  # hook: jmp stub' % (HOOK, jmp_bytes(HOOK, STUB).hex())]
    for k in range(1, 5):
        lines.append('ram 0x%x=%s  # tone k=%d %s slot %d'
                     % (slot_addr(k), tone[k].hex(), k, LOOKS[k - 1], LOOK_SLOT0 + k - 1))
    lines.append('ram 0x%x=%s  # stub %d B (read-only, no store)' % (STUB, stub.hex(), len(stub)))
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')          # mkpatch 的 patch 文件注释必须 ASCII
    pf = '.tmp/b45.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(stub=stub, tone=tone, ranges=sorted(ranges), patch=pf,
                off1=off1, off2=off2)


def verify(path, info):
    stock = payload_of(SRC)
    built = payload_of(path)
    ok = True

    print('\n== 差异面 ==')
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    outside = [i for i in diff
               if not any(a - BASE <= i < a - BASE + l for a, l in info['ranges'])]
    print('  改动 %d 字节；区外 %d 字节 -> %s'
          % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    for i in outside[:8]:
        print('    区外 %#x: %02x -> %02x' % (BASE + i, stock[i], built[i]))
    ok &= not outside

    print('\n== 回读 ==')
    for k in range(1, 5):
        got = built[slot_addr(k) - BASE: slot_addr(k) - BASE + 0x200]
        t46 = int.from_bytes(got[92:94], 'little')
        good = got == info['tone'][k]
        print('  k=%d slot %d T[46]=%d（期望 %d）逐字节=%s'
              % (k, LOOK_SLOT0 + k - 1, t46, T46_WANT[k - 1], good))
        ok &= good and t46 == T46_WANT[k - 1]

    got = built[HOOK - BASE:HOOK - BASE + HOOK_LEN]
    want = jmp_bytes(HOOK, STUB)
    print('  钩子 %#x = %s（期望 %s）-> %s' % (HOOK, got.hex(), want.hex(),
                                              'OK' if got == want else 'FAIL'))
    ok &= got == want
    stub = info['stub']
    print('  桩 %#x 一致=%s（%d B，止于 %#x < %#x）'
          % (STUB, built[STUB - BASE:STUB - BASE + len(stub)] == stub, len(stub),
             STUB + len(stub), STUB_LIMIT))
    ok &= built[STUB - BASE:STUB - BASE + len(stub)] == stub

    m0 = built[MATRIX_M0 - BASE:MATRIX_M0 - BASE + 18]
    m0s = stock[MATRIX_M0 - BASE:MATRIX_M0 - BASE + 18]
    print('  原厂 m0 @%#x 未改动=%s' % (MATRIX_M0, m0 == m0s))
    ok &= m0 == m0s

    dead = built[0x60479A28 - BASE:0x60479A2B - BASE]
    print('  死字节 %#x..%#x = %s（原厂 D0 30 12，桩跳回 0x%x 后不再执行）'
          % (0x60479A28, 0x60479A2A, dead.hex(), RET_LOAD))
    ok &= dead == b'\xd0\x30\x12'

    with open(path, 'rb') as f:
        whole = f.read()
    print('  BIN %d B  sha256=%s' % (len(whole), hashlib.sha256(whole).hexdigest()))
    print('  payload sha256=%s' % hashlib.sha256(built).hexdigest())
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    info = build()
    print('桩 %d B @ %#x ; jmp 目标 %#x / %#x' % (len(info['stub']), STUB, RET_LOAD, RET_NULL))
    print('patch: %s' % info['patch'])
    if a.dry_run:
        print('dry-run：不落 BIN')
        return
    if os.path.exists(OUT):
        if not a.force:
            raise SystemExit('拒绝覆盖 %s（--force 覆盖）' % OUT)
        os.remove(OUT)
    r = run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT,
             '--patch-file', info['patch']])
    print(r.stdout.strip()[-800:])
    ok = verify(OUT, info)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
