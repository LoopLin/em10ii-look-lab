"""用一对 RAW/JPEG 拟合「线性相机 RGB → sRGB JPEG」渲染链。

模型: Y_srgb_linear = clip(M @ X_cam_linear), 再对每个通道做单调 tone curve.
用法: python tools/fit_pipeline.py --raw build/orf/p82_linear16.npy --jpg <P3215682.JPG> --out build/orf/p82
"""
import argparse
import numpy as np
from PIL import Image

XYZ2SRGB = np.array([[3.2406, -1.5372, -0.4986],
                     [-0.9689, 1.8758, 0.0415],
                     [0.0557, -0.2040, 1.0570]], dtype=np.float64)


def srgb_decode(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def srgb_encode(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)


def fit_curve(x, y, nb=96):
    """x 单调分箱取中位 y，返回单调化的 (xs, ys)。"""
    order = np.argsort(x)
    x, y = x[order], y[order]
    edges = np.linspace(0, 1, nb + 1)
    xs, ys = [], []
    for i in range(nb):
        lo, hi = edges[i], edges[i + 1]
        m = (x >= lo) & (x < hi) if i < nb - 1 else (x >= lo) & (x <= hi)
        if m.sum() >= 20:
            xs.append((lo + hi) / 2)
            ys.append(float(np.median(y[m])))
    xs, ys = np.array(xs), np.array(ys)
    ys = np.maximum.accumulate(ys)
    return xs, ys


def apply_curve(x, xs, ys):
    out = np.interp(np.clip(x, 0, 1), xs, ys)
    return np.clip(out, 0, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--raw', required=True)
    ap.add_argument('--jpg', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--cam2xyz', default=None)
    ap.add_argument('--wb', default='1.0,1.0,1.0', help='R,G,B gains applied before matrix')
    ap.add_argument('--no-matrix', action='store_true', help='linear data already sRGB')
    a = ap.parse_args()

    lin = np.load(a.raw).astype(np.float32) / 65535.0  # 3456x4608x3
    wb = np.array([float(t) for t in a.wb.split(',')], dtype=np.float32)
    lin = lin * wb
    jpg = Image.open(a.jpg).convert('RGB')
    jpg = jpg.resize((lin.shape[1], lin.shape[0]), Image.LANCZOS)
    j = np.asarray(jpg).astype(np.float32) / 255.0

    cam2xyz = np.array([[0.8380, -0.2630, -0.0639],
                        [-0.2887, 1.0725, 0.2496],
                        [-0.0627, 0.1427, 0.5438]])
    if a.cam2xyz:
        cam2xyz = np.loadtxt(a.cam2xyz)
    M = np.eye(3) if a.no_matrix else XYZ2SRGB @ cam2xyz

    n = lin.shape[0] * lin.shape[1]
    rng = np.random.default_rng(1)
    idx = rng.choice(n, min(250000, n), replace=False)
    L = lin.reshape(-1, 3)[idx]
    J = srgb_decode(j.reshape(-1, 3)[idx])
    X = np.clip(L @ M.T, 0, 1)

    curves = {}
    for ci, name in enumerate('RGB'):
        xs, ys = fit_curve(X[:, ci], J[:, ci])
        curves[name] = (xs, ys)
        print(f'{name}: {len(xs)} pts, residual {np.abs(apply_curve(X[:, ci], xs, ys) - J[:, ci]).mean():.4f}')

    # 全图渲染（按块，避免内存峰值）
    out = np.empty_like(lin)
    for y0 in range(0, lin.shape[0], 256):
        blk = lin[y0:y0 + 256]
        Xb = np.clip(blk @ M.T, 0, 1)
        for ci, name in enumerate('RGB'):
            xs, ys = curves[name]
            out[y0:y0 + 256, :, ci] = apply_curve(Xb[..., ci], xs, ys)
    out8 = (srgb_encode(out) * 255 + 0.5).astype(np.uint8)
    small = np.ascontiguousarray(out8[::4, ::4])
    Image.fromarray(small).save(a.out + '_fit.jpg', quality=92)
    np.savez(a.out + '_fit.npz', matrix=M, R_x=curves['R'][0], R_y=curves['R'][1],
             G_x=curves['G'][0], G_y=curves['G'][1], B_x=curves['B'][0], B_y=curves['B'][1])
    print('written', a.out + '_fit.jpg', small.shape)


if __name__ == '__main__':
    main()
