"""四旋钮曲线（黑/白/阴影/高光）+ 对比度联动的饱和度，从 RAW 渲染多组方案。

曲线构造（在 0..255 显示域，逐通道）：
  base   = neutral + s*(CC - neutral)        # s = CC 强度
  blacks = 重映射，b>0 抬黑、b<0 压黑
  whites = 重映射，w>0 提白、w<0 压白
  shadow = 下半段 gamma
  high   = 上半段 gamma（压高光用 >1）
预览里额外做一次饱和度增益（与对比度联动），用来示意「对比高→饱和高」。
"""
import os, json
import numpy as np
import rawpy
from PIL import Image, ImageOps

ROOT = r"G:\em10fw"
W = 1.04
OUT = ROOT + r"\refs\preview\knobs"

def oetf(x):
    x = np.clip(x, 0, 1)
    return 255 * np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055)

def parse_cube(path):
    size, data = None, []
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith("lut_3d_size"):
            size = int(s.split()[-1]); continue
        if s.lower().startswith("domain"):
            continue
        p = s.split()
        if len(p) == 3:
            try:
                data.append([float(x) for x in p])
            except ValueError:
                pass
    a = np.array(data, np.float32)
    return a.reshape(size, size, size, 3).transpose(2, 1, 0, 3), size

CUBE, SIZE = parse_cube(ROOT + r"\.tmp\fujilut\cube lut\classic chrome_sRGB.cube")

def base_curve(s):
    i = np.arange(256)
    lin = i / 255.0 * W
    x = np.clip(lin, 0, 1) * (SIZE - 1)
    i0 = np.floor(x).astype(int); i1 = np.minimum(i0 + 1, SIZE - 1); f = (x - i0)[:, None]
    cc = oetf((CUBE[i0, i0, i0] * (1 - f) + CUBE[i1, i1, i1] * f)[:, 1])
    neutral = oetf(lin)
    return np.maximum.accumulate(np.clip(neutral + s * (cc - neutral), 0, 255))

def knobs_curve(s=1.0, black=0.0, white=0.0, shadow=1.0, high=1.0):
    v = base_curve(s) / 255.0
    # 黑白场
    v = (v - black) / max(1e-6, (1 - black - white))
    v = np.clip(v, 0, 1)
    # 分段 gamma
    lo = v <= 0.5
    out = np.empty_like(v)
    out[lo] = 0.5 * (2 * v[lo]) ** shadow
    out[~lo] = 1 - 0.5 * (2 * (1 - v[~lo])) ** high
    return np.maximum.accumulate(out * 255)

def load_lin(orf):
    with rawpy.imread(orf) as raw:
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                              gamma=(1, 1), output_bps=16)
    return rgb.astype(np.float32) / 65535.0

def render(lin, curve, sat=1.0):
    idx = np.clip(lin / W * 255.0, 0, 255)
    out = np.interp(idx, np.arange(256), curve)          # 0..255, 逐通道
    if sat != 1.0:
        y = out[..., 0] * 0.299 + out[..., 1] * 0.587 + out[..., 2] * 0.114
        out = y[..., None] + (out - y[..., None]) * sat
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

def med(im):
    a = np.asarray(im, np.float32)
    return float(np.median(a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114))

def match_scale(lin, curve, target):
    lo, hi = 0.05, 20.0
    for _ in range(24):
        mid = (lo + hi) / 2
        idx = np.clip(lin * mid / W * 255.0, 0, 255)
        v = float(np.median(np.interp(idx, np.arange(256), curve)))
        if v < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

def strip(imgs, path, h=520, gap=5):
    ps = [im.resize((int(im.width * h / im.height), h), Image.LANCZOS) for im in imgs]
    c = Image.new("RGB", (sum(p.width for p in ps) + gap * (len(ps) - 1), h), (20, 20, 20))
    x = 0
    for p in ps:
        c.paste(p, (x, 0)); x += p.width + gap
    c.save(path, quality=90)
    print("saved", path, c.size)

VARIANTS = [
    dict(name="1.7 无黑白场",   s=1.70, black=0.000,  white=0.000, shadow=1.00, high=1.00, sat=1.00),
    dict(name="黑-0.010 白+0.005", s=1.70, black=-0.010, white=0.005, shadow=0.97, high=1.06, sat=0.97),
    dict(name="黑-0.015 白+0.008", s=1.70, black=-0.015, white=0.008, shadow=0.96, high=1.08, sat=0.96),
    dict(name="黑-0.020 白+0.010", s=1.70, black=-0.020, white=0.010, shadow=0.95, high=1.10, sat=0.95),
    dict(name="黑-0.020 白+0.010 柔高光", s=1.70, black=-0.020, white=0.010, shadow=0.95, high=1.22, sat=0.95),
]

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    targets = [l.strip() for l in open(ROOT + r"\.tmp\knob_targets.txt", encoding="utf-8")
               if l.strip() and not l.startswith("#")]
    for orf in targets:
        tag = os.path.basename(orf).split(".")[0]
        jpg = orf[:-4] + ".JPG"
        cam = ImageOps.exif_transpose(Image.open(jpg)).convert("RGB") if os.path.exists(jpg) else None
        lin = load_lin(orf)
        target = med(cam) if cam else 110
        ref = knobs_curve(s=1.35)
        sc = match_scale(lin, ref, target)
        imgs = []
        if cam:
            c2 = cam.copy(); c2.thumbnail((900, 900)); imgs.append(c2)
        for v in VARIANTS:
            cur = knobs_curve(v["s"], v["black"], v["white"], v["shadow"], v["high"])
            im = render(lin * sc, cur, v["sat"])
            im.thumbnail((900, 900))
            imgs.append(im)
        strip(imgs, os.path.join(OUT, tag + "_knobs.jpg"))
        print("  ", tag, "scale", round(sc, 3), "target", round(target, 1))
