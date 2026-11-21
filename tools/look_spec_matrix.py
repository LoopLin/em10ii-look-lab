#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 tone + 100 B 色度块之外再加一层 3×3 矩阵，重算四套 look。

为什么需要它
    `.tmp/looks_final/*.json`（tools/look_spec4.py 产出）只有 tone 表 + 100 B 色度块。
    色度块是「Cb 轴、Cr 轴各自一条一维曲线」，硬件看不见 Y、更不能让两轴耦合，
    所以它**只能沿轴缩放，不能旋转色相**。残差的主项恰好是色相旋转 ⇒ 这是结构上限。
    固件里本来就有 3×3 矩阵单元（0x605ECECA 起 8 个，Q8 s16×9，行和 256），
    标准档被选择子 0 关掉了；补丁 0x604B6AD4 84→80 01 把它打开用 m0。
    于是三件套 = tone 表 + 矩阵 + 色度块。

本脚本干四件事
    1. dump/解码 0x605ECECA 的 8 个预设矩阵，钉死编码（Q8、行和、寄存器重编码）
       —— `--presets` 只做这一步。
    2. 在「tone → 矩阵 → 色度块」下重拟合矩阵（6 个自由参数，行和硬约束 = 1）与色度块。
    3. 量化成固件矩阵表能表示的整数（Q8；非对角恒偶），报量化代价。
    4. 残差按 亮度 / 饱和 / 色相 三向分解。

拟合口径：完全复用 tools/look_spec4.py（不重写一套）
    tone 表  = JSON 里的 tone[256]（构造 A：10 位显示值曲线，索引 = raw/x_white*255）
    色度块   = 复用 look_spec4 的 fit_M1 / fit_M3（M3 是指标域整数坐标下降）
    误差口径 = 同一组 21 灰阶 + 24 色块 = 45 个样本的 RGB MAE/255（文档里口语的「48 色块」）

矩阵落在哪个色空间：本脚本把两种候选都拟一遍
    space="disp"：矩阵作用在 **tone 之后**的显示域 RGB（0..255，rgb = M·tone(lin)）
    space="linear"：矩阵作用在 **tone 之前**的线性域（rgb = tone(M·lin)）
    两者都强制行和 = 256（硬件就是这么做的，见下），由误差决定用哪个。

用法
    python tools/look_spec_matrix.py --presets            # 只 dump/解码预设矩阵
    python tools/look_spec_matrix.py                      # 四套 look 全算
    python tools/look_spec_matrix.py --looks reala_ace --verbose
输出
    .tmp/looks_matrix/<look>.json、.tmp/looks_matrix/summary.json、.tmp/looks_matrix/presets.json

编码结论（本脚本 startup 会重算一遍并打印）
    表 0x605ECECA + 18*k = 9 个 s16（行主序），Q8：256 = 1.0。
    硬件/编码器 0x604B6A22 只存 6 个非对角项，对角项由「行和 = 256」反推：
        reg[0] = 0x80 - (m01&~1 + m02)   → 硬件 m00 = 256 - (m01 + m02)
        reg[4] = 0x80 - (m10' + m12')    → 硬件 m11 = 256 - (m10 + m12)
        reg[8] = 0x80 - (m20  + m21&~1)  → 硬件 m22 = 256 - (m20 + m21)
    ⇒ ① 硬件上**每行和恒为 1**（保中性，行和≠256 的矩阵写不进去）；
       ② 非对角项被强制成偶数（&~1，负数向 -∞ 取偶）⇒ 非对角的最小步长是 2/256 = 1/128，
          对角项精确；
       ③ 只要 6 个非对角项都是偶数且行和 256，编码-解码**无损**（本脚本 assert 这条）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import look_spec4 as L4                                    # noqa: E402
from lut_analyze import parse_cube, apply_lut              # noqa: E402
from lut_fit import eotf, ycbcr, from_ycbcr                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUBE_DIR = os.path.join(ROOT, ".tmp", "fujilut", "cube lut")
IN_DIR = os.path.join(ROOT, ".tmp", "looks_final")
OUT_DIR = os.path.join(ROOT, ".tmp", "looks_matrix")
STOCK_IMG = L4.STOCK_IMG
STOCK_BASE = L4.STOCK_BASE

PRESET_BASE = 0x605ECECA
PRESET_STRIDE = 18
PRESET_N = 8

LOOKS = ["reala_ace", "astia", "eterna", "bleach_bypass"]

