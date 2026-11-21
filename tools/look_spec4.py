#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把四种富士胶片观感落成「可直接写进固件」的规格：tone 256 + 色度块 100 B。

为什么需要它（口径问题）
    旧脚本 tools/fuji_fit.py 输出的 `chroma` 是**按亮度 Y 分箱**的饱和增益曲线：
        Cb' = Cb · g(Y),  Cr' = Cr · g(Y)
    这是「观感描述」，不是硬件的自变量。固件色度块（0x605D4829，k=0，100 B）是
    **两条款式独立的、以 Cb/Cr 通道值为自变量的分段线性曲线**：
        Cb_out = f_cb(Cb_in),  Cr_out = f_cr(Cr_in)
    硬件看不见 Y。把 gain(Y) 直接写成 y[i]=128+(x[i]-128)*g_i 得到的是
    「增益随**色度幅值**变化」，不是「增益随亮度变化」——口径错位。
    所以本脚本在**硬件坐标系**里重拟合，并给出新口径误差 e1h（验收口径）。

拟合口径
    样本：tools/fuji_fit.py 里同一组标板（21 级灰阶 + 24 色块 = 45 个样本）。
      注意 24 个「色块」是 ColorChecker 原样，底排 6 块是中性灰（x≈128），
      对色度曲线不提供任何信息 ⇒ 真正带色度的样本只有 18 个。
      输入：色块 sRGB → tone 表 → 相机显示值 → (Y, Cb, Cr)
      目标：色块 sRGB → 富士 LUT → 显示值 → (Y_t, Cb_t, Cr_t)
    通道换算：u8 通道值 = 128 + 255·Cb（Cb 为 -0.5..0.5 的 YCbCr 分量），
    与固件块的 x 域 0..255、中位 128 一致。
    对 Cb / Cr 各自拟合 f: 通道值 → 通道值。x 节点沿用库存 k=0：
        0, 0, 0, 0, 64, 128, 192, 255, 255×8
    ⇒ 有效节点只有 {0, 64, 128, 192, 255} 五个 / 四段。y[128] 硬性钉在 128
    （否则中性灰会被染色，这一点也有实测支撑：富士 LUT 对 21 级灰阶的
    色度偏移 ≤1.6/255）。

    四档模型（每通道独立）：
      M0  全局增益（1 参数）     y = 128 + (x-128)·k           最小化通道域 LS
      M1  两侧各自增益（2 参数）  x<128 用 k_neg，x>128 用 k_pos（各自过 (128,128)）
      M2  通道域四节点（4 参数）  平滑先验拉向 M1，投影梯度（确定性）
      M3  指标域四节点（4 参数）  直接最小化 e1h（RGB MAE），整数节点坐标下降 +
          单调投影 + 「近平局域内取最接近 M1 先验的值」的简化步（代替 λ 扫描）

    模型选择用 **留一交叉验证**：24 个色块逐个留出、其余 23 个重拟合，
    以 LOO 的色块 RGB MAE 为准；更复杂的模型必须比简单模型好 3% 以上才启用。
    （真正带色度的样本只有 18 个，16 自由节点的方案必定过拟合，所以上限放到 4。）

用法
    python tools/look_spec4.py                       # 四种观感全部重算
    python tools/look_spec4.py --looks reala_ace     # 只算一个
    python tools/look_spec4.py --outdir .tmp/looks_final --verbose

输出
    <outdir>/<look>.json    {look, tone[256], chroma{cb_x,cb_y,cb_z,cr_x,cr_y,cr_z},
                             chroma_block_hex, e1, e1h, ...}
    <outdir>/summary.json   四行汇总

证据等级：曲线结构 = **静态（结构/公式推断）**（0x605D4829 逆推，
docs/color/chroma-curve.md + S11 真机半块实验）；误差数字 = **模拟器**
（tone 表 + 100 B 块的整数分段线性，按 z 段增益逐段求值）；
「真机会得到这个观感」= **推理**。本轮没有真机。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lut_analyze import parse_cube, apply_lut          # noqa: E402
from lut_fit import eotf, oetf, ycbcr, from_ycbcr      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUBE_DIR = os.path.join(ROOT, ".tmp", "fujilut", "cube lut")
CAP_DIR = os.path.join(ROOT, ".tmp", "cap")
T_CSV = os.path.join(ROOT, ".tmp", "tone_T5.csv")

LOOKS = [
    ("reala_ace", "reala ace"),
    ("astia", "astia"),
    ("eterna", "eterna"),
    ("bleach_bypass", "bleach bypass"),
]

