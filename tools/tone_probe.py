#!/usr/bin/env python3
"""Recover the camera's raw->JPEG tone curve T by stacking exposures.

Each shot i is the same scene at exposure e_i (0, 1, 2 stops ...).  A patch of
true linear value L appears in shot i at raw level e_i*L and in the JPEG at
T(e_i*L) (gamma-encoded).  Stacking all shots samples T over the union of the
exposure ranges.

Usage:
  python tools/tone_probe.py --pair 32:0 33:1 34:2 35:3 --out .tmp/tone_T.png
where "NN:stops" gives the file number and its exposure offset.
"""
import argparse
import numpy as np
import rawpy
from PIL import Image, ImageDraw, ImageFont

BLACK = 253.0
WHITE = 4095.0
FONT = r"C:\Windows\Fonts\msyh.ttc"


def load_pair(num, prefix="refs/test-photos/PB1200"):
    with rawpy.imread(f"{prefix}{num}.ORF") as r:
        raw = r.raw_image_visible.astype(np.float32)
        wb = np.array(r.camera_whitebalance[:4], dtype=np.float32)
        # bilinear demosaic is unnecessary for luma: average each 2x2 Bayer cell
        h, w = raw.shape
        h2, w2 = h // 2 * 2, w // 2 * 2
        c = raw[:h2, :w2].reshape(h2 // 2, 2, w2 // 2, 2)
        r_ch = c[:, 0, :, 0] * wb[0]
        g_ch = (c[:, 0, :, 1] + c[:, 1, :, 0]) * 0.5 * wb[1]
        b_ch = c[:, 1, :, 1] * wb[2]
        lin = (0.299 * r_ch + 0.587 * g_ch + 0.114 * b_ch - BLACK) / (WHITE - BLACK)
    lin = np.clip(lin, 0, None)
    jpg = Image.open(f"{prefix}{num}.JPG")
    jpg.draft("RGB", (lin.shape[1], lin.shape[0]))
    jpg = jpg.convert("RGB").resize((lin.shape[1], lin.shape[0]), Image.BILINEAR)
    a = np.asarray(jpg, dtype=np.float32)
    luma = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    return lin, luma




def single_raw_mode(rawpath, jpgs, bins, out):
    """One RAW, several JPEGs produced in-camera at different algorithmic EV.

    The linear signal is identical in every JPEG, only the gain before the tone
    curve differs, so we can take the raw once and scale it by 2**ev.
    """
    with rawpy.imread(rawpath + ".ORF") as r:
        raw = r.raw_image_visible.astype(np.float32)
        wb = np.array(r.camera_whitebalance[:4], dtype=np.float32)
        h, w = raw.shape
        h2, w2 = h // 2 * 2, w // 2 * 2
        c = raw[:h2, :w2].reshape(h2 // 2, 2, w2 // 2, 2)
        lin = (0.299 * c[:, 0, :, 0] * wb[0]
               + 0.587 * (c[:, 0, :, 1] + c[:, 1, :, 0]) * 0.5 * wb[1]
               + 0.114 * c[:, 1, :, 1] * wb[2] - BLACK) / (WHITE - BLACK)
    lin = np.clip(lin, 0, None)
    xs, ys, labels = [], [], []
    for item in jpgs:
        path, ev = item.rsplit(":", 1)
        ev = float(ev)
        jpg = Image.open(path)
        jpg.draft("RGB", (lin.shape[1], lin.shape[0]))
        jpg = jpg.convert("RGB").resize((lin.shape[1], lin.shape[0]), Image.BILINEAR)
        a = np.asarray(jpg, dtype=np.float32)
        luma = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
        xs.append(np.clip(lin * (2.0 ** ev), 0, 1.0))
        ys.append(luma)
        labels.append(f"{path.split('/')[-1]} @{ev:+.1f}EV")
        print(f"{labels[-1]}: raw p50={np.median(lin*(2**ev)):.4f} jpg p50={np.median(luma):.1f}")
    x = np.concatenate([a.ravel() for a in xs])
    y = np.concatenate([a.ravel() for a in ys])
    edges = np.concatenate([[0], np.geomspace(2e-4, 1.05, bins + 1)])
    cx, cy, n = [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (x >= lo) & (x < hi)
        if m.sum() > 400:
            cx.append(np.sqrt(lo * hi)); cy.append(np.median(y[m])); n.append(m.sum())
    cx, cy = np.array(cx), np.array(cy)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    ax.set_xscale("log")
    ax.plot(cx, cy, "o-", ms=4)
    ax.set_xlabel("raw linear after algorithmic EV (1.0 = sensor white)")
    ax.set_ylabel("JPEG luma")
    ax.set_title("tone curve T from one RAW, in-camera EV bracket")
    ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(out, dpi=120)
    txt = out.rsplit(".", 1)[0] + ".csv"
    with open(txt, "w", encoding="utf-8") as f:
        f.write("raw_linear,jpeg_luma,count\n")
        for a_, b_, c_ in zip(cx, cy, n):
            f.write(f"{a_:.6f},{b_:.3f},{c_}\n")
    print("wrote", out, "and", txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", nargs="+")
    ap.add_argument("--prefix", default="refs/test-photos/PB1200")
    ap.add_argument("--out", default=".tmp/tone_T.png")
    ap.add_argument("--bins", type=int, default=48)
    ap.add_argument("--raw", help="single RAW for algorithmic-EV mode")
    ap.add_argument("--jpgs", nargs="+", help="path:ev pairs for algorithmic-EV mode")
    args = ap.parse_args()

    if args.raw:
        single_raw_mode(args.raw, args.jpgs or [], args.bins, args.out)
        return
    xs, ys = [], []
    for item in args.pair:
        num, stops = item.split(":")
        lin, luma = load_pair(num, args.prefix)
        xs.append(lin * (2.0 ** float(stops)))
        ys.append(luma)
        print(f"{num}: +{stops}EV  lin p50={np.median(lin):.4f}  jpg p50={np.median(luma):.1f}")
    x = np.concatenate([a.ravel() for a in xs])
    y = np.concatenate([a.ravel() for a in ys])

    # 1.0 = sensor white. Bin the raw axis and take the median JPEG value.
    edges = np.concatenate([[0], np.geomspace(2e-4, 1.05, args.bins + 1)])
    cx, cy, n = [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (x >= lo) & (x < hi)
        if m.sum() > 400:
            cx.append(np.sqrt(lo * hi))
            cy.append(np.median(y[m]))
            n.append(m.sum())
    cx, cy = np.array(cx), np.array(cy)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(13, 4.6))
    ax[0].set_xscale("log")
    ax[0].plot(cx, cy, "o-", ms=4)
    ax[0].set_xlabel("raw linear (1.0 = sensor white), log")
    ax[0].set_ylabel("JPEG luma")
    ax[0].set_title("tone curve T: raw -> JPEG")
    ax[0].grid(alpha=.3)

    ax[1].plot(np.log2(cx), cy, "o-", ms=4)
    ax[1].set_xlabel("log2(raw linear)")
    ax[1].set_ylabel("JPEG luma")
    ax[1].set_title("same, in stops")
    ax[1].grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(args.out, dpi=120)
    print("wrote", args.out)

    out_txt = args.out.rsplit(".", 1)[0] + ".csv"
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("raw_linear,jpeg_luma,count\n")
        for a, b, c in zip(cx, cy, n):
            f.write(f"{a:.6f},{b:.3f},{c}\n")
    print("wrote", out_txt)


if __name__ == "__main__":
    main()
