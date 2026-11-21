#!/usr/bin/env python3
"""S23: install a computed tone LUT and point every mode at it.

Writes:
  .tmp/s23_tone.patch   -- mkpatch patch file
  .tmp/s23_preview.jpg  -- before/after simulation from a real RAW
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import rawpy

LUT_TXT = ".tmp/s23_lut.txt"
TABLE_ADDR = 0x60DB0000      # blank 0xFF area in section 4
PTR_TABLE = 0x60600920       # 168 pointers, 21 groups x 8
PTR_COUNT = 168


def load_lut():
    return [int(t, 16) for t in open(LUT_TXT).read().split()]


def build_patch(lut):
    lines = []
    data = b"".join(int(v).to_bytes(2, "little") for v in lut)
    for off in range(0, len(data), 32):
        chunk = data[off:off + 32]
        lines.append(f"ram 0x{TABLE_ADDR + off:08x}={chunk.hex()}")
    ptr = TABLE_ADDR.to_bytes(4, "little")
    blob = ptr * PTR_COUNT
    for off in range(0, len(blob), 32):
        lines.append(f"ram 0x{PTR_TABLE + off:08x}={blob[off:off+32].hex()}")
    return "\n".join(lines) + "\n"


def srgb(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * v ** (1 / 2.4) - 0.055) * 255.0


def preview(lut, src="refs/test-photos/PB120034"):
    with rawpy.imread(src + ".ORF") as r:
        rgb = r.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8,
                            half_size=True, gamma=(1, 1))
    lin = np.asarray(rgb, dtype=np.float32) / 255.0
    x = np.clip(lin, 0, 1) * 1023.0
    k = np.clip(np.round(x / 4.0), 0, 255).astype(np.int32)
    lut_arr = np.array(lut, dtype=np.float32) / 1023.0
    after = lut_arr[k]
    d = np.loadtxt(".tmp/tone_T2.csv", delimiter=",", skiprows=1)
    tx, ty = d[:, 0], d[:, 1]
    ok = ty > 2.5
    tx, ty = tx[ok], ty[ok]
    ltx = np.log(tx)

    def Tcurve(v):
        vv = np.clip(v, tx[0], tx[-1])
        return np.interp(np.log(vv), ltx, ty) / 255.0 * 255.0 / 255.0

    before_img = Image.fromarray(np.clip(Tcurve(lin) * 255, 0, 255).astype(np.uint8))
    after_img = Image.fromarray(np.clip(Tcurve(after) * 255, 0, 255).astype(np.uint8))
    w, h = before_img.size
    sheet = Image.new("RGB", (w * 2 + 8, h), (255, 255, 255))
    sheet.paste(before_img, (0, 0))
    sheet.paste(after_img, (w + 8, 0))
    d = ImageDraw.Draw(sheet)
    f = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 26)
    for i, t in enumerate(["原版 T", "S23 胶片曲线"]):
        d.rectangle([i * (w + 8) + 8, 8, i * (w + 8) + 210, 46], fill=(0, 0, 0))
        d.text((i * (w + 8) + 16, 12), t, font=f, fill=(255, 255, 255))
    sheet.save(".tmp/s23_preview.jpg", quality=92)
    print("preview written")


if __name__ == "__main__":
    lut = load_lut()
    patch = build_patch(lut)
    open(".tmp/s23_tone.patch", "w").write(patch)
    print(f"patch lines: {patch.count(chr(10))}, bytes: {256*2 + 168*4}")
    preview(lut)
