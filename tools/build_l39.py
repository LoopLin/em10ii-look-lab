#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_l39.py —— L39 keyprobe：把 IMGPRO 的 mode 字段与 key 一起抓进 EXIF

只回答一个问题：**Vivid 与 Natural 在 ctx（IMGPRO_MDL_CREATE_PARAM）里究竟哪一
个字节不同**。所以它不改任何功能路径，只做两处只读快照：

  钩点 A 0x604B811D（7 B = 5 B jmp + 2 B 填充）-> SD 桩 0x605DED44
      a2 = ctx（该函数入口 mov a0,a2），重放 mov d1,d2（原指令）
      把完整 32 位 key 写 KEY_STASH（沿用 L30 已验证的 0x605DED70 方案，这里换址）
      再把 ctx 的 6 个待查字节写 PROBE_STASH（**不直接写 EXIF**：EXIF 缓冲区何时
      被模板覆盖尚未确认，故一律在 EXIF 组装那一刻再落地）
      复现原厂 cmp 0x01010066,d2 的 flags，尾回 0x604B8124

  钩点 B 0x60479A2B（5 B）-> SU 桩 0x605DEDA0
      重放原厂三条 mov (a0),d0 / mov d2,a1 / add 0x400,d2
      读 KEY_STASH 现算 k = ((key>>24)&3)<<3 | (key&7)，k∈1..4 时写
        EXIF 0x61894C90 = "k=N\\0"、0x61894C94 = key 四字节（与既有判别字段同格式）
      **不做 tone 指针改写**（d0 原样保留，走原厂 *(ctx+0x1230) 路径）
      把 PROBE_STASH 的 8 B 拷到 EXIF 探针区 0x61894CA0..0x61894CA7
      尾回 0x60479A32（原厂 cmp 0,d0 / beq … 由原厂代码完成）

探针区布局（EXIF UserComment，0x61894C90 起 117 B）
  +0x00 u32 "k=N\\0"       ; 与 B51/L30 同格式
  +0x04 u32 key            ; 完整 32 位 key
  +0x08 'B75A'             ; magic（tools/read_probe.py 直接认）
  +0x0C u32 key            ; 同 +0x04，供 read_probe 打印
  +0x10 u32 PB             ; 字节 0..3 = u8[ctx+0x424] / 0x427 / 0x42B / 0x42E
                              ★ PB 最低字节 = colorTone 候选（NATURAL 若=2 就是它）
  +0x14 u32 out            ; 字节 0..1 = u8[ctx+0x42F] / u8[ctx+0x430]，2..3 补 0

落点安全依据（存档）
  - 0x605DED44..0x605DED6B = 40 B 全零（LUT 之间的对齐空洞），B48/B54 的 SD 桩一直
    用这段。
  - PROVEN = 0x605DECD4..0x605DEE4C：**L30（真机判读 sha 9381ddeb）** 在这条带上覆盖了
    157 个非零字节（该带是 u16 单调曲线 0x0354/0x0358/0x035b…/0x020c/0x0210…）而相机
    工作正常 ⇒ 这条曲线在运行期不被读，属于死数据。本包全部落点都在 PROVEN 内，
    其中 PROBE_STASH 0x605DED88 / KEY_STASH 0x605DED90 压掉 10 个非零曲线字节，
    有 L30 的同一先例背书。不碰 bank2（0x605DEF4E 起，启动期重写）。
  - 不碰 0x605E9CCC（那张表已证伪，改了也没用，留原样做对照）。

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

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/L40_keyprobe.BIN'

# ---- 钩子 ----
SD_INSERT, SD_LEN = 0x604B811D, 7      # mov d1,d2 + cmp 0x01010066,d2
SD_RET = 0x604B8124                    # 原厂 bcc 0x604B8129
SD_STUB = 0x605DED44

SU_INSERT, SU_LEN = 0x60479A2B, 5
SU_RET = 0x60479A32
SU_STUB = 0x605DEDA0

