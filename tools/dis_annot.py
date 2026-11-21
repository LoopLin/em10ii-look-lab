"""把反汇编结果写成 HxD「数据段」注解文件（_dis.txt），配合同目录的 _dis.lst。

用法:
    python tools/dis_annot.py <块文件> <起始RAM地址> <长度> [--base 0x60021020]

HxD 打开 <块文件> 后：分析 → 数据段 → 载入… → 选同目录生成的 _dis.lst → 选「注解」。
之后跳转到任一指令地址（Ctrl+G，输入 RAM 地址的十六进制）就能在右侧看到对应汇编，
指令字节会按指令长度着色，一眼能分开指令和数据。
"""
import os
import re
import subprocess
import sys

BASE_DEFAULT = 0x60021020


def disassemble(block, off, length, base):
    out = subprocess.run(
        [sys.executable, os.path.join('tools', 'mn10300_dis.py'), block,
         '--base', hex(base), '--off', hex(off), '--len', hex(length)],
        capture_output=True, text=True).stdout
    rows = []
    pat = re.compile(r'^\s+([0-9A-F]{8})\s+([0-9A-F ]+?)\s{2,}(.*)$')
    for line in out.splitlines():
        m = pat.match(line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        nbytes = len(m.group(2).split())
        rows.append((addr, nbytes, m.group(3).strip()))
    return rows


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    block, start, length = sys.argv[1], int(sys.argv[2], 0), int(sys.argv[3], 0)
    base = BASE_DEFAULT
    if '--base' in sys.argv:
        base = int(sys.argv[sys.argv.index('--base') + 1], 0)
    off = start - base
    if off < 0:
        print('start 小于基址')
        return 2
    rows = disassemble(block, off, length, base)
    if not rows:
        print('反汇编为空')
        return 3
    lst = []
    for addr, nbytes, text in rows:
        text = text.replace('\t', ' ')
        lst.append(f'{nbytes:08X} {text}')
    stem = os.path.splitext(block)[0] + f'_dis_{start:08X}'
    with open(stem + '_dis.lst', 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(lst) + '\n')
    with open(stem + '_dis.txt', 'w', encoding='utf-8', newline='\r\n') as f:
        f.write(f'* 由 tools/dis_annot.py 生成；块 {os.path.basename(block)}，'
                f'基址 0x{base:08X}，范围 0x{start:08X}+0x{length:X}\n')
        f.write('* HxD: 分析 → 数据段 → 载入 _dis.lst（选「注解」）\n')
    print('wrote', stem + '_dis.lst', len(rows), 'instructions')
    print('first', hex(rows[0][0]), 'last', hex(rows[-1][0]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
