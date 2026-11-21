"""按标定后的模型生成 Classic Chrome 的 256 项 tone 表。

模型（由 S33 实验结果 + 6 组真机照片标定得到）：
    显示值 = table[i] / 1023 * 255
    i      = raw / W * 255,  W ≈ 1.04
所以反过来：table[i] = 目标显示(raw = i/255*W) * 1023 / 255

为防重演 S33 的「阴影压死」，底部 i<64 按比例向 stock 表回拉。
"""
import re, sys
import numpy as np

ROOT = r"G:\em10fw"
CUBE = ROOT + r"\.tmp\fujilut\cube lut\classic chrome_sRGB.cube"
STOCK_IMG = ROOT + r"\analysis\00_0100_40300020.decoded.bin"
BASE = 0x60021020
W = 1.04
STOCK_TABLE = 39          # 标定出来最能解释实测的那张
BLEND = 0.35              # 底部最多回拉 35% 到 stock

def oetf(x):
    x = np.clip(x, 0, 1)
    return 255 * np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055)

def parse_cube(path):
    size = None
    data = []
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith("lut_3d_size"):
            size = int(s.split()[-1]); continue
        if s.lower().startswith("domain"):
            continue
        parts = s.split()
        if len(parts) == 3:
            try:
                data.append([float(x) for x in parts])
            except ValueError:
                pass
    arr = np.array(data, np.float32)
    assert size and arr.shape[0] == size ** 3, (size, arr.shape)
    # .cube 数据 R 变化最快 → 转成 [r][g][b][3]
    return arr.reshape(size, size, size, 3).transpose(2, 1, 0, 3), size

def sample(cube, size, lin):
    x = np.clip(lin, 0, 1) * (size - 1)
    i0 = np.floor(x).astype(int); i1 = np.minimum(i0 + 1, size - 1)
    f = (x - i0)[:, None]
    return cube[i0, i0, i0] * (1 - f) + cube[i1, i1, i1] * f   # 灰度轴

cube, size = parse_cube(CUBE)
lin = np.arange(256) / 255.0 * W
disp = oetf(sample(cube, size, lin)[:, 1])          # 用 G 通道当亮度（灰度轴上 R=G=B）
disp = np.maximum.accumulate(disp)                  # 强制单调

img = open(STOCK_IMG, "rb").read()
off = 0x605D8344 + STOCK_TABLE * 0x200 - BASE
stock = np.frombuffer(img[off:off + 512], dtype="<u2").astype(float) / 1023 * 255

w = np.clip((64 - np.arange(256)) / 64.0, 0, 1) * BLEND
disp_b = disp * (1 - w) + stock * w
disp_b = np.maximum.accumulate(disp_b)

tab = np.round(np.clip(disp_b, 0, 255) / 255.0 * 1023).astype(int)
tab = np.maximum.accumulate(tab)

print("idx   raw     stock   CC     final")
for i in (0, 8, 16, 32, 48, 64, 96, 128, 160, 192, 224, 255):
    print(f"{i:4d}  {i/255*W:5.3f}  {stock[i]:6.1f} {disp[i]:6.1f} {disp_b[i]:6.1f}  -> {tab[i]:4d}")
print("monotonic:", bool(np.all(np.diff(tab) >= 0)), " endpoints:", tab[0], tab[-1])
open(ROOT + r"\.tmp\cc_tone_v4.txt", "w").write("\n".join(map(str, tab)))
print("written .tmp/cc_tone_v4.txt")
