"""任务 D：把 3x3 色彩矩阵接进离线渲染（相机链：WB -> 矩阵 -> tone 表）。

对比条：相机直出 | 只 tone | tone+矩阵(每个观感一条)
用法：把 ORF/JPG 写进 .tmp/render_targets.txt（第一行 ORF，第二行 JPG）；
     观感列表在 LOOKS。
"""
import sys, os, json
import numpy as np
import rawpy
from PIL import Image

ROOT = r"G:\em10fw"
BASE = 0x60021020
W = 1.04
LOOKS = ["pro neg std", "astia", "reala ace", "eterna"]


def load_table(name):
    t = np.array([int(x) for x in open(os.path.join(ROOT, ".tmp", name)).read().split()], float)
    return np.maximum.accumulate(t) / 1023.0


def load_matrix(look):
    d = json.load(open(ROOT + r"\.tmp\s37_matrices.json"))
    M = np.array(d[look], np.float32) * 256.0
    for r in range(3):                      # 行和强制 256
        M[r, 2] += 256.0 - M[r].sum()
    return M / 256.0                        # 归一（行和=1，灰不偏色）


def render(orf, curve, M=None, scale=1.0):
    with rawpy.imread(orf) as raw:
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                              gamma=(1, 1), output_bps=16)
    lin = rgb.astype(np.float32) / 65535.0 * scale
    if M is not None:
        lin = np.clip(lin.reshape(-1, 3) @ M.T, 0, 1).reshape(lin.shape)
    idx = np.clip(lin / W * 255.0, 0, 255)
    out = np.interp(idx, np.arange(256), curve)
    return (np.clip(out, 0, 1) * 255).astype(np.uint8)


def stats(a):
    y = a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114
    q = np.percentile(y, [1, 50, 95])
    mx = a.max(axis=-1).astype(np.float32); mn = a.min(axis=-1).astype(np.float32)
    sat = float(((mx - mn) / np.maximum(mx, 1)).mean())
    return [float(x) for x in q], sat


def strip(imgs, path, h=700, gap=6):
    ps = [im.resize((int(im.width * h / im.height), h), Image.LANCZOS) for im in imgs]
    c = Image.new("RGB", (sum(p.width for p in ps) + gap * (len(ps) - 1), h), (24, 24, 24))
    x = 0
    for p in ps:
        c.paste(p, (x, 0)); x += p.width + gap
    c.save(path, quality=92)
    print("saved", path, c.size)


if __name__ == "__main__":
    tgt = open(ROOT + r"\.tmp\render_targets.txt", encoding="utf-8").read().split()
    orf, jpg = tgt[0], tgt[1]
    tag = os.path.basename(orf).split(".")[0]
    curve = load_table("cc_tone_v6.txt")
    tiles = []
    cam = Image.open(jpg)
    from PIL import ImageOps
    cam = ImageOps.exif_transpose(cam).convert("RGB")
    tiles.append(cam)
    # 曝光匹配：让 tone 渲染的中位亮度对上相机 JPEG 的中位亮度
    cam_s = np.asarray(cam.resize((800, int(800*cam.height/cam.width)), Image.BOX), np.float32)
    cam_med = float(np.median(cam_s[...,0]*0.299+cam_s[...,1]*0.587+cam_s[...,2]*0.114))
    lo, hi = 0.2, 12.0
    for _ in range(14):
        mid = (lo*hi)**0.5
        r = render(orf, curve, None, mid)
        med = float(np.median(r[...,0]*0.299+r[...,1]*0.587+r[...,2]*0.114))
        if med < cam_med: lo = mid
        else: hi = mid
    scale = (lo*hi)**0.5
    print("exposure scale", round(scale,3))
    base = render(orf, curve, None, scale)
    tiles.append(Image.fromarray(base))
    q, s = stats(np.asarray(cam.resize((base.shape[1], base.shape[0]), Image.BOX)))
    print(f"cam   p1/p50/p95={['%.1f'%v for v in q]} sat={s:.3f}")
    q, s = stats(base)
    print(f"tone  p1/p50/p95={['%.1f'%v for v in q]} sat={s:.3f}")
    for lk in LOOKS:
        im = render(orf, curve, load_matrix(lk), scale)
        q, s = stats(im)
        print(f"{lk:<14} p1/p50/p95={['%.1f'%v for v in q]} sat={s:.3f}")
        tiles.append(Image.fromarray(im))
    os.makedirs(ROOT + r"\refs\preview\matrix", exist_ok=True)
    strip(tiles, ROOT + rf"\refs\preview\matrix\{tag}_matrix.jpg")
