import os, json
import numpy as np
from PIL import Image
import rawpy

ROOT = r"G:\em10fw"; CUBEDIR = os.path.join(ROOT, ".tmp", "fujilut", "cube lut")
ORF = os.path.join(ROOT, "refs", "test-photos", "P3215673.ORF")

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
    return np.clip(d, 0, 255).astype(np.uint8)

def stats(im):
    a = np.asarray(im, np.float32)
    y = 0.299*a[...,0]+0.587*a[...,1]+0.114*a[...,2]
    mx = a.max(2); mn = a.min(2)
    sat = np.where(mx > 4, (mx-mn)/np.maximum(mx, 1), 0)
    return dict(p1=np.percentile(y,1), p5=np.percentile(y,5), p50=np.percentile(y,50),
                p95=np.percentile(y,95), p99=np.percentile(y,99), sat=np.mean(sat))

with rawpy.imread(ORF) as r:
    lin = r.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                        output_bps=16, gamma=(1,1), output_color=rawpy.ColorSpace.raw).astype(np.float32)/65535.0
cam = Image.open(os.path.splitext(ORF)[0] + ".JPG").convert("RGB")
cam0 = cam.resize((lin.shape[1], lin.shape[0]))
print("camera      ", {k: round(float(v),1) for k,v in stats(cam0).items()})

cube, N = parse_cube(os.path.join(CUBEDIR, "pro neg std_sRGB.cube"))
def boost(lin, k):
    y = 0.299*lin[...,0]+0.587*lin[...,1]+0.114*lin[...,2]
    cb = 0.564*(lin[...,2]-y); cr = 0.713*(lin[...,0]-y)
    r = y + 1.402*cr*k; g = y - 0.344136*cb*k - 0.714136*cr*k; b = y + 1.772*cb*k
    return np.clip(np.stack([r,g,b],-1), 0, 1)

for name, sub, sc in [("scale 2.19 (现用)", 0.0, 2.19),
                      ("scale 3.50",       0.0, 3.50),
                      ("black-0.02 sq2.19", 0.02, 2.19),
                      ("black-0.02 sq3.50", 0.02, 3.50),
                      ("black-0.03 sq4.00", 0.03, 4.00)]:
    x = np.clip((lin - sub) / (1 - sub), 0, 1) * sc
    im = Image.fromarray(to8(apply_lut(x, cube, N)))
    print(f"{name:20s}", {k: round(float(v),1) for k,v in stats(im).items()})

print('--- 加色度增益 ---')
x = np.clip(lin - 0.02, 0, 1)/0.98 * 3.0
base = apply_lut(x, cube, N)
for k in (1.0, 2.0, 3.0, 4.0):
    im = Image.fromarray(to8(boost(base, k)))
    print(f"chroma x{k:3.1f}", {kk: round(float(v),1) for kk,v in stats(im).items()})
    im.resize((im.width//2, im.height//2), Image.LANCZOS).save(
        os.path.join(ROOT, ".tmp", f"haze_x{k:.0f}.jpg"), quality=92)
