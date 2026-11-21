# -*- coding: utf-8 -*-
"""把卡上的照片按固件版本归档到 G:/em10fw/photos/<版本>/ ，校验后删除卡上副本。

版本由本会话的实验记录确定（文件号连续段）；映射不到的放 _unknown/。
安全：先复制 -> 逐个比 size+sha256 -> 全部通过才删除卡上文件。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

CARD = Path(r'I:/DCIM/100OLYMP')
DEST = Path(r'G:/em10fw/photos')

# 连续号段 -> 版本（含端点，按 PB 全局编号）
SPANS = [
    (150086, 150093, 'B01_dirprobe'),
    (150094, 150105, 'B02b_reala'),
    (150106, 150109, 'B04_chroma_precomp'),
    (150110, 150117, 'B05_matrix_gate'),
    (150118, 150121, 'B06_gate_stock_matrix'),
    (150122, 150126, 'B18_slotprobe'),
    (150127, 150130, 'B20_keyprobe'),
    (150131, 150134, 'B21_v09probe'),
    (150135, 150138, 'B22_reala'),
    (150139, 150142, 'B24b_modeprobe'),
    # 2026-11-17 卡上归类：按拍摄时间落在两次刷机之间（FLASH-LOG）
    (160054, 160056, 'B48_extreme_mapping/_unattributed_B4x'),
    (160057, 160060, 'B48_extreme_mapping'),
    (160061, 160065, 'stock_reference'),
    (160066, 160070, 'B51_matrix_exif'),
    (160071, 160071, 'B54_b48plus'),
    (160072, 160076, 'B58_comp'),
    (160077, 160081, 'B59_real'),
    (160082, 160085, 'B60_chroma'),
    (160086, 160090, 'B61_chroma2'),
    (160091, 160092, 'B62_gateflip'),
    # 170093 在 B63 刷入之后、但 B63 未能写入成片，版本无法确认 -> _unknown
    (170001, 170004, 'B69_chroma_curves'),
    (170005, 170008, 'B72_hsl_dirtybit'),
    (170009, 170012, 'B73_hsl_probe'),
    (170013, 170016, 'B74_hsl_bit'),
    (170017, 170017, 'B76_hsl_trace2'),
    (170018, 170021, 'B77_hsl_content'),
    (170094, 170099, 'B65_slot1'),
    (170100, 170108, 'B65_slot1'),
    (170109, 170114, 'B66_slot1'),
    (170115, 170118, 'B67_slot1'),
    (170119, 170122, 'B68_slot1'),
]
UNKNOWN = '_unknown'


def sha(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def version_of(num: int) -> str:
    for lo, hi, name in SPANS:
        if lo <= num <= hi:
            return name
    return UNKNOWN


def main() -> int:
    if not CARD.is_dir():
        print(f'卡目录不存在: {CARD}')
        return 1
    stems = sorted({p.stem for p in CARD.iterdir() if p.is_file()})
    print(f'卡上 {len(stems)} 组')

    plan: dict[str, list[Path]] = {}
    for stem in stems:
        num = int(stem[2:]) if stem[2:].isdigit() else -1
        plan.setdefault(version_of(num), []).append(stem)
    for v in sorted(plan):
        print(f'  {v:24} {len(plan[v]):3} 组  {plan[v][0]}..{plan[v][-1]}')

    total = 0
    for v, names in plan.items():
        d = DEST / v
        d.mkdir(parents=True, exist_ok=True)
        for stem in names:
            for ext in ('.JPG', '.ORF', '.jpg', '.orf'):
                src = CARD / (stem + ext)
                if not src.exists():
                    continue
                dst = d / src.name
                if dst.exists() and dst.stat().st_size == src.stat().st_size:
                    continue
                shutil.copy2(src, dst)
                total += 1
    print(f'复制 {total} 个文件 -> {DEST}')

    # 校验
    bad = []
    for v, names in plan.items():
        for stem in names:
            for ext in ('.JPG', '.ORF', '.jpg', '.orf'):
                src = CARD / (stem + ext)
                if not src.exists():
                    continue
                dst = DEST / v / src.name
                if not dst.exists() or dst.stat().st_size != src.stat().st_size or sha(dst) != sha(src):
                    bad.append(f'{v}/{src.name}')
    if bad:
        print(f'校验失败 {len(bad)} 个，未删除任何卡上文件：')
        for b in bad[:10]:
            print('  ', b)
        return 2
    print('校验全部通过')

    # 删除
    n = 0
    for p in CARD.iterdir():
        if p.is_file() and p.suffix.upper() in ('.JPG', '.ORF'):
            p.unlink()
            n += 1
    print(f'已删除卡上 {n} 个文件')
    left = sorted(p.name for p in CARD.iterdir() if p.is_file())
    print(f'卡目录剩余: {left if left else "(空)"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
