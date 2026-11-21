#!/usr/bin/env python3
"""从"满幅 256 级灰阶 ramp"照片里抽出完整色调曲线。

用法：
  python tools/ramp_curve.py shot.jpg --csv out.csv --png out.png
  python tools/ramp_curve.py a.jpg b.jpg --compare cmp.png     # 比较两张的映射差

原理：屏幕上的 ramp 是 256 条等宽竖条，第 i 条的值就是 i。
照片里先找"单调递增的那一段列"，把第 i 条定位到对应列，取条带中段的亮度中位数。
输出：输入级 i(0..255) -> 照片里的亮度。
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from PIL import Image


def luma(rgb: np.ndarray) -> np.ndarray:
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def load(path: str, width: int = 2304) -> np.ndarray:
    im = Image.open(path)
    im.draft("RGB", (width, int(width * im.size[1] / im.size[0])))
    return np.asarray(im.convert("RGB"), dtype=np.float32)


def find_ramp(col: np.ndarray) -> tuple[int, int]:
    """在列亮度里找最长的一段整体递增区域（起点/终点）。"""
    n = len(col)
    k = max(3, n // 400)
    sm = np.convolve(col, np.ones(k) / k, mode="same")
    best = (0, n - 1)
    best_gain = -1.0
    # 双指针：从每个起点尽量向右延伸，只要整体上升
    i = 0
    while i < n - 10:
        j = i + 10
        while j < n and sm[j] >= sm[j - 1] - 1.0:
            j += 1
        gain = sm[j - 1] - sm[i]
        if gain > best_gain:
            best_gain, best = gain, (i, j - 1)
        i = max(j, i + 1)
    return best


def extract(path: str) -> np.ndarray:
    rgb = load(path)
    L = luma(rgb)
    h, w = L.shape
    # 去掉上下 8%，避开屏幕边框/背景
    band = L[int(h * 0.08): int(h * 0.92), :]
    col = np.median(band, axis=0)
    x0, x1 = find_ramp(col)
    # 256 条带：第 i 条中心 = x0 + (i+0.5)*(x1-x0+1)/256
    step = (x1 - x0 + 1) / 256.0
    out = np.full(256, np.nan, dtype=np.float32)
    half = max(0.5, step * 0.30)
    for i in range(256):
        c = x0 + (i + 0.5) * step
        lo, hi = int(round(c - half)), int(round(c + half)) + 1
        lo = max(0, min(lo, w - 1))
        hi = max(lo + 1, min(hi, w))
        patch = band[:, lo:hi]
        out[i] = float(np.median(patch))
    return out, (x0, x1)


def smooth_curve(y: np.ndarray, win: int = 5) -> np.ndarray:
    """滑动平均，两端用 edge 复制补齐（不能用 zero padding，会把首尾拉暗）。"""
    win = max(1, int(win))
    if win == 1:
        return y.copy()
    r = win // 2
    padded = np.pad(y, (r, r), mode="edge")
    k = np.ones(win) / win
    return np.convolve(padded, k, mode="valid")[: len(y)]


def fit_gamma(y: np.ndarray, x: np.ndarray | None = None) -> dict:
    x = np.arange(256, dtype=np.float32) if x is None else x
    y = np.clip(y, 0, 255)
    # 排除两端夹死点后做单调拟合的粗略诊断
    m = (y > 1) & (y < 254)
    if m.sum() < 8:
        return {"gamma": float("nan"), "gain": float("nan"), "rms": float("nan")}
    a = np.vstack([x[m], np.ones(m.sum())]).T
    g, b = np.linalg.lstsq(a, y[m], rcond=None)[0]
    r = y[m] - (g * x[m] + b)
    return {"gain": float(g), "offset": float(b), "rms": float(r.std())}


def save_png(y: np.ndarray, path: str, title: str = "", ref: np.ndarray | None = None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 6), dpi=130)
    ax.plot(np.arange(256), np.arange(256), ":", color="0.6", label="ideal (y=x)")
    ax.plot(np.arange(256), y, lw=2, label="measured")
    if ref is not None:
        ax.plot(np.arange(256), ref, lw=1.5, ls="--", label="reference")
    ax.set_xlabel("screen band value (0-255)")
    ax.set_ylabel("camera JPEG luma")
    ax.set_title(title or "tone curve")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("files", nargs="+")
    p.add_argument("--csv")
    p.add_argument("--png")
    p.add_argument("--compare")
    p.add_argument("--smooth", type=int, default=5)
    p.add_argument("--json")
    a = p.parse_args(argv)

    results = []
    for f in a.files:
        y, rng = extract(f)
        y = smooth_curve(y, a.smooth)
        results.append(y)
        stats = fit_gamma(y)
        print(f"{f}: ramp x={rng[0]}..{rng[1]}  y[0]={y[0]:.1f} y[64]={y[64]:.1f} "
              f"y[128]={y[128]:.1f} y[192]={y[192]:.1f} y[255]={y[255]:.1f}  "
              f"fit gain={stats['gain']:.3f} offset={stats['offset']:.1f} rms={stats['rms']:.2f}")

    if a.csv:
        cols = {"band": np.arange(256)}
        for f, y in zip(a.files, results):
            cols[f.split("/")[-1].split("\\")[-1]] = y
        arr = np.column_stack([cols[k] for k in cols])
        header = ",".join(cols.keys())
        np.savetxt(a.csv, arr, delimiter=",", header=header, comments="", fmt="%.3f")
        print("csv ->", a.csv)

    if a.json:
        payload = {
            "files": a.files,
            "curves": [y.tolist() for y in results],
            "fit": [fit_gamma(y) for y in results],
        }
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print("json ->", a.json)

    if a.png and results:
        save_png(results[0], a.png, a.files[0])
        print("png ->", a.png)

    if a.compare and len(results) >= 2:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(11, 5), dpi=130)
        for f, y in zip(a.files, results):
            ax[0].plot(np.arange(256), y, lw=2, label=f.split("/")[-1].split("\\")[-1])
            ax[1].plot(np.arange(256), y - results[0], lw=2,
                       label=f.split("/")[-1].split("\\")[-1])
        ax[0].plot(np.arange(256), np.arange(256), ":", color="0.6")
        ax[0].set_title("tone curves")
        ax[0].set_xlabel("band"); ax[0].set_ylabel("JPEG luma"); ax[0].legend(); ax[0].grid(alpha=0.3)
        ax[1].axhline(0, color="0.6", lw=1)
        ax[1].set_title("difference vs first")
        ax[1].set_xlabel("band"); ax[1].set_ylabel("delta"); ax[1].legend(); ax[1].grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(a.compare)
        print("compare ->", a.compare)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
