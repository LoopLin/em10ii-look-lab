"""把 .cube 3D LUT 应用到一张 sRGB 图片上（三线性插值 + 可选曝光补偿）。

用法:
    python tools/lut_apply.py <in.jpg> <out.jpg> <lut.cube> [--ev -0.5] [--mix 1.0]
"""
import sys, os
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lut_analyze import parse_cube, make_getter


def build_table(path):
    meta, s, vals = parse_cube(path)
    t = np.zeros((s, s, s, 3), dtype=np.float32)
    for bi in range(s):
        for gi in range(s):
            for ri in range(s):
                t[bi, gi, ri] = vals[((bi * s + gi) * s + ri)]
    return meta, s, t


def apply(image, table, s, ev=0.0, mix=1.0):
    a = np.asarray(image).astype(np.float32) / 255.0
    if ev:
        a = np.clip(a * (2.0 ** ev), 0, 1)
    x = a * (s - 1)
    i0 = np.floor(x).astype(np.int32)
    i0 = np.clip(i0, 0, s - 2)
    f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fb, fg, fr = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    c000 = table[b0, g0, r0]
    c001 = table[b0, g0, r0 + 1]
    c010 = table[b0, g0 + 1, r0]
    c011 = table[b0, g0 + 1, r0 + 1]
    c100 = table[b0 + 1, g0, r0]
    c101 = table[b0 + 1, g0, r0 + 1]
    c110 = table[b0 + 1, g0 + 1, r0]
    c111 = table[b0 + 1, g0 + 1, r0 + 1]
    c00 = c000 * (1 - fr) + c001 * fr
    c01 = c010 * (1 - fr) + c011 * fr
    c10 = c100 * (1 - fr) + c101 * fr
    c11 = c110 * (1 - fr) + c111 * fr
    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    out = c0 * (1 - fb) + c1 * fb
    out = a * (1 - mix) + out * mix
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


def main():
    src, dst, lut = sys.argv[1], sys.argv[2], sys.argv[3]
    ev = 0.0
    mix = 1.0
    for i, a in enumerate(sys.argv):
        if a == '--ev':
            ev = float(sys.argv[i + 1])
        if a == '--mix':
            mix = float(sys.argv[i + 1])
    _, s, table = build_table(lut)
    im = Image.open(src).convert('RGB')
    out = apply(im, table, s, ev, mix)
    Image.fromarray(out).save(dst, quality=92)
    print('written', dst, 'size', im.size, 'ev', ev, 'mix', mix)


if __name__ == '__main__':
    main()
