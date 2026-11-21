"""把富士 LUT 的观感翻译成我们固件能用的参数（按已知色块拟合，不走照片）。

为什么按色块拟合：
    我们手里有标板的**已知颜色**（21 级灰阶 + 24 色块，生成时写死的 sRGB 值）。
    富士 LUT 是「线性光进 → 10 位线性出」，所以：
        目标显示 = T_srgb( LUT_lin( linear(色块 sRGB) ) )
    我们的相机：      显示 = T_cam( tone表( sensor线性 ) )，sensor 白点 = x_white
    令两者相等 ⇒ tone表(v) = 1023 · T_cam⁻¹(目标显示) / x_white
    这样就绕开了「两条流水线的线性刻度相差多少」这个未知量——用 T_cam 自己当标尺。

输出：
    <out>_tone.txt     256 项（10 位），打进 44 张 tone 表
    <out>_chroma.csv   Y, 增益（16 点，喂 Cb/Cr 曲线）
    <out>_preview.jpg  标板三联：原图 / 富士 / 我们的拟合
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lut_analyze import parse_cube, apply_lut
from lut_fit import eotf, oetf, ycbcr, from_ycbcr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T_CSV = os.path.join(ROOT, ".tmp", "tone_T5.csv")
FFMPEG = "G:/Sovits/so-vits-svc/ffmpeg/bin/ffmpeg.exe"

# 标板：21 级灰阶 + 24 色块（与 colorcard 生成脚本一致）
GRAYS = [round(i * 255 / 20) for i in range(21)]
PATCHES = [
    (115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67), (133, 128, 177), (103, 189, 170),
    (214, 126, 44), (80, 91, 166), (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
    (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31), (187, 86, 149), (8, 133, 161),
    (243, 243, 242), (200, 200, 200), (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52),
]


def load_T():
    d = np.loadtxt(T_CSV, delimiter=",", skiprows=1)
    x, y = d[:, 0], d[:, 1]
    ok = y > 2.5
    return x[ok], y[ok]


def T_of(raw, tx, ty):
    return np.interp(np.log(np.clip(raw, tx[0], tx[-1])), np.log(tx), ty)


def T_inv(jpeg, tx, ty):
    order = np.argsort(ty)
    return np.exp(np.interp(jpeg, ty[order], np.log(tx[order])))


def lut_disp(lut, size, srgb_patches):
    """sRGB 色块 → 富士 LUT → sRGB 显示值 0..255"""
    lin = eotf(srgb_patches / 255.0)
    out = apply_lut(lut, size, lin)
    return oetf(out) * 255.0


def main(argv=None):
    cfg = {}
    p = os.path.join(ROOT, ".tmp", "fuji_fit_cfg.json")
    if os.path.exists(p):
        cfg = json.load(open(p, encoding="utf-8"))
    ap = argparse.ArgumentParser()
    ap.add_argument("cube", nargs="?", default=cfg.get("cube"))
    ap.add_argument("--out", default=cfg.get("out", ".tmp/fuji_fit"))
    a, _u = ap.parse_known_args([] if cfg else argv)
    if not a.cube:
        print("need .cube"); return 2

    lut, size = parse_cube(a.cube)
    tx, ty = load_T()
    x_white = tx[-1]
    name = os.path.basename(a.cube)
    print(f"# {name}  {size}^3")

    # ---- 1) 灰阶 → tone 表 ----
    g = np.array([[v, v, v] for v in GRAYS], float)
    tgt_g = lut_disp(lut, size, g)[:, 0]                 # 富士给这些灰阶的显示值
    v_lin = eotf(np.array(GRAYS) / 255.0)                # 输入线性
    need = T_inv(tgt_g, tx, ty)                          # 我们需要的传感器值
    tab = np.round(np.clip(need / x_white, 0, 1) * 1023).astype(int)
    # 用灰阶点做单调回归（累积最大 + 线性插值补点）
    xs = np.concatenate([[0.0], v_lin, [1.0]])
    ys = np.concatenate([[tab[0]], tab, [tab[-1]]])
    keep = np.concatenate([[True], np.diff(xs) > 0])
    xs, ys = xs[keep], ys[keep]
    u = np.linspace(0, 1, 256)
    tab256 = np.interp(u, xs, ys)
    tab256 = np.maximum.accumulate(np.round(tab256).astype(int))
    print("\n灰阶：输入线性 → 富士显示 → 我们表值")
    for i in range(0, 21, 4):
        print(f"  {v_lin[i]:.4f}  {tgt_g[i]:6.1f}  {tab[i]:5d}")
    print(f"tone 表端点 {tab256[0]}..{tab256[-1]}  单调 {bool(np.all(np.diff(tab256) >= 0))}")

    # ---- 2) 色块：先过 tone，再算还差多少色度 ----
    pch = np.array(PATCHES, float)

    def tone_only(patches):
        lin = eotf(patches / 255.0)
        r = np.interp(lin, u, tab256 / 1023.0) * x_white
        return T_of(r, tx, ty)

    tgt_p = lut_disp(lut, size, pch)
    tone_p = tone_only(pch)
    y_s, cb_s, cr_s = ycbcr(tone_p / 255.0)
    y_t, cb_t, cr_t = ycbcr(tgt_p / 255.0)
    c_s = np.hypot(cb_s, cr_s)
    c_t = np.hypot(cb_t, cr_t)
    gain = np.where(c_s > 0.01, c_t / np.maximum(c_s, 1e-6), 1.0)
    print("\n色块（前 8 个）：sRGB → 富士，色度增益")
    for i in range(8):
        print(f"  P{i+1:02d}  {tuple(int(x) for x in pch[i])} → "
              f"{tuple(int(x) for x in tgt_p[i])}  C {c_s[i]:.3f}→{c_t[i]:.3f}  ×{gain[i]:.2f}")

    # 按亮度分箱的增益曲线（16 点，喂 Cb/Cr 曲线）
    order = np.argsort(y_s)
    ys_, gs_ = y_s[order], gain[order]
    grid = np.linspace(0, 1, 16)
    gcurve = np.interp(grid, ys_, gs_)
    np.savetxt(a.out + "_chroma.csv", np.c_[grid, gcurve], delimiter=",",
               header="Y,gain", comments="", fmt="%.4f")

    # ---- 2b) 六个色相带的饱和增益 / 色相偏移（给六轴用） ----
    def hue_deg(cb, cr):
        return (np.degrees(np.arctan2(cr, cb)) + 360.0) % 360.0

    h_s = hue_deg(cb_s, cr_s)
    h_t = hue_deg(cb_t, cr_t)
    dh = (h_t - h_s + 180.0) % 360.0 - 180.0
    band = (h_s // 60).astype(int) % 6
    print("\n[六轴] 色相带  样本  饱和增益中位  色相偏移中位(度)")
    six = []
    for b in range(6):
        sel = (band == b) & (c_t > 0.05)   # 低色度块色相不稳定，排除
        if sel.sum() < 2:
            six.append((1.0, 0.0))
            continue
        g = float(np.median(gain[sel]))
        d = float(np.median(dh[sel]))
        six.append((g, d))
        print(f"  带{b}  {int(sel.sum()):3d}      ×{g:.2f}        {d:+.1f}°")


    # ---- 3b) 3x3 矩阵最小二乘（灰阶加权，保证中性不被染色） ----
    grays_arr = np.array([[v, v, v] for v in GRAYS], float)
    allp = np.concatenate([grays_arr, pch], axis=0)
    tone_all = tone_only(allp) / 255.0
    tgt_all = lut_disp(lut, size, allp) / 255.0
    w = np.concatenate([np.full(len(grays_arr) * 3, 1.0), np.ones(len(pch))])
    A = np.concatenate([np.tile(tone_all[:len(grays_arr)], (3, 1)), tone_all[len(grays_arr):]], axis=0)
    B = np.concatenate([np.tile(tgt_all[:len(grays_arr)], (3, 1)), tgt_all[len(grays_arr):]], axis=0)
    M, *_ = np.linalg.lstsq(A, B, rcond=None)
    M = M.T
    pred_m = np.clip((M @ tone_all.T).T, 0, 1) * 255.0
    err_m = np.abs(pred_m - tgt_all * 255.0)
    print(f"\n[3x3 矩阵·灰阶加权] 48 块均值 {err_m.mean():.1f} / p90 {np.percentile(err_m, 90):.1f}"
          f"；灰阶 {err_m[:len(grays_arr)].mean():.1f}，色块 {err_m[len(grays_arr):].mean():.1f}")
    print("  M =")
    for row in M:
        print("   ", " ".join(f"{v:7.4f}" for v in row))
    print("  行和 =", " ".join(f"{r.sum():.4f}" for r in M))

    def simulate_M(patches):
        t = tone_only(patches) / 255.0
        return np.clip((M @ t.T).T, 0, 1)

    strip_all = np.concatenate([np.array([[v, v, v] for v in GRAYS], float), pch], axis=0)
    e3 = np.abs(simulate_M(strip_all) - lut_disp(lut, size, strip_all) / 255.0) * 255
    print(f"[tone+3x3] 48 块均值 {e3.mean():.1f} / p90 {np.percentile(e3, 90):.1f}"
          f"；灰阶 {e3[:len(GRAYS)].mean():.1f}，色块 {e3[len(GRAYS):].mean():.1f}")

    # ---- 3c) 12 扇区 HSL：每扇区按通道增益（对应固件里 12×3×256 的表） ----
    hsv_after_M = np.clip((M @ tone_all.T).T, 0, 1) * 255.0
    hh_all = np.degrees(np.arctan2(*(lambda a: (
        0.5 * a[:, 2] - 0.418688 * a[:, 1] - 0.081312 * a[:, 0],
        0.5 * a[:, 0] - 0.168736 * a[:, 1] - 0.331264 * a[:, 2]))(hsv_after_M)[::-1]))
    hh_all = (hh_all + 360.0) % 360.0
    sec_all = (hh_all // 30).astype(int) % 12
    tgt_all255 = tgt_all * 255.0
    G12 = np.ones((12, 3))
    for s in range(12):
        sel = sec_all == s
        if sel.sum() == 0:
            continue
        num = (hsv_after_M[sel] * tgt_all255[sel]).sum(axis=0)
        den = (hsv_after_M[sel] ** 2).sum(axis=0)
        G12[s] = np.clip(num / np.maximum(den, 1e-6), 0.7, 1.4)
    # 没样本的扇区用邻居插值，保证曲线连续
    for s in range(12):
        if (sec_all == s).sum() == 0:
            prev, nxt = (s - 1) % 12, (s + 1) % 12
            while (sec_all == prev).sum() == 0:
                prev = (prev - 1) % 12
            while (sec_all == nxt).sum() == 0:
                nxt = (nxt + 1) % 12
            G12[s] = (G12[prev] + G12[nxt]) / 2.0

    def simulate_hsl(patches):
        d = np.clip((M @ (tone_only(patches) / 255.0).T).T, 0, 1) * 255.0
        y, cb, cr = ycbcr(d / 255.0)
        hh = (np.degrees(np.arctan2(cr, cb)) + 360.0) % 360.0
        sec = (hh // 30).astype(int) % 12
        w = np.clip((np.hypot(cb, cr) - 0.02) / 0.03, 0, 1)[:, None]   # 近中性不参与
        g = 1.0 + w * (G12[sec] - 1.0)
        return np.clip(d * g, 0, 255) / 255.0

    pred_h = simulate_hsl(strip_all) * 255.0
    e4 = np.abs(pred_h - tgt_all255)
    print(f"[tone+3x3+12扇区] 48 块均值 {e4.mean():.1f} / p90 {np.percentile(e4, 90):.1f}"
          f"；灰阶 {e4[:len(grays_arr)].mean():.1f}，色块 {e4[len(grays_arr):].mean():.1f}")
    print("  扇区增益（30° 一段，R/G/B）:")
    for s in range(12):
        print(f"   {s*30:3d}°  " + " ".join(f"{v:5.3f}" for v in G12[s]))

    # ---- 3d) 六轴：每带一个 2x2（缩放+旋转）最小二乘 ----
    d_all = np.clip((M @ tone_all.T).T, 0, 1) * 255.0
    y_d, cb_d, cr_d = ycbcr(d_all / 255.0)
    y_t2, cb_t2, cr_t2 = ycbcr(tgt_all)
    hh_d = (np.degrees(np.arctan2(cr_d, cb_d)) + 360.0) % 360.0
    sec6 = (hh_d // 60).astype(int) % 6
    six_ls = np.array([[1.0, 0.0, 0.0]] * 6)      # a, b, 1  (x'=a x - b y, y'=b x + a y)
    for s in range(6):
        sel = (sec6 == s) & (np.hypot(cb_t2, cr_t2) > 0.04)
        if sel.sum() < 2:
            continue
        X = np.zeros((2 * sel.sum(), 2)); Y = np.zeros(2 * sel.sum())
        X[0::2, 0] = cb_d[sel]; X[0::2, 1] = -cr_d[sel]; Y[0::2] = cb_t2[sel]
        X[1::2, 0] = cr_d[sel]; X[1::2, 1] = cb_d[sel];  Y[1::2] = cr_t2[sel]
        ab, *_ = np.linalg.lstsq(X, Y, rcond=None)
        six_ls[s] = (ab[0], ab[1], 1.0)

    def simulate_six(patches):
        d = np.clip((M @ (tone_only(patches) / 255.0).T).T, 0, 1) * 255.0
        y, cb, cr = ycbcr(d / 255.0)
        hh = (np.degrees(np.arctan2(cr, cb)) + 360.0) % 360.0
        s6 = (hh // 60).astype(int) % 6
        a = six_ls[s6, 0]; b = six_ls[s6, 1]
        cb2 = a * cb - b * cr
        cr2 = b * cb + a * cr
        return from_ycbcr(y, cb2, cr2)

    pred_s = simulate_six(strip_all) * 255.0
    e5 = np.abs(pred_s - tgt_all255)
    print(f"[tone+3x3+六轴2x2] 48 块均值 {e5.mean():.1f} / p90 {np.percentile(e5, 90):.1f}"
          f"；灰阶 {e5[:len(grays_arr)].mean():.1f}，色块 {e5[len(grays_arr):].mean():.1f}")
    print("  六轴（a,b）:", "  ".join(
        f"{s}:{a[0]:.2f},{a[1]:+.2f}" for s, a in enumerate(six_ls)))

    # ---- 3) 标板三联预览 + 误差 ----
    def simulate(patches, six=None):
        disp = tone_only(patches)
        yy, cb, cr = ycbcr(disp / 255.0)
        if six is None:
            gg = np.interp(yy, grid, gcurve)
            cb, cr = cb * gg, cr * gg
        else:
            hh = (np.degrees(np.arctan2(cr, cb)) + 360.0) % 360.0
            bnd = (hh // 60).astype(int) % 6
            gains = np.array([s[0] for s in six])[bnd]
            shifts = np.radians(np.array([s[1] for s in six])[bnd])
            cb2 = cb * np.cos(shifts) - cr * np.sin(shifts)
            cr2 = cb * np.sin(shifts) + cr * np.cos(shifts)
            cb, cr = cb2 * gains, cr2 * gains
        return from_ycbcr(yy, cb, cr)

    strip = np.concatenate([np.array([[v, v, v] for v in GRAYS], float), pch], axis=0)
    fuji = lut_disp(lut, size, strip) / 255.0
    e1 = np.abs(simulate(strip) - fuji) * 255
    e2 = np.abs(simulate(strip, six) - fuji) * 255
    ng = len(GRAYS)
    print(f"\n拟合误差（48 块）：全局色度 均值 {e1.mean():.1f} / p90 "
          f"{np.percentile(e1, 90):.1f}；加六轴 均值 {e2.mean():.1f} / p90 {np.percentile(e2, 90):.1f}")
    print(f"  其中灰阶 {ng} 块：{e2[:ng].mean():.1f}，色块 {48-ng} 块：{e2[ng:].mean():.1f}")

    # ---- 4) 逐块数值比对（RGB + HSB） ----
    import colorsys
    mine_p = simulate(pch, six) * 255.0
    print("\n逐块比对（ΔH 度，ΔS/ΔV 为 0-100）：")
    print("  # | 输入RGB | 富士RGB | 我们RGB | dH | dS | dV | dRGB")
    for i in range(len(pch)):
        ha, sa, va = colorsys.rgb_to_hsv(*(tgt_p[i] / 255.0))
        hb, sb, vb = colorsys.rgb_to_hsv(*(mine_p[i] / 255.0))
        dh = ((hb - ha) * 360.0 + 180.0) % 360.0 - 180.0
        drgb = float(np.abs(tgt_p[i] - mine_p[i]).mean())
        print(f"  P{i+1:02d} | {tuple(int(x) for x in pch[i])} | "
              f"{tuple(int(x) for x in tgt_p[i])} | {tuple(int(x) for x in mine_p[i])} | "
              f"{dh:+6.1f} | {100*(sb-sa):+6.1f} | {100*(vb-va):+6.1f} | {drgb:5.1f}")

    spec = {
        "look": os.path.splitext(os.path.basename(a.cube))[0],
        "tone": [int(x) for x in tab256],
        "matrix": [[float(v) for v in row] for row in M],
        "sixaxis": [[float(six_ls[s, 0]), float(six_ls[s, 1])] for s in range(6)],
        "chroma": [[float(y), float(g)] for y, g in zip(grid, gcurve)],
        "err_tone_only": float(e1.mean()),
        "err_with_matrix": float(e3.mean()),
        "err_full": float(e5.mean()),
    }
    with open(a.out + "_spec.json", "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)

    # 三联：同一组 48 个色块，左=原色（标板输入）、中=富士 LUT、右=我们的模拟
    src_chips = (strip / 255.0).reshape(-1, 1, 3)
    fuji_chips = fuji.reshape(-1, 1, 3)
    mine_chips = simulate_six(strip).reshape(-1, 1, 3)
    gapcol = np.ones((src_chips.shape[0], 2, 3))
    sheet = np.concatenate([src_chips, gapcol, fuji_chips, gapcol, mine_chips], axis=1)
    sheet = np.repeat(np.repeat(sheet, 16, axis=0), 48, axis=1)
    buf = (np.clip(sheet, 0, 1) * 255).astype(np.uint8).tobytes()
    subprocess.run([FFMPEG, "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", f"{sheet.shape[1]}x{sheet.shape[0]}", "-i", "-", "-y",
                    a.out + "_preview.jpg"], input=buf, capture_output=True)

    np.savetxt(a.out + "_tone.txt", tab256, fmt="%d")
    print(f"\n写出 {a.out}_tone.txt / _chroma.csv / _spec.json / _preview.jpg")


if __name__ == "__main__":
    sys.exit(main())
