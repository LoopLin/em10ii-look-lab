#!/usr/bin/env python3
"""Build a 256-entry 10-bit tone LUT from the measured raw->JPEG curve T.

The camera's LUT (0x605D8144 family) maps raw-linear 10-bit input to raw-linear
10-bit output; the identity table is 4*k, i.e. entry k is used for input 4*k.
So to get a target mapping F: raw -> JPEG we need

    LUT[k] = T^{-1}( F( T(4k/1023) ) ) * 1023

Usage:
  python tools/tone_lut_from_T.py --curve film --out .tmp/s23_tone_lut.txt
"""
import argparse
import json
import numpy as np

CURVE_CSV = ".tmp/tone_T2.csv"


def load_T():
    d = np.loadtxt(CURVE_CSV, delimiter=",", skiprows=1)
    x, y = d[:, 0], d[:, 1]
    keep = y > 2.5
    x, y = x[keep], y[keep]
    return x, y


def target_film(j, strength=1.0):
    """A gentle film-ish curve in JPEG space: lifted black, soft shoulder."""
    j = np.asarray(j, dtype=float)
    # lift shadows, fade to zero lift by mid grey
    lift = 9.0 * np.clip((110 - j) / 110.0, 0, 1) ** 1.4
    # roll off highlights
    roll = 7.0 * np.clip((j - 195) / 60.0, 0, 1) ** 1.2
    # very slight mid contrast
    mid = (j - 128.0) * 0.03
    out = j + lift - roll + mid
    return np.clip(128 * (1 - strength) + out * strength, 0, 255)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--curve", default="film")
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--out", default=".tmp/tone_lut.txt")
    args = ap.parse_args()

    x, y = load_T()
    # work in log raw space and use monotone interpolation both ways
    lx, ly = np.log(x), y
    order = np.argsort(lx)
    lx, ly = lx[order], ly[order]

    def T(v):
        return np.interp(np.log(np.clip(v, x[0], x[-1])), lx, ly)

    def Tinv(j):
        jj = np.clip(j, ly[0], ly[-1])
        return np.exp(np.interp(jj, ly, lx))

    ks = np.arange(256)
    xin = (4.0 * ks) / 1023.0            # entry k <-> input 4k of 1023
    xmax = x[-1]
    below = xin <= xmax
    j0 = T(xin)
    j1 = target_film(j0, args.strength)
    xout = np.where(below, Tinv(j1), xin)
    # blend into pass-through over the top 8 entries so there is no step
    blend = np.clip((xin - xmax * 0.85) / (xmax * 0.15), 0, 1)
    xout = np.where(xin > xmax * 0.85, (1 - blend) * xout + blend * xin, xout)
    lut = np.clip(np.round(xout * 1023.0), 0, 1023).astype(int)
    # enforce monotonicity (the table must not fold back)
    for i in range(1, 256):
        if lut[i] < lut[i - 1]:
            lut[i] = lut[i - 1]

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(" ".join(f"{v:04x}" for v in lut) + "\n")
    print("lut head:", lut[:8], "...", lut[-4:])
    print("gain at 0.25/0.5/0.75:", lut[64] / 256.0, lut[128] / 512.0, lut[192] / 768.0)
    print("wrote", args.out)

    # predicted JPEG for a few raw levels before/after
    for v in (0.02, 0.05, 0.1, 0.2, 0.4, 0.5):
        k = int(round(v * 1023 / 4))
        k = min(k, 255)
        print(f"raw {v:.2f}: before {T(v):5.1f}  after {T(lut[k] / 1023.0):5.1f}")


if __name__ == "__main__":
    main()