# 矩阵拟合的自由度：每行 3 个元素、行和钉死 1 ⇒ 每行 2 个自由参数，共 6 个。
# 第 3 个（对角）由行和反推 —— 这正是硬件编码器的做法，所以拟合与硬件同构。
NPAR = 6
P_IDENTITY = np.array([1.0, 0.0, 0.0, 1.0, 0.0, 0.0])   # params_to_mat(P_IDENTITY) == 单位阵
DIAG_MIN, DIAG_MAX = 0.40, 1.60      # 对角合理区间（挡住退化解）
OFF_MAX = 0.65                       # 非对角上限（Q8 里 ±166，离溢出很远）


# --------------------------------------------------------------------------- #
# 1. 预设矩阵表：dump + 解码 + 编码器往返
# --------------------------------------------------------------------------- #
def read_presets(path=STOCK_IMG):
    with open(path, "rb") as fh:
        fh.seek(PRESET_BASE - STOCK_BASE)
        raw = fh.read(PRESET_N * PRESET_STRIDE)
    out = []
    for k in range(PRESET_N):
        v = [int(x) for x in np.frombuffer(raw[k * PRESET_STRIDE:(k + 1) * PRESET_STRIDE], dtype="<i2")]
        out.append(v)
    return out


def q8_to_mat(vals):
    return np.asarray(vals, float).reshape(3, 3) / 256.0


def encode_register(vals):
    """复刻 0x604B6A22：9 个 s16（Q8，行和 256）→ 写进 register_image+0x58 的 9 个 u16。
    对角项存 (mii-128)；非对角项 &~1（偶化，负数向 -∞）；对角由行和反推。"""
    m = [int(x) for x in vals]
    d = [0] * 9
    d[1] = m[1] & ~1
    d[2] = m[2]
    d[0] = 0x80 - (d[1] + d[2])
    d[3] = m[3]
    d[5] = m[5]
    if (d[3] + d[5]) & 1:
        d[3] &= ~1
        d[5] &= ~1
    d[4] = 0x80 - (d[3] + d[5])
    d[6] = m[6]
    d[7] = m[7] & ~1
    d[8] = 0x80 - (d[6] + d[7])
    return d


def decode_register(d):
    """硬件视角：对角项 +128，得到真正乘上去的 9 个 Q8 值。"""
    hw = [0] * 9
    for i in range(9):
        hw[i] = d[i] + (0x80 if i in (0, 4, 8) else 0)
    return hw


def check_encoding(presets, verbose=True):
    """返回证据字典：m6 是否单位阵、行和、编码器往返误差。"""
    rows = []
    ok_rows = True
    for k, v in enumerate(presets):
        s = [sum(v[0:3]), sum(v[3:6]), sum(v[6:9])]
        rows.append(s)
        ok_rows &= all(x == 256 for x in s)
    id6 = (presets[6] == [256, 0, 0, 0, 256, 0, 0, 0, 256])
    rt = []
    for k, v in enumerate(presets):
        hw = decode_register(encode_register(v))
        rt.append([hw[i] - v[i] for i in range(9)])
    rt_max = max(max(abs(x) for x in r) for r in rt)
    ev = {
        "presets": presets,
        "row_sums": rows,
        "all_row_sums_256": bool(ok_rows),
        "m6_is_identity": bool(id6),
        "m0_as_float": q8_to_mat(presets[0]).tolist(),
        "row_sums_255": [[k, s] for k, s in enumerate(rows) if any(x != 256 for x in s)],
        "roundtrip_delta_max": int(rt_max),
        "roundtrip_delta": rt,
        "note": ("Q8 钉死：m6 == (256,0,0,0,256,0,0,0,256) 是单位阵 ⇒ 缩放 256=1.0；"
                 "8 个矩阵行和全 256 ⇒ 硬件按行和反推对角，行和不可改。"
                 "往返残差是 &~1 偶化造成的 ±1（1/256）。"),
    }
    if verbose:
        print("=== 预设矩阵 0x605ECECA（8 × 18 B = 9 × s16，Q8）===")
        for k, v in enumerate(presets):
            print("m%d raw=%-46s rowsum=%s" % (k, str(v), [sum(v[0:3]), sum(v[3:6]), sum(v[6:9])]))
            mm = q8_to_mat(v)
            for r in range(3):
                print("      [%7.4f %7.4f %7.4f]" % tuple(mm[r]))
        print("m6 单位阵: %s ；行和全 256: %s ；编码器往返最大偏差: %d (1/256 步长)"
              % (id6, ok_rows, rt_max))
    return ev


# --------------------------------------------------------------------------- #
# 2. 模型：tone → 矩阵 → YCbCr → 色度块
# --------------------------------------------------------------------------- #
def params_to_mat(p):
    a, b, c, d, e, f = [float(x) for x in p]
    return np.array([[a, b, 1.0 - a - b],
                     [c, d, 1.0 - c - d],
                     [e, f, 1.0 - e - f]])


