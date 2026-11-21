#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b63.py —— B63：B59 基线 + 四张极端色度块 + 扩展 SU 桩写 slot1+0x1D

B63 只比 B59 多两件东西：

1. 四张极端色度块（定义沿用 B60/B61）：Cb/Cr 的 y 分别取
   k=1 (0x00,0x00) / k=2 (0xFF,0xFF) / k=3 (0x00,0xFF) / k=4 (0xFF,0x00)；
   x 沿用库存形状，z 按库存公式现算，pad=0。落在槽 52 口袋 `0x605DEB44 + 0x64*(k-1)`。

2. **扩展 SU 桩**（钩点仍是 `0x60479A2B`，但改跳 `0x605DECD4`）：
   B59 的 SU 桩已经会算新鲜 k、写 EXIF、选 tone 表；B63 在同一趟里
   再把 slot1（`*(ctx+0x37D8)`，即成片链 `0x6047FD3D` 真正读的那份记录）
   `+0x1D` 的 100 B 换成该档极端块：

       slot1 = *(ctx + 0x37D8)          ; 钩点处 a0 = ctx+0x1230 ⇒ *(a0+0x25A8)
       if k in 1..4 and slot1 != 0:
           memcpy(slot1+0x1D, BLK_TAB[k-1], 100)

   **不装**色度 getter 钩（`0x604AA297` 保持原厂字节），**不装**双写桩。

## 落点（全部在 second_verify 的已知落点表 0x605DEB00..0x605DF000 内）

    0x60479A2B  5 B  jmp -> SU 桩（原 B59 指向 0x605DED80）
    0x605DEB44  400 B 四张块（槽 52 口袋，B60 真机用过的同一段）
    0x605DECD4  103 B 扩展 SU 桩（槽 52 口袋尾 112 B，止于 SD 桩 0x605DED44）
    0x605DED80   74 B 旧 SU 桩**还原成原厂字节**（新桩不再用这段）
    0x605DEDD4   16 B tone 指针表（B59 原样，不动）
    0x605DEDE4   16 B 块指针表（= SU_PTRTAB+0x10，桩里用 (0x10,a0) 取，省 6 B）

## 硬约束（build 时逐条断言）

- 只从 `build/B59_real.BIN` 起包；只写 build/
- 不许 bank2（0x605DEF4E 起 2 KB diff 必须为 0）、不许 0x60DA4E08 / 0x60DE0000
- 不写 tone 槽、不写矩阵数据、不写 m0、不写 ctx+0x1230、不写 EXIF 文本区
- 色度 getter 钩 0x604AA297 必须是原厂字节

只离线：不刷机、不碰任何盘。
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
import build_b61 as B61                         # noqa: E402

BASE = 0x60021020
SRC = 'build/B59_real.BIN'                      # 基线（B59 真机验证过）
STOCK = B61.SRC                                 # 原厂（只读，用来取库存色度块/还原旧桩）
OUT = 'build/B63_slot1.BIN'

# ---- 钩点 ----
HOOK, HOOK_LEN = 0x60479A2B, 5                  # B54/B59 的同一个 SU 钩点
SU_RET = 0x60479A32                             # 原厂 cmp 0,d0 / beq / mov (8,SP),d1 / mov (a0),a2
SU_STUB = 0x605DECD4                            # 扩展桩：槽 52 口袋尾（112 B）
SU_STUB_MAX = 0x605DED44 - SU_STUB              # = 112
OLD_SU_STUB, OLD_SU_LEN = 0x605DED80, 0x4A      # B59 旧桩，B63 还原成原厂字节

# ---- 落点 ----
STASH = 0x605DED70
SU_PTRTAB = 0x605DEDD4                          # B59 原样：4 x u32 -> tone 槽 22..25
BLK_TAB = SU_PTRTAB + 0x10                      # 0x605DEDE4：4 x u32 -> 四张块
CH_BASE = 0x605DEB44
CH_STRIDE = 0x64
CH_ADDRS = [CH_BASE + CH_STRIDE * i for i in range(4)]
EXIF_TEXT = 0x61894C90
EXIF_LEAD = 0x313D6B
CH_HOOK = 0x604AA297                            # 色度 getter 钩：必须是原厂字节

