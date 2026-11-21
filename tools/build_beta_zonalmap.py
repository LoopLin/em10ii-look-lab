# -*- coding: utf-8 -*-
"""B35 zonal-map: B28 的通道 + 一张「12 段排列码」的 12 区表。

Chain
  stock OLY_E_120_1400_0000_0000.BIN
    --(tools/build_beta_hook4mx.py: tone hook 0x604AA242, matrix hook 0x604B6AD4)-->
  .tmp/B26ref.BIN   (必须与 build/B26_four_looks_mx.BIN 逐字节相同)
    --(本脚本 via tools/mkpatch.py)-->
  build/B35_zonal_map.BIN

相对 B26 只改三处（与 B28 完全同位、同长度）：
  0x60479960   5 B    jmp 0x605DED44（上传器入口，回 0x60479965）
  0x605DED44 120 B    桩：out+0x3C4=0x0FFF + 十个指针（与 B28/B29 同一份桩源）
  0x605F5BB4 0xA80 B  十张「实验表」（B28 这里是全中性 0x3F00）

所以 **B35 与 B28 的差异只有 0xA80 B 的表内容**：图像上出现的任何差别都只能
归因于表值，通道/落点/触发三者完全相同。这是本实验的对照条件。

实验表的内容（96 项 = 12 段 × 8 项，段 c = 项 c*8..c*8+7）：
    DROPS = [6, 9, 4, 2, 8, 0, 3, 10, 7, 1, 5, 11]      <- 段 c 的「降档数」
    value(j) = 0x3F00 - DROPS[j//8]*0x200
即 c0 0x3300, c1 0x2D00, c2 0x3700, c3 0x3B00, c4 0x2F00, c5 0x3F00(中性),
   c6 0x3900, c7 0x2B00, c8 0x3100, c9 0x3D00, c10 0x3500, c11 0x2900。
u32 槽存 (value<<16)|value（库存表 hi16==lo16），u16 槽存 value。

为什么是**排列码**而不是「阶梯」或「阶梯 + 标记段」（m00159–m00179 的量化结论）：
  * 照片上 8 个色相柱只采到 12 区里的 8 个（45° 间距 vs 30° 宽），所以真正的未知量
    是「(族, 整体旋转, 相位)」共 72 个互不相同的预测。判据取错预测里 Pearson |r| 的
    最大值（拟合本身就是线性回归）+ 秩等价数（若某个错预测与真值同序，则任何单调
    响应都无法区分，最保守的口径）。
  * 阶梯 `[12,1,2,…,11]`：秩等价 59/72，|r| 最大 1.000 —— 基本不可判（旋转只换截距）。
  * 锯齿 `[0,6,1,7,…]`：秩等价 24/72，|r| 1.000 —— 周期 2 退化。
  * 本排列：秩等价 **0/72**，|r| 最大 **0.8018**（随机+爬山在 12! 上搜索得到的最好值）。
    错误候选最多只能解释 64% 的方差，而真值在合成图上能到 R²≈0.98，留出 0.34 的余量。
  * 段 5 恰好中性（0x3F00），是「这段真的没动」的活对照；其余 11 段都动，且值互不相同，
    照片上每 8 个柱子的高低次序就是一个 8 长排列 —— 与 72 个预测逐一比对即可定案。

用法:
    python tools/build_beta_zonalmap.py              # 出 build/B35_zonal_map.BIN
    python tools/build_beta_zonalmap.py --patch-only # 只写 .tmp/b35.patch
    python tools/build_beta_zonalmap.py --dump       # 只打印表内容/映射
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import oly_container  # noqa: E402

BASE = 0x60021020
STOCK = ROOT / 'stock' / 'OLY_E_120_1400_0000_0000.BIN'
B26_REF = ROOT / '.tmp' / 'B26ref.BIN'
B26_SHIPPED = ROOT / 'build' / 'B26_four_looks_mx.BIN'
OUT = ROOT / 'build' / 'B35_zonal_map.BIN'
DECODED = ROOT / '.tmp' / 'B35.decoded.bin'

ZF_INSERT = 0x60479960          # LUT 上传器入口（5 个可钩字节）
ZF_RESUME = 0x60479965
ZF_STUB = 0x605DED44            # tone 池 slot 53，120 B，全镜像仅 1 处 base 引用
ZF_STUB_SRC = ROOT / 'tools' / 'hooks' / 'zf_stub.s'
ZF_STUB_BIN = ROOT / '.tmp' / 'zf_stub.bin'
ZF_STUB_ELF = ROOT / '.tmp' / 'zf_stub.elf'
ZF_STUB_OBJ = ROOT / '.tmp' / 'zf_stub.o'
TC = ROOT / '.tmp' / 'olya' / 'toolchains' / 'mn10300-elf' / 'bin'

HSL = 0x605F5A64                # 库存 HSL 数据集（0xBD4 B）
SLOT_OFF = [0x150, 0x690, 0x2D0, 0x450, 0x810, 0x990, 0x510, 0xA50, 0x5D0, 0xB10]
U32_SLOTS = {0, 1, 2, 4}
NEUTRAL_U16 = 0x3F00
TABLE_RAM = HSL + 0x150         # 0x605F5BB4
TABLE_LEN = 0xA80               # 4*0x180 + 6*0xC0
ZONES = 12                      # 12 段 × 8 项 = 96
PER_ZONE = 8
STEP = 0x200                    # 每档 0x200 = 全刻度 0x4000 的 1/32

# drop(c)：值 = 0x3F00 - drop(c)*STEP。排列码，见文件头：秩等价 0/72、|r| 最大 0.8018。
DROPS = [6, 9, 4, 2, 8, 0, 3, 10, 7, 1, 5, 11]


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def value_of(chunk: int) -> int:
    v = NEUTRAL_U16 - DROPS[chunk] * STEP
    if not (0 <= v <= 0x3FFF):
        raise SystemExit(f'chunk {chunk} value {v:#x} out of 0..0x3FFF')
    return v


def table_items() -> list[int]:
    """96 项，项 j 属于第 j//8 段。"""
    return [value_of(j // PER_ZONE) for j in range(96)]


def ensure_b26() -> bytes:
    """从 stock 重建 B26，并与已发布的那份逐字节对拍。"""
    if not B26_REF.exists() or B26_REF.stat().st_mtime < STOCK.stat().st_mtime:
        r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'build_beta_hook4mx.py'),
                            '--out', str(B26_REF)],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode:
            sys.stderr.write(r.stdout[-800:] + r.stderr[-800:])
            raise SystemExit('B26 rebuild failed')
    data = B26_REF.read_bytes()
    if B26_SHIPPED.exists():
        ref = B26_SHIPPED.read_bytes()
        if ref != data:
            raise SystemExit('B26 rebuild differs from build/B26_four_looks_mx.BIN '
                             f'(rebuild {sha256(data)[:16]} vs shipped {sha256(ref)[:16]})')
    return data


