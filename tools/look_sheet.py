"""把 11 个富士观感套在同一张 RAW 上，出联络表（每格标名字+关键参数）。"""
import os, glob
import numpy as np
import rawpy
from PIL import Image, ImageOps, ImageDraw, ImageFont

ROOT = r"G:\em10fw"
CUBEDIR = os.path.join(ROOT, ".tmp", "fujilut", "cube lut")
OUT = ROOT + r"\refs\preview\looks"
W = 1.04

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

def apply_lut(rgb_lin, cube, N):
    p = np.clip(rgb_lin, 0, 1) * (N - 1)
    i0 = np.floor(p).astype(np.int32); i1 = np.minimum(i0 + 1, N - 1)
    f = (p - i0).astype(np.float32)
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
    w000 = (1-f[...,0])*(1-f[...,1])*(1-f[...,2]); w100 = f[...,0]*(1-f[...,1])*(1-f[...,2])
    w010 = (1-f[...,0])*f[...,1]*(1-f[...,2]);     w001 = (1-f[...,0])*(1-f[...,1])*f[...,2]
    w110 = f[...,0]*f[...,1]*(1-f[...,2]);         w101 = f[...,0]*(1-f[...,1])*f[...,2]
    w011 = (1-f[...,0])*f[...,1]*f[...,2];         w111 = f[...,0]*f[...,1]*f[...,2]
    out = (cube[r0,g0,b0]*w000[...,None] + cube[r1,g0,b0]*w100[...,None] +
           cube[r0,g1,b0]*w010[...,None] + cube[r0,g0,b1]*w001[...,None] +
           cube[r1,g1,b0]*w110[...,None] + cube[r1,g0,b1]*w101[...,None] +
           cube[r0,g1,b1]*w011[...,None] + cube[r1,g1,b1]*w111[...,None])
    return out

def tabled(cube, N):
    """把 3D LUT 折叠成「逐通道 1D 表」（沿中性轴），用于纯 tone 预览。"""
    i = np.arange(256)
    lin = np.clip(i / 255.0 * W, 0, 1)
    x = lin * (N - 1)
    i0 = np.floor(x).astype(int); i1 = np.minimum(i0 + 1, N - 1); f = (x - i0)[:, None]
    return (cube[i0, i0, i0] * (1 - f) + cube[i1, i1, i1] * f)[:, 1]   # G 通道，线性光

def load_lin(orf):
    with rawpy.imread(orf) as raw:
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                              gamma=(1, 1), output_bps=16)
    return rgb.astype(np.float32) / 65535.0

def med(im):
    a = np.asarray(im, np.float32)
    return float(np.median(a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114))

def font(sz):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, sz)
            except Exception:
                pass
    return ImageFont.load_default()

def tag(im, text):
    im = im.copy(); d = ImageDraw.Draw(im); f = font(max(16, im.width // 26))
    b = d.textbbox((0, 0), text, font=f)
    d.rectangle([0, 0, b[2] + 16, b[3] + 12], fill=(0, 0, 0))
    d.text((8, 4), text, fill=(255, 235, 120), font=f)
    return im

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    targets = [l.strip() for l in open(ROOT + r"\.tmp\look_targets.txt", encoding="utf-8")
               if l.strip() and not l.startswith("#")]
    looks = sorted(glob.glob(os.path.join(CUBEDIR, "*_sRGB.cube")))
    for orf in targets:
        base = os.path.basename(orf).split(".")[0]
        lin = load_lin(orf)
        jpg = os.path.splitext(orf)[0] + ".JPG"
        cam = ImageOps.exif_transpose(Image.open(jpg)).convert("RGB") if os.path.exists(jpg) else None
        # 曝光匹配：用 provia 作为参考
        prov = [p for p in looks if "provia" in p][0]
        cube, N = parse_cube(prov)
        t = tabled(cube, N)
        target = med(cam) if cam else 130
        lo, hi = 0.05, 30.0
        for _ in range(22):
            mid = (lo + hi) / 2
            idx = np.clip(lin * mid / W * 255.0, 0, 255)
            v = float(np.median(np.interp(idx, np.arange(256), t * 255)))
            if v < target: lo = mid
            else: hi = mid
        sc = (lo + hi) / 2
        panels = []
        if cam:
            c = cam.copy(); c.thumbnail((640, 640)); panels.append(tag(c, "相机直出"))
        for path in looks:
            name = os.path.basename(path).replace("_sRGB.cube", "")
            cube, N = parse_cube(path)
            lin_out = apply_lut(lin * sc, cube, N)
            disp = 255 * np.where(lin_out <= 0.0031308, 12.92 * lin_out,
                                  1.055 * np.power(np.clip(lin_out, 0, 1), 1/2.4) - 0.055)
            im = Image.fromarray(np.clip(disp, 0, 255).astype(np.uint8))
            im.thumbnail((640, 640))
            panels.append(tag(im, name))
        cols = 4
        w = max(p.width for p in panels); h = max(p.height for p in panels)
        rows = (len(panels) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * w + (cols - 1) * 4, rows * h + (rows - 1) * 4), (18, 18, 18))
        for i, p in enumerate(panels):
            sheet.paste(p, ((i % cols) * (w + 4), (i // cols) * (h + 4)))
        path = os.path.join(OUT, base + "_looks.jpg")
        sheet.save(path, quality=90)
        print("saved", path, sheet.size, "scale", round(sc, 3))
