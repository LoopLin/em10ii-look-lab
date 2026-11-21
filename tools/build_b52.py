#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_b52.py —— B52：纯仪表包（只离线）

B51 真机结果：矩阵通、EXIF 通，但暴露出两件事 ——
  1) i-e 与 natural 撞同一个 k（`k=((key>>24)&3)<<3 | (key&7)` 分辨力不够）；
  2) 四档都"蒙白"（比原厂亮 ~128 级），需要把变量降到最小再隔离。
B52 因此只当仪表：**不改变画面**，把每个档的**完整 32 位 key** 抄进 EXIF。

  A 0x604B811D (7 B) -> SD 桩 0x605DED44（19 B）
        `mov d1,d2` 原样重放；把**完整 32 位 key（d1）**写进 stash 0x605DED70；
        再 `cmp 0x1010066,d2` 复原标志，跳回 0x604B8124。
  B 0x60479A2B (5 B) -> SU 桩 0x605DED80（39 B）
        重放 `mov (a0),d0` / `mov d2,a1` / `add 0x400,d2`；
        `a2 = *(ctx+0x1230)`（原厂表指针，tone 一个字节都不换）；
        把 stash 的 4 字节**原样**（二进制，不是 ASCII）写进 EXIF
        UserComment 文本区 0x61894C90（X=0x618943FC 的 X+0x894）；
        `d0==0` 走原厂空指针分支。

**没有** tone 槽写入、**没有** tone 选表逻辑、**没有**矩阵钩子（0x604B6AD6 保持原厂
`call 0x604b6a95`、m0 0x605ECECA 保持原厂）。桩里唯一的写：stash（4 B）与 EXIF 文本槽。

照片应当与原厂一模一样 —— 这就是"画面正常"的对照组。
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
OUT = 'build/B52_keytap.BIN'

SD_INSERT, SD_LEN = 0x604B811D, 7
SD_STUB, SD_RET = 0x605DED44, 0x604B8124

SU_INSERT, SU_LEN = 0x60479A2B, 5
SU_STUB = 0x605DED80
SU_RET_LOAD, SU_RET_NULL = 0x60479A3A, 0x60479A4E

STASH = 0x605DED70          # 32 位完整 key
EXIF_TEXT = 0x61894C90      # UserComment(0x9286) 文本区，125 B 里的前 4 字节

TONES = 0x605D8344
POOL_LEN = 54 * 0x200
WINDOW = (TONES, TONES + POOL_LEN)      # 落点窗口（tone 池）
M0 = 0x605ECECA
MX_HOOK = 0x604B6AD6

PH1, PH2 = 0x11111111, 0x22222222

SD_TMPL = r"""\t.am33
\t.text
\t.globl\t_start
_start:
\tmov\td1,d2
\tmov\t0x%X,a0
\tmov\td1,(a0)
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
\tmov\td0,a2
\tmov\t0x%X,a0
\tmov\t(a0),d3
\tmov\t0x%X,a0
\tmov\td3,(a0)
\tmov\t(8,SP),d1
\tjmp\t@@J1@@
Lnull:
\tjmp\t@@J2@@
""" % (STASH, EXIF_TEXT)

SD_TMPL, SU_TMPL = (s.replace('\\t', '\t') for s in (SD_TMPL, SU_TMPL))


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def check_layout(items):
    lo, hi = WINDOW
    s = sorted(items)
    for i, (a, ln, name) in enumerate(s):
        if a < lo or a + ln > hi:
            raise SystemExit('%s 0x%x..0x%x 越界（窗口 %#x..%#x）' % (name, a, a + ln, lo, hi))
        if i and s[i - 1][0] + s[i - 1][1] > a:
            raise SystemExit('%s 与 %s 重叠' % (name, s[i - 1][2]))