# ---- 标板：与 tools/fuji_fit.py 完全一致（21 级灰阶 + 24 色块 = 45 个样本） ----
GRAYS = [round(i * 255 / 20) for i in range(21)]
PATCHES = [
    (115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67), (133, 128, 177), (103, 189, 170),
    (214, 126, 44), (80, 91, 166), (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
    (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31), (187, 86, 149), (8, 133, 161),
    (243, 243, 242), (200, 200, 200), (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52),
]
STRIP = np.array([[v, v, v] for v in GRAYS] + list(PATCHES), float)
NP_GREY = len(GRAYS)
NP_ALL = len(STRIP)            # 45（旧文档里的 "48 块" 是历史写法）
CIDX = np.arange(NP_GREY, NP_ALL)   # 24 个色块的索引
NEUTRAL_PATCH = [i for i in CIDX if abs(PATCHES[i - NP_GREY][0] - PATCHES[i - NP_GREY][2]) <= 2
                 and abs(PATCHES[i - NP_GREY][0] - PATCHES[i - NP_GREY][1]) <= 2]

# ---- 硬件块结构（stock k=0 逐字节核对过：analysis/00_0100_40300020.decoded.bin
#      0x605D4829 起 100 B） ----
NODES = np.array([0.0, 64.0, 128.0, 192.0, 255.0])
X16 = [0, 0, 0, 0, 64, 128, 192, 255, 255, 255, 255, 255, 255, 255, 255, 255]
Z_TAIL = 128        # 库存 k=0 尾部重复节点的 z 值
CH_C = 128.0        # 通道中位
CH_SCALE = 255.0    # 通道幅值 → YCbCr 分量的换算系数


# --------------------------------------------------------------------------- #
# 相机曲线 / LUT 管线（与 tools/fuji_fit.py 逐式一致）
# --------------------------------------------------------------------------- #
def load_T():
    d = np.loadtxt(T_CSV, delimiter=",", skiprows=1)
    x, y = d[:, 0], d[:, 1]
    ok = y > 2.5
    return x[ok], y[ok]


TX, TY = load_T()
XW = float(TX[-1])
U = np.linspace(0, 1, 256)

# tone 表口径：索引按白点归一（docs/color/tone-calibration.md 定案）
X_WHITE = 0.6626            # T5 灰阶最亮点；若真机自检偏暗/偏亮就按比例改这里
STOCK_IMG = os.path.join(ROOT, "analysis", "00_0100_40300020.decoded.bin")
STOCK_BASE = 0x60021020
TONE_BASE, TONE_STEP = 0x605D8344, 0x200
srgb_encode = oetf          # 场景线性 → sRGB 编码（与 eotf 互逆）
LOOK_NOTES = {
    "reala_ace": "中等强度：Cb 段增益 0.62~1.02、Cr 0.75~1.16；x=128 精确过 (128,128)，灰阶中性。",
    "astia": "Cr 通道两端 >1、64->128 段被压到 0.14，靠中低色度区加饱和；tone 明度最高。",
    "eterna": "色度块接近恒等（多数段 0.83~1.0），观感主要靠 tone 的柔和高光。",
    "bleach_bypass": "强降饱和：Cb 段 0.16~0.48、Cr 0.19~0.68；x 节点分辨率非瓶颈，风险在 x<64 段外推。",
}
G_SCENE = 0.18 / 0.058      # 场景线性 → 传感器码 的尺度（18% 灰落在传感器码 0.058）
STEP = G_SCENE * 0.6626     # 索引 → 场景线性 的尺度 = 2.056
BLACK_HEAD = False   # True=黑位照抄库存表头（会抬黑；默认关，见文档）
_STOCK_TAB = None


def stock_head(n=8, slot=1):
    """库存 B 槽（dir0，实测能复现 T5 曲线的那张）tone 表前 n 项"""
    global _STOCK_TAB
    if _STOCK_TAB is None:
        with open(STOCK_IMG, "rb") as fh:
            fh.seek(TONE_BASE - STOCK_BASE + slot * TONE_STEP)
            _STOCK_TAB = np.frombuffer(fh.read(512), dtype="<u2").astype(int)
    return _STOCK_TAB[:n].tolist()


def T_of(raw):
    return np.interp(np.log(np.clip(raw, TX[0], TX[-1])), np.log(TX), TY)


def T_inv(jpeg):
    order = np.argsort(TY)
    return np.exp(np.interp(jpeg, TY[order], np.log(TX[order])))


def lut_disp(lut, size, srgb):
    """sRGB 编码 0..255 → 富士 cube → 显示值 0..255（cube 输入=编码值，输出=显示值）

    与 tone 表同一构造：table[i] = cube(sRGB_encode(scene))*1023。
    所以这里不做 eotf/oetf 环绕，cube 后面没有第二条曲线。
    """
    return apply_lut(lut, size, np.clip(srgb / 255.0, 0.0, 1.0)) * 255.0


def tone_table(lut, size, black_head=BLACK_HEAD):
    """目标观感的显示曲线 F(raw) → 256 项 10 位 tone 表。

    口径（docs/color/tone-calibration.md，2026-11-15 定案）：
        表是**10 位显示值曲线**，display8 = table[i]/1023*255；
        索引 i 对应 raw 线性值 r = i/255 * X_WHITE（白点归一，不是满量程）。
    所以正确写法是 table[i] = round(F(r)*1023)，F = 该观感的目标显示曲线 0..1：

        scene = i/255 * STEP,  e = sRGB_encode(scene),  table[i] = cube(e,e,e)*1023
        （STEP = g*x_white, g = 0.18/0.058；18% 灰 ⇒ scene=0.18 ⇒ 索引 22）

    旧的 T⁻¹(target)/x_white*1023 是「表后还有一条实测曲线」的线性域口径，
    已被否（模型 C 全线 RMS>30），这里不再使用。
    """
    scene = np.arange(256) / 255.0 * STEP             # 索引 i 对应的场景线性反射率
    e = srgb_encode(np.clip(scene, 0.0, 1.0))         # 场景线性 → sRGB 编码（cube 的输入域）
    pts = np.repeat(e[:, None], 3, axis=1)
    f = apply_lut(lut, size, np.where(pts > 1.0, 1.0, pts))[:, 0]  # cube 输出（线性光→显示值）
    tab = np.round(np.clip(f, 0, 1) * 1023).astype(int)
    if black_head:
        # 表头 raw<0.01 处相机曲线不可信（黑位钳位/降噪），照抄库存 B 槽前 8 项
        h = stock_head(8)
        tab[:len(h)] = h
    tab = np.maximum.accumulate(np.clip(tab, 0, 1023))
    return tab


def tone_only(tab, patches):
    """色块 sRGB → tone 表 → 相机显示值 0..255（新口径：索引 = raw/x_white*255）"""
    lin = eotf(patches / 255.0)                       # 色块的线性反射率
    idx = np.clip(lin / STEP, 0, 1)                   # 场景线性 → 表索引
    return np.interp(idx, np.arange(256) / 255.0, np.asarray(tab, float) / 1023.0) * 255.0


# --------------------------------------------------------------------------- #
# 硬件坐标系
# --------------------------------------------------------------------------- #
def to_ch(v):
    """YCbCr 分量（-0.5..0.5）→ u8 通道值（0..255，中位 128），不裁切"""
    return CH_C + CH_SCALE * v


def from_ch(c):
    return (c - CH_C) / CH_SCALE


def curve(nodes, x):
    """浮点曲线：5 个节点 (0,64,128,192,255) 上的分段线性"""
    return np.interp(x, NODES, np.asarray(nodes, float))


def _monotone(n, strict=True):
    """把 5 个节点压回 0<=y0<=y64<=128<=y192<=y255<=255。

    strict=True 时每段至少升 1 级（x 步长 64 ⇒ 段增益 z>=2）：
    库存里 z=0 只出现在 dx=0 的填充节点上，**dx>0 且 z=0 的「硬钳位段」
    没有真机证据**（见 docs/color/chroma-curve.md）。宁可损失一点点误差，
    也不要在关键路径上依赖一个没验证过的硬件行为。
    """
    n = np.asarray(n, float).copy()
    n[2] = CH_C
    n[0] = float(np.clip(n[0], 0, 128))
    n[1] = float(np.clip(n[1], n[0], 128))
    n[3] = float(np.clip(n[3], 128, 255))
    n[4] = float(np.clip(n[4], n[3], 255))
    if strict:
        n[1] = min(max(n[1], n[0] + 1), 127)
        n[0] = min(n[0], n[1] - 1)
        n[3] = min(max(n[3], 129), 254)
        n[4] = max(n[4], n[3] + 1)
    return n


# --------------------------------------------------------------------------- #
# 100 B 色度块：编码 / 解码 / 整数模拟
# --------------------------------------------------------------------------- #
def nodes_to_block(nodes, dq=True):
    """5 个节点值（x=0/64/128/192/255）→ 16 项 x/y/z（z[0]=0，z[i]=段增益）"""
    n = [int(round(v)) for v in _monotone(nodes)]
    if dq:
        n = [int(np.clip(v, 0, 255)) for v in n]
    n[2] = 128
    # 单调 + 界内（1 个样本的偶然越界在这里被压回，最多影响 1 级）；严格段升 1 级
    n[1] = min(max(n[1], n[0] + 1), 127)
    n[0] = min(n[0], n[1] - 1)
    n[3] = min(max(n[3], 129), 254)
    n[4] = max(n[4], n[3] + 1)
    y = [n[0], n[0], n[0], n[0], n[1], n[2], n[3], n[4]] + [n[4]] * 8
    z = [0] * 17
    for i in range(1, 16):
        dx = X16[i] - X16[i - 1]
        if dx == 0:
            z[i] = Z_TAIL if i >= 8 else 0
            continue
        q = int(round((y[i] - y[i - 1]) * 128 / dx))
        if q > 255:                       # z 是 u8，斜率上限 ≈1.99
            raise SystemExit(f"段增益 {q} 溢出 u8（节点 {n}）")
        if q < 1:                         # dx>0 的段不允许出现 z=0（未验证行为）
            raise SystemExit(f"段增益 {q} 非法（节点 {n}）")
        z[i] = q
    return n, list(X16), y, z


def block_hex(cb, cr):
    x, y, z = cb["x"], cb["y"], cb["z"]
    x2, y2, z2 = cr["x"], cr["y"], cr["z"]
    b = bytes(x + y + z + [0] + x2 + y2 + z2 + [0])
    assert len(b) == 100
    return b.hex()


def decode_block(h):
    b = bytes.fromhex(h)
    assert len(b) == 100
    cb = {"x": list(b[0:16]), "y": list(b[16:32]), "z": list(b[32:49])}
    cr = {"x": list(b[50:66]), "y": list(b[66:82]), "z": list(b[82:99])}
    assert b[49] == 0 and b[99] == 0, "pad 必须是 0"
    return cb, cr


def hw_curve(ch, v):
    """按硬件的方式求值：找 dx>0 的第一段满足 v < x[i]，out = y[i-1] + round(dx_in*z[i]/128)。

    整数实现（z 是 u8 段增益，128 = 1.0），输出裁到 0..255。
    证据等级：**模拟器**（结构来自 stock k=0/k=2/k=3 的逐字节逆推，
    docs/color/chroma-curve.md 的 S11 半块实验证明 y 只作用于本通道）。
    """
    x, y, z = ch["x"], ch["y"], ch["z"]
    v = float(v)
    last = None
    for i in range(1, 16):
        dx = x[i] - x[i - 1]
        if dx <= 0:
            continue
        last = i
        if v < x[i]:
            out = y[i - 1] + (v - x[i - 1]) * z[i] / 128.0
            return float(np.clip(np.round(out), 0, 255))
    i = last
    out = y[i - 1] + (v - x[i - 1]) * z[i] / 128.0
    return float(np.clip(np.round(out), 0, 255))


def block_reconstruct_check(ch):
    """用 x/z 重建 y（验证 z 与 y 自洽）；返回最大偏差"""
    worst = 0.0
    for i in range(1, 16):
        dx = ch["x"][i] - ch["x"][i - 1]
        if dx <= 0:
            continue
        pred = ch["y"][i - 1] + dx * ch["z"][i] / 128.0
        worst = max(worst, abs(pred - ch["y"][i]))
    return worst


# --------------------------------------------------------------------------- #
# 拟合目标上下文（45 个样本一次算好，坐标下降里只做曲线求值）
# --------------------------------------------------------------------------- #
class Ctx:
    def __init__(self, tab, lut, size):
        self.disp = tone_only(tab, STRIP)
        self.fuji = lut_disp(lut, size, STRIP) / 255.0
        self.y, self.cb, self.cr = ycbcr(self.disp / 255.0)
        _, tcb, tcr = ycbcr(self.fuji)
        self.xc, self.xr = to_ch(self.cb), to_ch(self.cr)
        self.tc, self.tr = to_ch(tcb), to_ch(tcr)
        self._cbc = to_ch(self.cb)
        self._crc = to_ch(self.cr)

    def sim(self, nodes_cb, nodes_cr):
        cb2 = from_ch(curve(nodes_cb, self._cbc))
        cr2 = from_ch(curve(nodes_cr, self._crc))
        return from_ycbcr(self.y, cb2, cr2)

    def err(self, nodes_cb, nodes_cr, mask=None):
        e = np.abs(self.sim(nodes_cb, nodes_cr) - self.fuji).mean(axis=1) * 255.0
        return e if mask is None else e[mask]

    def e1h(self, nodes_cb, nodes_cr, mask=None):
        return float(self.err(nodes_cb, nodes_cr, mask).mean())

    def sim_block(self, cb, cr):
        """整数模拟：真正的 100 B 块按 x/y/z 求值"""
        cb2 = from_ch(np.array([hw_curve(cb, v) for v in self._cbc]))
        cr2 = from_ch(np.array([hw_curve(cr, v) for v in self._crc]))
        return from_ycbcr(self.y, cb2, cr2)

    def e1h_block(self, cb, cr, mask=None):
        e = np.abs(self.sim_block(cb, cr) - self.fuji).mean(axis=1) * 255.0
        return float(e.mean() if mask is None else e[mask].mean())


# --------------------------------------------------------------------------- #
# 四档拟合（每通道独立；y[128]=128 硬约束 → 中性不染色）
# --------------------------------------------------------------------------- #
def _ls_gain(x, t, w):
    d = x - CH_C
    den = float(np.sum(w * d * d))
    if den < 1e-9:
        return 1.0
    return float(np.sum(w * d * (t - CH_C)) / den)


def fit_M0(x, t, w):
    k = _ls_gain(x, t, w)
    return np.array([128 - 128 * k, 128 - 64 * k, 128.0, 128 + 64 * k, 128 + 127 * k])


def fit_M1(x, t, w):
    """两侧各自一个增益（都过 (128,128)）"""
    out = np.array([128.0, 128.0, 128.0, 128.0, 128.0])
    lo = x < CH_C
    hi = ~lo
    if lo.sum() >= 2:
        k = _ls_gain(x[lo], t[lo], w[lo])
        out[0], out[1] = 128 - 128 * k, 128 - 64 * k
    if hi.sum() >= 2:
        k = _ls_gain(x[hi], t[hi], w[hi])
        out[3], out[4] = 128 + 64 * k, 128 + 127 * k
    return _monotone(out)


def _interp_weights(x):
    A = np.zeros((len(x), len(NODES)))
    for i, v in enumerate(x):
        j = int(np.clip(np.searchsorted(NODES, v) - 1, 0, len(NODES) - 2))
        f = (v - NODES[j]) / (NODES[j + 1] - NODES[j])
        f = float(np.clip(f, 0.0, 1.0))
        A[i, j] += 1.0 - f
        A[i, j + 1] += f
    return A


def fit_M2(x, t, w, prior, lam, iters=4000):
    """通道域 4 个自由节点（y0,y64,y192,y255）+ 固定 y128=128。
    目标 = 加权最小二乘 + lam*||y-prior||^2；单调/界用精确投影（确定性）。"""
    A = _interp_weights(x)
    H = A.T @ (w[:, None] * A) + lam * np.eye(5)
    g = A.T @ (w * t) + lam * np.asarray(prior, float)
    idx = [0, 1, 3, 4]
    Hs = H[np.ix_(idx, idx)]
    gs = g[idx] - H[np.ix_(idx, [2])].ravel() * CH_C
    L = float(np.linalg.eigvalsh(Hs).max())
    step = 0.98 / max(L, 1e-9)

    def project(y):
        lo = np.clip(y[[0, 1]], 0, 128)
        if lo[0] > lo[1]:
            lo[:] = float(lo.mean())
        up = np.clip(y[[3, 4]], 128, 255)
        if up[0] > up[1]:
            up[:] = float(up.mean())
        return np.array([lo[0], lo[1], CH_C, up[0], up[1]])

    y = project(np.asarray(prior, float))
    for _ in range(iters):
        q = y.copy()
        q[idx] = y[idx] - step * (Hs @ y[idx] - gs)
        q = project(q)
        if np.max(np.abs(q - y)) < 1e-10:
            y = q
            break
        y = q
    return y


M2_LAM = 0.2
# 复杂度从低到高：M0(1 参数) < M1(2) < M4(3，y[0] 钉在 M1 外推) < M2(4，平滑先验) < M3(4，自由)
MODELS = ["M0", "M1", "M4", "M2", "M3"]
N_PARAMS = {"M0": 1, "M1": 2, "M4": 3, "M2": 4, "M3": 4}


def _bounds(arr, j):
    """坐标下降的可行区间（含严格段升 1 级：z>=2，杜绝 dx>0 且 z=0 的段）"""
    if j == 0:
        return 0, min(255, int(arr[1]) - 1)
    if j == 1:
        return int(arr[0]) + 1, 127
    if j == 3:
        return 129, min(255, int(arr[4]) - 1)
    return int(arr[3]) + 1, 255


def _obj(ctx, cb, cr, mask):
    return ctx.e1h(cb, cr, mask)


def fit_M3(ctx, prior_cb, prior_cr, mask=None, passes=4, tol=0.005, free0=True):
    """指标域 4 节点：直接最小化 e1h；整数坐标下降 + 单调投影。
    最后一步「近乎等价解里取最接近 M1 先验的那个」（等价于温和的平滑正则，
    但不引入 λ 扫描：确定性、可解释）。
    free0=False 时把 y[0] 钉在 M1 的线性外推上（x<64 区间样本太少，
    让它自由会在无数据区做出硬钳位；这是更保守的一档）。"""
    cb = _monotone(np.round(np.asarray(prior_cb, float)))
    cr = _monotone(np.round(np.asarray(prior_cr, float)))
    cur = _obj(ctx, cb, cr, mask)
    free = (0, 1, 3, 4) if free0 else (1, 3, 4)
    for _ in range(passes):
        moved = False
        for ch in ("cb", "cr"):
            arr = cb if ch == "cb" else cr
            for j in free:
                base = int(arr[j])
                lo, hi = _bounds(arr, j)
                best_v, best_e = base, cur
                for v in range(lo, hi + 1):
                    if v == base:
                        continue
                    arr[j] = float(v)
                    e = _obj(ctx, cb, cr, mask)
                    if e < best_e - 1e-9:
                        best_e, best_v = e, v
                arr[j] = float(best_v)
                if best_v != base:
                    moved = True
                    cur = best_e
        if not moved:
            break
    # 简化步：误差落在 best*(1+tol) 内的取值范围里，取最接近先验的那个值
    for ch in ("cb", "cr"):
        arr = cb if ch == "cb" else cr
        prior = prior_cb if ch == "cb" else prior_cr
        for j in free:
            base = int(arr[j])
            lo, hi = _bounds(arr, j)
            best, keep = None, []
            for v in range(lo, hi + 1):
                arr[j] = float(v)
                e = _obj(ctx, cb, cr, mask)
                if best is None or e < best - 1e-9:
                    best, keep = e, [v]
                elif e <= best * (1.0 + tol):
                    keep.append(v)
            cand = min(keep, key=lambda v: (abs(v - float(prior[j])), v))
            arr[j] = float(cand)
    return _monotone(cb), _monotone(cr)


def fit_all(ctx, xc, xr, tc, tr, mask_fit, mask_obj=None):
    """在给定样本集上拟合四档模型，返回 {(ch, model): nodes}"""
    out = {}
    w = np.ones(NP_ALL)
    xcs, xrs, tcs, trs = xc[mask_fit], xr[mask_fit], tc[mask_fit], tr[mask_fit]
    ws = w[mask_fit]
    m1c = fit_M1(xcs, tcs, ws)
    m1r = fit_M1(xrs, trs, ws)
    out[("cb", "M0")] = fit_M0(xcs, tcs, ws)
    out[("cr", "M0")] = fit_M0(xrs, trs, ws)
    out[("cb", "M1")] = m1c
    out[("cr", "M1")] = m1r
    out[("cb", "M2")] = fit_M2(xcs, tcs, ws, m1c, M2_LAM)
    out[("cr", "M2")] = fit_M2(xrs, trs, ws, m1r, M2_LAM)
    cb4, cr4 = fit_M3(ctx, m1c, m1r, mask_obj, free0=False)
    out[("cb", "M4")] = cb4
    out[("cr", "M4")] = cr4
    cb3, cr3 = fit_M3(ctx, m1c, m1r, mask_obj, free0=True)
    out[("cb", "M3")] = cb3
    out[("cr", "M3")] = cr3
    return out


# --------------------------------------------------------------------------- #
# 误差口径
# --------------------------------------------------------------------------- #
def e1_gain_by_luma(tab, lut, size):
    """旧口径 e1：tone + 按亮度分箱的饱和增益（fuji_fit 的 simulate()，逐式复刻）。
    增益曲线只用 24 个色块拟合（灰阶不参与），e1 取 45 个样本的 RGB 平均绝对误差。"""
    disp = tone_only(tab, STRIP)
    fuji = lut_disp(lut, size, STRIP) / 255.0
    y0, cb0, cr0 = ycbcr(disp / 255.0)
    ct = lut_disp(lut, size, np.array(PATCHES, float)) / 255.0
    yp, cbp, crp = ycbcr(tone_only(tab, np.array(PATCHES, float)) / 255.0)
    _, cbt, crt = ycbcr(ct)
    cp, ctg = np.hypot(cbp, crp), np.hypot(cbt, crt)
    gain = np.where(cp > 0.01, ctg / np.maximum(cp, 1e-6), 1.0)
    order = np.argsort(yp)
    grid = np.linspace(0, 1, 16)
    gcurve = np.interp(grid, yp[order], gain[order])
    gg = np.interp(y0, grid, gcurve)
    sim = from_ycbcr(y0, cb0 * gg, cr0 * gg)
    return float(np.abs(sim - fuji).mean() * 255), gcurve


# --------------------------------------------------------------------------- #
# 单条 look
# --------------------------------------------------------------------------- #
def build_look(name, cube_base, verbose=False):
    cube = os.path.join(CUBE_DIR, cube_base + "_sRGB.cube")
    lut, size = parse_cube(cube)
    tab = tone_table(lut, size)
    e1, gcurve = e1_gain_by_luma(tab, lut, size)

    ctx = Ctx(tab, lut, size)
    xc, xr, tc, tr = ctx.xc, ctx.xr, ctx.tc, ctx.tr
    tgt = ctx.fuji

    # ---- 每通道四档拟合（in-sample，只用 24 个色块；灰阶由 y[128]=128 约束保证）----
    fit_in = fit_all(ctx, xc, xr, tc, tr, CIDX)
    e1h_in = {m: ctx.e1h(fit_in[("cb", m)], fit_in[("cr", m)]) for m in MODELS}

    # ---- 留一交叉验证（24 个色块逐个留出，其余 23 个重拟合）----
    loo = {m: np.zeros(len(CIDX)) for m in MODELS}
    for k, i in enumerate(CIDX):
        keep = np.array([j for j in CIDX if j != i])
        f = fit_all(ctx, xc, xr, tc, tr, keep)
        for m in MODELS:
            e = ctx.e1h(f[("cb", m)], f[("cr", m)], mask=np.array([j == i for j in range(NP_ALL)]))
            loo[m][k] = e
    loo_col = {m: float(loo[m].mean()) for m in MODELS}

    # ---- 选型：LOO 最优；更复杂模型要赢简单模型 3% 才启用（逐通道各判一次）----
    best = min(MODELS, key=lambda m: loo_col[m])
    chosen = {"cb": best, "cr": best}
    for ch in ("cb", "cr"):
        for m in MODELS:
            if m == best:
                break
            if loo_col[m] <= loo_col[best] * 1.03:
                chosen[ch] = m
                break

    nodes_cb = fit_in[("cb", chosen["cb"])]
    nodes_cr = fit_in[("cr", chosen["cr"])]
    n_cb, x_cb, y_cb, z_cb = nodes_to_block(nodes_cb)
    n_cr, x_cr, y_cr, z_cr = nodes_to_block(nodes_cr)

    # ---- 误差：e1h（浮点曲线）与 e1h_block（整数 100 B 块模拟）----
    cb_d = {"x": x_cb, "y": y_cb, "z": z_cb}
    cr_d = {"x": x_cr, "y": y_cr, "z": z_cr}
    e1h = ctx.e1h(nodes_cb, nodes_cr)
    hexblock = block_hex(cb_d, cr_d)
    dcb, dcr = decode_block(hexblock)          # 从自己的 hex 解回来，闭包自检
    e1h_block = ctx.e1h_block(dcb, dcr)
    zc_err = max(block_reconstruct_check(dcb), block_reconstruct_check(dcr))

    err = ctx.err(nodes_cb, nodes_cr)
    grey_err = float(err[:NP_GREY].mean())
    col_err = float(err[NP_GREY:].mean())

    # 灰阶中性：x=128 是否精确过 128；灰阶残差
    neutral_ok = (y_cb[5] == 128 and y_cr[5] == 128)
    neutral_tint = float(ctx.e1h_block(dcb, dcr, mask=np.array(
        [i < NP_GREY or i in NEUTRAL_PATCH for i in range(NP_ALL)])))

    # ---- 关键点（tone，新口径：索引 = raw / x_white * 255）----
    UI = np.arange(256) / 255.0

    def idx_of(v):                       # v = 场景线性反射率
        return int(round(min(v / STEP, 1.0) * 255))

    def disp_of_v(v):                    # 场景线性 → 显示值 0..255（= 表值/1023*255）
        return float(np.interp(min(v / STEP, 1.0), UI, tab / 1023.0) * 255.0)

    def slope_around(v, d=0.02):
        a, b = max(v - d, 1e-4), v + d
        return float((np.log(max(disp_of_v(b), 1e-3)) - np.log(max(disp_of_v(a), 1e-3)))
                     / (np.log(b) - np.log(a)))

    # 富士对 18% 灰（18% **线性**反射率，和 disp18 同口径）的目标显示值
    e18 = float(srgb_encode(np.array(0.18)))
    tgt18 = float(apply_lut(lut, size, np.array([[[e18, e18, e18]]], float)).reshape(-1)[0] * 255.0)
    keys = {f"v{v:.2f}": int(tab[idx_of(v)]) for v in (0.05, 0.18, 0.50, 0.90)}
    keys_idx = {f"v{v:.2f}": idx_of(v) for v in (0.05, 0.18, 0.50, 0.90)}
    disp18 = disp_of_v(0.18)
    gamma18 = slope_around(0.18)
    gamma50 = slope_around(0.50)

    spec = {
        "look": name,
        "cube": os.path.basename(cube),
        "tone": [int(v) for v in tab],
        "chroma": {
            "cb_x": x_cb, "cb_y": y_cb, "cb_z": z_cb,
            "cr_x": x_cr, "cr_y": y_cr, "cr_z": z_cr,
        },
        "chroma_block_hex": hexblock,
        "e1": round(e1, 4),
        "e1h": round(e1h, 4),
        "e1h_block": round(e1h_block, 4),
        "e1h_loo": round(loo_col[best], 4),
        "model": {"cb": chosen["cb"], "cr": chosen["cr"],
                  "select": "LOO(24 色块, 色块 MAE)；复杂模型需赢 3%"},
        "candidates": {m: {"e1h_in": round(e1h_in[m], 4),
                           "loo_color": round(loo_col[m], 4)} for m in MODELS},
        "x_white": X_WHITE,
        "g_scene": round(G_SCENE, 4),
        "step": round(STEP, 5),
        "tone_convention": "display10: display8 = table[i]/1023*255, i = raw/x_white*255 (docs/color/tone-calibration.md)",
        "tone_keys": keys,
        "tone_keys_index": keys_idx,
        "gray18_disp": round(disp18, 2),
        "gray18_fuji": round(tgt18, 2),
        "gamma18": round(gamma18, 3),
        "gamma50": round(gamma50, 3),
        "error_split": {"gray": round(grey_err, 3), "color": round(col_err, 3),
                        "p90": round(float(np.percentile(err, 90)), 2)},
        "neutral_check": {
            "pass": bool(neutral_ok),
            "cb_y_at_x128": y_cb[5], "cr_y_at_x128": y_cr[5],
            "neutral_mae": round(neutral_tint, 3),
            "fuji_grey_tint_max": round(float(max(np.abs(ycbcr(tgt)[1][:NP_GREY]).max(),
                                                  np.abs(ycbcr(tgt)[2][:NP_GREY]).max()) * 255), 2),
        },
        "consistency": {"z_roundtrip_max": round(zc_err, 3),
                        "e1h_vs_block": round(abs(e1h - e1h_block), 4)},
        "gain": {
            "M0_cb": round(float((128 - fit_M0(xc[CIDX], tc[CIDX], np.ones(len(CIDX)))[0]) / 128), 4),
            "M0_cr": round(float((128 - fit_M0(xr[CIDX], tr[CIDX], np.ones(len(CIDX)))[0]) / 128), 4),
            "block_cb_seg_gain": [z_cb[i] / 128 for i in (4, 5, 6, 7)],
            "block_cr_seg_gain": [z_cr[i] / 128 for i in (4, 5, 6, 7)],
        },
        "evidence": "曲线结构=静态（结构/公式推断）；e1h=模拟器（tone+100B 整数块）；真机观感=推理",
        "notes": LOOK_NOTES.get(name, ""),
    }
    dbg = {"ctx": ctx, "err": err, "gcurve": gcurve, "fit_in": fit_in,
           "loo": loo, "loo_col": loo_col, "nodes_cb": nodes_cb,
           "nodes_cr": nodes_cr, "cb_d": dcb, "cr_d": dcr}
    return spec, dbg


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--looks", default=",".join(n for n, _ in LOOKS))
    ap.add_argument("--outdir", default=os.path.join(ROOT, ".tmp", "looks_final"))
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-check", action="store_true", help="跳过与 .tmp/cap 的交叉核对")
    ap.add_argument("--black-head", action="store_true",
                    help="黑位前 8 项照抄库存 B 槽（会抬黑，默认关）")
    ap.add_argument("--diag", action="store_true", help="打印 table[19]/[69]/[192] 与 display8")
    a = ap.parse_args(argv)
    global BLACK_HEAD
    BLACK_HEAD = bool(a.black_head)

    os.makedirs(a.outdir, exist_ok=True)
    summary = []
    for name, base in LOOKS:
        if name not in a.looks.split(","):
            continue
        spec, dbg = build_look(name, base, a.verbose)
        if a.diag:
            tb = spec["tone"]
            px = {f"table[{i}]": (tb[i], round(tb[i] / 1023.0 * 255.0, 1))
                  for i in (19, 69, 192)}
            print("  diag " + "  ".join(f"{k}={v[0]}(disp {v[1]})" for k, v in px.items()))

        # ---- 与旧产物交叉核对（tone / e1）----
        cap = os.path.join(CAP_DIR, name + "_spec.json")
        if not a.no_check and os.path.exists(cap):
            old = json.load(open(cap, encoding="utf-8"))
            dt = max(abs(int(x) - int(y)) for x, y in zip(spec["tone"], old["tone"]))
            de = abs(spec["e1"] - old["err_tone_only"])
            spec["crosscheck"] = {"tone_maxdiff": int(dt), "e1_old": round(old["err_tone_only"], 4),
                                  "e1_delta": round(de, 6)}
            if dt:
                print(f"  [!] {name}: tone 与旧口径产物差(正常,口径已改) {dt} 级")
            if de > 0.01:
                print(f"  [!] {name}: e1 与旧口径产物差(正常,口径已改) {de:.4f}（口径需核对）")

        path = os.path.join(a.outdir, name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False, indent=1)

        summary.append({
            "look": name, "e1": spec["e1"], "e1h": spec["e1h"],
            "e1h_block": spec["e1h_block"], "e1h_loo": spec["e1h_loo"],
            "model_cb": spec["model"]["cb"], "model_cr": spec["model"]["cr"],
            "cb_y": spec["chroma"]["cb_y"][:8], "cr_y": spec["chroma"]["cr_y"][:8],
            "cb_gain": spec["gain"]["block_cb_seg_gain"],
            "cr_gain": spec["gain"]["block_cr_seg_gain"],
            "e1_minus_e1h": round(spec["e1h"] - spec["e1"], 3),
        })
        print(f"\n=== {name} ===")
        print(f"  e1  (gain-by-luma, 旧口径)  = {spec['e1']:.3f}")
        print(f"  e1h (tone + 硬件 Cb/Cr)     = {spec['e1h']:.3f}"
              f"   [灰 {spec['error_split']['gray']:.2f} / 色 {spec['error_split']['color']:.2f}"
              f" / p90 {spec['error_split']['p90']:.1f}]")
        print(f"  e1h_block (整数 100B 模拟)  = {spec['e1h_block']:.3f}"
              f"   z 往返偏差 {spec['consistency']['z_roundtrip_max']:.2f}")
        for m, v in spec["candidates"].items():
            print(f"      {m:3s} in {v['e1h_in']:6.3f}   loo {v['loo_color']:6.3f}")
        print(f"  选型 cb={spec['model']['cb']}  cr={spec['model']['cr']}")
        print(f"  Cb y = {spec['chroma']['cb_y'][:8]}  z = {spec['chroma']['cb_z']}")
        print(f"  Cr y = {spec['chroma']['cr_y'][:8]}  z = {spec['chroma']['cr_z']}")
        print(f"  tone v=.05/.18/.50/.90 -> {spec['tone_keys']}  18%显示 {spec['gray18_disp']:.1f}"
              f" (富士 {spec['gray18_fuji']:.1f})  γ18={spec['gamma18']:.3f}")
        print(f"  中性: y128={spec['neutral_check']['cb_y_at_x128']}/"
              f"{spec['neutral_check']['cr_y_at_x128']} ok={spec['neutral_check']['pass']}"
              f"  中性样本 MAE={spec['neutral_check']['neutral_mae']:.3f}"
              f"  富士灰染色max={spec['neutral_check']['fuji_grey_tint_max']}")
        print(f"  -> {path}")

    sm = os.path.join(a.outdir, "summary.json")
    with open(sm, "w", encoding="utf-8") as f:
        json.dump({"tool": "tools/look_spec4.py", "samples": NP_ALL,
                   "gray_patches": NP_GREY, "color_patches": len(CIDX),
                   "chromatic_patches": len(CIDX) - len(NEUTRAL_PATCH),
                   "neutral_patch_idx": [int(i - NP_GREY) for i in NEUTRAL_PATCH],
                   "evidence": "曲线结构=静态；e1h=模拟器；真机观感=推理",
                   "looks": summary}, f, ensure_ascii=False, indent=1)
    print(f"\n汇总 -> {sm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
