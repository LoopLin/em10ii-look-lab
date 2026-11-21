#!/usr/bin/env python3
"""Compare two real-scene shots (same framing, different firmware).

Usage: python tools/scene_ab.py A.JPG B.JPG
Prints robust global colour statistics and their delta, so a firmware
change shows up without relying on colour patches.
"""
import sys
import numpy as np
from PIL import Image, ImageOps


def load(path):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im).convert('RGB')
    a = np.asarray(im, np.float32) / 255.0
    return a[::3, ::3]


def hsv(a):
    mx = a.max(2); mn = a.min(2)
    s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    h = np.zeros_like(mx); d = mx - mn + 1e-9
    m = (mx == r); h[m] = ((g - b) / d)[m] % 6
    m = (mx == g); h[m] = ((b - r) / d + 2)[m]
    m = (mx == b); h[m] = ((r - g) / d + 4)[m]
    return h * 60, s, mx


def stats(path):
    a = load(path)
    h, s, v = hsv(a)
    sel = v > 0.12
    out = {}
    out['lum'] = float(a.mean() * 255)
    out['rgb'] = tuple(float(x * 255) for x in a.reshape(-1, 3).mean(0))
    out['sat_all'] = float(s[sel].mean())
    out['sat_p50'] = float(np.percentile(s[sel], 50))
    out['sat_p90'] = float(np.percentile(s[sel], 90))
    out['sat_p99'] = float(np.percentile(s[sel], 99))
    out['clipped'] = float((s[sel] > 0.999).mean())
    out['hue'] = []
    for k in range(12):
        m = sel & (h >= k * 30) & (h < (k + 1) * 30)
        out['hue'].append(float(m.mean()))
    return out


def main(pa, pb):
    A, B = stats(pa), stats(pb)
    print(f'A = {pa}\nB = {pb}\n')
    rows = [('luma', A['lum'], B['lum']), ('sat mean', A['sat_all'], B['sat_all']),
            ('sat p50', A['sat_p50'], B['sat_p50']), ('sat p90', A['sat_p90'], B['sat_p90']),
            ('sat p99', A['sat_p99'], B['sat_p99']), ('clipped share', A['clipped'], B['clipped'])]
    print(f"{'metric':<14}{'A':>10}{'B':>10}{'B-A':>10}")
    for n, a, b in rows:
        print(f'{n:<14}{a:>10.4f}{b:>10.4f}{b-a:>+10.4f}')
    print(f"\n{'hue sector':<12}{'A%':>8}{'B%':>8}{'d':>8}")
    for k in range(12):
        print(f"{k*30:>3}-{(k+1)*30:<3}    {A['hue'][k]*100:>8.2f}{B['hue'][k]*100:>8.2f}"
              f"{(B['hue'][k]-A['hue'][k])*100:>+8.2f}")
    d = np.array(B['hue']) - np.array(A['hue'])
    print(f'\nhue distribution L1 = {np.abs(d).sum()*100:.2f} pp')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
