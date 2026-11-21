# -*- coding: utf-8 -*-
"""生成色度靶图:同一亮度下 8 色相 × 4 饱和度,用来反推色度块是否生效。

设计要点:
- 每块足够大(约 200×200 px),照到后中心 120×120 仍纯净;
- 全部块 Y 相同(L*≈55),所以逐块比较的差异只来自 Cb/Cr,不受 tone 曲线斜率干扰;
- 附 3 个中性块(白/中灰/黑)用于白平衡与黑位参考。
"""
from __future__ import annotations

import colorsys
import os

import numpy as np
from PIL import Image, ImageDraw

W, H = 1920, 1080
BG = (18, 18, 18)
OUT = os.path.join(os.path.expanduser('~'), 'Desktop', 'B02_chroma_target.png')

COLS, ROWS = 8, 4
SAT = [0.25, 0.50, 0.75, 1.00]
LUM = 0.55          # 目标亮度(HSV 的 V),保证所有块 Y 接近
CELL = 200
GAP = 12
LEFT = 60
TOP = 130


def hsv_to_rgb(h: float, s: float, v: float) -> tuple:
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def main() -> int:
    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((LEFT, 40), 'B02 chroma target  (same Y, sweep hue x saturation)', fill=(235, 235, 235))
    d.text((LEFT, 70), 'shoot with current firmware, M mode, ISO fixed, EV 0, AWB, half-press before each', fill=(170, 170, 170))
    d.text((LEFT, 98), 'order: filename ascending = vivid / natural / muted / portrait', fill=(120, 120, 120))

    for r, s in enumerate(SAT):
        for c in range(COLS):
            hue = c / COLS
            col = hsv_to_rgb(hue, s, LUM)
            x = LEFT + c * (CELL + GAP)
            y = TOP + r * (CELL + GAP)
            d.rectangle([x, y, x + CELL, y + CELL], fill=col)
            d.text((x + 8, y + 8), f'{"RGBYCM"[(c // 2) % 6]}{int(hue*360):03d}S{int(s*100)}',
                   fill=(20, 20, 20) if s > 0.5 else (230, 230, 230))

    # 中性参考
    n_y = TOP + ROWS * (CELL + GAP) + 20
    for i, v in enumerate((255, 128, 32)):
        x = LEFT + i * (CELL + GAP)
        d.rectangle([x, n_y, x + CELL, n_y + 120], fill=(v, v, v))
        d.text((x + 8, n_y + 8), f'GRAY {v}', fill=(20, 20, 20) if v > 120 else (230, 230, 230))

    d.text((LEFT, H - 40), 'keep the frame edges (crosses) inside the screen area', fill=(150, 150, 150))
    for (cx, cy) in ((20, 20), (W - 20, 20), (20, H - 20), (W - 20, H - 20)):
        d.line([cx - 14, cy, cx + 14, cy], fill=(200, 200, 200))
        d.line([cx, cy - 14, cx, cy + 14], fill=(200, 200, 200))

    img.save(OUT)
    print('written:', OUT)
    a = np.asarray(img).astype(float)
    yy = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    print('patch Y mean %.1f  min %.1f  max %.1f' % (yy[TOP:TOP + ROWS * (CELL + GAP), LEFT:LEFT + COLS * (CELL + GAP)].mean(),
                                                     yy[TOP:TOP + ROWS * (CELL + GAP), LEFT:LEFT + COLS * (CELL + GAP)].min(),
                                                     yy[TOP:TOP + ROWS * (CELL + GAP), LEFT:LEFT + COLS * (CELL + GAP)].max()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
