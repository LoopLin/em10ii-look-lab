"""Merge several raw/JPEG pairs into one tone curve T: raw-linear -> JPEG luma.

usage:
  python tools/tone_fit.py --out .tmp/tone_T4 --bins 44 [--wb] \
     --pair <orf> <jpg> <ev_stops> [--pair ...]

Pairs may come from different shots: T is a per-pixel mapping, we only need
raw value <-> JPEG value correspondence from the same frame.

--wb applies camera white balance gains before averaging (the pipeline applies
WB before the tone curve, so this is the scale the LUT probably works in).
"""
import argparse
import numpy as np
import rawpy
from PIL import Image


def load_raw(path, scale=1.0, wb=False):
    with rawpy.imread(path) as raw:
        a = raw.raw_image_visible.astype(np.float32)
        blk = np.array(raw.black_level_per_channel, dtype=np.float32).reshape(2, 2)
        wl = float(raw.white_level)
        g = np.array(raw.camera_whitebalance[:4], dtype=np.float32).reshape(2, 2)
        g = g / g[1, 0]
        h, w = a.shape
        c0, c1 = h // 2 * 2, w // 2 * 2
        b = a[:c0, :c1].reshape(c0 // 2, 2, c1 // 2, 2)
        v = (b - blk[None, :, None, :]) / (wl - blk[None, :, None, :])
        if wb:
            v = v * g[None, :, None, :]
        v = v.mean(axis=(1, 3))
    return np.clip(v * scale, 0.0, 4.0)


def load_jpg(path):
    im = Image.open(path).convert("RGB")
    return np.asarray(im, dtype=np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", nargs=3, action="append", metavar=("ORF", "JPG", "EV"), required=True)
    ap.add_argument("--bins", type=int, default=40)
    ap.add_argument("--wb", action="store_true")
    ap.add_argument("--out", default=".tmp/tone_fit")
    args = ap.parse_args()

    xs, ys = [], []
    for orf, jpg, ev in args.pair:
        lin = load_raw(orf, scale=2.0 ** float(ev), wb=args.wb)
        img = load_jpg(jpg)
        h, w = lin.shape
        img = np.asarray(Image.fromarray(img.astype(np.uint8)).resize((w, h)), dtype=np.float32)
        y = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
        xs.append(lin.ravel())
        ys.append(y.ravel())
        print(f"{jpg.split('/')[-1]}  ev={ev}  raw p50={np.median(lin):.4f}  jpg p50={np.median(y):.1f}")

    x = np.concatenate(xs)
    y = np.concatenate(ys)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]

    hi = float(args.__dict__.get("hi") or 1.6)
    edges = np.geomspace(0.004, hi, args.bins + 1)
    idx = np.clip(np.digitize(x, edges) - 1, 0, args.bins - 1)
    tx, ty, tn = [], [], []
    for i in range(args.bins):
        m = idx == i
        if m.sum() < 500:
            continue
        tx.append(float(np.median(x[m])))
        ty.append(float(np.median(y[m])))
        tn.append(int(m.sum()))

    tx, ty, tn = np.array(tx), np.array(ty), np.array(tn)
    order = np.argsort(tx)
    tx, ty, tn = tx[order], ty[order], tn[order]
    sm = ty.copy()
    for i in range(1, len(sm)):
        sm[i] = max(sm[i], sm[i - 1])
    ty = sm

    np.savetxt(args.out + ".csv", np.c_[tx, ty, tn], delimiter=",", header="raw,jpeg,count", comments="")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
    ax[0].plot(tx, ty, "o-", ms=3, lw=1.2, label="measured T")
    ref = np.clip(1.055 * np.power(np.clip(tx, 0, 1), 1 / 2.4) - 0.055, 0, 1) * 255
    ax[0].plot(tx, ref, "--", color="gray", lw=1, label="sRGB gamma")
    ax[0].set_xlabel("raw linear (1.0 = sensor white%s)" % (", WB applied" if args.wb else ""))
    ax[0].set_ylabel("JPEG luma")
    ax[0].set_title("tone curve T (merged)")
    ax[0].grid(alpha=0.3)
    ax[0].legend(fontsize=8)
    ax[1].semilogx(tx, ty, "o-", ms=3, lw=1.2)
    ax[1].semilogx(tx, ref, "--", color="gray", lw=1)
    ax[1].set_xlabel("raw linear (log)")
    ax[1].set_title("same, log x")
    ax[1].grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(args.out + ".png", dpi=140)
    print(f"\n{len(tx)} bins -> {args.out}.csv / .png")
    for a, b in zip(tx[::4], ty[::4]):
        print(f"  raw {a:0.4f} -> {b:6.1f}")


if __name__ == "__main__":
    main()
