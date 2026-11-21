#!/usr/bin/env python3
"""从平板标板照片里按透视变换取出 21 级灰阶曲线。

用法: python tools/screen_curve.py L:/DCIM/100OLYMP/PB110015.JPG [...]
输出: 每张照片的 21 级亮度数组（屏幕台阶 0..255 对应的 JPEG 亮度）。
"""
import sys
import numpy as np
from PIL import Image

SRC_W, SRC_H = 2560, 1440
STRIP_X0, STRIP_X1 = 512, 2045      # 21 格，每格 73px
STRIP_Y0, STRIP_Y1 = 1168, 1390


def load_gray(path, width=1200):
    im = Image.open(path)
    im.draft("L", (width, int(width * 0.75)))
    return np.asarray(im.convert("L"), dtype=np.float32)


def largest_quad(g, thr=60):
    """返回屏幕四角 (TL,TR,BR,BL)；用 4x 下采样的连通域找最大亮块。"""
    h, w = g.shape
    s = 4
    m = (g > thr)[: h // s * s, : w // s * s].reshape(h // s, s, w // s, s).mean(axis=(1, 3)) > 0.6
    lab = np.zeros(m.shape, np.int32)
    cur = 0
    best = (0, None)
    for y0 in range(m.shape[0]):
        for x0 in range(m.shape[1]):
            if not m[y0, x0] or lab[y0, x0]:
                continue
            cur += 1
            stack = [(y0, x0)]
            lab[y0, x0] = cur
            n = 0
            pts = []
            while stack:
                y, x = stack.pop()
                n += 1
                pts.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < m.shape[0] and 0 <= nx < m.shape[1] and m[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = cur
                        stack.append((ny, nx))
            if n > best[0]:
                best = (n, np.array(pts, float))
    pts = best[1]
    if pts is None:
        raise RuntimeError("no bright component")
    ys, xs = pts[:, 0] * s + s / 2, pts[:, 1] * s + s / 2
    tl = pts[np.argmin(ys + xs)]
    br = pts[np.argmax(ys + xs)]
    tr = pts[np.argmax(xs - ys)]
    bl = pts[np.argmin(xs - ys)]
    return np.array([[tl[1] * s + s / 2, tl[0] * s + s / 2],
                     [tr[1] * s + s / 2, tr[0] * s + s / 2],
                     [br[1] * s + s / 2, br[0] * s + s / 2],
                     [bl[1] * s + s / 2, bl[0] * s + s / 2]])


def homography(src, dst):
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    h = np.linalg.lstsq(np.array(A, float), np.array(b, float), rcond=None)[0]
    return np.append(h, 1.0).reshape(3, 3)


def apply_h(H, x, y):
    v = H @ np.array([x, y, 1.0])
    return v[0] / v[2], v[1] / v[2]


def curve(path):
    g = load_gray(path)
    quad = largest_quad(g)
    src = np.array([[0, 0], [SRC_W - 1, 0], [SRC_W - 1, SRC_H - 1], [0, SRC_H - 1]], float)
    H = homography(src, quad)
    yc = (STRIP_Y0 + STRIP_Y1) / 2
    n = 21
    cw = (STRIP_X1 - STRIP_X0) / n
    vals = []
    for i in range(n):
        xc = STRIP_X0 + cw * (i + 0.5)
        pts = []
        for dy in (-0.22, 0.0, 0.22):
            for dx in (0.0,):
                u, v = apply_h(H, xc + dx * cw, yc + dy * (STRIP_Y1 - STRIP_Y0))
                xi, yi = int(round(u)), int(round(v))
                if 0 <= yi < g.shape[0] and 0 <= xi < g.shape[1]:
                    patch = g[max(0, yi - 2):yi + 3, max(0, xi - 3):xi + 4]
                    pts.append(np.median(patch))
        vals.append(float(np.median(pts)) if pts else np.nan)
    return np.array(vals), quad


def main():
    files = sys.argv[1:]
    if not files:
        print(__doc__)
        return
    out = {}
    for f in files:
        v, quad = curve(f)
        out[f] = v
        name = f.split("/")[-1]
        print(f"{name}: " + " ".join(f"{x:5.1f}" for x in v))
        print(f"   quad {quad.round(0).tolist()}")
    if len(files) > 1:
        base = out[files[0]]
        for f in files[1:]:
            name = f.split("/")[-1]
            print(f"delta {name}: " + " ".join(f"{x:5.1f}" for x in (out[f] - base)))


if __name__ == "__main__":
    main()
