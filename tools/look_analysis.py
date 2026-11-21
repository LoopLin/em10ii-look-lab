"""逐个分析 11 个富士观感：中性轴 tone 形状 + 颜色统计。输出 markdown 表。"""
import os, glob, math
import numpy as np

ROOT = r"G:\em10fw"
CUBEDIR = os.path.join(ROOT, ".tmp", "fujilut", "cube lut")

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

def oetf(x):
    x = np.clip(x, 0, 1)
    return 255 * np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055)

def lin2srgb(x):
    return float(oetf(np.array([x]))[0])

rows = []
for path in sorted(glob.glob(os.path.join(CUBEDIR, "*_sRGB.cube"))):
    name = os.path.basename(path).replace("_sRGB.cube", "")
    cube, N = parse_cube(path)
    # 中性轴：R=G=B 输入
    neu = cube[:, :, :].diagonal(axis1=0, axis2=1).diagonal(axis1=0, axis2=1)[:, :]  # 近似
    idx = np.arange(N)
    neutral_lin = cube[idx, idx, idx]              # (N,3)
    disp = oetf(neutral_lin[:, 1])                 # 用 G 通道当亮度
    # 指标
    v0 = disp[0]; v100 = disp[-1]
    mid = disp[int(N * 0.5)]
    q25 = disp[int(N * 0.25)]; q75 = disp[int(N * 0.75)]
    contrast = (q75 - q25) / 0.5                   # 相对 sRGB 的陡度（sRGB 约 0.5*255=127.5 差）
    # 颜色：中层灰上的色相/饱和变化（取 6 个色相、饱和 0.5）
    shifts, scales = [], []
    lv = 0.35
    for h in range(6):
        ang = h * math.pi / 3
        rgb = np.array([lv + 0.25 * math.cos(ang), lv + 0.25 * math.cos(ang - 2 * math.pi / 3),
                        lv + 0.25 * math.cos(ang + 2 * math.pi / 3)], np.float32)
        rgb = np.clip(rgb, 0, 1)
        p = rgb * (N - 1)
        i0 = np.floor(p).astype(int); i1 = np.minimum(i0 + 1, N - 1); f = p - i0
        c = (cube[i0[0], i0[1], i0[2]] * (1 - f[0]) * (1 - f[1]) * (1 - f[2]) + cube[i1[0], i0[1], i0[2]] * f[0] * (1 - f[1]) * (1 - f[2]) +
             cube[i0[0], i1[1], i0[2]] * (1 - f[0]) * f[1] * (1 - f[2]) + cube[i0[0], i0[1], i1[2]] * (1 - f[0]) * (1 - f[1]) * f[2] +
             cube[i1[0], i1[1], i0[2]] * f[0] * f[1] * (1 - f[2]) + cube[i1[0], i0[1], i1[2]] * f[0] * (1 - f[1]) * f[2] +
             cube[i0[0], i1[1], i1[2]] * (1 - f[0]) * f[1] * f[2] + cube[i1[0], i1[1], i1[2]] * f[0] * f[1] * f[2])
        def hsv(y):
            mx, mn = y.max(), y.min()
            s = 0 if mx == 0 else (mx - mn) / mx
            r, g, b = y
            if mx == mn:
                hh = 0
            elif mx == r:
                hh = (60 * ((g - b) / (mx - mn))) % 360
            elif mx == g:
                hh = 60 * ((b - r) / (mx - mn)) + 120
            else:
                hh = 60 * ((r - g) / (mx - mn)) + 240
            return hh, s
        h0, s0 = hsv(rgb); h1, s1 = hsv(c)
        dh = (h1 - h0 + 180) % 360 - 180
        shifts.append(dh); scales.append(s1 / max(s0, 1e-6))
    rows.append(dict(name=name,
                     v0=v0, v25=q25, v50=mid, v75=q75, v100=v100,
                     contrast=contrast,
                     dh=np.mean(shifts), dhmax=np.max(np.abs(shifts)),
                     sat=np.mean(scales)))

hdr = "| 观感 | 黑位 | 25% | 50% | 75% | 白位 | 中段陡度 | 平均色相偏 | 最大色相偏 | 平均饱和比 |"
print(hdr); print("|" + "---|" * 10)
for r in rows:
    print(f"| {r['name']} | {r['v0']:.0f} | {r['v25']:.0f} | {r['v50']:.0f} | {r['v75']:.0f} | {r['v100']:.0f} | "
          f"{r['contrast']:.2f} | {r['dh']:+.1f}° | {r['dhmax']:.1f}° | {r['sat']:.2f} |")