POOL = (0x605D8344, 0x605DEF44)
BANK2 = (0x605DEF4E, 0x605DEF4E + 0x800)
BANNED = [0x60DA4E08, 0x60DE0000]
KNOWN_ZONE = (0x605DEB00, 0x605DF000)           # second_verify.KNOWN_ZONES 里的槽 52/53 一带

# 四张块的 y 极值：(Cb y, Cr y)
BLOCK_SPEC = [(0x00, 0x00), (0xFF, 0xFF), (0x00, 0xFF), (0xFF, 0x00)]

PH1 = 0x11111111

# 扩展 SU 桩：前 22 条指令与 B59 一字不差（重放三条 + 读 stash + 算 k + EXIF + 选 tone 表），
# 之后新增 slot1 写入。退出契约与 B59 完全相同：d0=1、a0=指针表单元格、
# a1=dest+0x400、d2=dest+0x800、a3 不动（原厂 0x60479A3C 起的拷贝循环要用）。
SU_TMPL = r"""	.am33
	.text
	.globl	_start
_start:
	mov	(a0),d0
	mov	d2,a1
	add	0x400,d2
	cmp	0,d0
	beq	Lgo
	mov	0x%X,a2
	mov	(a2),d0
	mov	d0,d3
	mov	d0,d1
	and	7,d0
	asr	21,d1
	and	0x18,d1
	or	d1,d0
	add	-1,d0
	cmp	3,d0
	bhi	Lgo
	mov	0x%X,a2
	mov	d3,(4,a2)
	mov	d0,d1
	asl	0x10,d1
	add	0x%X,d1
	mov	d1,(a2)
	mov	a0,a2
	add	0x25a8,a2
	mov	(a2),a2
	add	d0,d0
	add	d0,d0
	mov	0x%X,a0
	add	d0,a0
	mov	a0,d3
	cmp	0,a2
	beq	Lset
	add	0x1d,a2
	mov	(0x10,a0),a0
	mov	-100,d1
	setlb
Lcp:
	movbu	(a0),d0
	inc	a0
	movbu	d0,(a2)
	inc	a2
	inc	d1
	lne
Lset:
	mov	d3,a0
	mov	1,d0
Lgo:
	jmp	@@JRET@@
""" % (STASH, EXIF_TEXT, EXIF_LEAD, SU_PTRTAB)


def in_span(a, ln, span):
    return a >= span[0] and a + ln <= span[1]


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def assemble_stub():
    ph = b46.assemble(SU_TMPL.replace('@@JRET@@', '0x%X' % PH1), 'b63_su_ph', SU_STUB)
    j = b46.find_ph(ph, PH1)
    blob = b46.assemble(SU_TMPL.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (SU_RET, SU_STUB, j)),
                        'b63_su', SU_STUB)
    if blob[j:j + 5] != b'\xdc' + struct.pack('<i', SU_RET - (SU_STUB + j)):
        raise SystemExit('SU 桩 jmp 自检失败 @%#x' % (SU_STUB + j))
    if len(blob) > SU_STUB_MAX:
        raise SystemExit('SU 桩 %d B 超槽 52 口袋尾（<=%d）' % (len(blob), SU_STUB_MAX))
    # 结构自检：三处回跳都没有（本桩无回跳），拷贝循环体唯一
    seq = b'\xdb\xf0\x40\x41\xf0\x52\x49\x44\xd9'  # setlb/movbu(a0),d0/inc a0/movbu d0,(a2)/inc a2/inc d1/lne
    if blob.count(seq) != 1:
        raise SystemExit('SU 桩拷贝循环样本出现 %d 次（应为 1）' % blob.count(seq))
    if b'\x85\x9c' not in blob:
        raise SystemExit('SU 桩没有 mov -100,d1（100 B 计数）')
    return blob, j