def assemble_stub(force: bool = False) -> bytes:
    """汇编 tools/hooks/zf_stub.s（与 B28/B29 同一份桩源），补末条 jmp 的 disp32。"""
    need = force or not ZF_STUB_BIN.exists() or \
        ZF_STUB_BIN.stat().st_mtime < ZF_STUB_SRC.stat().st_mtime
    if need:
        env = dict(os.environ)
        env.update(TMP='.tmp/gtmp', TEMP='.tmp/gtmp', TMPDIR='.tmp/gtmp')
        (ROOT / '.tmp' / 'gtmp').mkdir(parents=True, exist_ok=True)
        runs = [
            [str(TC / 'mn10300-elf-as.exe'), '-alh=' + str(ROOT / '.tmp' / 'zf_stub.lst'),
             '-o', str(ZF_STUB_OBJ), str(ZF_STUB_SRC)],
            [str(TC / 'mn10300-elf-ld.exe'), '-Ttext', hex(ZF_STUB), '-e', 'zf_entry',
             '-o', str(ZF_STUB_ELF), str(ZF_STUB_OBJ)],
            [str(TC / 'mn10300-elf-objcopy.exe'), '-O', 'binary', str(ZF_STUB_ELF),
             str(ZF_STUB_BIN)],
        ]
        for cmd in runs:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
            if r.returncode:
                sys.stderr.write((r.stdout or '') + (r.stderr or ''))
                raise SystemExit('assembler failed: ' + cmd[0])
    stub = ZF_STUB_BIN.read_bytes()
    if stub[-5] != 0xDC:
        raise SystemExit('stub does not end with a jmp')
    j = len(stub) - 5
    stub = bytearray(stub)
    stub[j + 1:j + 5] = ((ZF_RESUME - (ZF_STUB + j)) & 0xFFFFFFFF).to_bytes(4, 'little')
    return bytes(stub)


def tables() -> bytes:
    """十张实验表，按内存顺序排出（4 张 u32 + 6 张 u16，各 96 项）。"""
    items = table_items()
    order = sorted(range(10), key=lambda i: SLOT_OFF[i])
    out = bytearray()
    for i in order:
        if i in U32_SLOTS:
            for v in items:
                out += struct.pack('<I', (v << 16) | v)
        else:
            for v in items:
                out += struct.pack('<H', v)
    if len(out) != TABLE_LEN:
        raise SystemExit(f'table length {len(out):#x} != {TABLE_LEN:#x}')
    return bytes(out)


