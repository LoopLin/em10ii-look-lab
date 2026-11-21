"""在真实照片上直接预览曲线改动（不刷机）。

原理：相机已经把所有处理（去马赛克/白平衡/锐化/降噪/饱和度）做到 JPEG 里了，
唯一要换的是 tone。所以：
  1. 用已知的 stock 表 39 的显示值，反查出每个 JPEG 亮度对应的「表索引」；
  2. 再把该索引喂给候选表，得到新的亮度；
  3. 只替换亮度，色度保留（Y'CbCr 里换 Y）。
这样预览的就是「相机同一条链、只换 tone 表」的效果。
"""
import sys, os
import numpy as np
from PIL import Image

ROOT = r"G:\em10fw"
IMG = open(ROOT + r"\analysis\00_0100_40300020.decoded.bin", "rb").read()
BASE = 0x60021020

def table(k):
    off = 0x605D8344 + k * 0x200 - BASE
    t = np.frombuffer(IMG[off:off + 512], dtype="<u2").astype(float)
    t = np.maximum.accumulate(t)
    return t / 1023 * 255

def load_txt(name):
    t = np.array([int(x) for x in open(os.path.join(ROOT, ".tmp", name)).read().split()], float)
    return np.maximum.accumulate(t) / 1023 * 255

stock = table(39)
old = load_txt("cc_tone_v5.txt")     # S35
new = load_txt("cc_tone_v6.txt")     # S36

# stock 显示值 → 表索引 的反函数
idx_axis = np.arange(256)
def jpeg_to_table(disp_curve, jpeg_lum):
    inv = np.interp(jpeg_lum, disp_curve, idx_axis)   # 允许非严格单调
    return np.clip(inv, 0, 255)

def retone(jpg_path, curve):
    im = Image.open(jpg_path).convert("RGB")
    a = np.asarray(im, np.float32)
    y = a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114
    i = jpeg_to_table(stock, y)
    y2 = np.interp(i, idx_axis, curve)
    # 用 Y 的比值缩放 RGB，保留色度
    scale = np.where(y > 1, y2 / np.maximum(y, 1), 1.0)[..., None]
    out = np.clip(a * scale, 0, 255).astype(np.uint8)
    return im, Image.fromarray(out)

def strip(paths, out_path, labels=("原图", "S35", "S36")):
    cols = []
    for jpg in paths:
        im, o = retone(jpg, old)
        _, n = retone(jpg, new)
        cols.append((im, o, n))
    h = 700
    panels = []
    for im, o, n in cols:
        for pic in (im, o, n):
            w = int(pic.width * h / pic.height)
            panels.append(pic.resize((w, h), Image.LANCZOS))
    gap = 6
    total = sum(p.width for p in panels) + gap * (len(panels) - 1)
    canvas = Image.new("RGB", (total, h), (24, 24, 24))
    x = 0
    for p in panels:
        canvas.paste(p, (x, 0)); x += p.width + gap
    canvas.save(out_path, quality=92)
    print("saved", out_path, canvas.size)

if __name__ == "__main__":
    outdir = os.path.join(ROOT, "refs", "preview")
    os.makedirs(outdir, exist_ok=True)
    for name in sys.argv[1:]:
        strip([name], os.path.join(outdir, os.path.basename(name).replace(".JPG", "") + "_3up.jpg"))
