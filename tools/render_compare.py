"""把 ORF 用三条流水线各渲一遍：原厂近似 / 富士 LUT / 我们的补丁。

三条都用同一份 raw 线性数据，保证可比。
"""
import json
import os
import sys
import subprocess

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = r"G:/Sovits/so-vits-svc/ffmpeg/bin/ffmpeg.exe"
X_WHITE = 0.527
SPEC = os.path.join(ROOT, ".tmp", "fit_cc_spec.json")
T_CSV = os.path.join(ROOT, ".tmp", "tone_T5.csv")
CUBE = os.path.join(ROOT, ".tmp", "fujilut", "cube lut", "classic chrome_sRGB.cube")


def oetf(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def eotf(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4))


def load_t():
    d = np.loadtxt(T_CSV, delimiter=",", skiprows=1)
    tx, ty = d[:, 0], d[:, 1]
    ok = ty > 2.5
    return tx[ok], ty[ok]


def T_of(v, tx, ty):
    v = np.clip(v, tx[0], tx[-1])
    return np.interp(np.log(v), np.log(tx), ty)


def T_inv(j, tx, ty):
    j = np.clip(j, ty[0], ty[-1])
    order = np.argsort(ty)
    return np.exp(np.interp(j, ty[order], np.log(tx[order])))


def load_cube():
    with open(CUBE, "r", encoding="utf-8", errors="ignore") as f:
        size, data = None, []
        for line in f:
            s = line.strip()
            if s.upper().startswith("LUT_3D_SIZE"):
                size = int(s.split()[-1])
            elif s and s[0] not in "#" and not s[0].isalpha():
                p = s.split()
                if len(p) == 3:
                    data.append([float(v) for v in p])
    arr = np.array(data, float).reshape(size, size, size, 3).transpose(2, 1, 0, 3)
    return arr, size


def flog2_encode(x):
    """线性 0..1 → F-Log2 编码 0..1。"""
    a, b, g = 0.245281, 0.384274, 0.29
    cut1 = 0.100686685370811
    slope = (a * cut1 ** g + b - 0.092864) / cut1
    x = np.clip(x, 0.0, 1.0)
    return np.where(x > cut1, a * np.power(np.maximum(x, 1e-8), g) + b,
                    0.092864 + slope * x)


def apply_cube(arr, size, rgb):
    p = np.clip(rgb, 0, 1) * (size - 1)
    i0 = np.floor(p).astype(int)
    i1 = np.minimum(i0 + 1, size - 1)
    f = p - i0
    out = np.zeros_like(rgb)
    for dx in (0, 1):
        wx = f[..., 0] if dx else 1 - f[..., 0]
        for dy in (0, 1):
            wy = f[..., 1] if dy else 1 - f[..., 1]
            for dz in (0, 1):
                wz = f[..., 2] if dz else 1 - f[..., 2]
                ix = i1[..., 0] if dx else i0[..., 0]
                iy = i1[..., 1] if dy else i0[..., 1]
                iz = i1[..., 2] if dz else i0[..., 2]
                out += (wx * wy * wz)[..., None] * arr[ix, iy, iz]
    return out


