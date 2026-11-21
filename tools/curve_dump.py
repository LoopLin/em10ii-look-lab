"""把固件里的 100 字节曲线单元（88 条）dump 成表格，便于与相机 JPEG 对照。

结构（2026-11-10 确认）:
  指针表 0x605FFDC8（141 项 u32 LE），指向 0x605D4829 起、步长 0x64 的曲线单元。
  每个单元 100 字节: x[16] (u8), y[16] (u8), slope[17] (u8) + 51 字节其它。
用法: python tools/curve_dump.py [--md analysis/notes_curves.md]
"""
import struct
import sys

BASE = 0x60021020
BIN = 'analysis/00_0100_40300020.decoded.bin'
TBL = 0x605FFDC8
FIRST = 0x605D4829
STEP = 0x64


def main():
    data = open(BIN, 'rb').read()
    off = lambda a: a - BASE
    ptrs = []
    for i in range(400):
        p = off(TBL) + 4 * i
        a = struct.unpack('<I', data[p:p + 4])[0]
        if not (0x60000000 <= a <= 0x61700000):
            break
        ptrs.append(a)
    uniq = []
    for a in ptrs:
        if a not in uniq:
            uniq.append(a)
    lines = ['# 固件曲线单元（88 条，100 B/条）', '',
             f'指针表 `0x{TBL:08X}`（{len(ptrs)} 项），曲线区 `0x{FIRST:08X}` 起、步长 0x{STEP:X}。',
             '', '| # | 地址 | x[0..15] | y[0..15] |', '|---|------|----------|----------|']
    for i, a in enumerate(uniq):
        b = data[off(a):off(a) + 100]
        x = ' '.join(f'{v:3d}' for v in b[0:16])
        y = ' '.join(f'{v:3d}' for v in b[16:32])
        lines.append(f'| {i} | 0x{a:08X} | {x} | {y} |')
    txt = '\n'.join(lines)
    out = 'analysis/notes_curves.md'
    for i, s in enumerate(sys.argv):
        if s == '--md':
            out = sys.argv[i + 1]
    open(out, 'w', encoding='utf-8').write(txt + '\n')
    print('written', out, len(uniq), 'curves')
    # 控制台摘要：找 y 前 16 字节不是恒等的
    for i, a in enumerate(uniq[:8]):
        b = data[off(a):off(a) + 100]
        print(i, hex(a), 'x', list(b[0:16]), 'y', list(b[16:32]))


if __name__ == '__main__':
    main()