def mat_to_params(M):
    return np.array([M[0, 0], M[0, 1], M[1, 0], M[1, 1], M[2, 0], M[2, 1]], float)


def params_ok(p):
    M = params_to_mat(p)
    dg = np.diag(M)
    off = np.array([M[0, 1], M[0, 2], M[1, 0], M[1, 2], M[2, 0], M[2, 1]])
    return bool(dg.min() >= DIAG_MIN and dg.max() <= DIAG_MAX and np.abs(off).max() <= OFF_MAX)


def apply_tone_index(idx, tab):
    """tone 表求值：idx ∈ 0..1（= raw/STEP/255 的口径），返回显示值 0..1"""
    return np.interp(np.clip(idx, 0.0, 1.0), L4.U, np.asarray(tab, float) / 1023.0)


class MatrixCtx:
    """tone + 3×3 矩阵 + 100 B 色度块。接口与 look_spec4.Ctx 对齐，
    好让 fit_M1 / fit_M3 / hw_curve 直接复用。"""

    def __init__(self, tab, fuji, space="disp", M=None):
        self.tab = np.asarray(tab, float)
        self.fuji = np.asarray(fuji, float)          # 0..1
        self.space = space
        self.lin = eotf(L4.STRIP / 255.0)            # 色块线性反射率（45×3）
        self.disp0 = L4.tone_only(tab, L4.STRIP) / 255.0
        self.M = np.eye(3) if M is None else np.asarray(M, float)
        self._set(self.M)

    def _set(self, M):
        self.M = np.asarray(M, float)
        if self.space == "disp":
            rgb = np.clip(self.disp0 @ self.M.T, 0.0, 1.0)
        else:                                        # 线性域：先矩阵，再 tone
            rgb = apply_tone_index((self.lin @ self.M.T) / L4.STEP, self.tab)
        self.rgb = rgb
        self.y, self.cb, self.cr = ycbcr(rgb)
        self._cbc, self._crc = L4.to_ch(self.cb), L4.to_ch(self.cr)
        _, tcb, tcr = ycbcr(self.fuji)
        self.tc, self.tr = L4.to_ch(tcb), L4.to_ch(tcr)

    def sim(self, nodes_cb, nodes_cr):
        cb2 = L4.from_ch(L4.curve(nodes_cb, self._cbc))
        cr2 = L4.from_ch(L4.curve(nodes_cr, self._crc))
        return from_ycbcr(self.y, cb2, cr2)

    def err(self, nodes_cb, nodes_cr, mask=None):
        e = np.abs(self.sim(nodes_cb, nodes_cr) - self.fuji).mean(axis=1) * 255.0
        return e if mask is None else e[mask]

    def e1h(self, nodes_cb, nodes_cr, mask=None):
        return float(self.err(nodes_cb, nodes_cr, mask).mean())

    def sim_block(self, cb, cr):
        cb2 = L4.from_ch(np.array([L4.hw_curve(cb, v) for v in self._cbc]))
        cr2 = L4.from_ch(np.array([L4.hw_curve(cr, v) for v in self._crc]))
        return from_ycbcr(self.y, cb2, cr2)

    def e1h_block(self, cb, cr, mask=None):
        e = np.abs(self.sim_block(cb, cr) - self.fuji).mean(axis=1) * 255.0
        return float(e.mean() if mask is None else e[mask].mean())


# --------------------------------------------------------------------------- #
# 3. 矩阵与色度块的交替拟合
# --------------------------------------------------------------------------- #
STEPS_COARSE = (0.08, 0.03, 0.01, 0.004)
STEPS_FINE = (0.02, 0.008, 0.003, 0.001)


def pattern_search(obj, p0, steps, max_iter=60):
    p = np.array(p0, float)
    best = obj(p)
    for st in steps:
        for _ in range(max_iter):
            improved = False
            for i in range(NPAR):
                for s in (st, -st):
                    q = p.copy()
                    q[i] += s
                    if not params_ok(q):
                        continue
                    e = obj(q)
                    if e < best - 1e-9:
                        best, p, improved = e, q, True
            if not improved:
                break
    return p, best


