# -*- coding: utf-8 -*-
"""build_guard.py —— "写后自检守卫"探针的离线构建器（不产出可上卡的包）。

设计见 docs/analysis/guard-probe-design.md。本工具只做三件事：

  1. 用仓库自带 MN10300 工具链把 G1（守卫桩）/ G2（上报桩）汇编成机器码；
  2. 把两处 5 字节远跳（DC disp32）与两个桩写进 base 包 payload 的副本；
  3. 自检（桩边界、口袋占用、钩点原字节、远跳位移），并把 scratch 包写到
     .tmp/ 下 —— **永远不写 build/**，因为探针还没过四道门、也还没上过真机。

用法：
    python tools/build_guard.py                        # base=build/L55_newton3.BIN
    python tools/build_guard.py --base build/L55_newton3.BIN --out .tmp/guard/guard.BIN

安全约定（与 docs/STATUS.md 一致）：
  * 只从 stock 派生的既有包（base）拷贝，不叠在上一版探针上；
  * 目标口袋 0x60672C20..0x60672FF6 必须全 FF，否则拒绝构建（说明别人占了）；
  * 两个钩点的原 5 字节必须与预期指令逐字节一致，否则拒绝构建（说明 base 变了）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import oly_container as oc  # noqa: E402

BASE_RAM = 0x60021020

# ---------------------------------------------------------------- 布局 ----
POCKET = (0x60672C20, 0x60672FF6)      # 982 B，L55 全 FF、无真引用
HDR = 0x60672C20                       # 32 B 头
REC0 = HDR + 0x20                      # 16 x 32 B 记录 -> 0x60672E40
NREC = 16
G1 = 0x60672E40                        # 守卫桩
G2 = 0x60672EC0                        # 上报桩（G1 之后，留缝）
EXIF_DST = 0x61894C98                  # UserComment 文本区里第一块未用字节
EXIF_END = 0x61894D05                  # 文本区结束（不含）

H1_SITE, H1_LEN, H1_RET = 0x604B9F03, 5, 0x604B9F08   # movbu (4,SP),d0 + cmp 0x0,d0
H2_SITE, H2_LEN, H2_RET = 0x604ED7BA, 5, 0x604ED7C0   # movhu d0,(18372,a0) + 1 B of next
H1_WANT = bytes.fromhex('f8b804a000')                  # base 里必须长这样
H2_WANT = bytes.fromhex('fa70c4475c')

STUB_MAX = {'G1': 0x80, 'G2': 0x100}   # 预算（B）

AS = ROOT / '.tmp' / 'olya' / 'toolchains' / 'mn10300-elf' / 'bin' / 'mn10300-elf-as.exe'
LD = AS.with_name('mn10300-elf-ld.exe')
OBJCOPY = AS.with_name('mn10300-elf-objcopy.exe')

# ------------------------------------------------------------- 汇编源 ----
# 约定：桩里不写最后那条 jmp（远跳位移必须按"指令自身地址"算，交给 Python 拼）。
G1_SRC = f"""
    .text
    .globl _start
