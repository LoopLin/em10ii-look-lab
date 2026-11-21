#!/usr/bin/env python3
"""build_beta_modeprobe.py - build the B24 "picture-mode probe" firmware package.

三个变体，只有桩的"从哪读 ctx"不同，探针表、插入点、落点全一致：

  b  a2=(12,SP) ; a0=*(O+4)          k=(u16(ctx+0x0A)&7)|((u8(ctx+8)&3)<<3)   [真机: k=1..5，基址错]
  c  a0=(12,SP)                      k=(u16(ctx+0x0A)&7)|((u8(ctx+8)&3)<<3)   [lead 假设：(12,SP) 即 ctx]
  d  a0=(12,SP)                      k=(u8(ctx+0x0A)&7)|((u8(ctx+9)&3)<<3)    [备选：u16 不是模式号]

共同部分：
  1. tone 池 slot 0..31 换成 32 张"平段"探针表（带内常量 300+20k，两侧线性过渡），
     其余 22 张（slot 32..53 里除桩那一张）与 168 个池指针保持原厂不动。
  2. 桩放在 tone 池 slot 53（0x605DED44，stock 全 0、池指针不引用它），62 B。
  3. 插入点 0x60479A27（原 add 0x1230,a0 / mov (a0),d0，共 5 B）改成 DC 远跳。
  4. 矩阵奇偶规范化（stock 自己就过不了 audit 的奇偶项）。

只写 build/ 下的 BIN；不碰 I: 盘，不刷机。

用法：
  python tools/build_beta_modeprobe.py            # 默认出 B24c
  python tools/build_beta_modeprobe.py --variant d
  python tools/build_beta_modeprobe.py --variant all   # b + c + d
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

BASE = 0x60021020
TONE = 0x605D8344          # tone 池基址，每档 0x200
SLOT = 0x200
N_TABLES = 32              # slot 0..31 换成探针表
STUB_RAM = 0x605DED44      # tone 池 slot 53：stock 全 0、池指针不引用
INSERT_RAM = 0x60479A27    # 原 add 0x1230,a0 / mov (a0),d0（5 B）
STUB_LEN = 62

STOCK = ROOT / 'stock' / 'OLY_E_120_1400_0000_0000.BIN'
STOCK_DECODED = ROOT / 'analysis' / '00_0100_40300020.decoded.bin'
MATRIX_RAM = 0x605ECECA          # 3x3 i16 色调矩阵（Q8），审计要求奇偶自洽
AUDIT = ROOT / 'tools' / 'audit_beta_pack.py'

VARIANTS = {
    'b': {'out': 'B24b_modeprobe.BIN', 'stub': 'b24.bin',
          'desc': 'a2=(12,SP); a0=*(O+4)：u16(ctx+0x0A)&7 | (u8(ctx+8)&3)<<3'},
    'c': {'out': 'B24c_modeprobe.BIN', 'stub': 'b24c.bin',
          'desc': 'a0=(12,SP)：u16(ctx+0x0A)&7 | (u8(ctx+8)&3)<<3'},
    'd': {'out': 'B24d_modeprobe.BIN', 'stub': 'b24d.bin',
          'desc': 'a0=(12,SP)：u8(ctx+0x0A)&7 | (u8(ctx+9)&3)<<3'},
    'e': {'out': 'B24e_modeprobe.BIN', 'stub': 'b24e.bin',
          'desc': 'a0=*(O+0xC)（0x604799C7 实证的 ctx）：u16(ctx+0x0A)&7 | (u8(ctx+8)&3)<<3'},
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


BAND_LO, BAND_HI = 48, 112     # 灰块 raw 索引落在这个区间（实测 70~90）
BAND_BASE, BAND_STEP = 300, 20  # 带内常量：300 + 20k -> 显示 74.8 + 5.0k


def probe_table(k: int) -> bytes:
    """带内常量 + 带外线性过渡：tone_k[i] = 300+20k（48<=i<=112），两侧接到 0/1023。

    带内是**平段**，读数只跟 k 有关，不受曝光导致索引漂移的影响；
    相机自身的对比/锐化是逐像素单调映射，平段仍是平段，因此读数是稳的。
    """
    v = BAND_BASE + BAND_STEP * k
    vals = []
    for i in range(256):
        if i < BAND_LO:
            x = round(v * i / BAND_LO)
        elif i <= BAND_HI:
            x = v
        else:
            x = round(v + (1023 - v) * (i - BAND_HI) / (255 - BAND_HI))
        vals.append(min(1023, max(0, x)))
    return struct.pack('<256H', *vals)


def evenize_matrix(m: list[int]) -> list[int]:
    """把 stock 矩阵整成审计要求的奇偶自洽（硬件本来就会丢掉 LSB）。

    规则：m01、m21 各自为偶数，m10+m12 为偶数；每行的行和必须仍是 256，
    所以被减 1 的系数由同行的"自由"系数补 1，净效果 |Δ| = 1（1/256，肉眼不可见）。
    """
    m = list(m)
    for i in (1, 7):                       # m01 / m21
        if m[i] & 1:
            m[i] -= 1
            m[i // 3 * 3 + 2] += 1         # 同行 m02 / m22 补 1
    if (m[3] + m[5]) & 1:                  # m10 + m12
        m[3] -= 1                          # m10 取偶
        m[4] += 1                          # m11 补 1
    return m


def build(variant: str) -> int:
    cfg = VARIANTS[variant]
    tag = f'B24{variant}'
    out = ROOT / 'build' / cfg['out']
    stub_bin = ROOT / '.tmp' / cfg['stub']
    patch = ROOT / '.tmp' / f'{tag}_modeprobe.patch'
    meta_path = ROOT / '.tmp' / f'{tag}_modeprobe.meta.json'
    decoded_path = ROOT / '.tmp' / f'{tag}.decoded.bin'

    stub = stub_bin.read_bytes()
    if not (40 <= len(stub) <= 0x100):
        raise SystemExit(f'stub is {len(stub)} B, out of 40..256')
    stub_len = len(stub)
    if stub[0] != 0x5c or stub[1] != 0x0c:
        raise SystemExit(f'stub does not start with mov (12,sp),a0: {stub[:4].hex()}')

    patches: list[tuple[int, bytes]] = []
    for k in range(N_TABLES):
        patches.append((TONE + k * SLOT, probe_table(k)))
    patches.append((STUB_RAM, stub))
    insert = b'\xdc' + ((STUB_RAM - INSERT_RAM) & 0xFFFFFFFF).to_bytes(4, 'little')
    patches.append((INSERT_RAM, insert))

    # 矩阵奇偶规范化（stock 自己就不过审计这一项；硬件本来就会偶化，改动量 1/256）
    stock_img = STOCK_DECODED.read_bytes()
    m_old = list(struct.unpack_from('<9h', stock_img, MATRIX_RAM - BASE))
    m_new = evenize_matrix(m_old)
    if m_new != m_old:
        patches.append((MATRIX_RAM, struct.pack('<9h', *m_new)))

    lines = [f'# {tag} modeprobe: 32 band tables in tone slots 0..31 + stub in slot 53',
             f'# stub: {tag} (desc in docs/firmware/beta/B24c-modeprobe.md)',
             '# generated by tools/build_beta_modeprobe.py']
    for ram, data in patches:
        lines.append(f'ram 0x{ram:08X}={data.hex()}')
    patch.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    out.parent.mkdir(exist_ok=True)
    if out.exists():
        out.unlink()
    cmd = [sys.executable, str(ROOT / 'tools' / 'mkpatch.py'), str(STOCK),
           '--out', str(out), '--patch-file', str(patch)]
    print(f'== {tag}: {cfg["desc"]}')
    print('$ ' + ' '.join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)

    blob = out.read_bytes()
    decoded = oc.parse(blob)[0].payload
    decoded_path.write_bytes(decoded)
    stock = STOCK_DECODED.read_bytes()

    # ---- 自检 1：落点 ----
    errs = []
    for k in range(N_TABLES):
        ram = TONE + k * SLOT
        got = decoded[ram - BASE:ram - BASE + 512]
        if got != probe_table(k):
            errs.append(f'table k={k} at 0x{ram:08X} mismatch')
    if decoded[STUB_RAM - BASE:STUB_RAM - BASE + stub_len] != stub:
        errs.append('stub mismatch')
    if decoded[INSERT_RAM - BASE:INSERT_RAM - BASE + 5] != insert:
        errs.append('insert mismatch')

    # ---- 自检 2：区外 0 字节 + 改动字节数 ----
    intended = [False] * len(stock)
    for ram, data in patches:
        o = ram - BASE
        for i in range(o, o + len(data)):
            intended[i] = True
    changed = sum(1 for a, b in zip(stock, decoded) if a != b)
    stray = [i for i, (a, b) in enumerate(zip(stock, decoded))
             if a != b and not intended[i]]
    if stray:
        errs.append(f'{len(stray)} stray byte(s), first at 0x{BASE + stray[0]:08X}')
    n_patch_bytes = sum(len(d) for _, d in patches)

    # ---- 自检 3：不该动的区域（slot 32..52、168 个池指针）----
    untouched = [(TONE + 32 * SLOT, STUB_RAM)]
    for a, b in untouched:
        if decoded[a - BASE:b - BASE] != stock[a - BASE:b - BASE]:
            errs.append(f'{a:#x}..{b:#x} changed but must stay stock')
    if decoded[0x60600920 - BASE:0x60600920 - BASE + 168 * 4] != stock[0x60600920 - BASE:0x60600920 - BASE + 168 * 4]:
        errs.append('pool pointers changed')

    meta = {
        'name': tag,
        'kind': 'modeprobe',
        'variant': variant,
        'stub_desc': cfg['desc'],
        'container_size': len(blob),
        'container_sha256': sha256(blob),
        'stub_ram': hex(STUB_RAM),
        'stub_len': stub_len,
        'stub_sha256': sha256(stub),
        'insert_ram': hex(INSERT_RAM),
        'insert_bytes': insert.hex(),
        'matrix_old': list(m_old),
        'matrix_new': list(m_new),
        'tables': {str(k): hex(TONE + k * SLOT) for k in range(N_TABLES)},
        'patch_bytes': n_patch_bytes,
        'changed_bytes': changed,
        'stray_bytes': len(stray),
        'decoded_image': str(decoded_path.relative_to(ROOT)),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print(f'OUT   {out}  {len(blob):,} B  sha256={meta["container_sha256"]}')
    print(f'patch lines          {len(patches)} ({n_patch_bytes:,} B 覆盖)')
    print(f'changed bytes        {changed:,}')
    print(f'stray bytes (区外)   {len(stray)}')
    print(f'stub                 {stub_len} B @ 0x{STUB_RAM:08X}  {stub.hex()}')
    print(f'insert @ 0x{INSERT_RAM:08X}  {insert.hex()}  (disp={STUB_RAM - INSERT_RAM:#x})')
    print(f'matrix  {m_old} -> {m_new}')
    for e in errs:
        print(f'ERR  {e}')
    if errs:
        return 1

    rc = subprocess.run([sys.executable, str(AUDIT), str(out)], cwd=ROOT)
    print(f'audit exit {rc.returncode}')
    return 0 if rc.returncode == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--variant', default='c', choices=['b', 'c', 'd', 'e', 'all'])
    args = ap.parse_args()
    todo = ['b', 'c', 'd', 'e'] if args.variant == 'all' else [args.variant]
    rc = 0
    for v in todo:
        rc |= build(v)
        print()
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