def fit_chroma(ctx, mask_fit, mask_obj=None):
    """用 look_spec4 的模型族拟合色度块（M1 → M4/M3 指标域），取 24 色块 in-sample 误差最小者。
    注：基线用 LOO 选型；这里为了速度用 in-sample 选型，且更复杂模型要赢 3% 才启用（同规则）。"""
    xc, xr, tc, tr = ctx._cbc, ctx._crc, ctx.tc, ctx.tr
    m1c = L4.fit_M1(xc[mask_fit], tc[mask_fit], np.ones(mask_fit.sum()))
    m1r = L4.fit_M1(xr[mask_fit], tr[mask_fit], np.ones(mask_fit.sum()))
    cand = {"M1": (m1c, m1r)}
    cand["M4"] = L4.fit_M3(ctx, m1c, m1r, mask_obj, free0=False)
    cand["M3"] = L4.fit_M3(ctx, m1c, m1r, mask_obj, free0=True)
    errs = {k: ctx.e1h(cb, cr) for k, (cb, cr) in cand.items()}
    order = ["M1", "M4", "M3"]
    best = min(order, key=lambda k: errs[k])
    chosen = best
    for k in order:
        if k == best:
            break
        if errs[k] <= errs[best] * 1.03:
            chosen = k
            break
    cb, cr = cand[chosen]
    return cb, cr, chosen, errs


def nodes_from_json(y8):
    """JSON 里的 8 项 y（[n0]*4 + [n1,n2,n3,n4]）→ 5 个节点（x=0/64/128/192/255）"""
    y = list(y8)
    return np.array([y[0], y[4], y[5], y[6], y[7]], float)


def block_of(nodes_cb, nodes_cr):
    _, x_cb, y_cb, z_cb = L4.nodes_to_block(nodes_cb)
    _, x_cr, y_cr, z_cr = L4.nodes_to_block(nodes_cr)
    cb = {"x": x_cb, "y": y_cb, "z": z_cb}
    cr = {"x": x_cr, "y": y_cr, "z": z_cr}
    hx = L4.block_hex(cb, cr)
    dcb, dcr = L4.decode_block(hx)
    return (cb, cr), (dcb, dcr), hx


def quant_matrix(M):
    """float 3×3（行和 1）→ 固件矩阵表的 9 个 i16（Q8，非对角偶化，对角由行和反推）。"""
    q = [int(round(v * 256.0)) for v in M.reshape(9)]
    # 非对角偶化（编码器 &~1，负数向 -∞ ⇒ 与 & ~1 等价）
    q[1] &= ~1                      # m01
    q[2] = q[2]                     # m02 原样
    q[3] = q[3]                     # m10 原样
    q[5] = q[5]                     # m12 原样
    if (q[3] + q[5]) & 1:           # 行 1：奇则两个都偶化（复刻编码器）
        q[3] &= ~1
        q[5] &= ~1
    q[6] = q[6]                     # m20 原样
    q[7] &= ~1                      # m21 偶化
    q[0] = 256 - (q[1] + q[2])      # m00 由行和反推
    q[4] = 256 - (q[3] + q[5])      # m11
    q[8] = 256 - (q[6] + q[7])      # m22
    return q


def quantize_and_certify(M):
    q = quant_matrix(M)
    hw = decode_register(encode_register(q))
    lossless = all(q[i] == hw[i] for i in (1, 2, 3, 5, 6, 7))
    exact_rows = [sum(q[0:3]), sum(q[3:6]), sum(q[6:9])]
    return {
        "table_values": q,
        "effective_float": (np.asarray(q, float).reshape(3, 3) / 256.0).tolist(),
        "row_sums": exact_rows,
        "register_18b": [int(x) for x in encode_register(q)],
        "encoder_even_ok": bool(q[1] % 2 == 0 and q[7] % 2 == 0 and (q[3] + q[5]) % 2 == 0),
        "encode_lossless": bool(lossless),
    }


def neutral_check(ctx, cb, cr, base_ctx=None, base_block=None):
    """中性是否被染色：21 级灰阶 + 底排 6 个中性色块，与本 look 的基线对比。
    判据：灰阶 MAE 与基线同量级（≤0.5），中性块 6 块 ≤0.8（基线是 0.343）。"""
    sim = ctx.sim_block(cb, cr)
    idx = list(range(L4.NP_GREY)) + list(L4.NEUTRAL_PATCH)
    d = np.abs(sim[idx] - ctx.fuji[idx]).mean(axis=1) * 255.0
    gray_mae = float(d[:L4.NP_GREY].mean())
    neu_mae = float(d[L4.NP_GREY:].mean())
    out = {"n": len(idx), "gray_mae": gray_mae, "neutral_mae": neu_mae,
           "max_abs": float(d.max()), "pass": bool(gray_mae <= 0.5 and neu_mae <= 0.8)}
    if base_ctx is not None and base_block is not None:
        sb = base_ctx.sim_block(base_block[0], base_block[1])
        db = np.abs(sb[idx] - base_ctx.fuji[idx]).mean(axis=1) * 255.0
        out["baseline_gray_mae"] = float(db[:L4.NP_GREY].mean())
        out["baseline_neutral_mae"] = float(db[L4.NP_GREY:].mean())
    return out


