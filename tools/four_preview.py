"""四个候选观感：真 3D LUT vs 我们的「tone 表 + 六轴」近似，并排看差距。"""
import os, sys, json
import numpy as np
from PIL import Image, ImageDraw
import rawpy

ROOT = r"G:\em10fw"
CUBEDIR = os.path.join(ROOT, ".tmp", "fujilut", "cube lut")
SPEC = json.load(open(os.path.join(ROOT, ".tmp", "four_looks.json")))
LOOKS = ["pro neg std", "astia", "reala ace", "eterna"]
BLACK = 0.02
CHROMA = 1.8

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
            try: data.append([float(x) for x in p])
            except ValueError: pass
    a = np.array(data, np.float32)
    return a.reshape(size, size, size, 3).transpose(2, 1, 0, 3), size

def apply_lut(rgb, cube, N):
    p = np.clip(rgb, 0, 1) * (N - 1)
    i0 = np.floor(p).astype(np.int32); i1 = np.minimum(i0 + 1, N - 1)
    f = (p - i0).astype(np.float32)
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
    ws = [(1-f[...,0])*(1-f[...,1])*(1-f[...,2]), f[...,0]*(1-f[...,1])*(1-f[...,2]),
          (1-f[...,0])*f[...,1]*(1-f[...,2]),   (1-f[...,0])*(1-f[...,1])*f[...,2],
          f[...,0]*f[...,1]*(1-f[...,2]),       f[...,0]*(1-f[...,1])*f[...,2],
          (1-f[...,0])*f[...,1]*f[...,2],       f[...,0]*f[...,1]*f[...,2]]
    cs = [cube[r0,g0,b0], cube[r1,g0,b0], cube[r0,g1,b0], cube[r0,g0,b1],
          cube[r1,g1,b0], cube[r1,g0,b1], cube[r0,g1,b1], cube[r1,g1,b1]]
    return sum(c * w[..., None] for c, w in zip(cs, ws))

def rgb2ycc(x):
    y = x[..., 0]*0.299 + x[..., 1]*0.587 + x[..., 2]*0.114
    return y, 0.564*(x[..., 2]-y), 0.713*(x[..., 0]-y)

def ycc2rgb(y, cb, cr):
    return np.stack([y + 1.402*cr, y - 0.344136*cb - 0.714136*cr, y + 1.772*cb], -1)

def to8(lin):
    d = 255*np.where(lin <= 0.0031308, 12.92*lin, 1.055*np.power(np.clip(lin, 0, 1), 1/2.4) - 0.055)
    return np.clip(d, 0, 255).astype(np.uint8)

def load_raw(path):
    with rawpy.imread(path) as r:
        rgb = r.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                            output_bps=16, gamma=(1, 1), output_color=rawpy.ColorSpace.raw)
        lin = rgb.astype(np.float32) / 65535.0
        try: lin = lin.max(2) if False else lin
        except Exception: pass
    return lin

def boost(lin, k):
    if k == 1.0: return lin
    y = 0.299*lin[...,0]+0.587*lin[...,1]+0.114*lin[...,2]
    cb = 0.564*(lin[...,2]-y); cr = 0.713*(lin[...,0]-y)
    return np.clip(np.stack([y+1.402*cr*k, y-0.344136*cb*k-0.714136*cr*k, y+1.772*cb*k], -1), 0, 1)

def approx_look(lin, spec, scale):
    tone = np.array(spec["tone"], np.float32) / 1023.0
    tone256 = np.interp(np.linspace(0, 1, 256), np.linspace(0, 1, len(tone)), tone)
    idx = np.arange(256)
    y, cb, cr = rgb2ycc(np.clip((lin - BLACK)/(1-BLACK), 0, 1) * scale)
    cb = cb * CHROMA; cr = cr * CHROMA
    lum = np.clip(y, 0, 1) * 255
    y2 = np.interp(lum, idx, tone256 * 255) / 255
    hue = np.degrees(np.arctan2(cr, cb)) % 360
    band = (hue // 60).astype(int) % 6
    cb2 = np.zeros_like(cb); cr2 = np.zeros_like(cr)
    for k, (a, b) in enumerate(spec["params"]):
        sel = band == k
        cb2[sel] = a*cb[sel] - b*cr[sel]
        cr2[sel] = b*cb[sel] + a*cr[sel]
    return ycc2rgb(y2, cb2, cr2)

def label(im, text):
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 8*len(text)*2, 34], fill=(0, 0, 0))
    d.text((8, 8), text, fill=(255, 220, 80))
    return im

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else r"G:\em10fw\refs\test-photos\P3215686.ORF"
    name = os.path.splitext(os.path.basename(src))[0]
    lin = load_raw(src)
    h, w = lin.shape[:2]
    cam = None
    jpg = os.path.splitext(src)[0] + ".JPG"
    if os.path.exists(jpg):
        cam = np.asarray(Image.open(jpg).convert("RGB").resize((w, h)), np.float32)/255.0
    # 曝光匹配：粗扫 scale，使渲染中位亮度贴近相机 JPEG
    scale = 3.0
    if cam is not None:
        cube0, N0 = parse_cube(os.path.join(CUBEDIR, LOOKS[0] + "_sRGB.cube"))
        small = lin[::4, ::4]
        target = np.median(0.299*cam[...,0] + 0.587*cam[...,1] + 0.114*cam[...,2]) * 255
        best, bestd = 3.0, 1e9
        for sc in np.arange(1.5, 9.01, 0.5):
            r0 = apply_lut(np.clip((small - BLACK)/(1-BLACK), 0, 1)*sc, cube0, N0)
            d0 = to8(r0).astype(np.float32)
            m = np.median(0.299*d0[...,0] + 0.587*d0[...,1] + 0.114*d0[...,2])
            if abs(m - target) < bestd:
                bestd, best = abs(m - target), float(sc)
        scale = best
    tiles = []
    if cam is not None:
        tiles.append(("相机直出", cam))
    for lk in LOOKS:
        cube, N = parse_cube(os.path.join(CUBEDIR, lk + "_sRGB.cube"))
        base = apply_lut(np.clip((lin - BLACK)/(1-BLACK), 0, 1)*scale, cube, N)
        tiles.append((lk + " 真LUT", to8(boost(base, CHROMA))/255.0))
        tiles.append((lk + " 表+六轴", to8(np.clip(approx_look(lin, SPEC[lk], scale), 0, 1))/255.0))
    # 3 列网格
    cols = 3
    rows = (len(tiles) + cols - 1)//cols
    H = 420; W = int(w*H/h)
    sheet = Image.new("RGB", (W*cols + 4*(cols-1), H*rows + 4*(rows-1)), (22, 22, 22))
    for i, (t, arr) in enumerate(tiles):
        im = Image.fromarray((np.clip(arr, 0, 1)*255).astype(np.uint8)).resize((W, H), Image.LANCZOS)
        label(im, t)
        sheet.paste(im, ((i % cols)*(W+4), (i//cols)*(H+4)))
    out = os.path.join(ROOT, "refs", "preview", "four", name + "_four.jpg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sheet.save(out, quality=92)
    print("saved", out, sheet.size, "scale", round(scale, 3))
