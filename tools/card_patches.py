#!/usr/bin/env python3
"""从平板标板照片里按透视变换取样每个色块，输出相对白块归一化后的色度。

用法:
    python tools/card_patches.py <photo1.JPG> [<photo2.JPG> ...] [--src G:/em10fw/colorcard_tablet_2560x1440.png]

设计:
  - 标板的色块位置从源 PNG 里自动检测（不规则背景上找矩形色块），不写死坐标；
  - 照片里先用 tools/screen_curve.largest_quad 找屏幕四角，做单应变换映射到源坐标；
  - 每块取中心 40% 区域的中位数颜色，再除以白块（P19）的亮度做归一化，
    这样两次拍摄的曝光差异会被消掉大半。
"""
import sys
import numpy as np
from PIL import Image

sys.path.insert(0, "tools")
from screen_curve import largest_quad, homography, apply_h  # noqa: E402

BG = 245.0


def detect_patches(path):
    """返回 [(cx, cy, w, h, rgb)]，按行优先。"""
    im = Image.open(path).convert("RGB")
    a = np.asarray(im, dtype=np.float32)
    # 背景是浅灰白；色块要么暗、要么饱和
    mx = a.max(2)
    mn = a.min(2)
    mask = (mx < BG - 6) | ((mx - mn) > 12)
    rows = mask.sum(1) > a.shape[1] * 0.02
    bands = []
    y = 0
    while y < len(rows):
        if rows[y]:
            y0 = y
            while y < len(rows) and rows[y]:
                y += 1
            bands.append((y0, y))
        else:
            y += 1
    out = []
    for (y0, y1) in bands:
        if y1 - y0 < 40:
            continue
        cols = mask[y0:y1].sum(0) > (y1 - y0) * 0.5
        x = 0
        while x < len(cols):
            if cols[x]:
                x0 = x
                while x < len(cols) and cols[x]:
                    x += 1
                if x - x0 < 40:
                    continue
                cx, cy = (x0 + x) // 2, (y0 + y1) // 2
                rgb = a[y0:y1, x0:x].reshape(-1, 3).mean(0)
                out.append((cx, cy, x - x0, y1 - y0, rgb.round(1)))
            else:
                x += 1
    return out


def warp_to_source(photo, src_w, src_h):
    """把照片透视校正到源标板坐标系，返回 uint8 (src_h, src_w, 3)。"""
    g = np.asarray(Image.open(photo).convert("L").resize((1200, 900)), dtype=np.float32)
    quad = largest_quad(g)
    if quad is None:
        raise SystemExit(f"{photo}: 找不到屏幕")
    q = np.array(quad, dtype=np.float64) * 1.92          # 1200x900 -> 2304x1728
    src = np.array([(0, 0), (src_w - 1, 0), (src_w - 1, src_h - 1), (0, src_h - 1)], dtype=np.float64)
    A, b = [], []
    for (x, y), (u, v) in zip(q, src):                    # 输出(src) -> 输入(photo)
        A.append([u, v, 1, 0, 0, 0, -x * u, -x * v]); b.append(x)
        A.append([0, 0, 0, u, v, 1, -y * u, -y * v]); b.append(y)
    coef = np.linalg.solve(np.array(A, float), np.array(b, float))
    im = Image.open(photo)
    im.draft("RGB", (2304, 1728))
    a = im.convert("RGB").resize((2304, 1728))
    return np.asarray(a.transform((src_w, src_h), Image.PERSPECTIVE, tuple(coef), Image.BICUBIC), dtype=np.float32)


def sample(photo, src_w, src_h, patches):
    a = warp_to_source(photo, src_w, src_h)
    res = []
    for (cx, cy, w, h, _rgb) in patches:
        r = max(4, int(min(w, h) * 0.2))
        win = a[cy - r:cy + r, cx - r:cx + r].reshape(-1, 3)
        res.append(np.median(win, 0))
    return np.array(res)


def report(names, cols):
    print(f"{'patch':10s}" + "".join(f"{n:>26s}" for n in names))
    for i, c in enumerate(cols[0]):
        line = f"P{i+1:02d}  "
        for arr in cols:
            r, g, b = arr[i]
            m = max(r, g, b)
            s = (m - min(r, g, b)) / max(m, 1)
            line += f"  {r:5.0f}/{g:5.0f}/{b:5.0f} S={s:.2f}"
        print(line)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src = "G:/em10fw/colorcard_tablet_2560x1440.png"
    for i, a in enumerate(sys.argv):
        if a == "--src":
            src = sys.argv[i + 1]
    patches = detect_patches(src)
    print(f"源标板检测到 {len(patches)} 块")
    cols = []
    for p in args:
        cols.append(sample(p, 2560, 1440, patches))
    report([p.split("/")[-1] for p in args], cols)


if __name__ == "__main__":
    main()