HDR = {HDR:#x}
PCW = 0x604B9EFF
_start:
    mov     a0,d1
    mov     0x1230,a1
    add     a3,a1
    mov     (a1),d0
    cmp     d1,d0
    bne     mism
    mov     HDR,a0
    mov     a3,(16,a0)
    mov     d1,(20,a0)
    mov     (12,a0),a1
    inc     a1
    mov     a1,(12,a0)
    bra     done
mism:
    mov     HDR,a0
    mov     a3,(16,a0)
    mov     d1,(20,a0)
    mov     (8,a0),d1
    and     0xf,d1
    mov     d1,(4,a0)
    asl     5,d1
    add     a0,d1
    add     0x20,d1
    mov     d1,a1
    mov     (20,a0),d1
    mov     d1,(20,a1)
    mov     (8,a0),d1
    inc     d1
    mov     d1,(8,a0)
    mov     d1,(a1)
    mov     a3,d1
    add     0x1230,d1
    mov     d1,(16,a1)
    mov     (12,a0),d1
    inc     d1
    mov     d1,(12,a0)
    mov     d1,(4,a1)
    mov     PCW,d1
    mov     d1,(12,a1)
    mov     d0,(24,a1)
    mov     0x1,d1
    mov     d1,(28,a1)
done:
    movbu   (4,SP),d0
    cmp     0x0,d0
"""

# 注意：G2 里**不用 setlb/llt**。SETLB/LOOP 用的是全局 LB 寄存器，
# 而这条钩点位于调用链中间，若上层调用者正在用 setlb 循环，我们一改 LB 就把别人的
# 循环计数改掉（同类事故见 B82：语义等价的跳板也能炸）。这里改成 add -1/bne，只用标志位。
G2_SRC = f"""
    .text
    .globl _start
HDR = {HDR:#x}
DST = {EXIF_DST:#x}
_start:
    movhu   d0,(18372,a0)
    mov     (16,SP),a0
    add     -4,sp
    mov     d1,(sp)
    add     -4,sp
    mov     a1,(sp)
    mov     HDR,a0
    mov     DST,a1
    mov     0x8,d1
l1:
    mov     (a0),d0
    inc4    a0
    mov     d0,(a1)
    inc4    a1
    add     -1,d1
    bne     l1
    mov     HDR,a0
    mov     (8,a0),d1
    add     -1,d1
    and     0xf,d1
    asl     0x5,d1
    add     d1,a0
    add     0x20,a0
    mov     0x8,d1
l2:
    mov     (a0),d0
    inc4    a0
    mov     d0,(a1)
    inc4    a1
    add     -1,d1
    bne     l2
    mov     (4,sp),d1
    mov     (sp),a1
    add     8,sp
    clr     d0
"""


def jmp_bytes(site: int, target: int) -> bytes:
    """DC + int32(target-site)：位移以指令地址为基准（仓库既有桩同口径）。"""
    return b'\xdc' + struct.pack('<i', target - site)


def assemble(src: str, base: int, work: Path, tag: str) -> bytes:
    work.mkdir(parents=True, exist_ok=True)
    s, o, e, b = (work / f'{tag}.s', work / f'{tag}.o', work / f'{tag}.elf', work / f'{tag}.bin')
    s.write_text(src, encoding='utf-8')
    for cmd in ([str(AS), '-o', str(o), str(s)],
                [str(LD), f'-Ttext={base:#x}', '-o', str(e), str(o)],
                [str(OBJCOPY), '-O', 'binary', str(e), str(b)]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f'[FAIL] {tag}: {" ".join(cmd)}\n{r.stdout}{r.stderr}')
    return b.read_bytes()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='build/L55_newton3.BIN')
    ap.add_argument('--stock', default='stock/OLY_E_120_1400_0000_0000.BIN')
    ap.add_argument('--out', default='.tmp/guard/guard_probe.BIN')
    ap.add_argument('--work', default='.tmp/guard/build')
    ap.add_argument('--json', default='.tmp/guard/manifest.json')
    args = ap.parse_args()

    out = Path(args.out)
    if out.resolve().parent == (ROOT / 'build').resolve():
        raise SystemExit('[REFUSE] 不往 build/ 写探针包（四道门未过），请换 --out')

    blocks = oc.parse((ROOT / args.base).read_bytes())
    pay = bytearray(blocks[0].payload)
    stock = bytearray(oc.parse((ROOT / args.stock).read_bytes())[0].payload)
    if len(pay) != len(stock):
        raise SystemExit('[FAIL] base 与 stock payload 长度不同')

    lo, hi = POCKET
    body = pay[lo - BASE_RAM:hi - BASE_RAM]
    if body != b'\xff' * len(body):
        bad = [i for i, v in enumerate(body) if v != 0xFF][:8]
        raise SystemExit(f'[FAIL] 口袋里已有非 FF 字节（{len(bad)}+ 处，例 {[hex(lo+i) for i in bad]}）')

    for site, want, tag in ((H1_SITE, H1_WANT, 'H1'), (H2_SITE, H2_WANT, 'H2')):
        got = bytes(pay[site - BASE_RAM:site - BASE_RAM + len(want)])
        if got != want:
            raise SystemExit(f'[FAIL] {tag} 钩点原字节不符：want {want.hex()} got {got.hex()}')

    w = ROOT / args.work
    # 桩体先汇编，再按"jmp 自己的地址 = 桩首 + 桩体长度"补上远跳
    g1b = assemble(G1_SRC, G1, w, 'g1')
    g1 = g1b + jmp_bytes(G1 + len(g1b), H1_RET)
    g2b = assemble(G2_SRC, G2, w, 'g2')
    g2 = g2b + jmp_bytes(G2 + len(g2b), H2_RET)

    for tag, blob, at, cap in (('G1', g1, G1, STUB_MAX['G1']), ('G2', g2, G2, STUB_MAX['G2'])):
        if len(blob) > cap:
            raise SystemExit(f'[FAIL] {tag} 超预算：{len(blob)} > {cap} B')
    if G1 + len(g1) > G2:
        raise SystemExit(f'[FAIL] G1 已伸到 G2 起点：{G1+len(g1):#x} > {G2:#x}')
    if G2 + len(g2) > hi:
        raise SystemExit(f'[FAIL] G2 越出口袋：{G2+len(g2):#x} > {hi:#x}')
    need = NREC * 32
    if REC0 + need != G1:
        raise SystemExit('[FAIL] 记录区/桩布局不自洽')

    for site, blob, tag in ((H1_SITE, g1, 'H1->G1'), (H2_SITE, g2, 'H2->G2')):
        tgt = G1 if tag.startswith('H1') else G2
        pay[site - BASE_RAM:site - BASE_RAM + 5] = jmp_bytes(site, tgt)
    pay[G1 - BASE_RAM:G1 - BASE_RAM + len(g1)] = g1
    pay[G2 - BASE_RAM:G2 - BASE_RAM + len(g2)] = g2

    img = oc.reconstruct([oc.replace_payload(blocks[0], bytes(pay))] + blocks[1:])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(img)

    diff_addrs = [i for i in range(len(pay)) if pay[i] != stock[i]]
    man = {
        'base': args.base, 'out': str(out),
        'sha256': hashlib.sha256(img).hexdigest(),
        'size': len(img), 'payload_sha256': hashlib.sha256(bytes(pay)).hexdigest(),
        'g1': {'addr': hex(G1), 'body': len(g1b), 'total': len(g1)},
        'g2': {'addr': hex(G2), 'body': len(g2b), 'total': len(g2)},
        'pocket': [hex(lo), hex(hi)],
        'hooks': [(hex(H1_SITE), 'G1', hex(H1_RET)), (hex(H2_SITE), 'G2', hex(H2_RET))],
        'changed_bytes': len(diff_addrs),
        'changed_ranges': _ranges([BASE_RAM + i for i in diff_addrs]),
    }
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(man, indent=2), encoding='utf-8')
    print(json.dumps(man, indent=2))
    return 0


def _ranges(addrs):
    out, start, prev = [], None, None
    for a in addrs:
        if start is None:
            start = prev = a
            continue
        if a == prev + 1:
            prev = a
            continue
        out.append([hex(start), hex(prev)]); start = prev = a
    if start is not None:
        out.append([hex(start), hex(prev)])
    return out


if __name__ == '__main__':
    sys.exit(main())
