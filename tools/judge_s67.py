#!/usr/bin/env python3
"""S67 判读（绝对判据，不依赖与历史照片对比）。

S67 = g7 全黑(0) + g20 全白(1023)，其余原厂。
判据基于「同一张画面」的绝对亮度分布：
  p50 < 0.12 且 p95 < 0.35  -> 整体压黑（g7）
  p50 > 0.80 且 p1  > 0.55  -> 整体发白（g20）
  两侧都缩到中间          -> 对比崩塌
  分布正常（p1<0.10 且 p95>0.55） -> 该档不受影响

用法: python tools/judge_s67.py <目录|图片...>
"""
import sys, glob, os
import numpy as np
from PIL import Image, ImageOps

MODES = ["i-Enhance", "Vivid", "Natural", "Muted", "Portrait"]

def stats(p):
    im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
    a = np.asarray(im, np.float32) / 255.0
    y = 0.299*a[...,0] + 0.587*a[...,1] + 0.114*a[...,2]
    return dict(p1=float(np.percentile(y,1)), p50=float(np.median(y)),
                p95=float(np.percentile(y,95)), mean=float(y.mean()))

def verdict(s):
    if s["p50"] < 0.12 and s["p95"] < 0.35: return "压黑 → 读 g7"
    if s["p50"] > 0.80 and s["p1"] > 0.55:  return "发白 → 读 g20"
    if s["p95"] - s["p1"] < 0.25 and 0.25 < s["p50"] < 0.75: return "对比崩塌"
    if s["p1"] < 0.10 and s["p95"] > 0.55:  return "正常（未受影响）"
    return "轻微变化"

def main():
    args = sys.argv[1:] or ["."]
    files = []
    for a in args:
        if os.path.isdir(a):
            files += sorted(glob.glob(os.path.join(a, "*.JPG")) + glob.glob(os.path.join(a, "*.jpg")))
        else:
            files.append(a)
    if not files: sys.exit("没找到图片")
    print(f"{'文件':<24}{'p1':>7}{'p50':>7}{'p95':>7}  判读")
    for i, f in enumerate(files):
        s = stats(f)
        name = os.path.basename(f)
        print(f"{name:<24}{s['p1']:>7.3f}{s['p50']:>7.3f}{s['p95']:>7.3f}  {verdict(s)}")
    print("\n对照预期：原厂正常照片 p1<0.10、p95>0.55；若五张全部落在同一异常区间，即该组被标准档读取。")

if __name__ == "__main__":
    main()
