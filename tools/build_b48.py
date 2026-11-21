# -*- coding: utf-8 -*-
"""build_b48.py —— B48：stash 分派 + 极端诊断表（四档一眼能分）

与 B47 的差别（B47 真机：开机正常、照片毫无异常）：
  B47 钩在 0x60479A23 并按"原厂 table 指针"做指纹匹配；实测三个指纹都没命中
  ⇒ 全部走"未知模式原样"，等于没改。
  B48 改回 B24h 已经真机验证过的几何：由【派发器】把 key 压成 k 存进 stash，
  上传侧按 k 选表——不依赖任何 ctx 偏移、也不依赖原厂指针值。

布局
  钩子 A 0x604B811D (7 B: 5 B jmp + 2 B 填充) -> SD 桩 0x605DED44 (36 B)
      k = ((key>>24)&3)<<3 | (key&7)，写成 32 位到 stash 0x605DED70
      回 0x604B8124（让原 bcc 用同一组标志）
  钩子 B 0x60479A2B (5 B) -> SU 桩 0x605DED80 (<64 B)
      重放 mov (a0),d0 / mov d2,a1 / add 0x400,d2
      空指针 -> 0x60479A4E；未知 k -> 用原指针 d0；k=1..4 -> 查表
      表指针目录 0x605DEE3C: [_, slot22, slot23, slot24, slot25]
      回 0x60479A3A（原厂拷贝循环）
  极端表：slot22 极亮(γ0.40) / slot23 极暗(γ2.60) / slot24 8 级海报化 / slot25 反相
"""
from __future__ import annotations

import hashlib
import io
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.chdir(ROOT)

import oly_container as oc                      # noqa: E402
import build_b46 as b46                         # noqa: E402  (复用 assemble/run)

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = 'build/B48_stash_extreme.BIN'

SD_INSERT, SD_LEN = 0x604B811D, 7
SD_STUB = 0x605DED44
SD_RET = 0x604B8124

SU_INSERT, SU_LEN = 0x60479A2B, 5
SU_STUB = 0x605DED80
SU_RET_LOAD = 0x60479A3A
SU_RET_NULL = 0x60479A4E

STASH = 0x605DED70
PTRTAB = 0x605DEE3C
TONES = 0x605D8344
SLOTS = [22, 23, 24, 25]

PH1, PH2 = 0x11111111, 0x22222222


