#!/usr/bin/env python3
"""Measure camera-JPEG vs raw-derived chroma mapping for a set of ORF/JPG pairs.

Usage: python tools/mode_measure.py L:/DCIM/100OLYMP/PB110026.ORF=label ...
For each pair prints: Y mean, neutral-pixel cast (Cb/Cr of camera JPEG where raw is neutral),
strong-chroma % of raw pixels, and least-squares slope of JPEG Cb (and Cr) vs raw Cb.
"""
import sys
import numpy as np
import rawpy
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def ycc(rgb):
    """float RGB 0..255 -> Y, Cb, Cr in 0..255."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return y, cb, cr


def main(args):
    for a in args:
        path, _, label = a.partition("=")
        raw = rawpy.imread(path)
        rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True,
                              output_bps=8, half_size=True)
        raw.close()
        jpg = np.asarray(Image.open(path[:-4] + ".JPG").convert("RGB"), dtype=np.float32)
        h, w = rgb.shape[:2]
        if jpg.shape[:2] != (h, w):
            jpg = np.asarray(Image.fromarray(jpg.astype(np.uint8)).resize((w, h), Image.BILINEAR),
                             dtype=np.float32)
        y0, cb0, cr0 = ycc(rgb.astype(np.float32))
        y1, cb1, cr1 = ycc(jpg)
        neutral = (np.abs(cb0 - 128) < 1.2) & (np.abs(cr0 - 128) < 1.2) & (y0 > 25)
        strong = (np.hypot(cb0 - 128, cr0 - 128) > 6) & (y0 > 25)
        def slope(x, y):
            m = np.abs(x - 128) > 3
            if m.sum() < 50:
                return float("nan")
            A = np.polyfit(x[m], y[m], 1)
            return float(A[0])
        print(f"{label:10s} file={path.split('/')[-1]}  Y={y1.mean():5.1f}")
        print(f"   neutral cast: Cb={cb1[neutral].mean()-128:+6.2f}  Cr={cr1[neutral].mean()-128:+6.2f}"
              f"  (n={neutral.sum()})")
        print(f"   strong chroma: {100.0*strong.sum()/max(1,(y0>25).sum()):5.1f}%")
        print(f"   slope: Cb={slope(cb0, cb1):5.2f}  Cr={slope(cr0, cr1):5.2f}")
        print(f"   median |Cb-128| raw={np.median(np.abs(cb0-128)):5.2f} jpg={np.median(np.abs(cb1-128)):5.2f}"
              f"   |Cr-128| raw={np.median(np.abs(cr0-128)):5.2f} jpg={np.median(np.abs(cr1-128)):5.2f}")


if __name__ == "__main__":
    main(sys.argv[1:])
