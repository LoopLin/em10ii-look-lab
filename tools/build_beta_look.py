# -*- coding: utf-8 -*-
"""B 测出包器：把一个 look 的 tone 与色度块写进固件（21 个目录全指同一张表）。

用法：
    python tools/build_beta_look.py reala_ace --out build/B02_realaace.BIN

设计（见 docs/firmware/beta/README.md）：
- tone 表写到 `0x605D8344`（目录 0 的首个槽位）；
- `0x60600920` 起 168 个指针 **全部** 指向 `0x605D8344`
  ⇒ 不管四档读的是哪个目录、哪个变体位、哪个 A/B 槽，读到的都是这一张表；
- 色度块写到 `0x605D4829`（标准拍照档只读 k=0）；
- 其余字节一个不动。矩阵与代码 hook 不在本包内。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys

TONE_RAM = 0x605D8344
POOL_RAM = 0x60600920
POOL_N = 168
CHROMA_RAM = 0x605D4829
CHROMA_LEN = 100
BASE = 0x60021020
SPEC_DIR = '.tmp/looks_matrix'
MATRIX_RAM = 0x605ECECA      # 色调矩阵 m0：9 x i16，Q8（256 = 1.0），行主序
GATE_RAM = 0x604B6AD4        # 矩阵门：84 14 (mov d1,d0 / extbu) -> 80 01 (mov 1,d0)
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'



IDX_OLD_18 = 22.0    # look 规格里的索引口径：18% 灰落在 22
IDX_CAM_18 = 46.0    # 相机口径（经验标定 P=255）：18% 灰落在 0.18*255


def retarget_tone(tone):
    """把 look 规格的索引口径重采样到相机口径。

    规格里 18% 灰在索引 22，相机实际把 18% 灰放在索引 46
    （P=255，见 .tmp/idx_cal.py：12 组库存配对回归 P=254.8）。
    new[j] = old[j * 22/46]，超出老表末尾的一律取饱和值。
    """
    import numpy as np
    old = np.asarray([int(v) for v in tone], dtype=float)
    k = IDX_OLD_18 / IDX_CAM_18
    j = np.arange(256, dtype=float)
    new = np.interp(j * k, np.arange(256, dtype=float), old)
    return [int(round(v)) for v in new]


def load_spec(look: str) -> dict:
    p = os.path.join(SPEC_DIR, look + '.json')
    with io.open(p, encoding='utf-8') as f:
        return json.load(f)


def tone_bytes(tone) -> bytes:
    if len(tone) != 256:
        raise SystemExit(f'tone 长度 {len(tone)} != 256')
    vals = [int(v) for v in tone]
    if any(v < 0 or v > 1023 for v in vals):
        raise SystemExit('tone 越界（0..1023）')
    if any(b < a for a, b in zip(vals, vals[1:])):
        raise SystemExit('tone 非单调')
    return b''.join(int(v).to_bytes(2, 'little') for v in vals)


def matrix_flat(spec: dict) -> list:
    """取 look 的 3x3 矩阵，并按硬件编码器（0x604B6A22）的规则先偶化。

    硬件那条 3x3 应用路径会把 m01/m21 的 LSB 无条件清零，并在 m10+m12 为奇时
    把两者一起清零；对角（m00/m11/m22）不在编码器里原样使用，而是由行和反推。
    所以写表前做同样的偶化、再由对角反推行和 256（Q8，256 = 1.0），
    保证"写进固件的 9 个数"就是硬件实际会用的 9 个数。

    这条实现被 B22（四套 look）与 B34（每档矩阵）共用 —— 只有一份，别另写。
    """
    m = spec.get('matrix')
    if not m:
        raise SystemExit('look 里没有 matrix 字段')
    if isinstance(m, dict):
        q = m.get('quantized') or {}
        if 'table_values' in q:
            flat = [int(v) for v in q['table_values']]
        else:
            raise SystemExit('matrix.quantized.table_values 缺失')
    else:
        flat = [int(round(v)) for row in m for v in row]
    if len(flat) != 9:
        raise SystemExit('matrix 不是 3x3')
    for _i in (1, 7):
        if flat[_i] & 1:
            flat[_i] &= ~1
    if (flat[3] + flat[5]) & 1:
        flat[3] &= ~1
        flat[5] &= ~1
    for _r in range(3):
        flat[_r * 3] = 256 - flat[_r * 3 + 1] - flat[_r * 3 + 2]
    for r in range(3):
        if sum(flat[r * 3:r * 3 + 3]) != 256:
            raise SystemExit(f'matrix 第 {r} 行和 != 256（硬件编码器要求）')
    return flat


def matrix_bytes(spec: dict) -> bytes:
    """9 个 i16（行主序，Q8）—— 直接可写进矩阵表/槽位的字节。"""
    return b''.join(int(v).to_bytes(2, 'little', signed=True)
                    for v in matrix_flat(spec))


def chroma_bytes(ch: dict) -> bytes:
    def arr(name, n):
        a = [int(v) for v in ch[name]]
        if len(a) != n:
            raise SystemExit(f'chroma.{name} 长度 {len(a)} != {n}')
        return bytes(a)

    x = arr('cb_x', 16)
    y = arr('cb_y', 16)
    z = arr('cb_z', 17)
    X = arr('cr_x', 16)
    Y = arr('cr_y', 16)
    def derive_z(xs, ys):
        # z[i] = 第 i-1 个节点到第 i 个节点这一段增益：
        #   z[i] = round((y[i]-y[i-1]) * 128 / (x[i]-x[i-1]))，dx=0 时填 128。
        # 与库存块、B01 探针块一致（x=[0,0,0,0,64,128,192,255,...] 时 z[4] 才是
        # 0→64 那一段）。旧实现把这段写到了 z[5]，整块右移一格 —— B07 的隐性 bug。
        # 退化段（dx==0）按库存块的写法"沿用上一段的斜率"，首段之前为 0。
        # 库存证据 k=3：x=[0,80,112,...,176,255,255] y=[37,87,...,169,218]
        #   z=[0,80,104,112,128,128,128,128,112,104,79,79,...] —— 尾部 dx=0 全是 79（沿用）。
        z = bytearray(17)
        for i in range(1, 16):
            dx = xs[i] - xs[i - 1]
            dy = ys[i] - ys[i - 1]
            z[i] = (int(round(dy * 128.0 / dx)) & 0xFF) if dx else z[i - 1]
        z[16] = z[15]
        return bytes(z)

    z = derive_z(x, y)
    Z = derive_z(X, Y)
    blk = bytearray(100)
    blk[0x00:0x10] = x
    blk[0x10:0x20] = y
    blk[0x20:0x31] = z
    blk[0x32:0x42] = X
    blk[0x42:0x52] = Y
    blk[0x52:0x63] = Z
    return bytes(blk)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('look')
    ap.add_argument('--out', required=True)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--matrix', action='store_true',
                    help='把 look 的 3x3 矩阵写进色调矩阵 m0（0x605ECECA，Q8，9x i16）')
    ap.add_argument('--gate', action='store_true',
                    help='打开矩阵门：0x604B6AD4 84 14 -> 80 01（恒选 m0）')
    ap.add_argument('--extra', action='append', default=[],
                    help='附加补丁文件（mkpatch --patch-file 格式，ram 行），可重复')
    args = ap.parse_args()

    spec = load_spec(args.look)
    tone = tone_bytes(retarget_tone(spec['tone']))
    blk = chroma_bytes(spec['chroma'])
    pool = b''.join(TONE_RAM.to_bytes(4, 'little') for _ in range(POOL_N))
    print(f"look={args.look}  tone={len(tone)}B  pool={len(pool)}B  chroma={len(blk)}B")
    print(f"  tone[22]={int.from_bytes(tone[44:46],'little')}  "
          f"tone[46]={int.from_bytes(tone[92:94],'little')}  "
          f"tone[64]={int.from_bytes(tone[128:130],'little')}  "
          f"tone[128]={int.from_bytes(tone[256:258],'little')}  "
          f"tone[255]={int.from_bytes(tone[510:512],'little')}")
    print(f"  Cb y = {list(blk[0x10:0x20])}")
    print(f"  Cr y = {list(blk[0x42:0x52])}")

    mx = b''
    if args.matrix:
        mx = matrix_bytes(spec)
        print(f"  matrix = {matrix_flat(spec)}")

    patch = io.StringIO()
    patch.write(f"# beta look {args.look}: tone -> {TONE_RAM:#x}, pool 168 -> same, chroma -> {CHROMA_RAM:#x}\n")
    patch.write(f"ram {TONE_RAM:#x}={tone.hex()}\n")
    patch.write(f"ram {POOL_RAM:#x}={pool.hex()}\n")
    patch.write(f"ram {CHROMA_RAM:#x}={blk.hex()}\n")
    if mx:
        patch.write(f"ram {MATRIX_RAM:#x}={mx.hex()}\n")
    if args.gate:
        patch.write(f"ram {GATE_RAM:#x}=8001\n")
    extras = []
    for ef in args.extra:
        for line in io.open(ef, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            patch.write(line + '\n')
            if line.startswith('ram '):
                addr, hexv = line[4:].split('=', 1)
                extras.append((int(addr, 16), bytes.fromhex(hexv)))
        print(f"  extra patch: {ef}")
    pf = os.path.join('.tmp', f'{args.look}.patch')
    io.open(pf, 'w', encoding='utf-8', newline='\n').write(patch.getvalue())
    print('  patch file:', pf)

    if args.dry_run:
        return 0
    if os.path.exists(args.out):
        raise SystemExit(f'拒绝覆盖已有文件：{args.out}')

    cmd = [sys.executable, 'tools/mkpatch.py', SRC, '--out', args.out, '--patch-file', pf]
    print('  ', ' '.join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    print(r.stdout[-3000:])
    if r.returncode:
        print(r.stderr[-2000:])
        return r.returncode

    # 独立回读
    sys.path.insert(0, 'tools')
    import oly_container  # noqa: E402

    out_blocks = oly_container.parse(open(args.out, 'rb').read())
    b0 = out_blocks[0].payload
    off_t = TONE_RAM - BASE
    got_tone = b0[off_t:off_t + 0x200]
    got_pool = b0[POOL_RAM - BASE: POOL_RAM - BASE + 4 * POOL_N]
    got_ch = b0[CHROMA_RAM - BASE: CHROMA_RAM - BASE + CHROMA_LEN]
    print(f"  回读 tone 一致: {got_tone == tone}")
    print(f"  回读 pool 一致: {got_pool == pool}")
    print(f"  回读 chroma 一致: {got_ch == blk}")
    if mx:
        got_mx = b0[MATRIX_RAM - BASE: MATRIX_RAM - BASE + 18]
        print(f"  回读 matrix 一致: {got_mx == mx}")
    for addr, want in extras:
        got = b0[addr - BASE: addr - BASE + len(want)]
        print(f"  回读 extra {addr:#x} 一致: {got == want}")
        if got != want:
            return 1
    if args.gate:
        got_g = b0[GATE_RAM - BASE: GATE_RAM - BASE + 2]
        print(f"  回读 gate 字节: {got_g.hex()} (期望 8001)")
        if got_g != b'\x80\x01':
            return 1
    if not (got_tone == tone and got_pool == pool and got_ch == blk):
        return 1
    size = os.path.getsize(args.out)
    print(f"  size={size} (期望 {os.path.getsize(SRC)})  ok={size == os.path.getsize(SRC)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
