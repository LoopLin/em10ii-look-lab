"""解析 .cube LUT，给出「它把颜色怎么改了」的可读结论。

用法：
    python tools/lut_analyze.py "路径.cube" [--preview 输入.jpg] [--out 前缀]
"""
from __future__ import annotations
import argparse, math, os, sys
import numpy as np


def flog2_encode(x):
    """线性 0..1 → F-Log2 编码 0..1（富士官方曲线）。"""
    a, b, g = 0.245281, 0.384274, 0.29
    cut1 = 0.100686685370811
    slope = (a * cut1 ** g + b - 0.092864) / cut1
    x = np.clip(x, 0.0, 1.0)
    return np.where(x > cut1, a * np.power(np.maximum(x, 1e-8), g) + b,
                    0.092864 + slope * x)


def flog2_decode(y):
    """F-Log2 编码 0..1 → 线性 0..1。"""
    a, b, g = 0.245281, 0.384274, 0.29
    cut1 = 0.100686685370811
    cut2 = a * cut1 ** g + b
    slope = (cut2 - 0.092864) / cut1
    y = np.clip(y, 0.0, 1.0)
    return np.where(y > cut2,
                    np.power(np.maximum((y - b) / a, 1e-8), 1.0 / g),
                    (y - 0.092864) / slope)


def parse_cube(path):
    size = None
    rows = []
    with open(path, "r", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            u = s.upper()
            if u.startswith("LUT_3D_SIZE"):
                size = int(s.split()[-1]); continue
            if u.startswith(("DOMAIN_MIN", "DOMAIN_MAX", "TITLE")):
                continue
            parts = s.split()
            if len(parts) == 3:
                try:
                    rows.append([float(x) for x in parts])
                except ValueError:
                    pass
    arr = np.array(rows, dtype=np.float64)
    if size is None:
        n = round(len(arr) ** (1 / 3))
        size = n
    assert len(arr) == size ** 3, (len(arr), size)
    # .cube 的数据顺序是 R 变化最快，所以 reshape 后第 0 轴是 B、第 2 轴是 R
    return arr.reshape(size, size, size, 3).transpose(2, 1, 0, 3), size


def apply_lut(lut, size, rgb):
    """rgb: (...,3) in 0..1 -> mapped"""
    x = np.clip(rgb, 0, 1) * (size - 1)
    i0 = np.floor(x).astype(int)
    i1 = np.minimum(i0 + 1, size - 1)
    t = x - i0
    out = np.zeros_like(rgb, dtype=np.float64)
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                ix = i1[..., 0] if dx else i0[..., 0]
                iy = i1[..., 1] if dy else i0[..., 1]
                iz = i1[..., 2] if dz else i0[..., 2]
                w = ((t[..., 0] if dx else 1 - t[..., 0]) *
                     (t[..., 1] if dy else 1 - t[..., 1]) *
                     (t[..., 2] if dz else 1 - t[..., 2]))
                out += w[..., None] * lut[ix, iy, iz]
    return out


def rgb2ycc(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 + (-0.168736 * r - 0.331264 * g + 0.5 * b) * 255 / 255
    cr = 128 + (0.5 * r - 0.418688 * g - 0.081312 * b) * 255 / 255
    return y, cb, cr


def neutral_curve(lut, size, n=33):
    g = np.linspace(0, 1, n)
    rgb = np.stack([g, g, g], -1)
    return g, apply_lut(lut, size, rgb)[:, 0]


def chroma_profile(lut, size, luma_pts=9, sat_pts=5):
    """每个亮度层上，色度增益随输入色度的变化"""
    res = []
    for ly in np.linspace(0.12, 0.82, luma_pts):
        row = []
        for s in np.linspace(0.0, 0.5, sat_pts):
            base = np.array([[ly, ly, ly]]) + s * np.array([[0.3, 0.0, -0.3]])
            base = np.clip(base, 0, 1)
            out = apply_lut(lut, size, base)
            y0, cb0, cr0 = (float(v[0]) for v in rgb2ycc(base))
            y1, cb1, cr1 = (float(v[0]) for v in rgb2ycc(out))
            c0 = math.hypot(cb0 - 128, cr0 - 128)
            c1 = math.hypot(cb1 - 128, cr1 - 128)
            row.append(c1 / c0 if c0 > 2 else float("nan"))
        res.append((ly, row))
    return res


def hue_report(lut, size, bands=12, sat=0.35, luma=0.5):
    out = []
    for k in range(bands):
        th = 2 * math.pi * k / bands
        base = np.array([[luma + sat * math.cos(th) * 0.5,
                          luma - sat * 0.25,
                          luma - sat * math.cos(th) * 0.5]])
        base = np.clip(base, 0, 1)
        y0, cb0, cr0 = (float(v[0]) for v in rgb2ycc(base))
        y1, cb1, cr1 = (float(v[0]) for v in rgb2ycc(apply_lut(lut, size, base)))
        h0 = math.degrees(math.atan2(cr0 - 128, cb0 - 128))
        h1 = math.degrees(math.atan2(cr1 - 128, cb1 - 128))
        c0 = math.hypot(cb0 - 128, cr0 - 128)
        c1 = math.hypot(cb1 - 128, cr1 - 128)
        out.append((k * 360 / bands, (h1 - h0 + 540) % 360 - 180, c1 / c0 if c0 > 2 else float("nan"),
                    (y1 - y0) * 255))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cube")
    ap.add_argument("--preview")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    lut, size = parse_cube(a.cube)
    print(f"file: {a.cube}\nsize: {size}^3")
    g, y = neutral_curve(lut, size)
    print("\n[neutral] in -> out (0..255)")
    for gi, yi in list(zip(g, y))[::4]:
        print(f"  {gi*255:6.1f} -> {yi*255:6.1f}   (Δ{(yi-gi)*255:+5.1f})")
    print("\n[chroma gain] luma vs input chroma")
    for ly, row in chroma_profile(lut, size):
        print("  Y=%.2f  " % ly + "  ".join("%.2f" % v for v in row))
    print("\n[hue bands] band, Δhue(deg), chroma gain, Δluma")
    for b in hue_report(lut, size):
        print("  %5.0f°  Δh %+6.1f  sat %5.2f  ΔL %+5.1f" % b)

    if a.preview:
        from PIL import Image
        im = Image.open(a.preview).convert("RGB")
        im.thumbnail((900, 900))
        arr = np.asarray(im, dtype=np.float64) / 255.0
        out = apply_lut(lut, size, arr)
        dst = Image.fromarray(np.clip(out * 255, 0, 255).astype(np.uint8))
        w, h = im.size
        sheet = Image.new("RGB", (w * 2 + 8, h), (255, 255, 255))
        sheet.paste(im, (0, 0)); sheet.paste(dst, (w + 8, 0))
        prefix = a.out or os.path.splitext(a.cube)[0].replace(" ", "_")
        prev = prefix + "_preview.jpg"
        sheet.save(prev, quality=92)
        print("\npreview:", prev)


if __name__ == "__main__":
    sys.exit(main())
