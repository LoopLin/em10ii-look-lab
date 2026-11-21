#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dispatch_map.py — 离线复核「预览 tone 分派」0x604B986B / 臂 0x604B9CC3。

只读 analysis/00_0100_40300020.decoded.bin（stock，payload 已 XOR 0xFF），
不写任何镜像文件。产出：调用点清单、六分支表、d0 公式里的基值表、bank2 曲线号。

用法：
  python tools/dispatch_map.py calls     # 扫 call/jmp 到给定目标
  python tools/dispatch_map.py arms      # 打印六分支 -> 表地址 -> bank2 k
  python tools/dispatch_map.py bias      # 打印 0x605E5954 基值表
  python tools/dispatch_map.py all
"""
import sys, struct, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BIN = 'analysis/00_0100_40300020.decoded.bin'
BASE = 0x60021020
BANK2 = 0x605DEF4E       # bank2 曲线 k=0 起点，步长 0x200
IDENT = 0x605D8144       # 恒等斜坡（v[i]=4i）

# ---- 分派臂 0x604B9CC3 的六个立即数（stock） ----
# (cmp 值, 承载 mov 的指令地址, 立即数)
ARM_LOW = [   # key ∉ {4,8}，臂 0x604B9CC3
    (-3, 0x604B9CFE, 0x605E014E),
    (-2, 0x604B9CE0, 0x605E034E),
    (-1, 0x604B9CE8, 0x605DF74E),
    (0,  0x604B9D18, 0x605DF34E),   # 门 0x604BDD42 != 0
    (0,  0x604B9D20, 0x605D8144),   # 门 == 0
    (1,  0x604B9CF0, 0x605DF54E),
    (2,  0x604B9D06, 0x605E054E),
]
ARM_HI = [    # key ∈ {4,8}，臂 0x604B9C77
    (-2, 0x604B9CB1, 0x605E074E),
    (-1, 0x604B9C90, 0x605E094E),
    (0,  0x604B9C99, 0x605E0B4E),
    (1,  0x604B9CA2, 0x605E0D4E),
    (2,  0x604B9CBA, 0x605E0F4E),
]

CALL_TARGETS = [
    0x604B9CC3, 0x604B986B, 0x604B9E61, 0x604B9EBF, 0x604B9EAF,
    0x604B9E2D, 0x604B9D29, 0x604AA228, 0x604B4B24, 0x604B9D18,
    0x604B8098, 0x604B8B8E, 0x604B9223,
]


def load():
    return open(BIN, 'rb').read()


def rd(data, addr, n):
    return data[addr - BASE: addr - BASE + n]


def k_of(addr):
    if addr == IDENT:
        return 'IDENT(0x605D8144)'
    d = addr - BANK2
    if 0 <= d < 0x200 * 49 and d % 0x200 == 0:
        return 'k=%d' % (d // 0x200)
    return '?'


def scan_calls(data):
    """扫 0xCD int16（call）、0xCC int16（jmp）、0xDD int32（call32）。
    实测：目标 = 指令起始地址 + disp（不是 pc+2+disp），已用 0x604B9E67→0x604B986B 与
    0x604AA237→0x604B8098 两点校准。"""
    hits = {t: [] for t in CALL_TARGETS}
    n = len(data)
    for off in range(n - 8):
        op = data[off]
        pc = BASE + off
        if op == 0xCD:
            disp = struct.unpack_from('<h', data, off + 1)[0]
            tgt = pc + disp
            if tgt in hits:
                hits[tgt].append((pc, 'call16'))
        elif op == 0xCC:
            disp = struct.unpack_from('<h', data, off + 1)[0]
            tgt = pc + disp
            if tgt in hits:
                hits[tgt].append((pc, 'jmp16'))
        elif op == 0xDD:
            disp = struct.unpack_from('<i', data, off + 1)[0]
            tgt = pc + disp
            if tgt in hits:
                hits[tgt].append((pc, 'call32'))
    return hits


def scan_imm32(data, value):
    """扫任何 4 字节小端立即数/指针等于 value 的位置（会含数据区，需人工甄别）。"""
    pat = struct.pack('<I', value)
    out, i = [], 0
    while True:
        i = data.find(pat, i)
        if i < 0:
            return out
        out.append(BASE + i)
        i += 1


def main():
    data = load()
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'

    if cmd in ('calls', 'all'):
        print('== 调用点扫描（CD/CC/DD，mn10300，disp 相对指令起始+2）==')
        hits = scan_calls(data)
        for t in CALL_TARGETS:
            lst = hits[t]
            print('  -> 0x%08X : %d 处%s' % (
                t, len(lst), '' if lst else '   << 无'))
            for pc, kind in lst:
                print('       0x%08X  %s' % (pc, kind))
        print()

    if cmd in ('arms', 'all'):
        for name, tbl in (('ARM_LOW  (key∉{4,8})', ARM_LOW),
                          ('ARM_HI   (key∈{4,8})', ARM_HI)):
            print('== %s ==' % name)
            for cmpv, ia, addr in tbl:
                data_at = rd(data, ia, 6)
                got = struct.unpack_from('<I', data_at, 2)[0]
                ok = 'OK' if got == addr else 'MISMATCH(0x%08X)' % got
                print('  d0=%+d  mov@0x%08X -> 0x%08X  %-12s  %s  字节=%s'
                      % (cmpv, ia, addr, k_of(addr), ok, data_at.hex(' ')))
            print()

    if cmd in ('bias', 'all'):
        print('== d0 基值表 0x605E5954（5 项字节，movbu (d0,a1),d2）==')
        b = rd(data, 0x605E5954, 8)
        print('  bytes :', b.hex(' '))
        print('  s8    :', [x - 256 if x > 127 else x for x in b])
        print('  指数映射: d1==1→n=0, 2→1, 3→2, 4→3, 8→3, 5→4, 其它→1')
        print()

    if 0x60ADB12B:
        if cmd in ('enum', 'all'):
            print('== 0x60ADB12B（影像风格枚举名表，步长 0x1A）立即数引用 ==')
            for a in scan_imm32(data, 0x60ADB12B):
                print('  0x%08X' % a)
            print()


if __name__ == '__main__':
    main()