# --------------------------------------------------------------------------- #
# 4. 残差分解：亮度 / 饱和 / 色相
# --------------------------------------------------------------------------- #
def decompose(sim, fuji):
    """sim/fuji = 显示域 RGB 0..1。在 YCbCr 里把色度残差拆成径向（饱和）与切向（色相）。

    ΔL = 255·ΔY
    ΔS = 255·(|C2|-|C1|)                     径向（正 = 更饱和）
    ΔH = 255·C_ref·Δθ,  Δθ = wrap(θ2-θ1)      切向（正 = 色相往 +θ 转）
    分解自检：由 (ΔS, ΔH) 重建的 (ΔCb, ΔCr) 与真实的偏差单独报。"""
    y1, cb1, cr1 = ycbcr(np.clip(sim, 0, 1))
    y2, cb2, cr2 = ycbcr(np.clip(fuji, 0, 1))
    dL = 255.0 * (y2 - y1)
    C1 = np.hypot(cb1, cr1)
    C2 = np.hypot(cb2, cr2)
    dS = 255.0 * (C2 - C1)
    th1 = np.arctan2(cr1, cb1)
    th2 = np.arctan2(cr2, cb2)
    dth = np.arctan2(np.sin(th2 - th1), np.cos(th2 - th1))
    Cref = 0.5 * (C1 + C2)
    dH = 255.0 * Cref * dth
    # 自检：径向/切向重建 vs 真实 Δchroma
    ux = np.where(C1 > 1e-9, cb1 / np.maximum(C1, 1e-9), 1.0)
    uy = np.where(C1 > 1e-9, cr1 / np.maximum(C1, 1e-9), 0.0)
    reb = 255.0 * C1 + dS * ux
    rcb = 255.0 * C1 * ux + dS * ux - dH * uy
    rcr = 255.0 * C1 * uy + dS * uy + dH * ux
    rec = float(np.sqrt(np.mean((rcb - 255.0 * cb2) ** 2 + (rcr - 255.0 * cr2) ** 2)))
    return {
        "dL": dL, "dS": dS, "dH": dH, "recon_rmse": rec,
        "theta": np.degrees(th2) % 360.0, "C2": C2,
        "rgb_err": np.abs(sim - fuji).mean(axis=1) * 255.0,
    }


def decomp_summary(dec, mask):
    return {
        "n": int(mask.sum()),
        "mae_L": float(np.abs(dec["dL"][mask]).mean()),
        "mae_S": float(np.abs(dec["dS"][mask]).mean()),
        "mae_H": float(np.abs(dec["dH"][mask]).mean()),
        "bias_L": float(dec["dL"][mask].mean()),
        "bias_S": float(dec["dS"][mask].mean()),
        "bias_H": float(dec["dH"][mask].mean()),
        "rgb_mae": float(dec["rgb_err"][mask].mean()),
        "recon_rmse": dec["recon_rmse"],
    }


def hue_sectors(dec, mask):
    """按目标色相分 6 个 60° 扇区统计（只有带色度的样本有意义）"""
    names = ["R-Y(345-75)", "Y-G(75-135)", "G-C(135-195)", "C-B(195-255)",
             "B-M(255-315)", "M-R(315-345)"]
    out = []
    edges = [345, 75, 135, 195, 255, 315, 345]
    th = dec["theta"]
    for i, nm in enumerate(names):
        lo, hi = edges[i], edges[i + 1]
        if lo < hi:
            sel = mask & (th >= lo) & (th < hi)
        else:
            sel = mask & ((th >= lo) | (th < hi))
        if not sel.any():
            out.append({"sector": nm, "n": 0})
            continue
        out.append({"sector": nm, "n": int(sel.sum()),
                    "mae_L": float(np.abs(dec["dL"][sel]).mean()),
                    "mae_S": float(np.abs(dec["dS"][sel]).mean()),
                    "mae_H": float(np.abs(dec["dH"][sel]).mean()),
                    "rgb_mae": float(dec["rgb_err"][sel].mean())})
    return out