def ycbcr(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = (b - y) * 0.564 + 0.5
    cr = (r - y) * 0.713 + 0.5
    return y, cb, cr


def from_ycbcr(y, cb, cr):
    cb = cb - 0.5
    cr = cr - 0.5
    r = y + 1.402 * cr
    g = y - 0.344136 * cb - 0.714136 * cr
    b = y + 1.772 * cb
    return np.clip(np.stack([r, g, b], -1), 0, 1)


def decode_to(path, w, h):
    """用 ffmpeg 把任意图片解成 w*h 的 float32 RGB（0..1），失败返回 None。"""
    try:
        out = subprocess.run([FFMPEG, "-v", "error", "-i", path,
                              "-vf", f"scale={w}:{h}:flags=bilinear",
                              "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        arr = np.frombuffer(out, np.uint8)
        if arr.size != w * h * 3:
            return None
        return arr.reshape(h, w, 3).astype(np.float32) / 255.0
    except Exception as e:
        print("  jpg decode failed:", e)
        return None


def main():
    import rawpy

    spec = json.load(open(SPEC, encoding="utf-8"))
    tab = np.array(spec["tone"], float) / 1023.0
    M = np.array(spec["matrix"], float)
    six = np.array(spec["sixaxis"], float)          # (a, b) × 6
    tx, ty = load_t()
    cube, csize = load_cube()
    u = np.linspace(0, 1, 256)

    args = []
    for a in sys.argv[1:]:
        if a.startswith("@"):
            with open(a[1:], "r", encoding="utf-8") as f:
                args += [ln.strip() for ln in f if ln.strip()]
        else:
            args.append(a)
    paths = [a for a in args if a.lower().endswith(".orf")]
    outdir = os.path.join(ROOT, "refs", "fuji-compare")
    os.makedirs(outdir, exist_ok=True)

    for p in paths:
        name = os.path.splitext(os.path.basename(p))[0]
        cache = os.path.join(ROOT, ".tmp", "lin_" + name + ".npy")
        if os.path.exists(cache):
            lin = np.load(cache)
        else:
            with rawpy.imread(p) as raw:
                lin = np.asarray(raw.postprocess(
                    use_camera_wb=True, no_auto_bright=True, output_bps=16,
                    half_size=True, gamma=(1, 1),
                    output_color=rawpy.ColorSpace.sRGB), np.float32) / 65535.0
            np.save(cache, lin)

        # 左格用相机自己的直出 JPG（同目录同名）
        jpg = os.path.splitext(p)[0] + ".JPG"
        if not os.path.exists(jpg):
            jpg = os.path.splitext(p)[0] + ".jpg"
        h0, w0 = lin.shape[:2]
        base = decode_to(jpg, w0, h0) if os.path.exists(jpg) else None
        if base is None:
            base = oetf(T_of(np.clip(lin, 0, 1), tx, ty) / 255.0)

        def render_fuji(s):
            # 这批 cube 是 F-Log2 输入、显示输出
            return np.clip(apply_cube(cube, csize, flog2_encode(np.clip(lin * s, 0, 1))), 0, 1)

        def render_ours(s):
            v_norm = np.clip(lin * s / X_WHITE, 0, 1)
            d = T_of(np.interp(v_norm, u, tab) * X_WHITE, tx, ty) / 255.0
            d = np.clip(np.einsum("ij,...j->...i", M, d), 0, 1)
            y, cb, cr = ycbcr(d)
            hh = (np.degrees(np.arctan2(cr - 0.5, cb - 0.5)) + 360.0) % 360.0
            s6 = (hh // 60).astype(int) % 6
            a, b = six[s6, 0], six[s6, 1]
            cb2 = a * (cb - 0.5) - b * (cr - 0.5) + 0.5
            cr2 = b * (cb - 0.5) + a * (cr - 0.5) + 0.5
            return from_ycbcr(y, cb2, cr2)

        def med(img):
            return float(np.median(0.299 * img[..., 0] + 0.587 * img[..., 1]))

        target = med(base)

        def fit_scale(fn):
            lo, hi = 0.05, 8.0
            for _ in range(10):
                mid = (lo * hi) ** 0.5
                if med(fn(mid)) < target:
                    lo = mid
                else:
                    hi = mid
            s = (lo * hi) ** 0.5
            return fn(s), s

        fuji, sf = fit_scale(render_fuji)
        ours, so = fit_scale(render_ours)
        print(f"  scale fuji={sf:.3f} ours={so:.3f}")

        panels = [base, fuji, ours]
        sheet = np.concatenate([np.pad(x, ((0, 0), (0, 8), (0, 0)), constant_values=1.0)
                                for x in panels], axis=1)
        h, w = sheet.shape[:2]
        scale = min(1.0, 2400.0 / w)
        if scale < 1.0:
            step = int(round(1 / scale))
            sheet = sheet[::step, ::step]
        h, w = sheet.shape[:2]
        buf = (np.clip(sheet, 0, 1) * 255).astype(np.uint8).tobytes()
        dst = os.path.join(outdir, f"{name}_3up.jpg")
        subprocess.run([FFMPEG, "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                        "-s", f"{w}x{h}", "-i", "-", "-q:v", "3", "-y", dst],
                       input=buf, capture_output=True)
        print(f"{name}: {w}x{h} -> {dst}")


if __name__ == "__main__":
    main()