def table_bytes(kind):
    out = []
    for i in range(256):
        v = i / 255.0
        if kind == 'bright':
            x = 1023.0 * (v ** 0.40)
        elif kind == 'dark':
            x = 1023.0 * (v ** 2.60)
        elif kind == 'poster':
            x = float(min(7, i * 8 // 256) * 146)
        elif kind == 'invert':
            x = 1023.0 - 4.0 * i
        else:
            x = 1023.0 * v
        out.append(max(0, min(1023, int(round(x)))))
    return b''.join(struct.pack('<H', v) for v in out)


SD_TMPL = r"""	.am33
	.text
	.globl	_start
_start:
	mov	d1,d2
	mov	d2,d0
	asr	24,d0
	and	3,d0
	asl	3,d0
	and	7,d1
	or	d1,d0
	mov	0x%X,a0
	mov	d0,(a0)
	mov	d2,d1
	cmp	0x1010066,d2
	jmp	-0x100000
""" % STASH

SU_TMPL = r"""	.am33
	.text
	.globl	_start
_start:
	mov	(a0),d0
	mov	d2,a1
	add	0x400,d2
	cmp	0x0,d0
	beq	Lnull
	mov	0x%X,a0
	mov	(a0),d3
	cmp	0x0,d3
	beq	Lstock
	cmp	0x4,d3
	bhi	Lstock
	mov	0x%X,a2
	add	d3,d3
	add	d3,d3
	add	d3,a2
	mov	(a2),a2
	bra	Lgo
Lstock:
	mov	d0,a2
Lgo:
	mov	(8,SP),d1
	jmp	@@J1@@
Lnull:
	jmp	@@J2@@
""" % (STASH, PTRTAB)


def assemble(text, tag, base):
    return b46.assemble(text, tag, base)


def build():
    sd = bytearray(assemble(SD_TMPL, 'b48_sd', SD_STUB))
    off = len(sd) - 5
    disp = SD_RET - (SD_STUB + off)
    sd[off + 1:off + 5] = (disp & 0xFFFFFFFF).to_bytes(4, 'little')
    sd = bytes(sd)
    if len(sd) > 40:
        raise SystemExit('SD 桩 %d B 太长' % len(sd))

    su1 = assemble(SU_TMPL.replace('@@J1@@', '0x%X' % PH1)
                           .replace('@@J2@@', '0x%X' % PH2), 'b48_su_ph', SU_STUB)
    i1 = b46.find_ph(su1, PH1)
    i2 = b46.find_ph(su1, PH2)
    su = assemble(SU_TMPL
                  .replace('@@J1@@', '0x%x - (0x%x + 0x%x)' % (SU_RET_LOAD, SU_STUB, i1))
                  .replace('@@J2@@', '0x%x - (0x%x + 0x%x)' % (SU_RET_NULL, SU_STUB, i2)),
                  'b48_su', SU_STUB)
    if len(su) > 0x50:
        raise SystemExit('SU 桩 %d B 太长' % len(su))
    for off_, want in ((i1, SU_RET_LOAD), (i2, SU_RET_NULL)):
        got = su[off_:off_ + 5]
        exp = b'\xdc' + struct.pack('<i', want - (SU_STUB + off_))
        if got != exp:
            raise SystemExit('jmp 自检失败 @%#x' % (SU_STUB + off_))

    kinds = ['bright', 'dark', 'poster', 'invert']
    tabs = {k: table_bytes(kind) for k, kind in zip(SLOTS, kinds)}
    ptr = struct.pack('<5I', 0, *[TONES + s * 0x200 for s in SLOTS])

    ranges = [(SD_INSERT, SD_LEN), (SU_INSERT, SU_LEN),
              (SD_STUB, len(sd)), (SU_STUB, len(su)),
              (STASH, 4), (PTRTAB, len(ptr))]
    ranges += [(TONES + s * 0x200, 0x200) for s in SLOTS]

    lines = ['# B48 build from stock (no persistent writes)']
    lines.append('ram 0x%x=%s  # hook A -> SD stub' % (SD_INSERT, b46.jmp_bytes(SD_INSERT, SD_STUB).hex() + '0000'))
    lines.append('ram 0x%x=%s  # hook B -> SU stub' % (SU_INSERT, b46.jmp_bytes(SU_INSERT, SU_STUB).hex()))
    lines.append('ram 0x%x=%s  # SD stub %d B' % (SD_STUB, sd.hex(), len(sd)))
    lines.append('ram 0x%x=%s  # SU stub %d B' % (SU_STUB, su.hex(), len(su)))
    lines.append('ram 0x%x=00000000  # stash init' % STASH)
    lines.append('ram 0x%x=%s  # ptr table [_,22,23,24,25]' % (PTRTAB, ptr.hex()))
    for s in SLOTS:
        lines.append('ram 0x%x=%s  # slot %d %s' % (TONES + s * 0x200, tabs[s].hex(), s, kinds[SLOTS.index(s)]))
    body = '\n'.join(lines) + '\n'
    body.encode('ascii')
    pf = '.tmp/b48.patch'
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(body)
    return dict(sd=sd, su=su, tabs=tabs, ptr=ptr, ranges=sorted(ranges), patch=pf)


def payload_of(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def main():
    info = build()
    print('SD 桩 %d B @%#x ; SU 桩 %d B @%#x' % (len(info['sd']), SD_STUB, len(info['su']), SU_STUB))
    if os.path.exists(OUT):
        os.remove(OUT)
    b46.run([sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT, '--patch-file', info['patch']])

    stock = payload_of(SRC)
    built = payload_of(OUT)
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    outside = [i for i in diff if not any(a - BASE <= i < a - BASE + l for a, l in info['ranges'])]
    print('\n改动 %d 字节；区外 %d 字节 -> %s' % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    ok = not outside

    def at(addr, blob):
        n = blob if isinstance(blob, int) else len(blob)
        return built[addr - BASE: addr - BASE + n]

    checks = [
        ('hook A', at(SD_INSERT, 7), b46.jmp_bytes(SD_INSERT, SD_STUB) + b'\x00\x00'),
        ('hook B', at(SU_INSERT, 5), b46.jmp_bytes(SU_INSERT, SU_STUB)),
        ('SD 桩', at(SD_STUB, len(info['sd'])), info['sd']),
        ('SU 桩', at(SU_STUB, len(info['su'])), info['su']),
        ('指针目录', at(PTRTAB, len(info['ptr'])), info['ptr']),
    ]
    for s in SLOTS:
        checks.append(('slot %d' % s, at(TONES + s * 0x200, 0x200), info['tabs'][s]))
    for name, got, want in checks:
        good = got == want
        ok &= good
        print('  %-8s 一致=%s' % (name, good))

    m0 = at(0x605ECECA, 18)
    print('  原厂 m0 未动=%s' % (m0 == stock[0x605ECECA - BASE: 0x605ECECA - BASE + 18]))
    ok &= m0 == stock[0x605ECECA - BASE: 0x605ECECA - BASE + 18]

    with open(OUT, 'rb') as f:
        whole = f.read()
    print('  BIN %d B sha256=%s' % (len(whole), hashlib.sha256(whole).hexdigest()))
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