def write_patch(stub: bytes, tab: bytes) -> Path:
    patches = [
        (ZF_INSERT, b'\xdc' + ((ZF_STUB - ZF_INSERT) & 0xFFFFFFFF).to_bytes(4, 'little'),
         'jmp_zf'),
        (ZF_STUB, stub, 'stub_zf'),
        (TABLE_RAM, tab, 'zf_tables_ladder'),
    ]
    pf = ROOT / '.tmp' / 'b35.patch'
    with io.open(pf, 'w', encoding='ascii', newline='\n') as f:
        for ram, data, label in patches:
            f.write(f'ram 0x{ram:08X}={data.hex()}  # {label}\n')
    return pf


def payload_of(path: Path) -> bytes:
    blocks = oly_container.parse(path.read_bytes())
    if len(blocks) != 3:
        raise SystemExit(f'{path.name}: expected 3 blocks, got {len(blocks)}')
    return blocks[0].payload


def report(b35: Path, stub: bytes, tab: bytes) -> int:
    data = b35.read_bytes()
    stock_payload = payload_of(STOCK)
    b26_payload = payload_of(B26_REF)
    b35_payload = payload_of(b35)
    DECODED.write_bytes(b35_payload)
    regions = [(ZF_INSERT, 5), (ZF_STUB, len(stub)), (TABLE_RAM, len(tab))]
    changed = [(TABLE_RAM, len(tab)), (ZF_STUB, len(stub)), (ZF_INSERT, 5)]
    inside = bytearray(len(b35_payload))
    for ram, n in regions:
        inside[ram - BASE:ram - BASE + n] = b'\x01' * n
    diff = 0
    ok = True
    for i, (a, b) in enumerate(zip(b35_payload, b26_payload)):
        if a != b and not inside[i]:
            diff += 1
            ok = False
    # 十张表必须真的和 B28 的中性表不同（否则等于没做实验）
    neutral = bytes(TABLE_LEN)
    nb = bytearray()
    items = [NEUTRAL_U16] * 96
    for i in sorted(range(10), key=lambda k: SLOT_OFF[k]):
        for v in items:
            nb += struct.pack('<I', (v << 16) | v) if i in U32_SLOTS else struct.pack('<H', v)
    tab_diff = sum(1 for a, b in zip(tab, nb) if a != b)
    print(f'file      : {b35}  {len(data):,} B')
    print(f'sha256    : {sha256(data)}')
    print(f'payload   : {len(b35_payload):,} B  sha256 {sha256(b35_payload)}')
    print(f'patched   : {sum(n for _, n in changed)} B  '
          + '  '.join(f'{n}B@{ram:#010x}' for ram, n in changed))
    print(f'outside regions vs B26: {diff} byte(s) differ -> {"OK (0)" if ok else "MISMATCH"}')
    print(f'tables    : {len(tab)} B @ {TABLE_RAM:#x}..{TABLE_RAM + len(tab):#x}  '
          f'({tab_diff} B 与中性表不同)')
    print('code      : ' + '  '.join(f'c{c}:{DROPS[c]}' for c in range(ZONES)))
    print('values    : ' + '  '.join(f'{value_of(c):04X}' for c in range(ZONES)))
    print('pointers  :')
    for i, off in enumerate(SLOT_OFF):
        kind = 'u32' if i in U32_SLOTS else 'u16'
        print(f'  slot{i} {kind} -> {HSL + off:#010x}  (dataset+{off:#05x})')
    return 0 if ok else 1


def dump() -> int:
    print('chunk  drop  value   hue-range(if linear,deg)')
    for c in range(ZONES):
        print(f'  {c:2d}   {DROPS[c]:2d}   0x{value_of(c):04X}   '
              f'{c * 360 // ZONES:3d}..{(c + 1) * 360 // ZONES:3d}')
    items = table_items()
    print('items   :', ' '.join(f'{v:04X}' for v in items[:16]), '...')
    print('span    : max drop %d * 0x%X = 0x%X' % (max(DROPS), STEP, max(DROPS) * STEP))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(OUT))
    ap.add_argument('--patch-only', action='store_true')
    ap.add_argument('--force-asm', action='store_true')
    ap.add_argument('--dump', action='store_true')
    a = ap.parse_args()
    if a.dump:
        return dump()
    ensure_b26()
    stub = assemble_stub(a.force_asm)
    tab = tables()
    pf = write_patch(stub, tab)
    print(f'patch file: {pf}  ({pf.stat().st_size} B)')
    if a.patch_only:
        return 0
    out = Path(a.out).resolve()
    if out.exists():
        out.unlink()          # mkpatch 拒绝覆盖已存在输出
    r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'mkpatch.py'),
                        str(B26_REF), '--out', str(out), '--patch-file', str(pf)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    print((r.stdout or '')[-400:] or (r.stderr or '')[-800:])
    if r.returncode:
        raise SystemExit('mkpatch failed')
    return report(out, stub, tab)


if __name__ == '__main__':
    raise SystemExit(main())
