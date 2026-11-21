"""多张 RAW 的四联对比：相机直出 | CC 强度 0.6 / 1.0 / 1.35 / 1.7。

每张图先做一次曝光匹配：找一个缩放 s，使「相机直出」和「我们渲染」的中位亮度相同。
这样比的是**曲线形状**，而不是曝光差异（不同场景/不同 ISO 的绝对刻度本来就不同）。
"""
import os
import numpy as np
import rawpy
from PIL import Image, ImageOps

ROOT = r"G:\em10fw"
W = 1.04
OUT = ROOT + r"\refs\preview\strength"

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

def build(strengths=(0.6, 1.0, 1.35, 1.7)):
    i = np.arange(256)
    lin = i / 255.0 * W
    x = np.clip(lin, 0, 1) * (SIZE - 1)
    i0 = np.floor(x).astype(int); i1 = np.minimum(i0 + 1, SIZE - 1); f = (x - i0)[:, None]
    cc = oetf((CUBE[i0, i0, i0] * (1 - f) + CUBE[i1, i1, i1] * f)[:, 1])
    neutral = oetf(lin)
    return {s: np.maximum.accumulate(np.clip(neutral + s * (cc - neutral), 0, 255)) for s in strengths}

def load_lin(orf):
    with rawpy.imread(orf) as raw:
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                              gamma=(1, 1), output_bps=16)
    return rgb.astype(np.float32) / 65535.0

def apply(lin, curve, s):
    idx = np.clip(lin * s / W * 255.0, 0, 255)
    out = np.interp(idx, np.arange(256), curve)
    return (np.clip(out, 0, 255)).astype(np.uint8)

def med(im):
    y = im[..., 0] * 0.299 + im[..., 1] * 0.587 + im[..., 2] * 0.114
    return float(np.median(y))

def match_scale(lin, curve, target):
    lo, hi = 0.05, 20.0
    for _ in range(28):
        mid = (lo + hi) / 2
        if med(apply(lin, curve, mid)) < target:
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

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    curves = build()
    targets = [l.strip() for l in open(ROOT + r"\.tmp\strength_targets.txt", encoding="utf-8")
               if l.strip() and not l.startswith("#")]
    for orf in targets:
        tag = os.path.basename(orf).split(".")[0]
        jpg = orf[:-4] + ".JPG"
        cam = ImageOps.exif_transpose(Image.open(jpg)).convert("RGB") if os.path.exists(jpg) else None
        lin = load_lin(orf)
        target = med(np.asarray(cam, np.float32)) if cam is not None else 110
        s = match_scale(lin, curves[1.0], target)
        imgs = []
        if cam is not None:
            c2 = cam.copy(); c2.thumbnail((1000, 1000)); imgs.append(c2)
        for st in (0.6, 1.0, 1.35, 1.7):
            im = Image.fromarray(apply(lin, curves[st], s))
            im.thumbnail((1000, 1000))
            imgs.append(im)
        strip(imgs, os.path.join(OUT, tag + "_4up.jpg"))
        print(f"  {tag}: target_median={target:.1f} scale={s:.3f}")
