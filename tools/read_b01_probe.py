# -*- coding: utf-8 -*-
"""读 B01 探针的四组 ORF+JPG，反查「画面模式 → tone 目录」。

原理（见 docs/color/tone-calibration.md 与 docs/firmware/beta/B01-dirprobe.md）：
- B01 把 21 个目录各写了一条签名曲线 `v(i) = 1023·(i/255)^0.62·s_d`，`s_d = 0.55+0.045·d`；
- tone 表把 raw 线性值 r 映射成 10 位显示值 v，索引 `i = r/x_white·255`，`x_white = 0.6626`；
- 8 位显示值 ≈ `v/1023·255` = `((i/255)^0.62·s_d)·255`。

于是：从 ORF 取灰块的平均线性值 → 算出 i；从同名 JPG 取同一区域的平均 8 位值；
两者相除就解出 s（因而解出 d）。这一步不需要知道屏幕亮度，也不需要知道曝光。
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image

X_WHITE = 0.6626
GAMMA = 0.62
S0, DS = 0.55, 0.045


def patch_box(luma: np.ndarray) -> tuple[float, float, float, float]:
    """在 JPG 里找中央那块大灰块，返回 (x0,x1,y0,y1) 的分数坐标。"""
    h, w = luma.shape
    lo, hi = np.percentile(luma, 5), np.percentile(luma, 99.5)
    thr = lo + 0.55 * (hi - lo)
    m = luma > thr
    # 只保留中央 90% 区域，避免屏幕边框
    m[: int(h * 0.05), :] = False
    m[int(h * 0.95):, :] = False
    m[:, : int(w * 0.05)] = False
    m[:, int(w * 0.95):] = False
    rows = m.sum(1) > (m.shape[1] * 0.25)
    cols = m.sum(0) > (m.shape[0] * 0.25)
    if not rows.any() or not cols.any():
        return 0.30, 0.70, 0.30, 0.60
    y0, y1 = np.where(rows)[0][[0, -1]]
    x0, x1 = np.where(cols)[0][[0, -1]]
    return x0 / w, (x1 + 1) / w, y0 / h, (y1 + 1) / h


def measure(jpg_path: str, orf_path: str) -> dict:
    import rawpy

    jpg = np.asarray(Image.open(jpg_path).convert('RGB')).astype(np.float32)
    luma = 0.2126 * jpg[..., 0] + 0.7152 * jpg[..., 1] + 0.0722 * jpg[..., 2]
    x0, x1, y0, y1 = patch_box(luma)

    # 取灰块内部一个避开四角色块与文字的子区域
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

    i = lin / X_WHITE * 255.0
    i = float(np.clip(i, 0, 255))
    return dict(jpg=jpg_mean, lin=lin, idx=i,
                box=(round(x0, 3), round(x1, 3), round(y0, 3), round(y1, 3)),
                sub=(round(sub.mean(), 2), round(float(sub.std()), 2)))


def main() -> int:
    base = r'I:\DCIM\100OLYMP'
    names = ['vivid','natural','muted','portrait']
    stems = ['PB150090', 'PB150091', 'PB150092', 'PB150093']
    rows = []
    for stem in stems:
        jpg = os.path.join(base, stem + '.JPG')
        orf = os.path.join(base, stem + '.ORF')
        if not (os.path.exists(jpg) and os.path.exists(orf)):
            print('missing', stem)
            continue
        m = measure(jpg, orf)
        i = m['idx']
        pred = [255.0 * (i / 255.0) ** GAMMA * (S0 + DS * d) for d in range(21)]
        best = int(np.argmin([abs(p - m['jpg']) for p in pred]))
        s = m['jpg'] / (255.0 * (i / 255.0) ** GAMMA) if i > 3 else float('nan')
        d_float = (s - S0) / DS
        print(f"{stem}: jpg={m['jpg']:.2f} (sd={m['sub'][1]:.2f})  raw_lin={m['lin']:.4f} "
              f"idx={i:6.2f}  s={s:.3f}  d~{d_float:6.2f}  best_dir={best} "
              f"(pred={pred[best]:.1f} vs meas={m['jpg']:.2f})")
        rows.append((stem, m['jpg'], i, best, d_float))

    print()
    print('dir   s     pred@idx(each file)')
    for d in range(21):
        s = S0 + DS * d
        cells = []
        for stem, jpg, i, best, df in rows:
            cells.append(f"{255.0*(i/255.0)**GAMMA*s:6.1f}")
        print(f"{d:3d} {s:5.3f}  " + ' '.join(cells))
    return 0


if __name__ == '__main__':
    sys.exit(main())
