# -*- coding: utf-8 -*-
"""读 B18 槽位探针的四组 ORF+JPG，反查「画面模式 → (dir, slot)」。

B18 的阶梯：
- dir0..dir5 共 48 个槽，第 j = dir*8+slot 个槽写 `v(i) = 1023·(i/255)^0.62·s_j`，`s_j = 0.40 + 0.02·j`；
- 其余目录一律指向占位表 s = 1.0（解出来的 s 若 ≈1.0 或解不出，说明该档落在 dir≥6）。

索引口径用**实测标定值**：`idx = y_lin · 255`（`y_lin = (raw - black)/(white - black)`，见
`docs/color/tone-calibration.md` 与 B02b 的 ≤2.2/255 落机验证）。注意旧版 `read_b01_probe.py`
用的是 `r/x_white*255`（x_white=0.6626），那个口径已作废。

判读：`display8 = 255·(idx/255)^0.62·s` ⇒ `s = display8 / (255·(idx/255)^0.62)` ⇒ `j = round((s-0.40)/0.02)`。
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image

GAMMA = 0.62
S0, DS = 0.40, 0.02
N_PROBE = 48
FILLER_S = 1.0

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from read_b01_probe import patch_box  # 复用灰块定位


def measure(jpg_path: str, orf_path: str) -> dict:
    import rawpy

    jpg = np.asarray(Image.open(jpg_path).convert('RGB')).astype(np.float32)
    luma = 0.2126 * jpg[..., 0] + 0.7152 * jpg[..., 1] + 0.0722 * jpg[..., 2]
    x0, x1, y0, y1 = patch_box(luma)

    ix0, ix1 = x0 + (x1 - x0) * 0.30, x0 + (x1 - x0) * 0.75
    iy0, iy1 = y0 + (y1 - y0) * 0.18, y0 + (y1 - y0) * 0.58
    H, W = luma.shape
    sub = luma[int(iy0 * H):int(iy1 * H), int(ix0 * W):int(ix1 * W)]
    jpg_mean = float(sub.mean())

    with rawpy.imread(orf_path) as raw:
        img = raw.raw_image_visible.astype(np.float32)
        black = float(np.mean(raw.black_level_per_channel))
        white = float(raw.white_level)
        h, w = img.shape
        rsub = img[int(iy0 * h):int(iy1 * h), int(ix0 * w):int(ix1 * w)]
        lin = (rsub - black) / (white - black)
        lin = float(np.clip(lin, 0, 1).mean())

    idx = float(np.clip(lin * 255.0, 0, 255))
    return dict(jpg=jpg_mean, lin=lin, idx=idx, std=float(sub.std()))


def solve(m: dict) -> dict:
    idx, disp = m['idx'], m['jpg']
    if idx < 3:
        return dict(ok=False, why='raw 太暗')
    base = 255.0 * (idx / 255.0) ** GAMMA
    s = disp / base
    j = int(round((s - S0) / DS))
    ok = 0 <= j < N_PROBE
    return dict(ok=ok, s=s, j=j, filler=abs(s - FILLER_S) < 0.03,
                dir=j // 8 if ok else None, slot=j % 8 if ok else None,
                pred=base * (S0 + DS * j) if ok else None)


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else r'I:\DCIM\100OLYMP'
    stems = argv[2:] or ['PB150122', 'PB150123', 'PB150124', 'PB150125']
    names = ['vivid', 'natural', 'muted', 'portrait']
    for name, stem in zip(names, stems):
        jpg = os.path.join(base, stem + '.JPG')
        orf = os.path.join(base, stem + '.ORF')
        if not (os.path.exists(jpg) and os.path.exists(orf)):
            print(f'{stem:10s} missing')
            continue
        m = measure(jpg, orf)
        r = solve(m)
        if not r['ok']:
            print(f"{name:9s} {stem} jpg={m['jpg']:6.2f} idx={m['idx']:5.1f} "
                  f"s={r['s']:5.2f}  -> 落在 dir>=6 或读不出 ({r['why'] if 'why' in r else 's 超范围'})")
        else:
            print(f"{name:9s} {stem} jpg={m['jpg']:6.2f} idx={m['idx']:5.1f} "
                  f"s={r['s']:.3f} -> j={r['j']:2d} = dir {r['dir']} slot {r['slot']} "
                  f"(pred {r['pred']:.1f} vs meas {m['jpg']:.1f})")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