# --------------------------------------------------------------------------- #
# 5. 单套 look
# --------------------------------------------------------------------------- #
def solve_look(name, space="disp", verbose=False):
    d = json.load(open(os.path.join(IN_DIR, name + ".json"), encoding="utf-8"))
    tab = np.asarray(d["tone"], float)
    lut, size = parse_cube(os.path.join(CUBE_DIR, d["cube"]))
    fuji = L4.lut_disp(lut, size, L4.STRIP) / 255.0
    patches = L4.CIDX                          # 24 个色块：矩阵/色度块只在这上面拟合
    mask_patch = np.zeros(L4.NP_ALL, bool)
    mask_patch[patches] = True
    mask_all = np.ones(L4.NP_ALL, bool)

    # ---- 基线：JSON 原样（无矩阵）的浮点/整数误差，校验口径一致 ----
    cb0 = d["chroma"]
    nodes_cb0 = nodes_from_json(cb0["cb_y"])
    nodes_cr0 = nodes_from_json(cb0["cr_y"])
    base = MatrixCtx(tab, fuji, "disp", np.eye(3))
    e_base_f = base.e1h(nodes_cb0, nodes_cr0)
    (cbB, crB), (dcbB, dcrB), hxB = block_of(nodes_cb0, nodes_cr0)
    e_base_b = base.e1h_block(dcbB, dcrB)
    if verbose:
        print("  [基线] 浮点 e1h=%.4f（JSON %.4f）/ 整数块 %.4f（JSON %.4f）"
              % (e_base_f, d["e1h"], e_base_b, d["e1h_block"]))

    # ---- 交替拟合：矩阵 ↔ 色度块 ----
    ctx = MatrixCtx(tab, fuji, space, np.eye(3))

    def obj_nomatrix(p):
        """不含色度块：矩阵单独能把 RGB 拉多近（给交替拟合一个稳的起点）"""
        ctx._set(params_to_mat(p))
        return float(np.abs(ctx.rgb - ctx.fuji)[mask_patch].mean() * 255.0)

    p, _ = pattern_search(obj_nomatrix, P_IDENTITY.copy(), STEPS_COARSE)
    nodes_cb = nodes_cr = None
    model = None
    for _ in range(2):
        ctx._set(params_to_mat(p))
        nodes_cb, nodes_cr, model, _ = fit_chroma(ctx, mask_patch, mask_patch)
        ctx._set(params_to_mat(p))

        def obj_chroma(q, _cb=nodes_cb, _cr=nodes_cr):
            ctx._set(params_to_mat(q))
            return ctx.e1h(_cb, _cr, mask_patch)

        p, _ = pattern_search(obj_chroma, p, STEPS_FINE)
    ctx._set(params_to_mat(p))
    nodes_cb, nodes_cr, model, model_errs = fit_chroma(ctx, mask_patch, mask_patch)
    e_mat_f = ctx.e1h(nodes_cb, nodes_cr)
    # 矩阵单独（色度块先不动）能把 RGB 拉多近 —— 说明矩阵本身贡献了多少
    e_mat_only = float(np.abs(ctx.rgb - ctx.fuji).mean(axis=1).mean() * 255.0)
    e_base_only = float(np.abs(base.rgb - base.fuji).mean(axis=1).mean() * 255.0)
    (cbD, crD), (dcbD, dcrD), hx = block_of(nodes_cb, nodes_cr)
    e_mat_b = ctx.e1h_block(dcbD, dcrD)

    # ---- 矩阵量化到固件能表示的整数 ----
    M = params_to_mat(p)
    qz = quantize_and_certify(M)
    Mq = np.asarray(qz["table_values"], float).reshape(3, 3) / 256.0
    ctxq = MatrixCtx(tab, fuji, space, Mq)
    nodes_cb_q, nodes_cr_q, model_q, _ = fit_chroma(ctxq, mask_patch, mask_patch)
    e_q_f = ctxq.e1h(nodes_cb_q, nodes_cr_q)
    (cbQ, crQ), (dcbQ, dcrQ), hxQ = block_of(nodes_cb_q, nodes_cr_q)
    e_q_b = ctxq.e1h_block(dcbQ, dcrQ)

    # ---- 半留出泛化检查（交替拟合也用一半样本）----
    def fit_eval(sel_fit, sel_eval):
        c = MatrixCtx(tab, fuji, space, np.eye(3))
        mfit = np.zeros(L4.NP_ALL, bool)
        mfit[np.asarray(sel_fit)] = True
        meval = np.zeros(L4.NP_ALL, bool)
        meval[np.asarray(sel_eval)] = True
        def obj(q, _c=c, _m=mfit):
            _c._set(params_to_mat(q))
            return float(np.abs(_c.rgb - _c.fuji)[_m].mean() * 255.0)
        pp, _ = pattern_search(obj, P_IDENTITY.copy(), STEPS_COARSE)
        c._set(params_to_mat(pp))
        ncb, ncr, _, _ = fit_chroma(c, mfit, mfit)
        e_fit = c.e1h(ncb, ncr, mfit)
        e_out = c.e1h(ncb, ncr, meval)
        return e_fit, e_out, params_to_mat(pp).tolist()

    half = {}
    for tag, (a, b) in {"even": (patches[::2], patches[1::2]),
                        "odd": (patches[1::2], patches[::2])}.items():
        fit_e, out_e, Mm = fit_eval(a, b)
        half[tag] = {"e_in": fit_e, "e_holdout": out_e, "matrix": Mm}

    # ---- 残差分解（基线与最终）----
    dec_base = decompose(base.sim_block(dcbB, dcrB), fuji)
    dec_final = decompose(ctxq.sim_block(dcbQ, dcrQ), fuji)
    dec_lin = None
    res = {
        "look": name,
        "space": space,
        "cube": d["cube"],
        "x_white": d["x_white"],
        "step": d["step"],
        "tone": [int(x) for x in tab],
        "matrix": {
            "encoding": "i16 Q8 row-major, 256 = 1.0, 行和 = 256（对角由行和反推）",
            "fit_float": M.tolist(),
            "params": [float(x) for x in p],
            "quantized": qz,
        },
        "chroma": {
            "model": model,
            "cb_x": [int(v) for v in cbQ["x"]],
            "cb_y": [int(v) for v in cbQ["y"]],
            "cb_z": [int(v) for v in cbQ["z"]],
            "cr_x": [int(v) for v in crQ["x"]],
            "cr_y": [int(v) for v in crQ["y"]],
            "cr_z": [int(v) for v in crQ["z"]],
            "chroma_block_hex": hxQ,
        },
        "errors": {
            "e1h_baseline_json": float(d["e1h"]),
            "e1h_baseline_block_json": float(d["e1h_block"]),
            "e1h_baseline_recomputed": e_base_f,
            "e1m_float": e_mat_f,
            "e1m_matrix_only": e_mat_only,
            "e1_baseline_matrix_only": e_base_only,
            "e1m_block": e_mat_b,
            "e1m": e_q_b,
            "e1m_float_quant_matrix": e_q_f,
            "e1m_gray": float(np.abs(ctxq.sim_block(dcbQ, dcrQ) - fuji)[:L4.NP_GREY].mean() * 255.0),
            "quant_cost": e_q_b - e_mat_b,
        },
        "neutral": neutral_check(ctxq, dcbQ, dcrQ, base, (dcbB, dcrB)),
        "chroma_models": model_errs,
        "halves": half,
        "residual": {
            "baseline": decomp_summary(dec_base, mask_all),
            "final": decomp_summary(dec_final, mask_all),
            "baseline_patches": decomp_summary(dec_base, mask_patch),
            "final_patches": decomp_summary(dec_final, mask_patch),
            "sectors_baseline": hue_sectors(dec_base, mask_patch),
            "sectors_final": hue_sectors(dec_final, mask_patch),
            "per_patch": [
                {"patch": int(i - L4.NP_GREY),
                 "target_rgb": [int(round(v * 255)) for v in fuji[i]],
                 "sim_rgb": [int(round(v * 255)) for v in np.clip(ctxq.sim_block(dcbQ, dcrQ)[i], 0, 1)],
                 "rgb_err": float(dec_final["rgb_err"][i]),
                 "dL": float(dec_final["dL"][i]),
                 "dS": float(dec_final["dS"][i]),
                 "dH": float(dec_final["dH"][i]),
                 "hue_deg": float(dec_final["theta"][i]),
                 "base_rgb_err": float(dec_base["rgb_err"][i])}
                for i in patches
            ],
        },
        "convention": {
            "samples": "21 灰阶 + 24 色块 = 45（Ctx 口径）；矩阵/色度块只在 24 色块上拟合",
            "metric": "RGB MAE / 255；e1h = 浮点曲线，e1m = 量化矩阵 + 整数 100 B 块",
            "tone": d.get("tone_convention"),
        },
    }
    return res, dec_base, dec_final, mask_all, mask_patch


