#!/usr/bin/env python3
"""用同一场景、同一模式、不同固件的 ORF/JPG 对，反推相机色度传递函数。

用法:
  python tools/chroma_transfer.py OUT.png LABEL=ORF [LABEL=ORF ...]

对每张：rawpy 中性显影 -> Cb/Cr；与相机 JPG 逐像素对齐；
按 raw 的 Cb/Cr 取值分箱，取每箱 JPG 色度的中位数，得到 g(raw)->jpeg。
不同固件的 g 叠在一张图上，就能看出每条曲线的真实压缩比。
"""
import sys

import numpy as np
import rawpy
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

EDGES = np.array([100, 108, 116, 120, 122, 124, 126, 128, 130, 132, 134, 136, 140, 148, 160],
                 dtype=float)


def ycc(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return y, cb, cr


def pair(orf):
    raw = rawpy.imread(orf)
    rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True,
                          output_bps=8, half_size=True)
    raw.close()
    jpg = np.asarray(Image.open(orf[:-4] + ".JPG").convert("RGB"), dtype=np.float32)
    h, w = rgb.shape[:2]
    if jpg.shape[:2] != (h, w):
        jpg = np.asarray(Image.fromarray(jpg.astype(np.uint8)).resize((w, h), Image.BILINEAR),
                         dtype=np.float32)
    return ycc(rgb.astype(np.float32)), ycc(jpg)


def binned(x_raw, x_jpg, y_raw, edges):
    idx = np.digitize(x_raw, edges) - 1
    centers, medians = [], []
    for i in range(len(edges) - 1):
        m = (idx == i) & (y_raw > 20)
        if int(m.sum()) < 200:
            continue
        centers.append((edges[i] + edges[i + 1]) / 2.0)
        medians.append(float(np.median(x_jpg[m])))
    return np.array(centers), np.array(medians)


def slope(x, y):
    if len(x) < 3:
        return float("nan")
    return float(np.polyfit(x, y, 1)[0])


def main(args):
    out = args[0]
    series = []
    for a in args[1:]:
        label, _, orf = a.partition("=")
        (y0, cb0, cr0), (y1, cb1, cr1) = pair(orf)
        series.append((label, cb0, cr0, cb1, cr1, y0))
        print(f"{label:14s} {orf.split('/')[-1]:14s}"
              f" raw|Cb|={np.median(np.abs(cb0-128)):5.2f} jpg|Cb|={np.median(np.abs(cb1-128)):5.2f}"
              f" | raw|Cr|={np.median(np.abs(cr0-128)):5.2f} jpg|Cr|={np.median(np.abs(cr1-128)):5.2f}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, (nam, iraw, ijpg) in zip(axes, (("Cb", 1, 3), ("Cr", 2, 4))):
        for s in series:
            label = s[0]
            x, y = binned(s[iraw], s[ijpg], s[5], EDGES)
            if len(x) < 3:
                continue
            ax.plot(x, y, "o-", ms=3.5, label=f"{label} (slope {slope(x, y):.2f})")
        ax.axhline(128, color="#999", lw=0.8)
        ax.axvline(128, color="#999", lw=0.8)
        ax.set_title(f"{nam} transfer (raw -> camera JPEG)")
        ax.set_xlabel("raw-derived")
        ax.set_ylabel("camera JPEG")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1:])
