#!/usr/bin/env python3
"""按「物体类别」对比不同固件对同一场景的颜色处理。

用 raw 中性显影给像素分类（红盒/黄盖/绿瓶/蓝盒/中性），
再读每一类在相机 JPG 里的 Cb/Cr 中位数 —— 同一场景、手动白平衡下可比。

用法: python tools/class_measure.py LABEL=ORF [LABEL=ORF ...]
"""
import sys

import numpy as np
import rawpy
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def ycc(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return y, cb, cr


def classify(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    y = 0.299 * r + 0.587 * g + 0.114 * b
    lit = y > 60
    return {
        "neutral": lit & ((mx - mn) < 10),
        "red": lit & (r > g + 35) & (r > b + 35),
        "yellow": lit & (r > b + 45) & (g > b + 30),
        "green": lit & (g > r + 15) & (g > b + 15),
        "blue": lit & (b > r + 25) & (b > g + 20),
    }


def main(args):
    rows = {}
    for a in args:
        label, _, orf = a.partition("=")
        raw = rawpy.imread(orf)
        rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True,
                              output_bps=8, half_size=True).astype(np.float32)
        raw.close()
        jpg = np.asarray(Image.open(orf[:-4] + ".JPG").convert("RGB"), dtype=np.float32)
        h, w = rgb.shape[:2]
        if jpg.shape[:2] != (h, w):
            jpg = np.asarray(Image.fromarray(jpg.astype(np.uint8)).resize((w, h), Image.BILINEAR),
                             dtype=np.float32)
        masks = classify(rgb)
        _, cb1, cr1 = ycc(jpg)
        rows[label] = {}
        for k, m in masks.items():
            n = int(m.sum())
            if n < 500:
                rows[label][k] = None
                continue
            rows[label][k] = (float(np.median(cb1[m])), float(np.median(cr1[m])), n)

    cats = ["neutral", "red", "yellow", "green", "blue"]
    print(f"{'label':14s} " + " ".join(f"{c:>18s}" for c in cats))
    for label, d in rows.items():
        cells = []
        for c in cats:
            v = d.get(c)
            cells.append("     --          " if v is None else f"Cb{v[0]:6.1f} Cr{v[1]:6.1f} n{v[2]:6d}")
        print(f"{label:14s} " + " ".join(cells))


if __name__ == "__main__":
    main(sys.argv[1:])
