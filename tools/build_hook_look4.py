# -*- coding: utf-8 -*-
"""B13_look4：按 picture mode 选 tone 曲线的代码 hook（E-M10 II / MN10300）。

一次性做四件事（其余字节一个不动）：

1. 四张 tone 表（256×u16）写到 FF 空区 0x60E00000 起（表 i 在 +i*0x200）；
   tone 区槽 0..3（0x605D8344 起）**保持原厂字节**——它们是 12 个池指针的目标，
   动它们等于顺手改了别的 picture mode
   `0x605D8344 + i*0x200`（i = 0..3）；
2. `0x60600920` 起 168 个池指针 **保持原样**（本包不碰它们）；
3. 插点 `0x60479A27` 的 5 字节（原 `FA D0 30 12 70`）换成 5 字节远跳
   `DC <disp32>` → stub `0x60DE0000`；
4. stub 按 `u32(O+0x30)`（filter id = 档位 key）选槽，把指针写进
   `d0` 和 `S+0x1230`，再 `jmp 0x60479A2C` 让 `mov d2,a1` 继续。

stub 的汇编源在 `tools/hooks/look4_stub.s`；本脚本用项目自带的
`tools/mn10300_dis.py` 反汇编自产字节逐条核对落点，再出包，再用
`oly_container.parse` 独立回读。

用法：
    python tools/build_hook_look4.py --out build/B13_look4.BIN
    python tools/build_hook_look4.py --patch-only        # 只写 patch 文件
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import struct
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, 'reconfigure'):
        _s.reconfigure(encoding='utf-8', errors='replace')

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
STOCK_PAYLOAD = 'analysis/00_0100_40300020.decoded.bin'   # 已经解出来的原厂 payload
TONE_RAM = 0x60E00000        # 四张表在 FF 空区（可写；只读数据，不需执行）
TONE_SLOT = 0x200          # 每张 256×u16
STOCK_SLOT = 0x605D8344     # 原厂槽 0（必须保持不动）
POOL_RAM = 0x60600920
POOL_N = 168
INSERT_RAM = 0x60479A27
INSERT_ORIG = bytes.fromhex('fad0301270')   # add 0x1230,a0 / mov (a0),d0
RESUME_RAM = 0x60479A2C                     # mov d2,a1
STUB_RAM = 0x60DE0000                       # FF 空区 0x60DA4E08..0x60E10000
STUB_SRC = 'tools/hooks/look4_stub.s'
WORK = '.tmp/look4'
TC = '.tmp/olya/toolchains/mn10300-elf/bin'
LOOK_DIR = '.tmp/looks_beta'
PATCH_FILE = '.tmp/B13_look4.patch'

# (look 名, 档位 key = u32 filter id @ O+0x30)；组 i 用表 i
GROUPS = [
    ('reala_ace', 0x01010079),
    ('astia', 0x01020079),
    ('eterna', 0x01030079),
    ('bleach_bypass', 0x01010066),
]


# ---------------------------------------------------------------- stub 汇编
def assemble_stub(force: bool = False) -> bytes:
    os.makedirs(WORK, exist_ok=True)
    obj = os.path.join(WORK, 'look4_stub.o')
    elf = os.path.join(WORK, 'look4_stub.elf')
    bin_ = os.path.join(WORK, 'look4_stub.bin')
    need = force or not os.path.exists(bin_)
    if not need:
        need = os.path.getmtime(bin_) < os.path.getmtime(STUB_SRC)
    if need:
        asm = os.path.join(TC, 'mn10300-elf-as.exe')
        ld = os.path.join(TC, 'mn10300-elf-ld.exe')
        oc = os.path.join(TC, 'mn10300-elf-objcopy.exe')
        for exe in (asm, ld, oc):
            if not os.path.exists(exe):
                raise SystemExit(f'缺工具链：{exe}')
        env = dict(os.environ)
        env.update(TMP='.tmp/gtmp', TEMP='.tmp/gtmp', TMPDIR='.tmp/gtmp')
        os.makedirs('.tmp/gtmp', exist_ok=True)
        runs = [
            [asm, '-alh=' + os.path.join(WORK, 'look4_stub.lst'), '-o', obj, STUB_SRC],
            [ld, '-Ttext', hex(STUB_RAM), '-e', 'look4_entry', '-o', elf, obj],
            [oc, '-O', 'binary', elf, bin_],
        ]
        for cmd in runs:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
            if r.returncode:
                sys.stderr.write(r.stdout or '')
                sys.stderr.write(r.stderr or '')
                raise SystemExit(f'汇编失败：{cmd[0]}')
    return open(bin_, 'rb').read()


def disassemble(path: str, base: int, length: int):
    """用项目自带的反汇编器（tools/mn10300_dis.py）列一遍。"""
    r = subprocess.run([sys.executable, 'tools/mn10300_dis.py', path,
                        '--base', hex(base), '--len', str(length)],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    if r.returncode:
        raise SystemExit('mn10300_dis 失败：' + (r.stderr or '')[:400])
    out = []
    for ln in r.stdout.splitlines():
        m = re.match(r'\s*([0-9A-F]{8})\s+((?:[0-9A-F]{2} ?)+?)\s{2,}(\S.*)$', ln)
        if m:
            out.append((int(m.group(1), 16), m.group(3).strip()))
    return out


def check_stub(stub: bytes) -> None:
    """逐条核对 stub：读 key、算 a0、四个 cmp/beq、四个表地址、末尾远跳。"""
    if b'\xff' * 8 in stub:
        raise SystemExit('stub 里有 0xFF 空洞，链接地址可能不对')
    path = os.path.join(WORK, 'look4_stub.bin')
    lines = disassemble(path, STUB_RAM, len(stub))
    if not lines:
        raise SystemExit('反汇编为空')
    text = [t for _, t in lines]
    print('  --- stub 反汇编（tools/mn10300_dis.py @ %#x，%d 字节）---' % (STUB_RAM, len(stub)))
    for a, t in lines:
        print('   %08X  %s' % (a, t))
    want_head = [
        'mov (12,SP),a2',       # a2 = O
        'mov (48,a2),d1',       # d1 = u32 filter id  (O+0x30)
        'mov (4,SP),a0',        # a0 = S
        'add 0x1230,a0',        # a0 = S+0x1230
        'mov (a0),d0',          # 原厂指针
    ]
    if text[:len(want_head)] != want_head:
        raise SystemExit('stub 头部与预期不符：%r' % (text[:len(want_head)],))
    # 四个 (cmp 立即数, beq, mov 表地址)
    i = len(want_head)
    for gi, (look, key) in enumerate(GROUPS):
        want_cmp = 'cmp 0x%x,d1' % key
        if text[i] != want_cmp:
            raise SystemExit('组 %d 判别量不是 %s：%s' % (gi, want_cmp, text[i]))
        if not text[i + 1].startswith('beq ('):
            raise SystemExit('组 %d cmp 后不是 beq：%s' % (gi, text[i + 1]))
        tgt = int(re.search(r'0x([0-9a-f]+)', text[i + 1]).group(1), 16)
        tbl_line = dict(lines).get(tgt, '')
        want_tbl = 'mov 0x%x,d0' % (TONE_RAM + gi * TONE_SLOT)
        if tbl_line != want_tbl:
            raise SystemExit('组 %d 的表地址不是 %s：%s' % (gi, want_tbl, tbl_line))
        i += 2
    if text[-1] != 'jmp 0x%x' % RESUME_RAM:
        raise SystemExit('stub 末尾远跳落点不对：%s' % text[-1])
    # 末尾远跳的位移必须相对指令自身地址
    off = lines[-1][0] - STUB_RAM
    disp = struct.unpack('<i', stub[off + 1:off + 5])[0]
    if lines[-1][0] + disp != RESUME_RAM:
        raise SystemExit('末尾远跳位移算错：%#x + %d != %#x'
                         % (lines[-1][0], disp, RESUME_RAM))
    print('  stub 核对：%d 字节，%d 条，末尾 jmp 落点 %#x ✔'
          % (len(stub), len(lines), RESUME_RAM))


def entry_bytes() -> bytes:
    """插点 5 字节：DC + disp32，位移基准 = 指令自身地址 0x60479A27。"""
    disp = STUB_RAM - INSERT_RAM
    eb = b'\xdc' + struct.pack('<i', disp)
    # 用反汇编器核对：把 5 字节放在插点地址上看落点
    p = os.path.join(WORK, 'entry_probe.bin')
    open(p, 'wb').write(eb)
    lines = disassemble(p, INSERT_RAM, len(eb))
    if not lines or lines[0][1] != 'jmp 0x%x' % STUB_RAM:
        raise SystemExit('插点远跳落点不对：%r' % (lines,))
    print('  插点 %#x：%s → %#x（%s）' % (INSERT_RAM, eb.hex(), STUB_RAM, lines[0][1]))
    return eb


# ---------------------------------------------------------------- tone 表
def load_tone(look: str) -> list:
    p = os.path.join(LOOK_DIR, look + '.json')
    if not os.path.exists(p):
        raise SystemExit('缺 look 文件：' + p)
    with io.open(p, encoding='utf-8') as f:
        spec = json.load(f)
    tone = [int(v) for v in spec['tone']]
    if len(tone) != 256:
        raise SystemExit('%s: tone 长度 %d != 256' % (look, len(tone)))
    if any(v < 0 or v > 1023 for v in tone):
        raise SystemExit('%s: tone 越界（0..1023）' % look)
    if any(b < a for a, b in zip(tone, tone[1:])):
        raise SystemExit('%s: tone 非单调' % look)
    return tone


def tone_bytes(tone) -> bytes:
    return b''.join(int(v).to_bytes(2, 'little') for v in tone)


# ---------------------------------------------------------------- 出包
def build_patch(tables) -> str:
    L = []
    L.append('# B13 look4: picture-mode hook -- four tone tables in FF zone + 5-byte far jump + stub')
    L.append('# tone tables: 0x60e00000 + i*0x200 (256 x u16, little endian)')
    L.append('# stock tone slots 0x605d8344..0x605d8944 are NOT written (left stock)')
    for i, (look, tone_b) in enumerate(tables):
        L.append('# slot %d <- %s' % (i, look))
        L.append('ram 0x%x=%s' % (TONE_RAM + i * TONE_SLOT, tone_b.hex()))
    L.append('# pool 0x60600920 (168 pointers): untouched, deliberately')
    L.append('# insert point 0x60479a27: fad0301270 -> dc <disp32> (stub 0x60de0000)')
    L.append('ram 0x%x=%s' % (INSERT_RAM, ENTRY.hex()))
    L.append('# stub @ 0x60de0000 (%d bytes)' % len(STUB))
    L.append('ram 0x%x=%s' % (STUB_RAM, STUB.hex()))
    txt = '\n'.join(L) + '\n'
    txt.encode('ascii')            # patch 文件注释必须是 ASCII
    return txt


def diff_offsets(a: bytes, b: bytes):
    """所有不同的字节偏移（相对 payload 起点）。"""
    n = min(len(a), len(b))
    return [i for i in range(n) if a[i] != b[i]]


def region_hits(offsets, regions):
    """把差异偏移归到预期区段里；返回 (每段命中数, 落在区段外的偏移)。"""
    per = []
    outside = []
    for o in offsets:
        for (s, n) in regions:
            if s <= o < s + n:
                per.append(o)
                break
        else:
            outside.append(o)
    return per, outside


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='build/B13_look4.BIN')
    ap.add_argument('--patch-only', action='store_true')
    ap.add_argument('--force-asm', action='store_true', help='强制重新汇编 stub')
    args = ap.parse_args()

    global STUB, ENTRY
    print('== 1. stub')
    STUB = assemble_stub(args.force_asm)
    check_stub(STUB)
    ENTRY = entry_bytes()

    print('== 2. 四张 tone 表')
    tones = []
    for look, key in GROUPS:
        t = load_tone(look)
        tones.append(t)
        print('  %-14s key=%08X  t22=%-4d t46=%-4d t64=%-4d t128=%-4d t255=%-4d'
              % (look, key, t[22], t[46], t[64], t[128], t[255]))
    for i in range(len(tones)):
        for j in range(i + 1, len(tones)):
            if tones[i] == tones[j]:
                raise SystemExit('组 %d 与组 %d 的表完全相同' % (i, j))
    tables = [(GROUPS[i][0], tone_bytes(tones[i])) for i in range(len(GROUPS))]

    print('== 3. 原厂 payload 预检')
    stock = open(STOCK_PAYLOAD, 'rb').read()
    off_ins = INSERT_RAM - BASE
    got = stock[off_ins:off_ins + 5]
    print('  插点原字节 %s（期望 %s）%s' % (got.hex(), INSERT_ORIG.hex(),
                                          '✔' if got == INSERT_ORIG else '✘'))
    if got != INSERT_ORIG:
        return 1
    off_stub = STUB_RAM - BASE
    got = stock[off_stub:off_stub + len(STUB)]
    print('  stub 区原字节全 0xFF：%s（FF 空区 0x60DA4E08..0x60E10000）'
          % ('✔' if got == b'\xff' * len(STUB) else '✘ ' + got[:16].hex()))
    if got != b'\xff' * len(STUB):
        return 1

    print('== 4. patch 文件（注释全 ASCII）')
    txt = build_patch(tables)
    with io.open(PATCH_FILE, 'w', encoding='utf-8', newline='\n') as f:
        f.write(txt)
    print('  %s（%d 行，%d 字节）' % (PATCH_FILE, txt.count('\n'), len(txt)))
    if args.patch_only:
        return 0
    if os.path.exists(args.out):
        raise SystemExit('拒绝覆盖已有文件：' + args.out)

    print('== 5. mkpatch')
    cmd = [sys.executable, 'tools/mkpatch.py', SRC, '--out', args.out,
           '--patch-file', PATCH_FILE]
    print('  ' + ' '.join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    print('\n'.join('   ' + s for s in (r.stdout or '').strip().splitlines()[-12:]))
    if r.returncode:
        sys.stderr.write(r.stderr or '')
        return r.returncode

    print('== 6. 独立回读（oly_container.parse）')
    sys.path.insert(0, 'tools')
    import oly_container  # noqa: E402

    blocks = oly_container.parse(open(args.out, 'rb').read())
    b0 = blocks[0].payload
    ok = True
    for i, (look, t_b) in enumerate(tables):
        a = TONE_RAM + i * TONE_SLOT
        got = b0[a - BASE:a - BASE + TONE_SLOT]
        good = got == t_b
        ok &= good
        print('  表 %d @%#x <- %-14s %s  (t46=%d)'
              % (i, a, look, '✔' if good else '✘',
                 int.from_bytes(t_b[92:94], 'little')))
    got_ins = b0[off_ins:off_ins + 5]
    print('  插点 @%#x = %s  %s' % (INSERT_RAM, got_ins.hex(),
                                    '✔' if got_ins == ENTRY else '✘'))
    ok &= got_ins == ENTRY
    got_stub = b0[off_stub:off_stub + len(STUB)]
    print('  stub @%#x = %d 字节  %s' % (STUB_RAM, len(STUB),
                                         '✔' if got_stub == STUB else '✘'))
    ok &= got_stub == STUB
    # 槽 0..3 必须一个字节都没动（它们是 12 个池指针的目标：动它等于改别人的 mode）
    for i in range(4):
        a = STOCK_SLOT + i * TONE_SLOT
        got = b0[a - BASE:a - BASE + TONE_SLOT]
        good = got == stock[a - BASE:a - BASE + TONE_SLOT]
        ok &= good
        print('  原厂槽 %d @%#x 与原厂逐字节相同  %s'
              % (i, a, '✔' if good else '✘'))
    got_pool = b0[POOL_RAM - BASE:POOL_RAM - BASE + 4 * POOL_N]
    stock_pool = stock[POOL_RAM - BASE:POOL_RAM - BASE + 4 * POOL_N]
    print('  168 个池指针原样未动  %s' % ('✔' if got_pool == stock_pool else '✘'))
    ok &= got_pool == stock_pool

    print('== 7. 与官方包逐字节对比（差异必须全部落在 6 个预期区段内）')
    want = [(TONE_RAM + i * TONE_SLOT - BASE, TONE_SLOT) for i in range(len(GROUPS))]
    want.append((INSERT_RAM - BASE, 5))
    want.append((STUB_RAM - BASE, len(STUB)))
    diffs = diff_offsets(stock, b0)
    inside, outside = region_hits(diffs, want)
    names = ['表 %d (%s)' % (i, GROUPS[i][0]) for i in range(len(GROUPS))]
    names += ['插点 0x60479a27', 'stub 0x60de0000']
    for (s, n), nm in zip(want, names):
        cnt = sum(1 for o in diffs if s <= o < s + n)
        print('  %-22s @%#x +%#x：%3d/%3d 字节与库存不同' % (nm, s + BASE, n, cnt, n))
    if outside:
        print('  ✘ 有 %d 个差异字节落在预期之外，首个 @%#x'
              % (len(outside), outside[0] + BASE))
        ok = False
    else:
        print('  ✔ 全部 %d 个差异字节都在这 6 段里，其余 %d 字节与原厂一致'
              % (len(diffs), len(stock) - len(diffs)))
    size = os.path.getsize(args.out)
    same = size == os.path.getsize(SRC)
    print('== 产物 %s  %d B（等于官方包大小：%s）' % (args.out, size, same))
    return 0 if (ok and same) else 1


STUB = b''
ENTRY = b''

if __name__ == '__main__':
    sys.exit(main())
