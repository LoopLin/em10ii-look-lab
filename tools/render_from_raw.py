"""从 RAW 渲染候选曲线（不做 LUT-on-JPEG），并与相机实际 JPEG 对比。

相机链（据标定）：raw_linear → WB → tone 表 → 挡位标量 → JPEG
这里：raw_linear(camera WB) → 逐通道 tone 表 → sRGB 编码输出。
如果模型对，渲染结果应该接近相机 JPEG —— 这同时是模型的验证。
"""
import sys, os
import numpy as np
import rawpy
from PIL import Image

ROOT = r"G:\em10fw"
IMG = open(ROOT + r"\analysis\00_0100_40300020.decoded.bin", "rb").read()
BASE = 0x60021020
W = 1.04

def load_table(name):
    t = np.array([int(x) for x in open(os.path.join(ROOT, ".tmp", name)).read().split()], float)
    return np.maximum.accumulate(t) / 1023.0          # 0..1

def render(orf, curve):
    with rawpy.imread(orf) as raw:
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                              gamma=(1, 1), output_bps=16)
    lin = rgb.astype(np.float32) / 65535.0
    idx = np.clip(lin / W * 255.0, 0, 255)
    out = np.interp(idx, np.arange(256), curve)        # 0..1 显示值
    return (np.clip(out, 0, 1) * 255).astype(np.uint8), lin

def stats(a):
    y = a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114
    q = np.percentile(y, [1, 5, 50, 95, 99])
    mx = a.max(axis=-1); mn = a.min(axis=-1)
    sat = float(((mx - mn) / np.maximum(mx, 1)).mean())
    return [float(x) for x in q], sat

def strip(imgs, path, h=700, gap=6):
    ps = []
    for im in imgs:
        w = int(im.width * h / im.height)
        ps.append(im.resize((w, h), Image.LANCZOS))
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
    out, _ = render(orf, load_table("cc_tone_v6.txt"))
    cam = Image.open(jpg).convert("RGB")
    cam_small = cam.resize((out.shape[1], out.shape[0]), Image.BOX)
    cam_np = np.asarray(cam_small, np.uint8)
    mj, sj = stats(cam_np)
    mo, so = stats(out)
    print(f"{tag}  相机 JPEG   p1/p5/p50/p95/p99 = {['%.1f'%x for x in mj]}  sat={sj:.3f}")
    print(f"{tag}  模型渲染   p1/p5/p50/p95/p99 = {['%.1f'%x for x in mo]}  sat={so:.3f}")
    print("  逐像素 |ΔY| 中位 =", round(float(np.median(np.abs(
        (cam_np[...,0]*0.299+cam_np[...,1]*0.587+cam_np[...,2]*0.114) -
        (out[...,0]*0.299+out[...,1]*0.587+out[...,2]*0.114)))), 1))
    os.makedirs(ROOT + r"\refs\preview", exist_ok=True)
    strip([cam, Image.fromarray(out)], ROOT + rf"\refs\preview\{tag}_model_vs_cam.jpg")
