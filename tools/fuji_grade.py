"""参数化「胶片感」调色，直接作用于 sRGB 照片（不是 LUT 套用）。

思路：富士感 = 暖肤色 + 受控的蓝（不推青）+ 暗部抬升 + 高光滚降 + 轻对比。
用法: python tools/fuji_grade.py <in.jpg> <out.jpg> [--strength 0.6]
"""
import sys
import numpy as np
from PIL import Image


def rgb_to_hsv(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx = np.max(a, axis=-1)
    mn = np.min(a, axis=-1)
    d = mx - mn + 1e-9
    h = np.zeros_like(mx)
    m = (mx == r)
    h[m] = ((g[m] - b[m]) / d[m]) % 6
    m = (mx == g)
    h[m] = (b[m] - r[m]) / d[m] + 2
    m = (mx == b)
    h[m] = (r[m] - g[m]) / d[m] + 4
    h = (h / 6.0) % 1.0
    s = np.where(mx <= 0, 0, d / (mx + 1e-9))
    return h, s, mx


def hsv_to_rgb(h, s, v):
    i = np.floor(h * 6.0)
    f = h * 6.0 - i
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    i = i.astype(np.int32) % 6
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=-1)


def grade(a, k=0.6):
    # a: float 0..1 sRGB
    # 1) 轻微 S 曲线（对比），保两端
    x = a
    x = x + k * 0.10 * np.sin(np.pi * (x - 0.5)) * -1
    # 2) 暗部抬升 + 高光滚降（胶片肩/趾）
    x = x + k * 0.030 * (1 - x) ** 6
    x = x - k * 0.040 * x ** 6
    # 3) 色相/饱和度：红→橙，蓝不动（防青），肤色暖
    h, s, v = rgb_to_hsv(np.clip(x, 0, 1))
    hue = h.copy()
    # 红带 ~0.0/1.0 附近 +0.012，橙带 0.08 附近 +0.008
    d = np.minimum(np.abs(hue - 0.0), np.abs(hue - 1.0))
    hue = (hue + k * 0.012 * np.exp(-(d / 0.05) ** 2)) % 1.0
    d2 = np.abs(hue - 0.085)
    hue = (hue + k * 0.010 * np.exp(-(d2 / 0.05) ** 2)) % 1.0
    # 蓝/青带轻微降饱和（天空不炸）
    db = np.abs(hue - 0.58)
    sat = s * (1 - k * 0.10 * np.exp(-(db / 0.10) ** 2))
    # 全局饱和 +6%
    sat = np.clip(sat * (1 + k * 0.10), 0, 1)
    out = hsv_to_rgb(hue, sat, v)
    # 4) 暖：R+1.5%, B-1.5%
    out[..., 0] *= 1 + k * 0.015
    out[..., 2] *= 1 - k * 0.015
    return np.clip(out, 0, 1)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    k = 0.6
    for i, a in enumerate(sys.argv):
        if a == '--strength':
            k = float(sys.argv[i + 1])
    im = Image.open(src).convert('RGB')
    a = np.asarray(im).astype(np.float32) / 255.0
    out = grade(a, k)
    Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)).save(dst, quality=92)
    print('written', dst, 'strength', k)


if __name__ == '__main__':
    main()
