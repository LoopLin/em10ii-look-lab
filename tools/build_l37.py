#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_l37.py —— L37 dialprobe：把四个档位下的 tone 拨盘值取进 EXIF

本包只回答一个问题：**弯曲前那 5 个 int16 拨盘，在 Vivid/Natural/Muted/Portrait
四档下到底是多少**。所以它不改任何功能路径，只在一处做只读快照：

  钩点 0x604B9EBF（tone 门入口，8 B = 5 B jmp + 3 B 填充）-> SD 桩 0x605DED44
      a0 = 帧作业结构指针（job）
      重放 mov (4,a0),a2 / mov a0,d3            ; a2 = model, d3 = job
      再取 a3 = mov (12,a0)                     ; a3 = S = model+0x20C
      快照写进 EXIF UserComment 文本区 0x61894C90（117 B，等长改写）
      回 0x604B9EC4（原第 3 条指令 mov (12,a0),a3；它会把 a3 再算一遍，无害）

字段（详见 docs/analysis/dial-probe.md）：
  +0x00 u32 "k=7\x00"                   ; 帧标记，与 B51 SU 桩同格式
  +0x04 u32 "dL37"                     ; 包 key
  +0x08 u16 magic 0x5044 ('D','P')
  +0x0A u8  version 1
  +0x0B u8  valid = *(model+0x1616)    ; 拨盘有效字节 / 弯曲门条件字节
  +0x0C 5 x u16  dial_p = *(p+2i)      ; p = *(model+0x3B4)，门实际吃的那 5 个值
  +0x16 5 x u16  dial_m = *(model+0x159C+2i)  ; 直读候选区，用来和 dial_p 对账
  +0x20 u32 ptr = *(S+0x1230)          ; 弯曲后换上的表指针（== S+0x1234 即弯了）
  +0x24 u32 S     = *(job+12)
  +0x28 u32 model = *(job+4)
  +0x2C u32 p     = *(model+0x3B4)     ; 门实际解引用的拨盘指针
  +0x30 u32 sum32 = word[+0x04..+0x2F] 之和（含自身之前的全部字）

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

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/L37_dialprobe.BIN'

HOOK, HOOK_LEN = 0x604B9EBF, 8          # 5 B jmp + 3 B 填充（跳过）
HOOK_RET = 0x604B9EC4                   # 原第 3 条指令 mov (12,a0),a3
STUB = 0x605DED44                       # SD 桩可用区
GATE_BYTE = 0x604B9F1F                  # 弯曲门 C8（本包不动它）

WINDOW = (0x605D8344, 0x605DEF44)       # 落点必须全在这个区间内
SD_LIMIT = 0x1C0                        # 桩长上限（窗口内还留余量给别的包）

EXIF_TEXT = 0x61894C90                  # UserComment 文本区（8 B 字符集之后 117 B）
EXIF_TEXT_LEN = 117

PH1 = 0x11111111

STUB_TMPL = r"""\t.am33
\t.text
\t.globl\t_start
_start:
\tadd\t-0x8,sp
\tmov\td2,(0,sp)
\tmov\ta0,(4,sp)
\tmov\t(4,a0),a2
\tmov\ta0,d3
\tmov\t(12,a0),a3
\tmov\t0x%X,a1
\tmov\t0x%X,d0
\tmov\td0,(a1)
\tmov\t0x%X,d0
\tmov\td0,(4,a1)
\tmov\t0x%X,d0
\tmovhu\td0,(8,a1)
\tmovbu\t(0x1616,a2),d1
\tmov\td1,d0
\tasl\t8,d0
\tadd\t0x1,d0
\tmovhu\td0,(10,a1)
\tmov\t0x3b4,a0
\tadd\ta2,a0
\tmov\t(a0),a0
\tcmp\t0x0,d1
\tbeq\tLnod
\tcmp\t0x0,a0
\tbeq\tLnod
\tmovhu\t(0,a0),d0
\tmovhu\td0,(12,a1)
\tmovhu\t(2,a0),d0
\tmovhu\td0,(14,a1)
\tmovhu\t(4,a0),d0
\tmovhu\td0,(16,a1)
\tmovhu\t(6,a0),d0
\tmovhu\td0,(18,a1)
\tmovhu\t(8,a0),d0
\tmovhu\td0,(20,a1)
\tbra\tLdone
Lnod:
\tmov\t0x8001,d0
\tmovhu\td0,(12,a1)
\tmovhu\td0,(14,a1)
\tmovhu\td0,(16,a1)
\tmovhu\td0,(18,a1)
\tmovhu\td0,(20,a1)
Ldone:
\tmovhu\t(0x159c,a2),d0
\tmovhu\td0,(22,a1)
\tmovhu\t(0x159e,a2),d0
\tmovhu\td0,(24,a1)
\tmovhu\t(0x15a0,a2),d0
\tmovhu\td0,(26,a1)
\tmovhu\t(0x15a2,a2),d0
\tmovhu\td0,(28,a1)
\tmovhu\t(0x15a4,a2),d0
\tmovhu\td0,(30,a1)
\tmov\t0x1230,a0
\tadd\ta3,a0
\tmov\t(a0),d0
\tmov\td0,(32,a1)
\tmov\ta3,d0
\tmov\td0,(36,a1)
\tmov\ta2,d0
\tmov\td0,(40,a1)
\tmov\t0x3b4,a0
\tadd\ta2,a0
\tmov\t(a0),d0
\tmov\td0,(44,a1)
\tclr\td1
\tmov\t0x%X,a0
\tmov\t(0,a0),d0
\tadd\td0,d1
\tmov\t(4,a0),d0
\tadd\td0,d1
\tmov\t(8,a0),d0
\tadd\td0,d1
\tmov\t(12,a0),d0
\tadd\td0,d1
\tmov\t(16,a0),d0
\tadd\td0,d1
\tmov\t(20,a0),d0
\tadd\td0,d1
\tmov\t(24,a0),d0
\tadd\td0,d1
\tmov\t(28,a0),d0
\tadd\td0,d1
\tmov\t(32,a0),d0
\tadd\td0,d1
\tmov\t(36,a0),d0
\tadd\td0,d1
\tmov\t(40,a0),d0
\tadd\td0,d1
\tmov\td1,(48,a1)
\tmov\t(0,sp),d2
\tmov\t(4,sp),a0
\tadd\t0x8,sp
\tjmp\t@@JRET@@
"""

