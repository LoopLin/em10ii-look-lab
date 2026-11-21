# -*- coding: utf-8 -*-
"""rawpipe —— 「ORF RAW → 相机 JPG」纯离线复现管线（库 + CLI）。

设计口径（全部为可复算的数值，不依赖任何他人私有工具）
-----------------------------------------------------
1. 解码：rawpy，`gamma=(1,1)`, `no_auto_bright=True`, `output_bps=16`,
   `use_camera_wb=False`, `output_color=rawpy.ColorSpace.raw`, `no_auto_scale=True`
   ⇒ 输出 = 传感器码 − 黑电平（1:1，未缩放、未做白平衡、未做色彩矩阵）。
   线性值 `lin = (code - BLACK) / (WHITE - BLACK)`，白平衡增益在浮点域显式相乘。
   （实测：`no_auto_scale=True` 时 rawpy 连白平衡也不乘，`use_camera_wb` 不起作用，
   所以 WB 必须自己乘 —— 见 `load_orfs` 的返包。）

2. tone 表：镜像 `analysis/00_0100_40300020.decoded.bin`，映射
   `文件偏移 = 文件内地址 - 0x60021020`。tone 表 RAM `0x605D8344 + k*0x200`，
   256 × u16 小端，值域 0..1023 = **10 位显示值**（`display8 = v/1023*255`）。
   基址自检：`0x60600920` 的 u32 必须等于 `0x605D8344`。

3. 索引口径：`i = clip(Y / x_white * 255 * s, 0, 255)`，`Y` 为线性亮度。
   **重要（实测）**：`(x_white, s)` 只以乘积 `P = 255*s/x_white` 进入管线，
   两者不可分别辨识；本库对外暴露 `index_scale`（= P），并在给 `x_white/s`
   时换算。默认 `x_white=1.1087, s=1.0` ⇒ P=230（本轮 9 组配对的最小二乘结果）。

4. 色度块：RAM `0x605D4829 + k*0x64`，100 B/块。库存 k=0 为恒等。
   块内 `z[i] = round((y[i]-y[i-1])*128/(x[i]-x[i-1]))`，读入时校验。

5. 顺序：线性 RGB → 3×3 CCM（默认单位阵）→ 亮度 → tone 表 → 色度块（YCbCr 域）
   → sRGB 编码 → 8 bit。

命令行
------
    python tools/rawpipe.py --orf refs/shoots/01_stock_contrast_series/PB110001.ORF \
        --out .tmp/sim/PB110001_sim.jpg --triplet
    python tools/rawpipe.py --orf .../.ORF --look .tmp/looks_final/eterna.json --out p.jpg
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

for _s in (sys.stdout, sys.stderr):   # 控制台可能是 GBK，强制 UTF-8 输出
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

try:  # rawpy 只在需要解码时才必须存在
    import rawpy
except Exception:  # pragma: no cover
    rawpy = None

# ---------------------------------------------------------------- 常量 / 镜像

RAM_BASE = 0x60021020            # 文件内地址 -> 文件偏移 的换算基址
MIRROR_DEFAULT = os.path.join('analysis', '00_0100_40300020.decoded.bin')

TONE_BASE = 0x605D8344           # tone 表基址（RAM）
TONE_STRIDE = 0x200
TONE_COUNT = 54
TONE_GUARD_ADDR = 0x60600920     # 基址自检用的指针槽
TONE_GUARD_EXPECT = 0x605D8344

CHROMA_BASE = 0x605D4829         # 色度块基址（RAM）
CHROMA_STRIDE = 0x64
CHROMA_COUNT = 65

BLACK = 252                      # 黑电平（四通道同）
WHITE = 4094                     # 白电平（任务书口径；rawpy 报 4095）

# 拟合得到的默认索引刻度：P = 255*s/x_white = 230
DEFAULT_INDEX_SCALE = 230.0
DEFAULT_X_WHITE = 255.0 / DEFAULT_INDEX_SCALE   # 1.1087
DEFAULT_S = 1.0
DEFAULT_TONE_K = 3               # dir1 的 B 槽（对比度 0 档的候选）
DEFAULT_CHROMA_K = 0             # 库存恒等块；标准拍照四档只读 k=0

_MIRROR_CACHE: dict = {}


def _resolve_mirror(path: str | None = None) -> str:
    if path:
        return path
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cand = os.path.join(here, MIRROR_DEFAULT)
    return cand if os.path.exists(cand) else MIRROR_DEFAULT


def load_mirror(path: str | None = None) -> np.ndarray:
    """把解码后的固件镜像读成只读 uint8 数组（只读，不产生任何 BIN）。"""
    p = _resolve_mirror(path)
    key = os.path.abspath(p)
    if key not in _MIRROR_CACHE:
        if not os.path.exists(p):
            raise FileNotFoundError('找不到固件镜像: %s' % p)
        _MIRROR_CACHE[key] = np.memmap(p, dtype=np.uint8, mode='r')
    return _MIRROR_CACHE[key]


def ram_offset(addr: int) -> int:
    """RAM 地址 → 镜像文件偏移。"""
    off = int(addr) - RAM_BASE
    if off < 0:
        raise ValueError('地址 %#x 低于镜像基址 %#x' % (addr, RAM_BASE))
    return off


def _u32(d: np.ndarray, addr: int) -> int:
    o = ram_offset(addr)
    return int(np.frombuffer(d[o:o + 4].tobytes(), dtype='<u4')[0])


def check_base(mirror: np.ndarray | None = None) -> int:
    """基址自检：返回 0x60600920 处的 u32，并断言等于 tone 表基址。"""
    d = load_mirror() if mirror is None else mirror
    got = _u32(d, TONE_GUARD_ADDR)
    if got != TONE_GUARD_EXPECT:
        raise RuntimeError('镜像基址自检失败: [%#x]=%#x，期望 %#x'
                           % (TONE_GUARD_ADDR, got, TONE_GUARD_EXPECT))
    return got


def tone_table(k: int, mirror: np.ndarray | None = None) -> np.ndarray:
    """读第 k 张 tone 表：uint16[256]，值域 0..1023（10 位显示值）。"""
    if not 0 <= k < TONE_COUNT:
        raise ValueError('k 越界: %d（0..%d）' % (k, TONE_COUNT - 1))
    d = load_mirror() if mirror is None else mirror
    check_base(d)
    o = ram_offset(TONE_BASE + k * TONE_STRIDE)
    t = np.frombuffer(d[o:o + 512].tobytes(), dtype='<u2').copy()
    if t.min() < 0 or t.max() > 1023:
        raise RuntimeError('tone 表 %d 值域异常: %d..%d' % (k, t.min(), t.max()))
    return t


def tone_table_display(k: int, mirror: np.ndarray | None = None) -> np.ndarray:
    """tone 表换算成 8 位显示值（0..255，浮点）。"""
    return tone_table(k, mirror).astype(np.float64) / 1023.0 * 255.0


def chroma_block(k: int, mirror: np.ndarray | None = None) -> dict:
    """读第 k 个色度块（100 B）：返回 Cb/Cr 的 x/y 节点与段增益 z。"""
    if not 0 <= k < CHROMA_COUNT:
        raise ValueError('k 越界: %d（0..%d）' % (k, CHROMA_COUNT - 1))
    d = load_mirror() if mirror is None else mirror
    o = ram_offset(CHROMA_BASE + k * CHROMA_STRIDE)
    b = np.frombuffer(d[o:o + 100].tobytes(), dtype=np.uint8)
    blk = {
        'k': int(k),
        'cb_x': b[0x00:0x10].astype(np.int32),
        'cb_y': b[0x10:0x20].astype(np.int32),
        'cb_z': b[0x20:0x31].astype(np.int32),
        'cr_x': b[0x32:0x42].astype(np.int32),
        'cr_y': b[0x42:0x52].astype(np.int32),
        'cr_z': b[0x52:0x63].astype(np.int32),
        'pad': (int(b[0x31]), int(b[0x63])),
        'identity': bool(np.array_equal(b[0x00:0x10], b[0x10:0x20])
                         and np.array_equal(b[0x32:0x42], b[0x42:0x52])),
    }
    # z 一致性自检。实测布局：z[i] 是「节点 i-1 → i」这一段的 7.8 定比增益
    # （恒等段 = 128），退化段（dx=0）填 0。见 docs/color/chroma-curve.md。
    for ch in ('cb', 'cr'):
        x, y, z = blk[ch + '_x'], blk[ch + '_y'], blk[ch + '_z']
        errs = []
        for i in range(1, 16):
            dx = int(x[i]) - int(x[i - 1])
            if dx == 0:
                continue
            want = int(np.rint((int(y[i]) - int(y[i - 1])) * 128.0 / dx))
            errs.append(abs(want - int(z[i])))
        blk[ch + '_z_err_max'] = int(max(errs) if errs else 0)
    return blk


# ---------------------------------------------------------------- 解码

_WB_NAMES = ('camera', 'daylight', 'none')


def _wb_gains(raw, wb: str) -> np.ndarray:
    if wb == 'camera':
        g = np.array(raw.camera_whitebalance[:3], dtype=np.float64)
    elif wb == 'daylight':
        g = np.array(raw.daylight_whitebalance[:3], dtype=np.float64)
    elif wb in ('none', 'off'):
        return np.ones(3)
    else:
        raise ValueError('wb 只支持 %s' % (_WB_NAMES,))
    return g / g[1]


def load_orfs(path: str, wb: str = 'camera', half_size: bool = False,
              color: str = 'raw') -> dict:
    """ORF → 线性 RGB（0..1，1.0 = 白电平），相机口径 RGB，已乘白平衡增益。

    返回 dict:
        lin   (H, W, 3) float32，线性、相机白平衡后、0..1
        wb    实际使用的增益（G 归一）
        meta  dict（尺寸/黑电平/白电平/文件名…）
    """
    if rawpy is None:
        raise RuntimeError('需要 rawpy（pip install rawpy）')
    raw = rawpy.imread(path)
    cs = rawpy.ColorSpace.raw if color == 'raw' else rawpy.ColorSpace.sRGB
    img = raw.postprocess(gamma=(1, 1), no_auto_bright=True, output_bps=16,
                          use_camera_wb=False, output_color=cs,
                          no_auto_scale=True, half_size=bool(half_size))
    lin = img.astype(np.float32) / float(WHITE - BLACK)
    g = _wb_gains(raw, wb)
    lin *= g.reshape((1, 1, 3)).astype(np.float32)

    # 与相机 JPG 对齐的裁切框（rawpy 已给出 crop_*；half_size 时减半）
    s = raw.sizes
    sc = 2 if half_size else 1
    crop = (s.crop_top_margin // sc, s.crop_top_margin // sc + s.crop_height // sc,
            s.crop_left_margin // sc, s.crop_left_margin // sc + s.crop_width // sc)
    meta = {
        'path': os.path.abspath(path), 'wb': wb, 'wb_gains': g.tolist(),
        'shape': tuple(img.shape), 'half_size': bool(half_size),
        'black': BLACK, 'white': WHITE,
        'black_level_raw': int(raw.black_level_per_channel[0]),
        'white_level_raw': int(raw.white_level),
        'camera_whitebalance': [float(x) for x in raw.camera_whitebalance[:3]],
        'daylight_whitebalance': [float(x) for x in raw.daylight_whitebalance[:3]],
        'crop': crop, 'sizes': {k: getattr(s, k) for k in
                                ('width', 'height', 'crop_width', 'crop_height',
                                 'crop_top_margin', 'crop_left_margin')},
    }
    raw.close()
    return {'lin': lin, 'wb': g, 'meta': meta}


def crop_to_jpg_frame(lin: np.ndarray, meta: dict) -> np.ndarray:
    """把线性图裁到相机 JPG 的取景框（4608×3456 或 half）。"""
    t0, t1, l0, l1 = meta['crop']
    return lin[t0:t1, l0:l1]


# ---------------------------------------------------------------- 基础色彩运算

LUMA_601 = np.array([0.299, 0.587, 0.114], dtype=np.float32)
LUMA_709 = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def luma(rgb: np.ndarray, weights=LUMA_601) -> np.ndarray:
    w = np.asarray(weights, dtype=np.float32)
    return (rgb[..., 0] * w[0] + rgb[..., 1] * w[1] + rgb[..., 2] * w[2])


def apply_ccm(rgb: np.ndarray, m: np.ndarray) -> np.ndarray:
    """线性 RGB 乘 3×3 矩阵。m 为 None / identity 时原样返回。"""
    if m is None:
        return rgb
    m = np.asarray(m, dtype=np.float32)
    if m.shape != (3, 3):
        raise ValueError('CCM 必须是 3×3')
    if np.allclose(m, np.eye(3)):
        return rgb
    return np.stack([rgb[..., 0] * m[i, 0] + rgb[..., 1] * m[i, 1] + rgb[..., 2] * m[i, 2]
                     for i in range(3)], axis=-1)


def srgb_encode(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def srgb_decode(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4))


def rgb_to_ycbcr(rgb8: np.ndarray) -> tuple:
    """BT.601 全范围 YCbCr（输入 0..255 显示值）。"""
    r, g, b = rgb8[..., 0], rgb8[..., 1], rgb8[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return y, cb, cr


def ycbcr_to_rgb(y: np.ndarray, cb: np.ndarray, cr: np.ndarray) -> np.ndarray:
    r = y + 1.402 * (cr - 128.0)
    g = y - 0.344136 * (cb - 128.0) - 0.714136 * (cr - 128.0)
    b = y + 1.772 * (cb - 128.0)
    return np.stack([r, g, b], axis=-1)


# ---------------------------------------------------------------- 管线

def tone_index(y_lin: np.ndarray, x_white: float = DEFAULT_X_WHITE,
               s: float = DEFAULT_S, index_scale: float | None = None) -> np.ndarray:
    """线性亮度 → tone 表索引（0..255）。只认乘积 P = 255*s/x_white。"""
    p = float(index_scale) if index_scale is not None else 255.0 * float(s) / float(x_white)
    return np.clip(y_lin * p, 0.0, 255.0)


def tone_display_case(index: np.ndarray, table: np.ndarray) -> np.ndarray:
    """索引 → 8 位显示值（表值 0..1023 → 0..255），分段线性插值。"""
    t = np.asarray(table, dtype=np.float64)
    if t.max() > 255.5:          # 原始表（0..1023）
        t = t / 1023.0 * 255.0
    return np.interp(index, np.arange(256.0, dtype=np.float64), t)


def apply_chroma_block(cb: np.ndarray, cr: np.ndarray, blk: dict) -> tuple:
    """按色度块的折线缩放 Cb/Cr（节点域为 0..255 显示域）。"""
    cb2 = np.interp(cb, np.asarray(blk['cb_x'], float), np.asarray(blk['cb_y'], float))
    cr2 = np.interp(cr, np.asarray(blk['cr_x'], float), np.asarray(blk['cr_y'], float))
    return cb2, cr2


# ---- 相机色彩矩阵与配套饱和度（2026-11-15 定案，见 docs/color/wb-gap.md） ----
# ORF MakerNote 0x2040:0x0200（三行和恰为 1.000，中性进中性出）。
# 实测定案：该矩阵 + sat=2.2 时，低通 RGB RMS 8.73→6.65、逐像素 10.84→9.51、
# 亮度 5.19→4.83。白平衡**不做任何修正**：JPG 的 WB_RBLevels 与 cam_mul 逐位相同。
DEFAULT_CCM = ((1.78125, -0.67188, -0.10938),
               (-0.26562, 1.54688, -0.28125),
               (0.04688, -0.46875, 1.42188))
DEFAULT_SAT = 2.2

DEFAULT_CFG = {
    'ccm': np.array(DEFAULT_CCM, dtype=np.float32),   # 默认=相机矩阵；None=单位阵
    'index_scale': DEFAULT_INDEX_SCALE,
    'x_white': DEFAULT_X_WHITE,
    's': DEFAULT_S,
    'tone': None,                   # 256 表（0..1023 或 0..255）或 None → tone_k
    'tone_k': DEFAULT_TONE_K,
    'chroma': None,                 # 色度块 dict 或 None → chroma_k
    'chroma_k': DEFAULT_CHROMA_K,
    'sat': DEFAULT_SAT,             # 色度整体增益（与 DEFAULT_CCM 配套）
    'mode': 'luma',                 # 'luma'：亮度过表；'perchannel'：逐通道过表
    'luma_weights': tuple(LUMA_601),
}


def render(lin: np.ndarray, cfg: dict | None = None,
           return_intermediates: bool = False) -> np.ndarray | dict:
    """线性 RGB（0..1）→ 8 位显示 RGB。见模块 docstring 的顺序说明。"""
    c = dict(DEFAULT_CFG)
    if cfg:
        c.update(cfg)

    rgb = apply_ccm(lin, c['ccm'])
    y = luma(rgb, c['luma_weights'])
    idx = tone_index(y, c['x_white'], c['s'], c['index_scale'])

    table = c['tone'] if c['tone'] is not None else tone_table(c['tone_k'])
    y_disp = tone_display_case(idx, table)

    if c['mode'] == 'perchannel':
        # 逐通道过表：每个通道用自己的线性值查同一条表
        ch = [tone_display_case(tone_index(rgb[..., i], c['x_white'], c['s'],
                                           c['index_scale']), table) for i in range(3)]
        rgb_disp = np.stack(ch, axis=-1)
        _, cb, cr = rgb_to_ycbcr(rgb_disp)
        y_out = luma(rgb_disp, c['luma_weights'])
    else:
        # 亮度过表：色度取自 sRGB 编码后的 RGB（模长口径与 8 位显示域一致）
        enc = srgb_encode(rgb) * 255.0
        _, cb, cr = rgb_to_ycbcr(enc)
        y_out = y_disp

    if c['sat'] != 1.0:
        cb = 128.0 + (cb - 128.0) * c['sat']
        cr = 128.0 + (cr - 128.0) * c['sat']

    blk = c['chroma'] if c['chroma'] is not None else chroma_block(c['chroma_k'])
    cb, cr = apply_chroma_block(cb, cr, blk)

    out = ycbcr_to_rgb(y_out, cb, cr)
    out8 = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    if not return_intermediates:
        return out8
    return {'rgb8': out8, 'y_disp': y_out, 'idx': idx, 'cb': cb, 'cr': cr,
            'luma_lin': y, 'cfg': c}


def render_file(orf: str, cfg: dict | None = None, wb: str = 'camera',
                half_size: bool = False) -> dict:
    """便捷封装：解码 + 裁到 JPG 取景框 + 渲染。"""
    d = load_orfs(orf, wb=wb, half_size=half_size)
    lin = crop_to_jpg_frame(d['lin'], d['meta'])
    out = render(lin, cfg, return_intermediates=True)
    out['lin'] = lin
    out['meta'] = d['meta']
    return out


# ---------------------------------------------------------------- look JSON

def load_look(path: str) -> dict:
    """读 `.tmp/looks_final/<look>.json`，取出 tone 表与色度 y 节点。

    返回 dict（可直接 merge 进 cfg）：
        {'tone': 256 表, 'chroma': {cb_x,cb_y,cb_z,cr_x,cr_y,cr_z},
         'index_scale': look 自带的索引刻度, 'name': 名字, 'raw': 原始 json}
    """
    if not os.path.exists(path):
        for cand in (os.path.join('.tmp', 'looks_final',
                                  path if path.endswith('.json') else path + '.json'),
                     os.path.join('docs', 'color', path)):
            if os.path.exists(cand):
                path = cand
                break
    with open(path, 'r', encoding='utf-8') as f:
        d = json.load(f)
    tone = d.get('tone')
    if tone is None:
        raise ValueError('look 文件里没有 tone[256]: %s' % path)
    tone = np.asarray(tone, dtype=np.float64).reshape(-1)
    if tone.size != 256:
        raise ValueError('tone 长度 %d ≠ 256' % tone.size)
    ch = d.get('chroma') or {}
    blk = None
    if ch.get('cb_x'):
        blk = {k: np.asarray(ch[k], dtype=np.int32) for k in
               ('cb_x', 'cb_y', 'cb_z', 'cr_x', 'cr_y', 'cr_z') if k in ch}
        if 'cb_z' not in blk:
            blk['cb_z'] = _derive_z(blk['cb_x'], blk['cb_y'])
        if 'cr_z' not in blk:
            blk['cr_z'] = _derive_z(blk['cr_x'], blk['cr_y'])
    xw = float(d.get('x_white', DEFAULT_X_WHITE))
    scale = d.get('index_scale')
    if scale is None:
        scale = 255.0 / xw if xw else DEFAULT_INDEX_SCALE
    return {'tone': tone, 'chroma': blk, 'index_scale': float(scale),
            'x_white': xw, 'name': d.get('look') or os.path.splitext(os.path.basename(path))[0],
            'raw': d}


def _derive_z(x, y) -> np.ndarray:
    """由 x/y 节点反推段增益 z：z[i] = round(128*(y[i]-y[i-1])/(x[i]-x[i-1]))。"""
    x = np.asarray(x, np.int32)
    y = np.asarray(y, np.int32)
    z = np.full(17, 0, np.int32)
    for i in range(1, 16):
        dx = int(x[i]) - int(x[i - 1])
        z[i] = 128 if dx == 0 else int(np.rint((int(y[i]) - int(y[i - 1])) * 128.0 / dx))
    z[16] = 0
    return z


# ---------------------------------------------------------------- 拼图 / IO

def save_preview(rgb8: np.ndarray, path: str, quality: int = 92) -> str:
    from PIL import Image
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    Image.fromarray(rgb8).save(path, quality=quality, subsampling=1)
    return path


def make_triptych(panels, path: str, width: int | None = 1600) -> str:
    """panels: [(title, HxWx3 uint8), ...] → 横向拼成一张（可等比缩放）。"""
    from PIL import Image, ImageDraw
    ims = []
    for title, arr in panels:
        im = Image.fromarray(arr)
        if width and im.width > width // max(len(panels), 1):
            w = width // len(panels)
            im = im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)
        ims.append((title, im))
    h = max(im.height for _, im in ims) + 22
    w = sum(im.width for _, im in ims) + 4 * (len(ims) + 1)
    canvas = Image.new('RGB', (w, h), (24, 24, 24))
    dr = ImageDraw.Draw(canvas)
    x = 4
    for title, im in ims:
        canvas.paste(im, (x, 22))
        dr.text((x + 2, 6), title, fill=(235, 235, 235))
        x += im.width + 4
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    canvas.save(path, quality=92, subsampling=1)
    return path


def load_reference_jpg(path: str) -> np.ndarray:
    from PIL import Image
    return np.asarray(Image.open(path).convert('RGB'))


# ---------------------------------------------------------------- CLI

def _parse_ccm(txt: str | None):
    if not txt or txt == 'auto':
        return np.array(DEFAULT_CCM, dtype=np.float32)
    if txt in ('identity', 'none', 'I'):
        return None
    vals = [float(x) for x in txt.replace(';', ',').split(',') if x.strip()]
    if len(vals) != 9:
        raise SystemExit('--ccm 需要 9 个逗号分隔的数，或 identity')
    return np.array(vals, dtype=np.float32).reshape(3, 3)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='ORF RAW → 相机 JPG 风格复现（离线，不刷机、不出 BIN）',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument('--orf', required=True, help='输入 .ORF')
    ap.add_argument('--jpg', help='配对的相机 JPG（用于三联图与误差）')
    ap.add_argument('--out', help='输出预览 JPEG')
    ap.add_argument('--wb', default='camera', choices=list(_WB_NAMES),
                    help='白平衡：camera / daylight / none')
    ap.add_argument('--tone-k', type=int, default=DEFAULT_TONE_K, help='tone 表编号')
    ap.add_argument('--chroma-k', type=int, default=DEFAULT_CHROMA_K, help='色度块编号')
    ap.add_argument('--x-white', type=float, default=DEFAULT_X_WHITE, help='索引白点')
    ap.add_argument('--s', type=float, default=DEFAULT_S, help='索引缩放')
    ap.add_argument('--index-scale', type=float, default=None,
                    help='直接给 P=255*s/x_white（给了就忽略 x-white/s）')
    ap.add_argument('--ccm', default='auto',
                    help='3×3 CCM：auto（默认，相机矩阵）/ identity / 9 个数')
    ap.add_argument('--sat', type=float, default=DEFAULT_SAT, help='色度整体增益')
    ap.add_argument('--mode', default='luma', choices=('luma', 'perchannel'),
                    help='过表方式')
    ap.add_argument('--half', action='store_true', help='half_size 解码（半分辨率）')
    ap.add_argument('--look', help='.tmp/looks_final/<look>.json')
    ap.add_argument('--triplet', action='store_true',
                    help='三联图：原 JPG / 复现 / 候选 look')
    ap.add_argument('--report', help='把体积/误差写成 JSON')
    a = ap.parse_args(argv)

    cfg = {'ccm': _parse_ccm(a.ccm), 'sat': a.sat, 'mode': a.mode,
           'tone_k': a.tone_k, 'chroma_k': a.chroma_k,
           'x_white': a.x_white, 's': a.s}
    if a.index_scale is not None:
        cfg['index_scale'] = a.index_scale
    # 用 x_white/s 重新换算 index_scale（除非显式给了）
    if a.index_scale is None:
        cfg['index_scale'] = None

    res = render_file(a.orf, cfg, wb=a.wb, half_size=a.half)
    sim = res['rgb8']
    info = {'orf': a.orf, 'cfg': {k: (v.tolist() if isinstance(v, np.ndarray) else v)
                                  for k, v in res['cfg'].items() if k != 'tone'},
            'shape': list(sim.shape), 'meta': res['meta']}

    ref = None
    if a.jpg and os.path.exists(a.jpg):
        ref = load_reference_jpg(a.jpg)
        if ref.shape != sim.shape:
            from PIL import Image
            ref = np.asarray(Image.open(a.jpg).convert('RGB').resize(
                (sim.shape[1], sim.shape[0]), Image.LANCZOS))
    look = None
    if a.look:
        L = load_look(a.look)
        lcfg = dict(cfg)
        lcfg['tone'] = L['tone']
        lcfg['chroma'] = L['chroma']
        lcfg['index_scale'] = L['index_scale']
        look = (L['name'], render_file(a.orf, lcfg, wb=a.wb, half_size=a.half)['rgb8'])

    if a.out:
        if a.triplet and ref is not None:
            panels = [('camera JPG', ref), ('sim k%d P%.0f' % (
                a.tone_k, res['cfg']['index_scale'] or 0), sim)]
            if look:
                panels.append(('look ' + look[0], look[1]))
            make_triptych(panels, a.out)
        else:
            save_preview(sim, a.out)
        info['out'] = os.path.abspath(a.out)

    # 误差
    if ref is not None:
        ys = luma(sim.astype(np.float32))
        yr = luma(ref.astype(np.float32))
        _, cbs, crs = rgb_to_ycbcr(sim.astype(np.float32))
        _, cbr, crr = rgb_to_ycbcr(ref.astype(np.float32))
        info['error'] = {
            'lum_rms': float(np.sqrt(((ys - yr) ** 2).mean())),
            'rgb_rms': float(np.sqrt(((sim.astype(np.float64) - ref) ** 2).mean())),
            'dcb_mean': float((cbs - cbr)[::4, ::4].mean()),
            'dcr_mean': float((crs - crr)[::4, ::4].mean()),
            'lum_mae': float(np.abs(ys - yr).mean()),
        }
    if a.report:
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        with open(a.report, 'w', encoding='utf-8') as f:
            json.dump(info, f, ensure_ascii=False, indent=1,
                      default=lambda o: o.item() if hasattr(o, 'item') else str(o))
    print(json.dumps({k: info[k] for k in info if k in ('error', 'out', 'shape')},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
