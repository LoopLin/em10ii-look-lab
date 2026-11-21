# -*- coding: utf-8 -*-
"""色卡判读：稳健取样 + 逐块 ΔE。

取样：Otsu 找相纸 → 黑框 → 排除两侧标尺（左右各 6.5% 宽）
     → 6x4 均匀网格 → 每格中心 50% 区域取中位数（用户 2026-11-18 定的抗噪规则）。

用法：
    python tools/judge_chart.py I:/DCIM/100OLYMP/PB210089.JPG
    python tools/judge_chart.py 新版.JPG --ref 旧版.JPG
"""
import argparse
import numpy as np
from PIL import Image


def _lab(rgb):
    r, g, b = [v / 255.0 for v in rgb]
    f = lambda t: t / 12.92 if t <= 0.04045 else ((t + 0.055) / 1.055) ** 2.4
    r, g, b = f(r), f(g), f(b)
    X = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    Y = r * 0.2126 + g * 0.7152 + b * 0.0722
    Z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883
    g2 = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = g2(X), g2(Y), g2(Z)
    return np.array([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)])


def _otsu(v):
    h, _ = np.histogram(v, 256, (0, 256))
    h = h.astype(float)
    tot = h.sum()
    w = np.cumsum(h)
    mu = np.cumsum(h * np.arange(256))
    muT = mu[-1]
    with np.errstate(divide='ignore', invalid='ignore'):
        sb = (muT * w / tot - mu) ** 2 / (w * (tot - w))
    return int(np.argmax(np.nan_to_num(sb)))


def _frame(a):
    g = a.mean(2)
    t = _otsu(g.clip(0, 255).astype(np.uint8))
    bright = g >= t
    rows = np.where(bright.sum(1) > 0.35 * g.shape[1])[0]
    cols = np.where(bright.sum(0) > 0.35 * g.shape[0])[0]
    y0, y1, x0, x1 = rows[0], rows[-1], cols[0], cols[-1]
    sub = g[y0:y1 + 1, x0:x1 + 1]
    dark = sub < np.percentile(sub, 30)
    fr = np.where(dark.sum(1) / sub.shape[1] > 0.5)[0]
    fc = np.where(dark.sum(0) / sub.shape[0] > 0.5)[0]
    return y0 + fr[0], y0 + fr[-1], x0 + fc[0], x0 + fc[-1]


def sample(path, inset=0.25):
    im = Image.open(path).convert('RGB').resize((960, 720))
    a = np.asarray(im).astype(float)
    fy0, fy1, fx0, fx1 = _frame(a)
    W, H = fx1 - fx0, fy1 - fy0
    px0, px1 = fx0 + 0.065 * W, fx1 - 0.062 * W
    py0, py1 = fy0 + 0.018 * H, fy1 - 0.009 * H
    out = []
    for r in range(4):
        for c in range(6):
            w0 = px0 + (px1 - px0) * c / 6
            w1 = px0 + (px1 - px0) * (c + 1) / 6
            h0 = py0 + (py1 - py0) * r / 4
            h1 = py0 + (py1 - py0) * (r + 1) / 4
            mw, mh = int((w1 - w0) * inset), int((h1 - h0) * inset)
            p = a[int(h0 + mh):int(h1 - mh), int(w0 + mw):int(w1 - mw)].reshape(-1, 3)
            out.append(np.median(p, axis=0))
    return np.array(out)


def analyse(path, ref=None):
    rgb = sample(path)
    lab = np.array([_lab(x) for x in rgb])
    C = np.hypot(lab[:, 1], lab[:, 2])
    res = {'patches': len(rgb), 'meanJ': float(lab[:, 0].mean()), 'meanC': float(C.mean()),
           'grayJ': [round(float(lab[i, 0]), 1) for i in (18, 19, 20, 21, 22, 23)]}
    if ref is not None:
        l0 = np.array([_lab(x) for x in sample(ref)])
        dE = np.sqrt(((lab - l0) ** 2).sum(1))
        res['dE_mean'] = float(dE.mean())
        res['dE_max'] = float(dE.max())
        res['dE_p90'] = float(np.percentile(dE, 90))
    return res, rgb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--ref')
    ns = ap.parse_args()
    res, rgb = analyse(ns.image, ns.ref)
    print(ns.image, 'meanJ %.1f meanC %.1f' % (res['meanJ'], res['meanC']))
    print('gray J:', res['grayJ'])
    if 'dE_mean' in res:
        print('ΔE00(approx Lab) vs ref: mean %.1f  p90 %.1f  max %.1f'
              % (res['dE_mean'], res['dE_p90'], res['dE_max']))


if __name__ == '__main__':
    main()
