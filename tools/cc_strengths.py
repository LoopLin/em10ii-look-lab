"""从 RAW 生成多档强度的候选曲线并渲染，用于离线挑版。"""
import os, numpy as np, rawpy
from PIL import Image

ROOT = r"G:\em10fw"
W = 1.04
ORF = r"I:\DCIM\100OLYMP\PB120068.ORF"
JPG = r"I:\DCIM\100OLYMP\PB120068.JPG"

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

cube, size = parse_cube(ROOT + r"\.tmp\fujilut\cube lut\classic chrome_sRGB.cube")

def curves(strengths=(0.6, 1.0, 1.35)):
    i = np.arange(256)
    lin = i / 255.0 * W
    x = np.clip(lin, 0, 1) * (size - 1)
    i0 = np.floor(x).astype(int); i1 = np.minimum(i0 + 1, size - 1); f = (x - i0)[:, None]
    cc = oetf((cube[i0, i0, i0] * (1 - f) + cube[i1, i1, i1] * f)[:, 1])
    neutral = oetf(lin)
    out = {}
    for s in strengths:
        v = np.clip(neutral + s * (cc - neutral), 0, 255)
        out[s] = np.maximum.accumulate(v)
    return out

def render(curve):
    with rawpy.imread(ORF) as raw:
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                              gamma=(1, 1), output_bps=16)
    lin = rgb.astype(np.float32) / 65535.0
    idx = np.clip(lin / W * 255.0, 0, 255)
    out = np.interp(idx, np.arange(256), curve) / 255.0
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))

if __name__ == "__main__":
    cs = curves()
    cam = Image.open(JPG).convert("RGB")
    imgs = [cam] + [render(cs[s]) for s in (0.6, 1.0, 1.35)]
    h = 700; gap = 6
    ps = [im.resize((int(im.width * h / im.height), h), Image.LANCZOS) for im in imgs]
    c = Image.new("RGB", (sum(p.width for p in ps) + gap * (len(ps) - 1), h), (24, 24, 24))
    x = 0
    for p in ps:
        c.paste(p, (x, 0)); x += p.width + gap
    path = ROOT + r"\refs\preview\PB120068_strengths.jpg"
    c.save(path, quality=92)
    print("saved", path, c.size)
    for s, cv in cs.items():
        print(f"s={s}: 0->{cv[0]:.0f} 64->{cv[64]:.0f} 128->{cv[128]:.0f} 255->{cv[255]:.0f}")
