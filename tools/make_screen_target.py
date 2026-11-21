# -*- coding: utf-8 -*-
"""生成 B01 用的屏幕测试图（横板 1920×1080）。

用途：拍电脑屏幕，比较四个画面模式在**同一曝光**下的中间调亮度，
从而反查「四档 → tone 目录」的映射。

设计要点：
- 中央一大块 sRGB 119 的 18% 灰，占画面主体（测量区）；
- 四角各一个 0/255 参考块（看是否削顶）；
- 底部 9 级灰梯（看肩部与线性）；
- 边框与定位十字（便于对齐、也避免屏幕边缘溢出干扰）。
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
BG = (18, 18, 18)
GRAY = 119          # 18% 灰（sRGB）
WHITE = 255
BLACK = 0

DESKTOP = os.path.join(os.path.expanduser('~'), 'Desktop')


def font(size: int):
    for name in ('msyh.ttc', 'msyhl.ttc', 'arial.ttf', 'consola.ttf'):
        p = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
    return ImageFont.load_default()


def main() -> int:
    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)
    f_big, f_mid, f_small = font(34), font(22), font(18)

    # 外框（留 40 px 安全边）
    d.rectangle([40, 40, W - 40, H - 40], outline=(90, 90, 90), width=3)

    # 中央测量区：18% 灰
    gx0, gy0, gx1, gy1 = 470, 200, W - 470, 760
    d.rectangle([gx0, gy0, gx1, gy1], fill=(GRAY, GRAY, GRAY))
    d.text((gx0 + 24, gy0 + 18), 'GRAY 119  (18%)', font=f_small, fill=(70, 70, 70))

    # 测量区内的四个参考块（黑白各两枚）
    chip = 90
    pads = [(gx0 + 40, gy1 - chip - 40, BLACK), (gx0 + 40 + chip + 30, gy1 - chip - 40, WHITE),
            (gx1 - 40 - chip * 2 - 30, gy1 - chip - 40, BLACK), (gx1 - 40 - chip, gy1 - chip - 40, WHITE)]
    for x, y, v in pads:
        d.rectangle([x, y, x + chip, y + chip], fill=(v, v, v))

    # 底部 9 级灰梯（标签写在格子内部，避免与页脚重叠）
    n = 9
    sx0, sx1, sy0, sy1 = 160, W - 160, 800, 930
    step_w = (sx1 - sx0) / n
    vals = [0, 32, 64, 96, 119, 152, 192, 224, 255]
    for i, v in enumerate(vals):
        x0 = sx0 + i * step_w
        d.rectangle([x0, sy0, x0 + step_w - 3, sy1], fill=(v, v, v))
        fg = (235, 235, 235) if v < 128 else (30, 30, 30)
        d.text((x0 + step_w / 2, sy0 + 14), str(v), font=f_small, fill=fg, anchor='ma')

    # 定位十字（四角）
    for cx, cy in ((40, 40), (W - 40, 40), (40, H - 40), (W - 40, H - 40)):
        d.line([cx - 26, cy, cx + 26, cy], fill=(200, 200, 200), width=3)
        d.line([cx, cy - 26, cx, cy + 26], fill=(200, 200, 200), width=3)

    # 标题
    d.text((70, 70), 'B01 gray target  ·  1920×1080  ·  sRGB', font=f_big, fill=(230, 230, 230))
    d.text((70, 118), '固定曝光拍四张：Natural / Vivid / Muted / Portrait', font=f_mid, fill=(180, 180, 180))
    d.text((70, H - 70), 'M 档 · ISO 200 · 光圈不变 · 快门不变 · AWB · 关 i-Enhance/Art',
           font=f_mid, fill=(180, 180, 180))

    os.makedirs(DESKTOP, exist_ok=True)
    out = os.path.join(DESKTOP, 'B01_gray_target.png')
    img.save(out)
    print('written:', out, os.path.getsize(out), 'bytes')
    # 供核对：中心像素值
    print('center px:', img.getpixel(((gx0 + gx1) // 2, (gy0 + gy1) // 2)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