def verify_one(name, outdir=None):
    """规格回放：只从产物 JSON 重建流水线（tone 256 + 9 个矩阵整数 + 100 B 块），重算 e1m。"""
    outdir = outdir or OUT_DIR
    d = json.load(open(os.path.join(outdir, name + ".json"), encoding="utf-8"))
    tab = np.asarray(d["tone"], float)
    lut, size = parse_cube(os.path.join(CUBE_DIR, d["cube"]))
    fuji = L4.lut_disp(lut, size, L4.STRIP) / 255.0
    q = np.asarray(d["matrix"]["quantized"]["table_values"], float).reshape(3, 3) / 256.0
    ctx = MatrixCtx(tab, fuji, d["space"], q)
    c = d["chroma"]
    hx = c["chroma_block_hex"]
    cb, cr = L4.decode_block(hx)
    e = ctx.e1h_block(cb, cr)
    dec = decompose(ctx.sim_block(cb, cr), fuji)
    return {"space": d["space"], "e1m": e, "e1m_file": d["errors"]["e1m"],
            "gray_mae": float(np.abs(ctx.sim_block(cb, cr) - fuji)[:L4.NP_GREY].mean() * 255.0),
            "neutral_pass": d["neutral"]["pass"],
            "residual": decomp_summary(dec, np.ones(L4.NP_ALL, bool))}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--looks", nargs="*", default=None)
    ap.add_argument("--outdir", default=OUT_DIR)
    ap.add_argument("--presets", action="store_true", help="只 dump/解码预设矩阵表")
    ap.add_argument("--spaces", nargs="*", default=["disp", "linear"])
    ap.add_argument("--verify", action="store_true",
                    help="只读已产出的 <look>.json，用里面的 tone+矩阵整数+块 hex 重算 e1m（规格回放）")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.outdir, exist_ok=True)
    presets = read_presets()
    ev = check_encoding(presets)
    with open(os.path.join(args.outdir, "presets.json"), "w", encoding="utf-8") as fh:
        json.dump(ev, fh, ensure_ascii=False, indent=1)
    if args.presets:
        return 0

    looks = args.looks or LOOKS
    if args.verify:
        rc = 0
        print("=== 规格回放（只读 <look>.json，不信任何中间量）===")
        for name in looks:
            v = verify_one(name)
            print("%-14s space=%-6s e1m=%.4f (文件里写 %.4f) Δ=%.4f  gray=%.3f neutral=%s"
                  % (name, v["space"], v["e1m"], v["e1m_file"], v["e1m"] - v["e1m_file"],
                     v["gray_mae"], v["neutral_pass"]))
            rc |= abs(v["e1m"] - v["e1m_file"]) > 1e-6
        return rc
    summary = {}
    for name in looks:
        best = None
        per_space = {}
        for sp in args.spaces:
            res, dec_b, dec_f, mask_all, mask_patch = solve_look(name, sp, args.verbose)
            per_space[sp] = res
            key = res["errors"]["e1m"]
            if best is None or key < best[1]:
                best = (res, key, sp)
        res = best[0]
        res["spaces_compared"] = {sp: {"e1m_float": v["errors"]["e1m_float"],
                                       "e1m": v["errors"]["e1m"]}
                                  for sp, v in per_space.items()}
        res["space_chosen"] = best[2]
        res["best_space"] = best[2]
        with open(os.path.join(args.outdir, name + ".json"), "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
        e = res["errors"]
        summary[name] = {
            "space": best[2],
            "e1h_baseline": e["e1h_baseline_json"],
            "e1h_baseline_block": e["e1h_baseline_block_json"],
            "e1m_float": e["e1m_float"],
            "e1m_block": e["e1m_block"],
            "e1m": e["e1m"],
            "quant_cost": e["quant_cost"],
            "matrix_table_values": res["matrix"]["quantized"]["table_values"],
            "row_sums": res["matrix"]["quantized"]["row_sums"],
            "encode_lossless": res["matrix"]["quantized"]["encode_lossless"],
            "model": res["chroma"]["model"],
            "halves": {k: v["e_holdout"] for k, v in res["halves"].items()},
            "residual_final": res["residual"]["final"],
            "residual_baseline": res["residual"]["baseline"],
            "spaces": res["spaces_compared"],
        }
        print("%-14s space=%-6s e1h %.3f → e1m %.3f (float %.3f, 量化代价 %+.3f) matrix=%s"
              % (name, best[2], e["e1h_baseline_json"], e["e1m"], e["e1m_float"],
                 e["quant_cost"], res["matrix"]["quantized"]["table_values"]))
        print("              残差: base L/S/H %.2f/%.2f/%.2f → final %.2f/%.2f/%.2f (RGB MAE %.2f)"
              % (res["residual"]["baseline"]["mae_L"], res["residual"]["baseline"]["mae_S"],
                 res["residual"]["baseline"]["mae_H"],
                 res["residual"]["final"]["mae_L"], res["residual"]["final"]["mae_S"],
                 res["residual"]["final"]["mae_H"], res["residual"]["final"]["rgb_mae"]))

    with open(os.path.join(args.outdir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