def build():
    sd = bytearray(b46.assemble(SD_TMPL, 'b52_sd', SD_STUB))
    off = len(sd) - 5
    sd[off + 1:off + 5] = ((SD_RET - (SD_STUB + off)) & 0xFFFFFFFF).to_bytes(4, 'little')
    sd = bytes(sd)
    if len(sd) > 40:
        raise SystemExit('SD 桩 %d B 太长（<=40）' % len(sd))
    exp = b'\xdc' + struct.pack('<i', SD_RET - (SD_STUB + off))
    if sd[off:off + 5] != exp:
        raise SystemExit('SD 桩 jmp 自检失败')

    su1 = b46.assemble(SU_TMPL.replace('@@J1@@', '0x%X' % PH1)
                       .replace('@@J2@@', '0x%X' % PH2), 'b52_su_ph', SU_STUB)
    i1 = b46.find_ph(su1, PH1)
    i2 = b46.find_ph(su1, PH2)
    su = b46.assemble(SU_TMPL
                      .replace('@@J1@@', '0x%x - (0x%x + 0x%x)' % (SU_RET_LOAD, SU_STUB, i1))
                      .replace('@@J2@@', '0x%x - (0x%x + 0x%x)' % (SU_RET_NULL, SU_STUB, i2)),
                      'b52_su', SU_STUB)
    if len(su) > 0x50:
        raise SystemExit('SU 桩 %d B 太长（<=0x50）' % len(su))
    for off_, want in ((i1, SU_RET_LOAD), (i2, SU_RET_NULL)):
        got = su[off_:off_ + 5]
        exp = b'\xdc' + struct.pack('<i', want - (SU_STUB + off_))
        if got != exp:
            raise SystemExit('SU 桩 jmp 自检失败 @%#x' % (SU_STUB + off_))

    check_layout([(SD_STUB, len(sd), 'SD 桩'),
                  (STASH, 4, 'stash'),
                  (SU_STUB, len(su), 'SU 桩')])

    ranges = sorted([(SD_INSERT, SD_LEN), (SU_INSERT, SU_LEN),
                     (SD_STUB, len(sd)), (STASH, 4), (SU_STUB, len(su))])

    lines = ['# B52 keytap from stock: instrument only, picture unchanged (offline)',
             'ram 0x%x=%s  # hook A 0x604B811D -> SD stub (full 32-bit key)' % (SD_INSERT, (b46.jmp_bytes(SD_INSERT, SD_STUB) + b'\x00\x00').hex()),
             'ram 0x%x=%s  # hook B 0x60479A2B -> SU stub (key -> EXIF UserComment)' % (SU_INSERT, b46.jmp_bytes(SU_INSERT, SU_STUB).hex()),
             'ram 0x%x=%s  # SD stub %d B' % (SD_STUB, sd.hex(), len(sd)),
             'ram 0x%x=%s  # SU stub %d B' % (SU_STUB, su.hex(), len(su)),
             'ram 0x%x=00000000  # stash init' % STASH]
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/b52.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(sd=sd, su=su, ranges=ranges, patch=pf)


def main():
    info = build()
    print('SD 桩 %d B @%#x ; SU 桩 %d B @%#x' % (len(info['sd']), SD_STUB, len(info['su']), SU_STUB))
    print('stash @%#x（32 位完整 key）-> EXIF 文本槽 @%#x（4 字节原样）' % (STASH, EXIF_TEXT))

    if os.path.exists(OUT):
        os.remove(OUT)
    b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT, '--patch-file', info['patch']])

    stock = payload_of(SRC)
    built = payload_of(OUT)
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    hit = [(a, l) for a, l in info['ranges']]
    outside = [i for i in diff if not any(a - BASE <= i < a - BASE + l for a, l in hit)]
    print('\n改动 %d 字节；区外 %d 字节 -> %s' % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    ok = not outside

    def at(addr, n):
        return built[addr - BASE: addr - BASE + n]

    for name, got, want in (('hook A', at(SD_INSERT, 7), b46.jmp_bytes(SD_INSERT, SD_STUB) + b'\x00\x00'),
                            ('hook B', at(SU_INSERT, 5), b46.jmp_bytes(SU_INSERT, SU_STUB)),
                            ('SD 桩', at(SD_STUB, len(info['sd'])), info['sd']),
                            ('SU 桩', at(SU_STUB, len(info['su'])), info['su']),
                            ('stash 初值', at(STASH, 4), b'\x00\x00\x00\x00')):
        good = got == want
        ok &= good
        print('  %-12s 一致=%s' % (name, good))

    # tone 池：除桩区之外必须逐字节等于 stock；四张 B51 用过的槽也没被写
    occ = [(a - TONES, l) for a, l in info['ranges'] if TONES <= a < TONES + POOL_LEN]
    bad = [i for i in range(POOL_LEN)
           if built[TONES - BASE + i] != stock[TONES - BASE + i]
           and not any(o <= i < o + l for o, l in occ)]
    print('  tone 池区外改动=%d（桩区占 %d B / %d B）-> %s'
          % (len(bad), sum(l for _o, l in occ), POOL_LEN, 'OK' if not bad else 'FAIL'))
    ok &= not bad
    slots = {s: (at(TONES + s * 0x200, 0x200) == stock[TONES + s * 0x200 - BASE: TONES + s * 0x200 - BASE + 0x200])
             for s in (9, 11, 12, 13)}
    print('  槽 9/11/12/13 == 原厂：%s' % slots)
    ok &= all(slots.values())

    mx_hook_ok = at(MX_HOOK, 3) == stock[MX_HOOK - BASE: MX_HOOK - BASE + 3]
    m0_ok = at(M0, 18) == stock[M0 - BASE: M0 - BASE + 18]
    print('  矩阵钩子 0x%x 仍是原厂 call=%s ；m0 未动=%s' % (MX_HOOK, mx_hook_ok, m0_ok))
    ok &= mx_hook_ok and m0_ok

    with open(OUT, 'rb') as f:
        whole = f.read()
    lay = {'pkg': OUT, 'sha256': hashlib.sha256(whole).hexdigest(), 'len': len(whole),
           'sd_stub': SD_STUB, 'sd_len': len(info['sd']), 'su_stub': SU_STUB,
           'su_len': len(info['su']), 'stash': STASH, 'exif_text': EXIF_TEXT,
           'ranges': info['ranges']}
    with io.open('.tmp/b52_layout.json', 'w', encoding='utf-8') as f:
        json.dump(lay, f, ensure_ascii=False, indent=2)
    print('  BIN %d B sha256=%s' % (len(whole), lay['sha256']))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
