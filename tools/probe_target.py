# -*- coding: utf-8 -*-
"""按靶图已知几何测量照片里的色块（不依赖外部库，只用 numpy + PIL）。

思路：先在照片里找到"中央大灰块"（大块均匀中亮区域）→ 用它与靶图里的
已知矩形 (470,200)-(1450,760) 求缩放与平移 → 再把每个色块矩形映射过去采样。

这样测出来的是**同一张照片内部**的数值，可以跨包比形状，但不该跨包比绝对亮度。
"""
from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

# 靶图几何（make_screen_target.py 常量）
TW, TH = 1920, 1080
BLOCK = (470, 200, 1450, 760)
GRAY = 119
CHIPS = [(510, 630, 90), (630, 630, 90), (1230, 630, 90), (1350, 630, 90)]
CHIP_VALS = [0, 255, 0, 255]
STRIP = dict(x0=160, x1=1760, y0=800, y1=930, n=9, vals=[0, 32, 64, 96, 119, 152, 192, 224, 255])


def load(path, width=900):
    im = Image.open(path).convert('RGB')
    im = im.resize((width, int(width * im.height / im.width)), Image.BILINEAR)
    return np.asarray(im, dtype=np.float32)


def largest_uniform_block(a, tol=10.0):
    """找中央大灰块：以中区出现最多的亮度为基准，取最均匀的大连通域。"""
    v = a.mean(2)
    h, w = v.shape
    core = v[int(h * 0.20):int(h * 0.70), int(w * 0.25):int(w * 0.75)]
    hist, edges = np.histogram(core, bins=64, range=(0, 255))
    mode = 0.5 * (edges[hist.argmax()] + edges[hist.argmax() + 1])
    m = np.abs(v - mode) < tol
    m[: int(h * 0.10), :] = False
    m[int(h * 0.88):, :] = False
    seen = np.zeros_like(m, dtype=bool)
    best, best_area = None, 0
    step = 2
    for y in range(0, h, step):
        for x in range(0, w, step):
            if not m[y, x] or seen[y, x]:
                continue
            q = deque([(y, x)]); seen[y, x] = True; n = 0
            ys = [y]; xs = [x]
            while q:
                cy, cx = q.popleft(); n += 1
                for dy, dx in ((step, 0), (-step, 0), (0, step), (0, -step)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx)); ys.append(ny); xs.append(nx)
            if n > best_area:
                best_area, best = n, (min(xs), min(ys), max(xs), max(ys))
    if best_area < 400:
        return None
    return best


def fit(a):
    bb = largest_uniform_block(a)
    if bb is None:
        return None
    x0, y0, x1, y1 = bb
    sx = (x1 - x0) / (BLOCK[2] - BLOCK[0])
    sy = (y1 - y0) / (BLOCK[3] - BLOCK[1])
    ox, oy = x0 - BLOCK[0] * sx, y0 - BLOCK[1] * sy
    return sx, sy, ox, oy


def sample(a, rect):
    x, y, w = rect
    sx, sy, ox, oy = fit_cache
    h, wid, _ = a.shape
    xa = int(x + w * 0.25); xb = int(x + w * 0.75)
    ya = int(y + w * 0.25); yb = int(y + w * 0.75)
    pts = []
    for tx, ty in ((xa, ya), (xb, ya), (xa, yb), (xb, yb)):
        px = int(ox + tx * sx); py = int(oy + ty * sy)
        if 0 <= px < wid and 0 <= py < h:
            pts.append(a[py, px])
    return np.mean(pts, 0) if pts else None


def measure(path):
    global fit_cache
    a = load(path)
    f = fit(a)
    if f is None:
        return None
    fit_cache = f
    out = {'block': sample(a, (BLOCK[0], BLOCK[1], BLOCK[2] - BLOCK[0])),
           'chips': [], 'strip': []}
    for (x, y, s), v in zip(CHIPS, CHIP_VALS):
        out['chips'].append((v, sample(a, (x, y, s))))
    step = (STRIP['x1'] - STRIP['x0']) / STRIP['n']
    for i, v in enumerate(STRIP['vals']):
        x = STRIP['x0'] + i * step
        out['strip'].append((v, sample(a, (int(x), STRIP['y0'], int(step) - 20))))
    return out


fit_cache = None


def chroma_pct(rgb):
    return 100.0 * (rgb.max() - rgb.min()) / max(rgb.mean(), 1e-6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ns = ap.parse_args()
    for p in ns.files:
        r = measure(p)
        if r is None:
            print(Path(p).name, '找不到大灰块')
            continue
        print('\n%s  大灰块 %s  偏色 %.1f%%' % (Path(p).name, np.round(r['block'], 1), chroma_pct(r['block'])))
        print('  参考块: ' + '  '.join('%d→%s' % (v, np.round(c, 0)) for v, c in r['chips'] if c is not None))
        print('  灰阶条: ' + '  '.join('%d→%3.0f(%.1f%%)' % (v, c.mean(), chroma_pct(c))
                                      for v, c in r['strip'] if c is not None))


if __name__ == '__main__':
    main()
