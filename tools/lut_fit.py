"""把富士 LUT 当成「显示域里的映射」来测：它让颜色怎么变。

背景：这批复刻 LUT 的输入域是 **线性光**（已用 Provia 验证：按线性域套回去
最大偏差 28/255，按 sRGB 域套则 48/255，且中间调被抬 47 级）。所以这里一律
「显示域 sRGB 采样 → 转到线性 → 查表 → 转回 sRGB」，再统计变化。

用法：
    python tools/lut_fit.py <lut.cube> [--preview x.jpg] [--out 前缀]
"""
from __future__ import annotations
import argparse, math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lut_analyze import parse_cube, apply_lut


def eotf(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def oetf(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def ycbcr(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b
    return y, cb, cr


def from_ycbcr(y, cb, cr):
    r = y + 1.402 * cr
    g = y - 0.344136 * cb - 0.714136 * cr
    b = y + 1.772 * cb
    return np.clip(np.stack([r, g, b], -1), 0, 1)


def apply_disp(lut, size, rgb_disp):
    out = apply_lut(lut, size, eotf(rgb_disp))
    return oetf(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cube")
    ap.add_argument("--preview")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    lut, size = parse_cube(a.cube)
    name = os.path.basename(a.cube)
    print(f"# {name}  ({size}^3, linear-domain LUT)")

    # 1) 中性轴
    g = np.linspace(0, 1, 17)
    rgb = np.stack([g] * 3, -1)
    out = apply_disp(lut, size, rgb)[:, 0]
    print("\n[neutral] in -> out (display 0..255)")
    for gi, oi in zip(g, out):
        print(f"  {gi*255:6.1f} -> {oi*255:6.1f}   d{(oi-gi)*255:+6.1f}")

    # 2) 色度增益：按亮度层 × 输入色度
    print("\n[chroma gain = out/ in]  rows: luma, cols: input chroma (0.05..0.35)")
    sats = [0.05, 0.10, 0.15, 0.22, 0.30]
    for ly in [0.15, 0.30, 0.45, 0.60, 0.75]:
        row = []
        for s in sats:
            acc = []
            for th in range(0, 360, 30):
                rad = math.radians(th)
                cb, cr = s * math.cos(rad), s * math.sin(rad)
                base = from_ycbcr(ly, cb, cr)[None, :]
                o = apply_disp(lut, size, base)
                y0, cb0, cr0 = ycbcr(base)
                y1, cb1, cr1 = ycbcr(o)
                c0 = math.hypot(cb0[0], cr0[0]); c1 = math.hypot(cb1[0], cr1[0])
                if c0 > 0.02:
                    acc.append(c1 / c0)
            row.append(float(np.median(acc)) if acc else float("nan"))
        print("  Y=%.2f  " % ly + "  ".join("%5.2f" % v for v in row))

    # 3) 色相带：每个色相 12 等分，测色相旋转 / 饱和变化 / 亮度变化
    print("\n[hue bands]  luma=0.5, chroma=0.25")
    print("  band   dhue(deg)  sat_gain   dLuma")
    for k in range(12):
        th = 2 * math.pi * k / 12
        base = from_ycbcr(0.5, 0.25 * math.cos(th), 0.25 * math.sin(th))[None, :]
        o = apply_disp(lut, size, base)
        y0, cb0, cr0 = ycbcr(base); y1, cb1, cr1 = ycbcr(o)
        h0 = math.degrees(math.atan2(cr0[0], cb0[0]))
        h1 = math.degrees(math.atan2(cr1[0], cb1[0]))
        c0 = math.hypot(cb0[0], cr0[0]); c1 = math.hypot(cb1[0], cb1[0] if False else cr1[0])
        print("  %4.0f   %+7.1f    %5.2f    %+6.1f" % (
            k * 30, (h1 - h0 + 540) % 360 - 180, c1 / c0, (y1[0] - y0[0]) * 255))

    if a.preview:
        from PIL import Image
        im = Image.open(a.preview).convert("RGB")
        im.thumbnail((820, 820))
        arr = np.asarray(im, dtype=np.float64) / 255.0
        o = apply_disp(lut, size, arr)
        dst = Image.fromarray(np.clip(o * 255, 0, 255).astype(np.uint8))
        w, h = im.size
        sheet = Image.new("RGB", (w * 2 + 8, h), (255, 255, 255))
        sheet.paste(im, (0, 0)); sheet.paste(dst, (w + 8, 0))
        prefix = a.out or os.path.join(".tmp", os.path.splitext(name)[0].replace(" ", "_"))
        prev = prefix + "_preview.jpg"
        sheet.save(prev, quality=92)
        print("\npreview:", prev)


if __name__ == "__main__":
    sys.exit(main())
