"""在 LUT 结果上叠四旋钮（黑白场/阴影/高光/饱和），出阶梯图。"""
import os, sys
import numpy as np
from PIL import Image, ImageDraw
import rawpy

ROOT = r"G:\em10fw"; CUBEDIR = os.path.join(ROOT, ".tmp", "fujilut", "cube lut")
LOOK = "pro neg std"
BLACK = 0.02

def parse_cube(path):
    size, data = None, []
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if not s or s.startswith("#"): continue
        if s.lower().startswith("lut_3d_size"): size = int(s.split()[-1]); continue
        if s.lower().startswith("domain"): continue
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

def to8(lin):
    d = 255*np.where(lin <= 0.0031308, 12.92*lin, 1.055*np.power(np.clip(lin,0,1), 1/2.4)-0.055)
    return np.clip(d, 0, 255)

def knobs(disp, black, white, shadow, high, sat):
    x = disp / 255.0
    x = black + x * (1.0 - black - white)
    x = np.clip(x, 0, 1)
    lo = np.where(x < 0.5, np.power(np.clip(x/0.5, 0, 1), shadow) * 0.5, x)
    hi = np.where(x >= 0.5, 0.5 + np.power(np.clip((x-0.5)/0.5, 0, 1), high) * 0.5, x)
    out = np.where(x < 0.5, lo, hi)
    y = 0.299*out[...,0]+0.587*out[...,1]+0.114*out[...,2]
    cb = 0.564*(out[...,2]-y)*sat; cr = 0.713*(out[...,0]-y)*sat
    r = y + 1.402*cr; g = y - 0.344136*cb - 0.714136*cr; b = y + 1.772*cb
    return np.clip(np.stack([r,g,b],-1), 0, 1)*255

def label(im, text):
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 12*len(text)+16, 32], fill=(0,0,0))
    d.text((8, 8), text, fill=(255, 220, 80))
    return im

def main(src):
    name = os.path.splitext(os.path.basename(src))[0]
    with rawpy.imread(src) as r:
        lin = r.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                            output_bps=16, gamma=(1,1), output_color=rawpy.ColorSpace.raw).astype(np.float32)/65535.0
    h, w = lin.shape[:2]
    cam = np.asarray(Image.open(os.path.splitext(src)[0]+".JPG").convert("RGB").resize((w,h)), np.float32)
    cube, N = parse_cube(os.path.join(CUBEDIR, LOOK + "_sRGB.cube"))
    x0 = np.clip((lin - BLACK)/(1-BLACK), 0, 1)
    small = x0[::4, ::4]
    target = np.median(0.299*cam[...,0]+0.587*cam[...,1]+0.114*cam[...,2])
    best, bd = 3.0, 1e9
    for sc in np.arange(1.5, 9.01, 0.5):
        d0 = to8(apply_lut(small*sc, cube, N))
        m = np.median(0.299*d0[...,0]+0.587*d0[...,1]+0.114*d0[...,2])
        if abs(m-target) < bd: bd, best = abs(m-target), float(sc)
    disp = to8(apply_lut(x0*best, cube, N))
    sets = [
        ("直给 饱和1.8",      0.000, 0.000, 1.00, 1.00, 1.80),
        ("柔一档 饱和1.4",    0.035, 0.030, 1.06, 0.95, 1.40),
        ("柔两档 饱和1.3",    0.060, 0.050, 1.10, 0.92, 1.30),
        ("柔三档 饱和1.2",    0.085, 0.070, 1.14, 0.88, 1.20),
        ("更平 饱和1.1",      0.110, 0.090, 1.20, 0.85, 1.10),
    ]
    tiles = [("相机直出", cam)]
    for t, b, wt, sh, hi, sa in sets:
        tiles.append((f"{t} b{b:.3f} w{wt:.3f} s{sh:.2f} h{hi:.2f}", knobs(disp, b, wt, sh, hi, sa)))
    H = 380; W = int(w*H/h)
    cols, rows = 3, 2
    sheet = Image.new("RGB", (W*cols+4*(cols-1), H*rows+4*(rows-1)), (22,22,22))
    for i,(t,arr) in enumerate(tiles):
        im = Image.fromarray(np.clip(arr,0,255).astype(np.uint8)).resize((W,H), Image.LANCZOS)
        label(im, t)
        sheet.paste(im, ((i%cols)*(W+4), (i//cols)*(H+4)))
    out = os.path.join(ROOT, "refs", "preview", "four", name + "_knobs.jpg")
    sheet.save(out, quality=92)
    print("saved", out, sheet.size, "scale", round(best,2))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "refs", "test-photos", "P3215673.ORF"))
