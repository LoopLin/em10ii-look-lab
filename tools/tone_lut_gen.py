#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成色调 LUT 补丁（0x605D8144 恒等表 + 三张 gradation 表）。

ISP 侧的 tone 是 256 个 u16（10 位，0..1023），由 0x60479960 上传到
0x9E00A400 / 0x9E00CC00 的 +0x000。恒等表长这样：0,4,8,...,1020,1023。

用法：
    python tools/tone_lut_gen.py --out .tmp/s22_film.patch --curve film
    python tools/tone_lut_gen.py --preview refs/test-photos/PB110034.JPG --curve film --save .tmp/tone_preview.jpg
"""
from __future__ import annotations
import argparse
import struct

BASE = 0x60021020
DECODED = r"G:\em10fw\analysis\00_0100_40300020.decoded.bin"
TABLES = [0x605D8144, 0x605E3B4E, 0x605E3D4E, 0x605E3F4E]
TOP = 1023.0


def _sstep(x, a, b):
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def curve(name, x):
    """x in 0..1 -> y in 0..1"""
    if name == "identity":
        return x
    if name == "invert":
        return 1.0 - x
    if name == "film":
        # 抬一点黑、压一点白、中间加轻 S：像胶片
        y = x ** 0.92
        y = y + 0.10 * _sstep(y, 0.0, 0.55) - 0.10 * _sstep(y, 0.45, 1.0)
        return 0.035 + 0.93 * max(0.0, min(1.0, y))
    if name == "film_strong":
        y = x ** 0.90
        y = y + 0.16 * _sstep(y, 0.0, 0.55) - 0.16 * _sstep(y, 0.40, 1.0)
        return 0.05 + 0.90 * max(0.0, min(1.0, y))
    if name == "faded":
        # 低对比、提黑、压白
        return 0.10 + 0.78 * x ** 0.95
    if name == "contrast":
        y = x + 0.18 * _sstep(x, 0.0, 0.5) - 0.18 * _sstep(x, 0.5, 1.0)
        return max(0.0, min(1.0, y))
    raise SystemExit("unknown curve " + name)


def lut_values(name, n=256):
    return [max(0, min(1023, round(curve(name, i / (n - 1)) * TOP))) for i in range(n)]


def make_patch(name, path):
    vals = lut_values(name)
    b = b"".join(struct.pack("<H", v) for v in vals)
    lines = [f"ram {hex(a)}={b.hex()}" for a in TABLES]
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return vals


def preview(name, src, dst):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    vals = np.array(lut_values(name), dtype=np.float32)
    im = Image.open(src)
    im.thumbnail((760, 760))
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    y = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    yn = np.clip(y / 255.0 * 255.0, 0, 255)
    idx = np.clip(yn.round().astype(np.int32), 0, 255)
    out_y = vals[idx] / 1023.0 * 255.0
    scale = np.where(y > 1, out_y / np.maximum(y, 1), 1.0)
    out = np.clip(a * scale[..., None], 0, 255).astype(np.uint8)
    res = Image.fromarray(out)
    w, h = im.size
    sheet = Image.new("RGB", (w * 2 + 8, h), (255, 255, 255))
    sheet.paste(im, (0, 0)); sheet.paste(res, (w + 8, 0))
    d = ImageDraw.Draw(sheet)
    f = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 26)
    for i, t in enumerate(["原图", "tone=" + name]):
        d.rectangle([i * (w + 8) + 8, 8, i * (w + 8) + 260, 44], fill=(0, 0, 0))
        d.text((i * (w + 8) + 16, 12), t, font=f, fill=(255, 255, 255))
    sheet.save(dst, quality=92)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--curve", default="film")
    ap.add_argument("--out")
    ap.add_argument("--preview")
    ap.add_argument("--save")
    a = ap.parse_args()
    if a.out:
        v = make_patch(a.curve, a.out)
        print(a.curve, "->", a.out, "head", v[:6], "tail", v[-3:])
    if a.preview:
        preview(a.curve, a.preview, a.save or ".tmp/tone_preview.jpg")
        print("preview ->", a.save or ".tmp/tone_preview.jpg")


if __name__ == "__main__":
    main()
