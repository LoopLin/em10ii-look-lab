"""两端去饱和：照 handoff-notes 的量化结论，高光/暗部都往中性拉。

链：raw -> (矩阵) -> tone 表 -> 亮度分离的双端去饱和 -> sRGB 编码
用法：python tools/look_ends_desat.py <ORF> <JPG>
输出：refs/preview/desat/<name>_desat.jpg（三栏：相机 | 旧 | 新）
"""
import sys, os, json
import numpy as np
import rawpy
from PIL import Image, ImageOps

ROOT = r"G:\em10fw"
W = 1.04


def load_curve(name):
    t = np.array([int(x) for x in open(os.path.join(ROOT, ".tmp", name)).read().split()], float)
    return np.maximum.accumulate(t) / 1023.0


def load_matrix(look):
    d = json.load(open(ROOT + r"\.tmp\s37_matrices.json"))
    M = np.array(d[look], np.float32) * 256.0
    for r in range(3):
        M[r, 2] += 256.0 - M[r].sum()
    return M / 256.0


def to8(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055) * 255


def rgb2ycc(a):
    y = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    cb = 0.564 * (a[..., 2] - y)
    cr = 0.713 * (a[..., 0] - y)
    return y, cb, cr


def ycc2rgb(y, cb, cr):
    return np.stack([y + 1.402 * cr, y - 0.344136 * cb - 0.714136 * cr, y + 1.772 * cb], -1)


def desat_ends(disp, hi_amt, hi_start, sh_amt, sh_end):
    """disp: 0..1 显示域；按亮度两端把彩度压向中性。"""
    y, cb, cr = rgb2ycc(disp)
    # 高光：从 hi_start 起到 1.0 线性增强
    t_hi = np.clip((y - hi_start) / max(1e-6, 1.0 - hi_start), 0, 1)
    # 暗部：从 0 到 sh_end 线性减弱
    t_sh = np.clip((sh_end - y) / max(1e-6, sh_end), 0, 1)
    k = 1.0 - hi_amt * t_hi - sh_amt * t_sh
    k = np.clip(k, 0.0, 1.0)
    return ycc2rgb(y, cb * k, cr * k)


def render(orf, curve, M=None, scale=1.0):
    with rawpy.imread(orf) as raw:
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True,
                              gamma=(1, 1), output_bps=16)
    lin = rgb.astype(np.float32) / 65535.0 * scale
    if M is not None:
        lin = np.clip(lin.reshape(-1, 3) @ M.T, 0, 1).reshape(lin.shape)
    idx = np.clip(lin / W * 255.0, 0, 255)
    return np.interp(idx, np.arange(256), curve)


def main():
    orf = sys.argv[1]
    jpg = sys.argv[2] if len(sys.argv) > 2 and os.path.exists(sys.argv[2]) else None
    name = os.path.splitext(os.path.basename(orf))[0]
    curve = load_curve("cc_tone_v6.txt")
    M = load_matrix("pro neg std")

    lin_out = render(orf, curve, M)
    disp = np.clip(lin_out, 0, 1)

    old = to8(disp)
    new = to8(desat_ends(disp, hi_amt=0.75, hi_start=0.72, sh_amt=0.45, sh_end=0.22))

    tiles = []
    if jpg:
        cam = ImageOps.exif_transpose(Image.open(jpg)).convert("RGB")
        tiles.append((cam, "camera"))
    tiles.append((Image.fromarray(old.astype(np.uint8)), "old"))
    tiles.append((Image.fromarray(new.astype(np.uint8)), "ends-desat"))

    h = 700
    ims = [im.resize((int(im.width * h / im.height), h), Image.LANCZOS) for im, _ in tiles]
    canvas = Image.new("RGB", (sum(i.width for i in ims) + 6 * (len(ims) - 1), h), (24, 24, 24))
    x = 0
    for i in ims:
        canvas.paste(i, (x, 0))
        x += i.width + 6
    outdir = ROOT + r"\refs\preview\desat"
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, name + "_desat.jpg")
    canvas.save(p, quality=92)
    print("saved", p, canvas.size)

    for lab, im in (("old", old), ("new", new)):
        y, cb, cr = rgb2ycc(np.asarray(im, np.float32) / 255.0)
        for lo, hi in ((0, 0.235), (0.235, 0.47), (0.47, 0.71), (0.71, 0.88), (0.88, 1.01)):
            m = (y >= lo) & (y < hi)
            if m.sum() > 500:
                s = np.sqrt(cb[m] ** 2 + cr[m] ** 2).mean()
                print(f"  {lab} Y[{lo:.2f},{hi:.2f}) sat={s:.3f} n={m.sum()}")


if __name__ == "__main__":
    main()