# ---- 数据落点 ----
PROBE_STASH = 0x605DED88               # 8 B：6 个探针字节 + 2 B 填充
KEY_STASH = 0x605DED90                 # 4 B：完整 32 位 key

EXIF_TEXT = 0x61894C90                 # UserComment 文本区（8 B 字符集之后 117 B）
EXIF_KEY = 0x61894C94
EXIF_PROBE = 0x61894CA0                # 探针数据（+0x18，PB/out 两个 u32）
EXIF_REC = 0x61894C98                  # 探针记录基址（unc+0x10，read_probe.py 认的 B75A）
B75A_MAGIC = 0x41353742                # 'B75A' 小端 u32
EXIF_LEAD = 0x313D6B                   # "k=" + '1' + NUL（小端）

WINDOW = (0x605D8344, 0x605DEF44)      # 全部落点必须在此区间内
PROVEN = (0x605DECD4, 0x605DEE4C)      # L30 已用 157 个非零字节证明这段是死曲线
BANK2 = (0x605DEF4E, 0x605DEF4E + 0x800)

SD_LIMIT = 0x44                        # 0x605DED44..0x605DED87
SU_LIMIT = 0xAC                        # 0x605DEDA0..0x605DEE4B（PROVEN 带尾 0x605DEE4C）

PH1 = 0x11111111

SD_TMPL = r"""
	.am33
	.text
	.globl	_start
_start:
	mov	d1,d2
	mov	0x%(key_stash)X,a0
	mov	d2,(a0)
	mov	0x%(probe_stash)X,a1
	movbu	(0x424,a2),d0
	movbu	d0,(0,a1)
	movbu	(0x427,a2),d0
	movbu	d0,(1,a1)
	movbu	(0x42b,a2),d0
	movbu	d0,(2,a1)
	movbu	(0x42e,a2),d0
	movbu	d0,(3,a1)
	movbu	(0x42f,a2),d0
	movbu	d0,(4,a1)
	movbu	(0x430,a2),d0
	movbu	d0,(5,a1)
	cmp	0x1010066,d2
	jmp	@@JRET@@
"""

SU_TMPL = r"""
	.am33
	.text
	.globl	_start
_start:
	mov	(a0),d0
	mov	d2,a1
	add	0x400,d2
	add	-12,sp
	mov	a0,(0,sp)
	mov	a1,(4,sp)
	mov	d2,(8,sp)
	mov	0x%(key_stash)X,a2
	mov	(a2),d3
	mov	d3,d1
	and	7,d1
	asr	21,d3
	and	0x18,d3
	or	d3,d1
	add	-1,d1
	cmp	3,d1
	bhi	Lprobe
	mov	d1,d3
	asl	0x10,d3
	add	0x%(exif_lead)X,d3
	mov	0x%(exif_text)X,a2
	mov	d3,(a2)
	mov	0x%(key_stash)X,a0
	mov	(a0),d3
	mov	d3,(4,a2)
Lprobe:
	mov	0x%(probe_stash)X,a2
	mov	0x%(exif_rec)X,a0
	mov	0x%(b75a)X,d3
	mov	d3,(0,a0)
	mov	0x%(key_stash)X,a1
	mov	(a1),d3
	mov	d3,(4,a0)
	movbu	(0,a2),d3
	movbu	d3,(8,a0)
	movbu	(1,a2),d3
	movbu	d3,(9,a0)
	movbu	(2,a2),d3
	movbu	d3,(10,a0)
	movbu	(3,a2),d3
	movbu	d3,(11,a0)
	movbu	(4,a2),d3
	movbu	d3,(12,a0)
	movbu	(5,a2),d3
	movbu	d3,(13,a0)
	clr	d3
	movbu	d3,(14,a0)
	movbu	d3,(15,a0)
	mov	(0,sp),a0
	mov	(4,sp),a1
	mov	(8,sp),d2
	add	12,sp
	jmp	@@JRET@@
"""

