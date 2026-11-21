#!/usr/bin/env python3
"""离线预览：把固件里的色度曲线直接作用到一张已有照片上。

用法:
  python tools/look_preview.py IN.JPG OUT.png --y "0,54,128,84,172,202"

--y 给的是曲线结点（x=0/64/128/192/255 对应的输出值），中间线性插值。
作用对象是照片的 Cb/Cr，亮度不动，然后回 RGB。

为什么可以这么做：相机里这条曲线作用在「色度」通道上，曲线之前是 tone/矩阵，
曲线之后是模式饱和等。把曲线作用到一张已经过相机处理的照片上，等于把曲线
挪到最后一级 —— 位置不同，但同一张照片上「曲线改变色度多少」的比例是可比的。
"""
import sys

import numpy as np
from PIL import Image


def ycc(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return y, cb, cr


def rgb_from(y, cb, cr):
    r = y + 1.402 * (cr - 128.0)
    g = y - 0.344136 * (cb - 128.0) - 0.714136 * (cr - 128.0)
    b = y + 1.772 * (cb - 128.0)
    return np.clip(np.stack([r, g, b], axis=-1), 0, 255)


def make_curve(knots):
    """knots = [y(0), y(64), y(128), y(192), y(255)] -> 256 项查表。"""
    xs = np.array([0, 64, 128, 192, 255], dtype=float)
    ys = np.array(knots, dtype=float)
    lut = np.interp(np.arange(256), xs, ys)
    return lut.astype(np.uint8)


def apply_curve(img, lut, strength=1.0):
    y, cb, cr = ycc(img)
    def curve(ch):
        idx = np.clip(np.round(ch), 0, 255).astype(np.int32)
        out = lut[idx].astype(np.float32)
        if strength != 1.0:
            out = ch + (out - ch) * strength
        return out
    return rgb_from(y, curve(cb), curve(cr))


def main(args):
    src, out = args[0], args[1]
    knots = [0, 64, 128, 192, 255]
    strength = 1.0
    for a in args[2:]:
        if a.startswith("--y="):
            knots = [float(v) for v in a[4:].split(",")]
        elif a.startswith("--strength="):
            strength = float(a[11:])

    img = np.asarray(Image.open(src).convert("RGB"), dtype=np.float32)
    lut = make_curve(knots)
    res = apply_curve(img, lut, strength)

    before = Image.fromarray(img.astype(np.uint8))
    after = Image.fromarray(res.astype(np.uint8))
    w, h = before.size
    canvas = Image.new("RGB", (w * 2 + 8, h), (24, 24, 24))
    canvas.paste(before, (0, 0))
    canvas.paste(after, (w + 8, 0))
    canvas.thumbnail((2400, 1400))
    canvas.save(out)
    print("knots", knots, "strength", strength, "->", out)


if __name__ == "__main__":
    main(sys.argv[1:])