def build():
    stock = payload_of(STOCK)
    img = bytearray(payload_of(SRC))

    stub, jmp_off = assemble_stub()
    stock_blk = bytes(stock[B61.CH_STOCK - BASE:B61.CH_STOCK - BASE + CH_STRIDE])
    blocks = []
    for i, (cb_y, cr_y) in enumerate(BLOCK_SPEC):
        blk = B61.extreme_block(cb_y, cr_y, stock_blk)
        B61.check_block(blk, 'B63 k=%d' % (i + 1), cb_y, cr_y, stock_blk)
        blocks.append(blk)

    blk_tab = struct.pack('<4I', *CH_ADDRS)
    old_stub_stock = bytes(stock[OLD_SU_STUB - BASE:OLD_SU_STUB - BASE + OLD_SU_LEN])

    # ---- 落点检查 ----
    spans = [(SU_STUB, len(stub), '扩展 SU 桩'),
             (BLK_TAB, len(blk_tab), '块指针表'),
             (OLD_SU_STUB, OLD_SU_LEN, '旧桩还原区')]
    spans += [(a, CH_STRIDE, '块 k=%d' % (i + 1)) for i, a in enumerate(CH_ADDRS)]
    for a, ln, name in spans:
        if not in_span(a, ln, POOL):
            raise SystemExit('%s 0x%X..0x%X 不在池内' % (name, a, a + ln))
        if not in_span(a, ln, KNOWN_ZONE):
            raise SystemExit('%s 0x%X..0x%X 不在已知落点 0x605DEB00..0x605DF000' % (name, a, a + ln))
        for b in BANNED:
            if a <= b < a + ln:
                raise SystemExit('%s 压到禁地 0x%X' % (name, b))
        if a < BANK2[1] and a + ln > BANK2[0]:
            raise SystemExit('%s 压到 bank2' % name)
    # 钩点：必须正好是 5 B 远跳，且落在 second_verify 认的钩点区
    if not in_span(HOOK, HOOK_LEN, (0x60479A20, 0x60479A30)):
        raise SystemExit('钩点 0x%X 不在 0x60479A20..0x60479A30' % HOOK)
    srt = sorted((a, ln, n) for a, ln, n in spans)
    for i in range(1, len(srt)):
        if srt[i - 1][0] + srt[i - 1][1] > srt[i][0]:
            raise SystemExit('%s 与 %s 重叠' % (srt[i - 1][2], srt[i][2]))
    if SU_STUB + len(stub) > 0x605DED44:
        raise SystemExit('扩展桩压到 SD 桩 0x605DED44')

    # ---- 基线前提（B59 该有的东西必须在） ----
    def at(addr, n, buf=None):
        buf = img if buf is None else buf
        return bytes(buf[addr - BASE:addr - BASE + n])

    pre = {}
    pre['hook_old'] = at(HOOK, HOOK_LEN) == b46.jmp_bytes(HOOK, OLD_SU_STUB)
    pre['old_stub'] = at(OLD_SU_STUB, OLD_SU_LEN) != old_stub_stock
    pre['su_ptrtab'] = (struct.unpack('<4I', at(SU_PTRTAB, 16))
                        == tuple(B61.b54.TONE_ADDRS))
    pre['stash'] = at(STASH, 4) == bytes(stock[STASH - BASE:STASH - BASE + 4])
    pre['sd_stub'] = at(B61.b54.SD_STUB, 0x28) != bytes(stock[B61.b54.SD_STUB - BASE:B61.b54.SD_STUB - BASE + 0x28])
    pre['mx_stub'] = at(B61.b54.MX_STUB, 0x2E) != bytes(stock[B61.b54.MX_STUB - BASE:B61.b54.MX_STUB - BASE + 0x2E])
    pre['mx_ptrtab'] = (struct.unpack('<4I', at(B61.b54.MX_PTRTAB, 16))
                        == tuple(B61.b54.MX_DATA + i * 18 for i in range(4)))
    pre['mx_data'] = at(B61.b54.MX_DATA, 72) != bytes(stock[B61.b54.MX_DATA - BASE:B61.b54.MX_DATA - BASE + 72])
    pre['ch_hook_stock'] = at(CH_HOOK, 5) == bytes(stock[CH_HOOK - BASE:CH_HOOK - BASE + 5])
    pre['blocks_free'] = all(at(a, CH_STRIDE) != b'\xEE' * CH_STRIDE for a in CH_ADDRS)
    pre['blk_tab_free'] = at(BLK_TAB, 16) != blk_tab
    for k, v in pre.items():
        if not v:
            raise SystemExit('基线前提不成立：%s' % k)

    # ---- 打补丁 ----
    lines = ['# B63 = B59_real + 4 extreme chroma blocks (slot52) + extended SU stub -> slot1+0x1D',
             'ram 0x%x=%s  # hook 0x60479A2B -> extended SU stub 0x%X'
             % (HOOK, b46.jmp_bytes(HOOK, SU_STUB).hex(), SU_STUB),
             'ram 0x%x=%s  # extended SU stub %d B (k from fresh stash, EXIF, tone tab, slot1+0x1D <- block[k])'
             % (SU_STUB, stub.hex(), len(stub)),
             'ram 0x%x=%s  # block ptr table [k-1] (== SU_PTRTAB+0x10)'
             % (BLK_TAB, blk_tab.hex()),
             'ram 0x%x=%s  # restore stock bytes over B59 old SU stub (%d B)'
             % (OLD_SU_STUB, old_stub_stock.hex(), OLD_SU_LEN)]
    for i, (a, blk) in enumerate(zip(CH_ADDRS, blocks)):
        cb, cr = BLOCK_SPEC[i]
        lines.append('ram 0x%x=%s  # chroma block k=%d Cb y=%#04x Cr y=%#04x'
                     % (a, blk.hex(), i + 1, cb, cr))
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/b63.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)

    if os.path.exists(OUT):
        os.remove(OUT)
    # mkpatch 拒绝把输出写进源文件所在目录 ⇒ 从 .tmp 的同一份副本起包（sha 已断言相同）
    import shutil
    base_copy = '.tmp/b63/B59_base.BIN'
    os.makedirs('.tmp/b63', exist_ok=True)
    shutil.copyfile(SRC, base_copy)
    if hashlib.sha256(open(base_copy, 'rb').read()).hexdigest() != \
            hashlib.sha256(open(SRC, 'rb').read()).hexdigest():
        raise SystemExit('基线副本 sha 不一致')
    b46.run([sys.executable, 'tools/mkpatch.py', base_copy, '--out', OUT, '--patch-file', pf])

    built = payload_of(OUT)
    diff = [i for i in range(len(built)) if built[i] != img[i]]
    allowed = [(a - BASE, ln) for a, ln, _ in spans] + [(HOOK - BASE, HOOK_LEN)]
    outside = [i for i in diff if not any(s <= i < s + ln for s, ln in allowed)]
    print('\n相对 B59 改动 %d 字节；区外 %d 字节 -> %s'
          % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    ok = not outside

    bad = [i for i in range(BANK2[0] - BASE, BANK2[1] - BASE) if built[i] != img[i]]
    print('  bank2 0x%X..0x%X 相对 B59 改动 %d 字节 -> %s'
          % (BANK2[0], BANK2[1], len(bad), 'OK' if not bad else 'FAIL'))
    ok &= not bad

    def bt(addr, n):
        return bytes(built[addr - BASE:addr - BASE + n])

    checks = [('钩点 jmp', bt(HOOK, HOOK_LEN), b46.jmp_bytes(HOOK, SU_STUB)),
              ('扩展 SU 桩', bt(SU_STUB, len(stub)), stub),
              ('块指针表', bt(BLK_TAB, len(blk_tab)), blk_tab),
              ('旧桩区==原厂', bt(OLD_SU_STUB, OLD_SU_LEN), old_stub_stock),
              ('tone 指针表未动', bt(SU_PTRTAB, 16), at(SU_PTRTAB, 16)),
              ('色度钩点==原厂', bt(CH_HOOK, 5), bytes(stock[CH_HOOK - BASE:CH_HOOK - BASE + 5])),
              ('stash 未动', bt(STASH, 4), at(STASH, 4)),
              ('m0 未动', bt(B61.b54.MATRIX_M0, 18), bytes(stock[B61.b54.MATRIX_M0 - BASE:B61.b54.MATRIX_M0 - BASE + 18]))]
    for i, (a, blk) in enumerate(zip(CH_ADDRS, blocks)):
        checks.append(('块 k=%d' % (i + 1), bt(a, CH_STRIDE), blk))
    for name, got, want in checks:
        good = got == want
        ok &= good
        print('  %-16s 一致=%s' % (name, good))

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'base': SRC, 'hook': HOOK, 'su_stub': SU_STUB, 'su_stub_len': len(stub),
           'su_ret': SU_RET, 'su_jmp_off': jmp_off, 'old_su_stub': OLD_SU_STUB,
           'su_ptrtab': SU_PTRTAB, 'blk_tab': BLK_TAB,
           'blocks': CH_ADDRS, 'block_spec': BLOCK_SPEC, 'exif_text': EXIF_TEXT,
           'spans': [(a, ln, n) for a, ln, n in spans],
           'bank2': BANK2, 'known_zone': KNOWN_ZONE,
           'chroma_hook_stock': True, 'chroma_getter_hook_installed': False}
    with io.open('.tmp/b63_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(build())