# 常量（与 read_dial_probe.py / dial-probe.md 保持一致）
K_WORD = 0x00373D6B       # b'k=7\x00'
KEY_WORD = 0x37334C64     # b'dL37'
MAGIC = 0x5044            # b'DP'
VERSION = 0x01
NOD_SENTINEL = 0x8001
PAYLOAD_LEN = 0x34        # 52 B（<=117）


def stub_template():
    return (STUB_TMPL % (EXIF_TEXT, K_WORD, KEY_WORD, MAGIC, EXIF_TEXT + 4)
            ).replace('\\t', '\t').replace('\\n', '\n')


def build():
    ph = b46.assemble(stub_template().replace('@@JRET@@', '0x%X' % PH1), 'l37_ph', STUB)
    j = b46.find_ph(ph, PH1)
    stub = b46.assemble(stub_template()
                        .replace('@@JRET@@', '0x%x - (0x%x + 0x%x)' % (HOOK_RET, STUB, j)),
                        'l37_stub', STUB)
    if len(stub) > SD_LIMIT:
        raise SystemExit('SD 桩 %d B 太长（<=%d）' % (len(stub), SD_LIMIT))
    want = b'\xdc' + struct.pack('<i', HOOK_RET - (STUB + j))
    if stub[j:j + 5] != want:
        raise SystemExit('桩尾 jmp 自检失败 @%#x' % (STUB + j))

    hook = b46.jmp_bytes(HOOK, STUB) + b'\x00\x00\x00'

    # 落点检查：桩在窗口内、4 对齐，且不与 EXIF/门字节重叠
    lo, hi = WINDOW
    if not (lo <= STUB and STUB + len(stub) <= hi):
        raise SystemExit('桩 0x%x..0x%x 越界（窗口 %#x..%#x）'
                         % (STUB, STUB + len(stub), lo, hi))
    if STUB % 4:
        raise SystemExit('桩未 4 对齐')
    if not (BASE <= HOOK < 0x60600000):
        raise SystemExit('钩点不在代码段')

    ranges = sorted([(HOOK, HOOK_LEN), (STUB, len(stub))])

    lines = ['# L37 build from stock: dial probe at tone gate entry (offline only)',
             'ram 0x%x=%s  # hook 0x604B9EBF -> SD stub (8B: jmp + 3B pad)'
             % (HOOK, hook.hex()),
             'ram 0x%x=%s  # SD stub %d B' % (STUB, stub.hex(), len(stub))]
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/l37.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(hook=hook, stub=stub, ranges=ranges, patch=pf, jret_off=j)


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def main():
    info = build()
    print('钩点 0x%08X (%d B) -> 桩 0x%08X (%d B)，回 0x%08X'
          % (HOOK, HOOK_LEN, STUB, len(info['stub']), HOOK_RET))
    print('EXIF 文本区 0x%08X，载荷 %d B / %d B' % (EXIF_TEXT, PAYLOAD_LEN, EXIF_TEXT_LEN))

    if os.path.exists(OUT):
        os.remove(OUT)
    stock_sha = hashlib.sha256(open(SRC, 'rb').read()).hexdigest()
    b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT,
             '--patch-file', info['patch'], '--expect-sha256', stock_sha])

    stock = payload_of(SRC)
    built = payload_of(OUT)
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    outside = [i for i in diff
               if not any(a - BASE <= i < a - BASE + l for a, l in info['ranges'])]
    ok = not outside
    print('\n改动 %d 字节；区外 %d 字节 -> %s'
          % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))

    def at(addr, n):
        return built[addr - BASE: addr - BASE + n]

    hook_ok = at(HOOK, HOOK_LEN) == info['hook']
    stub_ok = at(STUB, len(info['stub'])) == info['stub']
    gate_ok = at(GATE_BYTE, 1) == stock[GATE_BYTE - BASE: GATE_BYTE - BASE + 1]
    exif_ok = at(EXIF_TEXT, EXIF_TEXT_LEN) == stock[EXIF_TEXT - BASE: EXIF_TEXT - BASE + EXIF_TEXT_LEN]
    for name, good in (('钩点', hook_ok), ('桩', stub_ok), ('门字节未动', gate_ok),
                       ('EXIF 区未动', exif_ok)):
        ok &= good
        print('  %-10s 一致=%s' % (name, good))

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'hook': HOOK, 'hook_len': HOOK_LEN, 'stub': STUB, 'stub_len': len(info['stub']),
           'hook_ret': HOOK_RET, 'exif_text': EXIF_TEXT, 'payload_len': PAYLOAD_LEN,
           'gate_byte': GATE_BYTE, 'ranges': info['ranges']}
    with io.open('.tmp/l37_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('  布局表 .tmp/l37_layout.json')
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
