#!/usr/bin/env python3
"""从"色彩标板"照片里抽出 21 级灰阶条的实测亮度。

思路：先在画面里找到屏幕（亮区），再在屏幕下部找"从左到右单调上升"的横带，
把这条带的起点/终点等分成 21 格取样。

用法：python tools/card_gray.py a.JPG b.JPG ...
"""
from __future__ import annotations

import sys

import numpy as np
from PIL import Image

STEPS = 21


def gray(path: str, width: int = 1600) -> np.ndarray:
    im = Image.open(path)
    im.draft("L", (width, int(width * im.size[1] / im.size[0])))
    return np.asarray(im.convert("L"), dtype=np.float32)


def screen_rows(g: np.ndarray) -> tuple[int, int]:
    rowmed = np.median(g, axis=1)
    thr = max(12.0, rowmed.max() * 0.25)
    rows = np.where(rowmed > thr)[0]
    if len(rows) == 0:
        raise SystemExit("找不到亮区")
    return int(rows[0]), int(rows[-1])


def find_strip(g: np.ndarray) -> tuple[int, int, int, int]:
    h, w = g.shape
    y0, y1 = screen_rows(g)
    # 灰阶条在屏幕下半部
    ys_range = range(int(y0 + (y1 - y0) * 0.55), int(y1), max(1, (y1 - y0) // 120))
    best = None
    for y in ys_range:
        prof = np.median(g[max(0, y - 2): y + 3, :], axis=0)
        k = max(5, w // 300)
        sm = np.convolve(prof, np.ones(k) / k, mode="same")
        # 屏幕的横向范围
        cols = np.where(sm > max(12.0, sm.max() * 0.25))[0]
        if len(cols) < w * 0.3:
            continue
        a, b = int(cols[0]), int(cols[-1])
        span = b - a
        if span < w * 0.25:
            continue
        left = sm[a: a + span // 8].mean()
        right = sm[b - span // 8: b].mean()
        # 单调性：把中间分成 20 份，统计下降幅度
        q = [sm[a + int(span * t / 20): a + int(span * (t + 1) / 20)].mean() for t in range(20)]
        drops = sum(max(0.0, q[i] - q[i + 1]) for i in range(19))
        score = (right - left) - 2.0 * drops
        if best is None or score > best[0]:
            best = (score, y, a, b)
    if best is None:
        raise SystemExit("找不到灰阶条")
    score, yc, a, b = best
    band_h = max(4, int(h * 0.015))
    ys, ye = max(0, yc - band_h), min(h, yc + band_h)
    # 用这条带的中位数剖面精修左右边界（去掉白边）
    prof = np.median(g[ys:ye, :], axis=0)
    seg = prof[a:b]
    lo_thr = seg.min() + (seg.max() - seg.min()) * 0.02
    hi_thr = seg.max() - (seg.max() - seg.min()) * 0.02
    xs = np.where((seg > lo_thr))[0]
    xe = np.where(seg < hi_thr)[0]
    x_start = a + (xs[0] if len(xs) else 0)
    x_end = a + (xe[-1] if len(xe) else len(seg) - 1)
    if x_end - x_start < (b - a) * 0.5:
        x_start, x_end = a, b
    return ys, ye, x_start, x_end


def sample(path: str) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    g = gray(path)
    ys, ye, x0, x1 = find_strip(g)
    vals = []
    for i in range(STEPS):
        a = x0 + (x1 - x0) * i / STEPS
        b = x0 + (x1 - x0) * (i + 1) / STEPS
        lo = int(a + (b - a) * 0.25)
        hi = int(a + (b - a) * 0.75) + 1
        vals.append(float(np.median(g[ys:ye, lo:hi])))
    return np.array(vals), (ys, ye, x0, x1)


def main(argv: list[str]) -> int:
    for f in argv:
        v, box = sample(f)
        name = f.split("/")[-1].split("\\")[-1]
        print(f"{name}  strip y={box[0]}..{box[1]} x={box[2]}..{box[3]}")
        print("   " + " ".join(f"{x:5.1f}" for x in v))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