FMT = dict(key_stash=KEY_STASH, probe_stash=PROBE_STASH, exif_text=EXIF_TEXT,
           exif_probe=EXIF_PROBE, exif_rec=EXIF_REC, b75a=B75A_MAGIC,
           exif_lead=EXIF_LEAD)


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def assemble_stub(tmpl, tag, base, ret):
    text = (tmpl % FMT).replace('\\t', '\t').replace('\\n', '\n')
    ph = b46.assemble(text.replace('@@JRET@@', '0x%X' % PH1), tag + '_ph', base)
    j = b46.find_ph(ph, PH1)
    blob = b46.assemble(text.replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (ret, base, j)),
                        tag, base)
    want = b'\xdc' + struct.pack('<i', ret - (base + j))
    if blob[j:j + 5] != want:
        raise SystemExit('%s 尾 jmp 自检失败 @%#x' % (tag, base + j))
    return bytes(blob), j


def check_layout(items):
    lo, hi = WINDOW
    spans = sorted((a, a + n) for a, n, _ in items)
    for a, e in spans:
        if not (lo <= a and e <= hi):
            raise SystemExit('落点 0x%x..0x%x 越出窗口 0x%x..0x%x' % (a, e, lo, hi))
        if a % 2:
            raise SystemExit('落点 0x%x 未对齐' % a)
    for (a1_, e1), (a2_, e2) in zip(spans, spans[1:]):
        if e1 > a2_:
            raise SystemExit('落点重叠 0x%x..0x%x / 0x%x..0x%x' % (a1_, e1, a2_, e2))
    plo, phi = PROVEN
    for a, e in spans:
        if not (plo <= a and e <= phi):
            raise SystemExit('落点 0x%x..0x%x 不在 L30 已验证可覆盖带 0x%x..0x%x 内' % (a, e, plo, phi))
    b2lo, b2hi = BANK2
    for a, e in spans:
        if a < b2hi and e > b2lo:
            raise SystemExit('落点 0x%x..0x%x 侵入 bank2' % (a, e))


def build():
    sd, sdj = assemble_stub(SD_TMPL, 'l39_sd', SD_STUB, SD_RET)
    if len(sd) > SD_LIMIT:
        raise SystemExit('SD 桩 %d B 超过 %d' % (len(sd), SD_LIMIT))
    su, suj = assemble_stub(SU_TMPL, 'l39_su', SU_STUB, SU_RET)
    if len(su) > SU_LIMIT:
        raise SystemExit('SU 桩 %d B 超过 %d' % (len(su), SU_LIMIT))
    if SD_STUB + len(sd) > PROBE_STASH or KEY_STASH + 4 > SU_STUB:
        raise SystemExit('桩与数据落点重叠')

    items = [(SD_STUB, len(sd), 'SD 桩'), (PROBE_STASH, 8, 'probe stash'),
             (KEY_STASH, 4, 'key stash'), (SU_STUB, len(su), 'SU 桩')]
    check_layout(items)

    hook_sd = b46.jmp_bytes(SD_INSERT, SD_STUB) + b'\x00\x00'
    hook_su = b46.jmp_bytes(SU_INSERT, SU_STUB)
    if len(hook_sd) != SD_LEN or len(hook_su) != SU_LEN:
        raise SystemExit('钩子长度不符')

    lines = ['# L39 keyprobe from stock: ctx+0x424/427/42B/42E/42F/430 + key -> EXIF',
             'ram 0x%x=%s  # hook A 0x604B811D -> SD stub (replays mov d1,d2)' % (SD_INSERT, hook_sd.hex()),
             'ram 0x%x=%s  # hook B 0x60479A2B -> SU stub' % (SU_INSERT, hook_su.hex()),
             'ram 0x%x=%s  # SD stub %d B: key -> KEY_STASH, ctx 6 bytes -> PROBE_STASH' % (SD_STUB, sd.hex(), len(sd)),
             'ram 0x%x=%s  # SU stub %d B: k=N + key32 + 8 probe bytes -> EXIF' % (SU_STUB, su.hex(), len(su)),
             'ram 0x%x=%s  # zero init, written at runtime' % (PROBE_STASH, '00' * 8),
             'ram 0x%x=%s  # zero init, written at runtime' % (KEY_STASH, '00' * 4)]
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/l39.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(sd=sd, su=su, sdj=sdj, suj=suj, hook_sd=hook_sd, hook_su=hook_su,
                items=items, patch=pf)


