# -*- coding: utf-8 -*-
"""为文档收集事实：读 .tmp/looks_final/<look>.json，独立解码 + 独立模拟，打印/doc 用的表。

独立性：本脚本不 import look_spec4，tone/色度块都从 JSON 的字节级数据重新实现，
只复用 lut_analyze/lut_fit 的 LUT 与 YCbCr 基础函数（与 look_spec4 相同来源，
但路径不同：这里从 tone 数组 + hex 出发，不经过拟合代码）。
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from lut_analyze import parse_cube, apply_lut   # noqa: E402
from lut_fit import eotf, oetf, ycbcr, from_ycbcr  # noqa: E402

D = os.path.join(ROOT, ".tmp", "looks_final")
CUBES = {"reala_ace": "reala ace", "astia": "astia", "eterna": "eterna",
         "bleach_bypass": "bleach bypass"}
GRAYS = [round(i * 255 / 20) for i in range(21)]
PATCHES = [
    (115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67), (133, 128, 177), (103, 189, 170),
    (214, 126, 44), (80, 91, 166), (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
    (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31), (187, 86, 149), (8, 133, 161),
    (243, 243, 242), (200, 200, 200), (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52),
]
STRIP = np.array([[v, v, v] for v in GRAYS] + list(PATCHES), float)
NP_GREY = len(GRAYS)


def load_T():
    d = np.loadtxt(os.path.join(ROOT, ".tmp", "tone_T5.csv"), delimiter=",", skiprows=1)
    x, y = d[:, 0], d[:, 1]
    ok = y > 2.5
    return x[ok], y[ok]


TX, TY = load_T()
XW = float(TX[-1])
U = np.linspace(0, 1, 256)


def T_of(raw):
    return np.interp(np.log(np.clip(raw, TX[0], TX[-1])), np.log(TX), TY)


def T_inv(jpeg):
    o = np.argsort(TY)
    return np.exp(np.interp(jpeg, TY[o], np.log(TX[o])))


def lut_disp(lut, size, srgb):
    """cube 输入 = sRGB 编码，输出 = 显示值（cube 后没有第二条曲线）"""
    return apply_lut(lut, size, np.clip(srgb / 255.0, 0, 1)) * 255.0


STEP = 2.05634   # g*x_white = (0.18/0.058)*0.6626；索引 → 场景线性


def tone_apply(tab, patches):
    """色块 sRGB → 线性反射率 → 表索引 → 显示值 0..255（新口径）"""
    lin = eotf(patches / 255.0)
    idx = np.clip(lin / STEP, 0, 1)
    return np.interp(idx, U, np.asarray(tab, float) / 1023.0) * 255.0


def ch(v):
    return 128.0 + 255.0 * v


def unch(c):
    return (c - 128.0) / 255.0


def block_curve(x, y, z, v):
    """独立的硬件段求值：dx>0 的段里找第一个 v < x[i]"""
    last = None
    for i in range(1, 16):
        dx = x[i] - x[i - 1]
        if dx <= 0:
            continue
        last = i
        if v < x[i]:
            return float(np.clip(np.round(y[i - 1] + (v - x[i - 1]) * z[i] / 128.0), 0, 255))
    i = last
    return float(np.clip(np.round(y[i - 1] + (v - x[i - 1]) * z[i] / 128.0), 0, 255))


def main():
    print(f"{'look':14s} {'e1':>6} {'e1h':>6} {'blk':>6} {'grey':>5} {'color':>6} {'p90':>5} "
          f"{'tone0':>5} {'tone255':>7} {'cb_y':>28} {'cr_y':>28}")
    rows = []
    for name, base in CUBES.items():
        spec = json.load(open(os.path.join(D, name + ".json"), encoding="utf-8"))
        tab = spec["tone"]
        assert len(tab) == 256 and all(0 <= v <= 1023 for v in tab)
        assert all(b >= a for a, b in zip(tab, tab[1:])), "tone 非单调"
        lut, size = parse_cube(os.path.join(ROOT, ".tmp", "fujilut", "cube lut",
                                            base + "_sRGB.cube"))
        fuji = lut_disp(lut, size, STRIP) / 255.0
        disp = tone_apply(tab, STRIP) / 255.0
        yy, cb, cr = ycbcr(disp)
        _, cbt, crt = ycbcr(fuji)

        b = bytes.fromhex(spec["chroma_block_hex"])
        assert len(b) == 100 and b[49] == 0 and b[99] == 0
        cbd = {"x": list(b[0:16]), "y": list(b[16:32]), "z": list(b[32:49])}
        crd = {"x": list(b[50:66]), "y": list(b[66:82]), "z": list(b[82:99])}
        assert cbd["x"] == [0, 0, 0, 0, 64, 128, 192, 255] + [255] * 8, "x 不是库存节点"
        # z 公式自检
        for d in (cbd, crd):
            for i in range(1, 16):
                dx = d["x"][i] - d["x"][i - 1]
                if dx > 0:
                    want = (d["y"][i] - d["y"][i - 1]) * 128.0 / dx
                    assert abs(round(want) - d["z"][i]) <= 1, (name, i, want, d["z"][i])
                    assert d["z"][i] > 0, "dx>0 段出现 z=0"
        cb2 = unch(np.array([block_curve(cbd["x"], cbd["y"], cbd["z"], v) for v in ch(cb)]))
        cr2 = unch(np.array([block_curve(crd["x"], crd["y"], crd["z"], v) for v in ch(cr)]))
        sim = from_ycbcr(yy, cb2, cr2)
        e = np.abs(sim - fuji).mean(axis=1) * 255
        e1h = float(e.mean())
        print(f"{name:14s} {spec['e1']:6.3f} {spec['e1h']:6.3f} {e1h:6.3f} "
              f"{e[:NP_GREY].mean():5.2f} {e[NP_GREY:].mean():6.2f} "
              f"{np.percentile(e, 90):5.1f} {tab[0]:5d} {tab[255]:7d} "
              f"{str(cbd['y'][:8]):>28} {str(crd['y'][:8]):>28}")
        # 最差色块
        worst = np.argsort(-e)[:4]
        print(f"   最差样本: " + ", ".join(
            f"#{i - NP_GREY if i >= NP_GREY else 'G' + str(i)}={e[i]:.1f}" for i in worst))
        # 18% 灰
        d18 = float(np.interp(0.18 / STEP, U, np.asarray(tab) / 1023.0) * 255.0)
        e18 = float(oetf(np.array(0.18)))          # 场景线性 → cube 输入（sRGB 编码）
        f18 = float(apply_lut(lut, size, np.array([[[e18, e18, e18]]], float)).reshape(-1)[0] * 255)
        # 灰阶染色
        gc, gr = ycbcr(disp)[1], ycbcr(disp)[2]
        print(f"   18% 灰: 表渲染 {d18:.1f} / cube期望 {f18:.1f} / 差 {d18 - f18:+.1f}"
              f"   灰阶色度 max |Cb|,|Cr| = {np.abs(gc[:NP_GREY]).max() * 255:.2f}, "
              f"{np.abs(gr[:NP_GREY]).max() * 255:.2f}")
        rows.append((name, spec, cbd, crd))
    # 富士灰阶色度（目标侧）
    print()
    for name, base in CUBES.items():
        lut, size = parse_cube(os.path.join(ROOT, ".tmp", "fujilut", "cube lut", base + "_sRGB.cube"))
        y_, cb_, cr_ = ycbcr(lut_disp(lut, size, STRIP) / 255.0)
        print(f"{name:14s} 富士灰阶色度 max |Cb|={np.abs(cb_[:NP_GREY]).max() * 255:.2f} "
              f"|Cr|={np.abs(cr_[:NP_GREY]).max() * 255:.2f}")


if __name__ == "__main__":
    main()
