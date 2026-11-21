"""把选定的观感拆成「tone 表 + 六轴(6 段 sat/hue)」，并评估拟合误差。

模型假设（与固件六轴同构）：
  Y 不变；色度 (Cb,Cr) 按输入色相分 6 段，每段做 (a,b) 的 2x2 线性变换：
      Cb' = a*Cb - b*Cr
      Cr' = b*Cb + a*Cr
  a = 饱和度缩放，b = 色相旋转（弧度近似）。
"""
import os, json, glob, math
import numpy as np

ROOT = r"G:\em10fw"
CUBEDIR = os.path.join(ROOT, ".tmp", "fujilut", "cube lut")
LOOKS = ["pro neg std", "astia", "reala ace", "eterna"]

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

def apply_lut(rgb, cube, N):
    p = np.clip(rgb, 0, 1) * (N - 1)
    i0 = np.floor(p).astype(np.int32); i1 = np.minimum(i0 + 1, N - 1)
    f = (p - i0).astype(np.float32)
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
    w = [(1-f[...,0])*(1-f[...,1])*(1-f[...,2]), f[...,0]*(1-f[...,1])*(1-f[...,2]),
         (1-f[...,0])*f[...,1]*(1-f[...,2]),   (1-f[...,0])*(1-f[...,1])*f[...,2],
         f[...,0]*f[...,1]*(1-f[...,2]),       f[...,0]*(1-f[...,1])*f[...,2],
         (1-f[...,0])*f[...,1]*f[...,2],       f[...,0]*f[...,1]*f[...,2]]
    c = [cube[r0,g0,b0], cube[r1,g0,b0], cube[r0,g1,b0], cube[r0,g0,b1],
         cube[r1,g1,b0], cube[r1,g0,b1], cube[r0,g1,b1], cube[r1,g1,b1]]
    out = sum(ci * wi[..., None] for ci, wi in zip(c, w))
    return out

def rgb2ycc(x):
    y = x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114
    cb = 0.564 * (x[..., 2] - y)
    cr = 0.713 * (x[..., 0] - y)
    return y, cb, cr

def ycc2rgb(y, cb, cr):
    r = y + 1.402 * cr
    g = y - 0.344136 * cb - 0.714136 * cr
    b = y + 1.772 * cb
    return np.stack([r, g, b], -1)

def fit_look(name):
    cube, N = parse_cube(os.path.join(CUBEDIR, name + "_sRGB.cube"))
    rng = np.random.default_rng(0)
    # 采样线性光下的颜色（0..1），偏向有意义的亮度范围
    src = rng.random((60000, 3)).astype(np.float32)
    src = np.clip(src * 0.95 + 0.02, 0, 1)
    tgt = apply_lut(src, cube, N)
    y0, cb0, cr0 = rgb2ycc(src)
    y1, cb1, cr1 = rgb2ycc(tgt)
    # 只用有色度的样本参与拟合
    mag = np.hypot(cb0, cr0)
    m = mag > 0.02
    hue = np.degrees(np.arctan2(cr0[m], cb0[m])) % 360
    band = (hue // 60).astype(int) % 6
    X = np.stack([cb0[m], -cr0[m]], 1)   # 对应 Cb' = a*Cb - b*Cr
    Y = cb1[m]
    Z = np.stack([cr0[m], cb0[m]], 1)
    W = cr1[m]
    params = []
    for k in range(6):
        sel = band == k
        A = np.vstack([X[sel], Z[sel]])
        Bv = np.concatenate([Y[sel], W[sel]])
        sol, *_ = np.linalg.lstsq(A, Bv, rcond=None)
        params.append(sol)   # (a, b)
    # 评估：tone(中性轴) + 六轴 的近似
    i = np.arange(256)
    tone = cube[np.round(np.clip(i/255,0,1)*(N-1)).astype(int)] if False else None
    neutral = cube[(np.arange(N)), (np.arange(N)), (np.arange(N))][:, 1]
    # 近似渲染
    yy = (y0 - 0)  # 亮度：直接用中性轴查表
    lum_in = src[..., 1] * 0 + (src[..., 0]*0.299 + src[..., 1]*0.587 + src[..., 2]*0.114)
    tone_lut = np.interp(np.clip(lum_in, 0, 1) * (N - 1), np.arange(N), neutral)
    y_out = tone_lut
    cb2 = np.zeros_like(cb0); cr2 = np.zeros_like(cr0)
    hue_all = np.degrees(np.arctan2(cr0, cb0)) % 360
    band_all = (hue_all // 60).astype(int) % 6
    for k in range(6):
        a, b = params[k]
        sel = band_all == k
        cb2[sel] = a * cb0[sel] - b * cr0[sel]
        cr2[sel] = b * cb0[sel] + a * cr0[sel]
    approx = np.clip(ycc2rgb(y_out, cb2, cr2), 0, 1)
    err = np.abs(approx - tgt).mean() * 255
    return dict(name=name, params=[[round(float(a), 3), round(float(b), 3)] for a, b in params],
                tone=[int(round(float(v) / 1.0 * 1023)) for v in neutral],
                err=round(float(err), 1))

if __name__ == "__main__":
    out = {}
    print("| 观感 | 六轴 (a,b) × 6 | 平均误差 |")
    print("|---|---|---|")
    for name in LOOKS:
        r = fit_look(name)
        out[name] = r
        ps = " ".join(f"({p[0]:.2f},{p[1]:+.2f})" for p in r["params"])
        print(f"| {name} | {ps} | {r['err']:.1f}/255 |")
    json.dump(out, open(os.path.join(ROOT, ".tmp", "four_looks.json"), "w"), indent=1)
    print("saved .tmp/four_looks.json")