def main():
    info = build()
    print('SD 桩 %d B @%#x（尾 jmp @%#x）; SU 桩 %d B @%#x（尾 jmp @%#x）'
          % (len(info['sd']), SD_STUB, SD_STUB + info['sdj'],
             len(info['su']), SU_STUB, SU_STUB + info['suj']))

    if os.path.exists(OUT):
        os.remove(OUT)
    b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT, '--patch-file', info['patch']])

    stock = payload_of(SRC)
    built = payload_of(OUT)
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    spans = [(a, n) for a, n, _ in info['items']] + [(SD_INSERT, SD_LEN), (SU_INSERT, SU_LEN)]
    outside = [i for i in diff
               if not any(a - BASE <= i < a - BASE + n for a, n in spans)]
    print('改动 %d 字节；区外 %d 字节 -> %s'
          % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    ok = not outside

    def at(addr, n):
        return bytes(built[addr - BASE: addr - BASE + n])

    for name, got, want in (('hook A', at(SD_INSERT, SD_LEN), info['hook_sd']),
                            ('hook B', at(SU_INSERT, SU_LEN), info['hook_su']),
                            ('SD 桩', at(SD_STUB, len(info['sd'])), info['sd']),
                            ('SU 桩', at(SU_STUB, len(info['su'])), info['su'])):
        good = got == want
        ok &= good
        print('  %-8s 一致=%s' % (name, good))

    # 关键不变量：不动任何 tone 表 / 不动 0x605E9CCC / 不动 EXIF 文本区
    guards = [('键表 0x605E9CC8', 0x605E9CC8, 12),
              ('EXIF 文本区', EXIF_TEXT, 117),
              ('弯曲门 0x604B9F1F', 0x604B9F1F, 1),
              ('tone 槽 22', 0x605DAF44, 0x200),
              ('tone 槽 25', 0x605DB544, 0x200)]
    for name, a, n in guards:
        same = at(a, n) == bytes(stock[a - BASE:a - BASE + n])
        ok &= same
        print('  %-18s 未动=%s' % (name, same))

    bank2_bad = [i for i in range(BANK2[0] - BASE, BANK2[1] - BASE) if built[i] != stock[i]]
    print('  bank2 改动 %d 字节 -> %s' % (len(bank2_bad), 'OK' if not bank2_bad else 'FAIL'))
    ok &= not bank2_bad

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'sd_insert': SD_INSERT, 'sd_len_hook': SD_LEN, 'sd_stub': SD_STUB, 'sd_stub_len': len(info['sd']),
           'sd_ret': SD_RET, 'su_insert': SU_INSERT, 'su_stub': SU_STUB, 'su_stub_len': len(info['su']),
           'su_ret': SU_RET, 'probe_stash': PROBE_STASH, 'key_stash': KEY_STASH,
           'exif_text': EXIF_TEXT, 'exif_key': EXIF_KEY, 'exif_probe': EXIF_PROBE,
           'probe_offsets': [0x424, 0x427, 0x42B, 0x42E, 0x42F, 0x430],
           'window': WINDOW, 'bank2': BANK2, 'spans': spans,
           'patch_bytes_total': len(diff)}
    with io.open('.tmp/l39_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
