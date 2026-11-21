#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b40_chain.py —— B40：tone + 每档矩阵 + 每档色度，全部按 stash 的 k 分派。

与 B38 相同的两个钩子（SD 0x604AA242 -> 0x605DED44；SU 0x60479960 -> 0x605DEDD0），
再加第三个钩子：色度块取指针 0x604B4B25 -> 0x605DEEB0。

* k 由 SD 桩写进 stash 0x605DEDC0（ctx+8/9/0x0A 在那一刻仍有效）。
* SU 桩按 k 选 tone 槽 22..25，并把第 k 组 9 个 i16 拷进 m0。
* 色度桩按 k 选池块 0x605D4829 + k*0x64；k 不在 1..4 时走原厂 k=0。
  桩保留原厂的两条提前返回（obj+0x3C0 覆写、4:3 特判），不改变它们的语义。

用法:  python tools/build_b40_chain.py [--dry-run] [--no-tools]
"""
from __future__ import annotations

import argparse, os, struct, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oly_container as oc
import build_b37_b38_chain as C
from build_beta_look import chroma_bytes, load_spec

CH_HOOK = 0x604B4B25      # 色度取块函数内（5 B 远跳）
CH_STUB = 0x605DEEB0      # 空槽：0x605DEEA8(矩阵尾) .. 0x605DEF44(槽 53 尾)
CH_RET  = 0x604B4B35      # 原厂逻辑之后：把 a0 交给拷贝循环
CH_POOL = 0x605D4829      # 色度池，块步长 0x64
CH_STEP = 0x64

OUT = 'build/B40_chain.BIN'
PATCH = '.tmp/b40.patch'

CH_TMPL = r"""	.am33
	.text
	.globl	_start
_start:
	# entry 0x604B4B25: a3 = obj（与 B39 同一插入点）
	# 保留原厂两条提前返回
	mov	0x3c0,a1
	add	a3,a1
	mov	(a1),d0
	cmp	0,d0
	beq	Lchk43
	mov	(a1),a0
	add	0x1d,a0
	jmp	Lback
Lchk43:
	movbu	(1071,a3),d0
	cmp	2,d0
	bne	Lsel
	movbu	(12,a3),d0
	cmp	0,d0
	bne	Lsel
	movbu	(13,a3),d0
	add	0x80,d0
	cmp	0x24,d0
	beq	Lstock
	cmp	0x28,d0
	beq	Lstock
	cmp	0x29,d0
	beq	Lstock
Lsel:
	mov	0x605DEDC0,a1
	movbu	(a1),d0
	cmp	1,d0
	bcs	Lstock
	cmp	4,d0
	bhi	Lstock
	mov	0x605D4829,a0
Lloop:
	add	0x64,a0
	add	-1,d0
	cmp	0,d0
	bne	Lloop
	jmp	Lback
Lstock:
	mov	0x605D4829,a0
Lback:
	jmp	@@JRET@@
"""

def assemble_blob(src, tag, base):
    return C.assemble(src, tag, base)

def build(dry=False):
    tone, mx, flats = C.tables()
    patches = []
    # tone: 槽 22..25
    for k in (1, 2, 3, 4):
        patches.append((C.TONES + (C.LOOK_SLOT0 + k - 1) * 0x200, tone[k], 'tone k=%d' % k))
    # 矩阵四组
    for k in (1, 2, 3, 4):
        patches.append((C.MX_DATA + (k - 1) * C.MX_STRIDE, mx[k], 'matrix k=%d' % k))
    # 色度四块
    for k in (1, 2, 3, 4):
        spec = load_spec(C.LOOKS[k - 1])
        blk = chroma_bytes(spec['chroma'])
        assert len(blk) == 100, len(blk)
        patches.append((CH_POOL + k * CH_STEP, blk, 'chroma k=%d' % k))

    sd, _ = C.assemble_with_jmp(C.SD_TMPL, 'b40sd', C.SD_STUB, 0x5D5D0000, C.SD_RET)
    su_src = C.SU_TMPL.replace('@@MATRIX@@', C.MX_SNIPPET)
    su, _ = C.assemble_with_jmp(su_src, 'b40su', C.SU_STUB, 0x5D5D0001, C.SU_RET)
    ch, ch_off = C.assemble_with_jmp(CH_TMPL, 'b40ch', CH_STUB, 0x5D5D0002, CH_RET)

    patches.append((C.SD_STUB, sd, 'SD stub'))
    patches.append((C.SU_STUB, su, 'SU stub'))
    patches.append((CH_STUB, ch, 'chroma stub'))
    # 三个钩子
    patches.append((C.HOOK_SD, b'\xdc' + struct.pack('<i', C.SD_STUB - C.HOOK_SD) + b'\x00', 'SD hook'))
    patches.append((C.HOOK_SU, b'\xdc' + struct.pack('<i', C.SU_STUB - C.HOOK_SU), 'SU hook'))
    patches.append((CH_HOOK, b'\xdc' + struct.pack('<i', CH_STUB - CH_HOOK), 'chroma hook'))
    # stash 初值 0xFF（三态判读）
    patches.append((C.STASH, b'\xff', 'stash init'))

    print('stub sizes: sd=%d su=%d ch=%d (ch jmp off=%d)  patches=%d'
          % (len(sd), len(su), len(ch), ch_off, len(patches)))
    old = C.SD_TMPL
    # 生成 mkpatch patch-file
    lines = []
    for addr, data, name in patches:
        off = addr - C.BASE
        lines.append('# %s' % name)
        lines.append('off 0x%X=%s' % (off, data.hex()))
    open(PATCH, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')
    if dry:
        print('dry-run: wrote', PATCH); return
    run = lambda cmd: subprocess.run(cmd, check=True, capture_output=True, text=True)
    r = run([sys.executable, 'tools/mkpatch.py', C.SRC, '--out', OUT, '--patch-file', PATCH])
    print(r.stdout.strip()[:800])
    print('--- gates ---')
    for cmd in ([sys.executable, 'tools/audit_beta_pack.py', OUT],
                [sys.executable, 'tools/patch_check.py', '--reach', '0x604B4B24', '0x604B4B25'],
                [sys.executable, 'tools/patch_check.py', '--reach', '0x604AA228', '0x604AA242'],
                [sys.executable, 'tools/patch_check.py', '--reach', '0x60479960', '0x60479960']):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            print('$', ' '.join(cmd[1:]))
            print((r.stdout or r.stderr).strip()[:600])
        except Exception as e:
            print('gate failed', e)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    build(a.dry_run)
