#!/usr/bin/env python3
"""S24: build a 256-entry 10-bit tone LUT from the merged measured curve T.

The camera LUT (0x605D8144 family) maps linear 10-bit -> linear 10-bit.
We normalise the measured T so that its shoulder (where JPEG saturates)
corresponds to LUT input 1023, then

    LUT[k] = T^-1( F( T(x_k) ) ) / x_white * 1023,   x_k = (4k/1023) * x_white

with F a film-ish target defined in JPEG space.
"""
import argparse
import numpy as np

import matplotlib
matplotlib.use("Agg")


def load_T(path):
    d = np.loadtxt(path, delimiter=",", skiprows=1)
    x, y = d[:, 0], d[:, 1]
    keep = (y > 2.0) & (x > 0)
    x, y = x[keep], y[keep]
    o = np.argsort(x)
    return x[o], y[o]


def target_film(j, strength=1.0):
    j = np.asarray(j, dtype=float)
    lift = 9.0 * strength * np.exp(-(j / 55.0) ** 2)   # black lift
    shadow = 3.5 * strength * np.exp(-((j - 50.0) / 42.0) ** 2)
    roll = 0.16 * strength * np.clip(j - 185.0, 0, None)
    mid = (j - 120.0) * 0.015 * strength
    out = j + lift + shadow - roll + mid
    return np.clip(out, 0, 255)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tone", default=".tmp/tone_T5.csv")
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--out", default=".tmp/s24_lut.txt")
    args = ap.parse_args()

    x, y = load_T(args.tone)
    # white point: first x where the curve is essentially saturated
    sat = np.where(y >= 253.5)[0]
    x_white = float(x[sat[0]]) if len(sat) else float(x[-1])
    print(f"x range {x[0]:.4f}..{x[-1]:.4f}, white point {x_white:.4f}")

    lx = np.log(np.clip(x / x_white, 1e-6, None))
    ly = y

    def T(v):
        return np.interp(np.log(np.clip(v / x_white, 1e-6, None)), lx, ly)

    def Tinv(j):
        jj = np.clip(j, ly[0], ly[-1])
        return np.exp(np.interp(jj, ly, lx)) * x_white

    k = np.arange(256)
    xin = (4.0 * k / 1023.0) * x_white
    j0 = T(xin)
    j1 = target_film(j0, args.strength)
    xout = Tinv(j1)
    xhat = xout / x_white
    # entries past the measured white point follow the identity (no hard knee)
    over = 4.0 * k / 1023.0 > 1.0
    xhat = np.where(over, 4.0 * k / 1023.0, xhat)
    lut = np.clip(np.round(xhat * 1023.0), 0, 1023).astype(int)
    for i in range(1, 256):
        lut[i] = max(lut[i], lut[i - 1])

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(" ".join(f"{v:04x}" for v in lut) + "\n")

    print("lut head:", lut[:8], "...", lut[-4:])
    print("gain @ jpeg 32/64/128/192:",
          [round(lut[int(round(min(x_white, np.exp(np.interp(jj, ly, lx)) * x_white) / x_white * 255))] / max(1, int(round(min(x_white, np.exp(np.interp(jj, ly, lx)) * x_white) / x_white * 255))), 3)
           for jj in (32, 64, 128, 192)])
    print("wrote", args.out)

    for v in (0.01, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5):
        kk = int(round(v * 255))
        kk = min(kk, 255)
        print(f"  raw {v:.3f} ({v*x_white:.4f}): before {T(v*x_white):5.1f} -> after {T(lut[kk]/1023.0*x_white):5.1f}")

    # preview on a real photo if available
    try:
        from PIL import Image
        src = Image.open("refs/test-photos/PB120034.JPG")
        src.thumbnail((760, 760))
        a = np.asarray(src.convert("RGB"), dtype=float)
        yv = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
        # approximate: apply the JPEG-space target directly
        f = target_film(yv, args.strength)
        scale = np.where(yv > 3, f / np.maximum(yv, 1e-6), 1.0)
        b = np.clip(a * scale[..., None], 0, 255)
        w, h = src.size
        sheet = Image.new("RGB", (w * 2 + 8, h), (255, 255, 255))
        sheet.paste(src, (0, 0))
        sheet.paste(Image.fromarray(b.astype(np.uint8)), (w + 8, 0))
        from PIL import ImageDraw, ImageFont
        d = ImageDraw.Draw(sheet)
        F = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 26)
        for i, t in enumerate(["原版", "S24 胶片曲线"]):
            d.rectangle([i * (w + 8) + 8, 8, i * (w + 8) + 190, 44], fill=(0, 0, 0))
            d.text((i * (w + 8) + 16, 12), t, font=F, fill=(255, 255, 255))
        sheet.save(".tmp/s24_preview.jpg", quality=92)
        print("preview -> .tmp/s24_preview.jpg")
    except Exception as e:
        print("preview skipped:", e)


if __name__ == "__main__":
    main()
