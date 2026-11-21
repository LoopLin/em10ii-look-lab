# -*- coding: utf-8 -*-
"""B22 四套 look 的离线对比：渲染 + 五联图 + 数值摘要（只读，不改出包器）。

用法（控制台请设 PYTHONIOENCODING=utf-8）：
    python tools/b22_compare.py

做的三件事
----------
1. 对同一张官方固件实拍 ORF（refs/shoots/01_stock_contrast_series/PB110003，
   对比度 0、stock）渲染四套 look，两遍：
     tc：只 tone + 色度块（= BIN 中除 3×3 矩阵外的全部改动）
     m ：再加矩阵 m0，按《docs/color/look-spec-matrix.md》§1.3 的前向模型
         `rgb = tone(M · lin)`，即 rawpipe 的 ccm=M/256（**拟合口径，不是硬件预测**）
2. 以机内 stock JPG 为参照算亮度 RMS / RGB RMS / CbCr 偏移。
3. 拼五联图（机内 JPG + 四套）：
     .tmp/cmp_four_looks.jpg          （tc）
     .tmp/cmp_four_looks_matrix.jpg   （m）
   汇总 `.tmp/cmp22_report.json`。

索引口径（重要，别混用）
------------------------
- **规格口径**：`idx = lin / STEP * 255`，STEP=2.05634 ⇒ P=255/STEP≈124.0，
  18% 灰落在索引 22（= look JSON 里的 `step`，见 docs/color/look-spec-matrix.md
  §1.2 第 118 行 `idx = lin/STEP`）。四套 e1m 就是在这个口径下拟合的，
  所以**渲染 look 规格本身**用这个口径。
- **相机口径**：`idx = Y * P`，P = 255*s/x_white，raw-sim 用 15 组配对扫出
  P 中位 231、范围 204–241（docs/color/raw-sim.md）。BIN 里的 tone 是
  `retarget_tone` 把规格表按 `k=22/46` 重采样过的成品，要在**这个**口径下消费。
- 两者差 ~11%：规格 18% 在 22，相机 18% 按 raw-sim 在 41、按 idx_cal 在 46。
  出包器取 46。若真相是 41，交付表会整体偏暗约 0.35 EV —— 属【推理】，
  见交付文档「未验证」一节。本脚本两种口径都算，分别上报。

⚠️ 走 rawpipe 的库函数而不是 `--look` CLI：2026-11-15 发现 CLI 的 `--look` 只影响
`--triplet` 的第三格，`--out` 存的仍是基础复现图（look 被忽略）；`--ccm` 正常。
这里用同一条 render 路径，避免踩那个坑。
"""
from __future__ import annotations

import json
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import rawpipe      # noqa: E402  只读：render_file / luma / save_preview
import oly_container as oc   # noqa: E402  只读：容器解析

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOOT = 'refs/shoots/01_stock_contrast_series'
ORF = os.path.join(SHOOT, 'PB110003.ORF')
JPG = os.path.join(SHOOT, 'PB110003.JPG')
SPEC_DIR = '.tmp/looks_matrix'
OUT_DIR = '.tmp/cmp22'
LOOKS = ['reala_ace', 'astia', 'eterna', 'bleach_bypass']
TITLE = {'reala_ace': 'reala ace', 'astia': 'astia',
         'eterna': 'eterna', 'bleach_bypass': 'bleach bypass'}
BASE = 0x60021020
TONE_RAM = 0x605D8344
CHROMA_RAM = 0x605D4829
MATRIX_RAM = 0x605ECECA
IDX_18_SPEC = 22.0     # 规格口径的 18% 灰索引
IDX_18_CAM = 46.0      # 相机口径的 18% 灰索引（BIN 里就是 tone[46]）
DISP = 1023.0          # tone 表值域 → display8 = v/1023*255
CAM_P = 230.0          # 相机口径索引刻度（raw-sim 15 组配对：中位 231，取 230）


def log(msg: str) -> None:
    print(msg, flush=True)


def base_cfg(index_scale):
    return {'ccm': rawpipe._parse_ccm('auto'),
            'sat': rawpipe.DEFAULT_SAT, 'mode': 'luma',
            'tone_k': rawpipe.DEFAULT_TONE_K,
            'chroma_k': rawpipe.DEFAULT_CHROMA_K,
            'x_white': rawpipe.DEFAULT_X_WHITE, 's': rawpipe.DEFAULT_S,
            'index_scale': index_scale}


def render(tone, chroma, index_scale, mccm=None):
    cfg = base_cfg(index_scale)
    cfg['tone'] = tone
    cfg['chroma'] = chroma
    if mccm is not None:
        cfg['ccm'] = mccm
    return rawpipe.render_file(ORF, cfg, wb='camera', half_size=True)['rgb8']


def spec_chroma(d: dict) -> dict:
    c = d['chroma']
    return {'cb_x': list(c['cb_x']), 'cb_y': list(c['cb_y']), 'cb_z': list(c['cb_z']),
            'cr_x': list(c['cr_x']), 'cr_y': list(c['cr_y']), 'cr_z': list(c['cr_z'])}


def read_pack(path: str) -> dict:
    """从 BIN 里读回 tone / 色度块 / 矩阵（只读，不经出包器）。"""
    blk = oc.parse(open(path, 'rb').read())[0].payload
    tone = list(struct.unpack_from('<256H', blk, TONE_RAM - BASE))
    ch = blk[CHROMA_RAM - BASE:CHROMA_RAM - BASE + 100]
    chroma = {'cb_x': list(ch[0x00:0x10]), 'cb_y': list(ch[0x10:0x20]),
              'cb_z': list(ch[0x20:0x31]),
              'cr_x': list(ch[0x32:0x42]), 'cr_y': list(ch[0x42:0x52]),
              'cr_z': list(ch[0x52:0x63])}
    mx = list(struct.unpack_from('<9h', blk, MATRIX_RAM - BASE))
    return {'tone': tone, 'chroma': chroma, 'matrix': mx}


def metrics(sim: np.ndarray, ref: np.ndarray) -> dict:
    s = sim.astype(np.float32)
    r = ref.astype(np.float32)
    ys, yr = rawpipe.luma(s), rawpipe.luma(r)
    _, cbs, crs = rawpipe.rgb_to_ycbcr(s)
    _, cbr, crr = rawpipe.rgb_to_ycbcr(r)
    return {
        'lum_rms': float(np.sqrt(((ys - yr) ** 2).mean())),
        'lum_mae': float(np.abs(ys - yr).mean()),
        'rgb_rms': float(np.sqrt(((s - r) ** 2).mean())),
        'dcb_mean': float((cbs - cbr)[::4, ::4].mean()),
        'dcr_mean': float((crs - crr)[::4, ::4].mean()),
        'mean_y_look': float(ys.mean()), 'mean_y_ref': float(yr.mean()),
    }


def font(size: int):
    from PIL import ImageFont
    for p in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyhbd.ttc',
              r'C:\Windows\Fonts\simhei.ttf'):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


# 只保留色卡纸面（同一张 ORF 的几何一致）：归一化裁切框 (x0, y0, x1, y1)
CROP = (0.075, 0.105, 0.955, 0.855)


def grid5(panels, path: str, head: str, sub: str, width_panel: int = 624) -> str:
    """五联图：上排 3 张、下排 2 张居中；总宽 8+3*(624+8)=1904 ≤ 1920。"""
    from PIL import Image, ImageDraw
    ims = []
    for t, a in panels:
        h, w = a.shape[:2]
        x0, y0, x1, y1 = CROP
        ims.append((t, Image.fromarray(a[int(h * y0):int(h * y1), int(w * x0):int(w * x1)])))
    ims = [(t, im.resize((width_panel, int(im.height * width_panel / im.width)),
                         Image.LANCZOS)) for t, im in ims]
    pad, band, top, cols = 8, 26, 74, 3
    ph = max(im.height for _, im in ims)
    W = pad + cols * (width_panel + pad)
    H = top + 2 * (ph + band + pad) + 46
    canvas = Image.new('RGB', (W, H), (18, 18, 18))
    dr = ImageDraw.Draw(canvas)
    f1, f2, f3 = font(20), font(15), font(13)
    dr.text((pad + 2, 10), head, fill=(245, 245, 245), font=f1)
    dr.text((pad + 2, 36), sub, fill=(165, 165, 165), font=f3)
    for i, (t, im) in enumerate(ims):
        if i < cols:
            x, y = pad + i * (width_panel + pad), top
        else:                                        # 第二排两张居中（各占 1.5 槽）
            x = pad + (i - cols) * (width_panel + pad) + (width_panel + pad) // 2
            y = top + (ph + band + pad)
        canvas.paste(im, (x, y + band))
        dr.text((x + 2, y + 4), t, fill=(240, 240, 240), font=f2)
    dr.text((pad + 2, H - 30),
            'rawpipe 离线渲染（half_size）· 参照=机内 stock JPG（对比度 0）· 未上机验证',
            fill=(150, 150, 150), font=f3)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    canvas.save(path, quality=93, subsampling=1)
    log('image -> %s  %dx%d' % (path, canvas.width, canvas.height))
    return path


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    from PIL import Image
    ref0 = np.asarray(Image.open(JPG).convert('RGB'))
    log('ORF %s ; 参照 %s %s' % (ORF, JPG, ref0.shape))

    summary = json.load(open(os.path.join(SPEC_DIR, 'summary.json'), encoding='utf-8'))
    ver_by = json.load(open('.tmp/B22_verify.json', encoding='utf-8'))['looks']

    rows, panels_tc, panels_m = {}, [], []
    sim_base = render(None, None, CAM_P)
    err_base = None
    for look in LOOKS:
        d = json.load(open(os.path.join(SPEC_DIR, look + '.json'), encoding='utf-8'))
        pk = read_pack('build/B22_%s.BIN' % look)
        mat = pk['matrix']
        mccm = np.array([v / 256.0 for v in mat], dtype=np.float32).reshape(3, 3)
        spec_p = 255.0 / float(d['step'])

        tone_s = [float(x) for x in d['tone']]
        ch_s = spec_chroma(d)
        sim_tc = render(tone_s, ch_s, spec_p)
        sim_m = render(tone_s, ch_s, spec_p, mccm)
        sim_bin = render(pk['tone'], pk['chroma'], CAM_P)   # 交付表在相机口径下
        p_tc = os.path.join(OUT_DIR, '%s_tc.jpg' % look)
        p_m = os.path.join(OUT_DIR, '%s_m.jpg' % look)
        rawpipe.save_preview(sim_tc, p_tc)
        rawpipe.save_preview(sim_m, p_m)
        panels_tc.append((TITLE[look], sim_tc))
        panels_m.append((TITLE[look], sim_m))

        ref = ref0
        if ref.shape != sim_tc.shape:
            ref = np.asarray(Image.open(JPG).convert('RGB').resize(
                (sim_tc.shape[1], sim_tc.shape[0]), Image.LANCZOS))
        if err_base is None:
            err_base = metrics(sim_base.astype(np.uint8), ref)
            log('  (基准) stock 复现 lum_rms=%.2f rgb_rms=%.2f' % (
                err_base['lum_rms'], err_base['rgb_rms']))

        v = ver_by.get(look, {})
        df = v.get('diff', {})
        rows[look] = {
            'path': v.get('path'), 'sha256': v.get('sha256'),
            'sha256_16': v.get('sha256_16'), 'sha256_32': v.get('sha256_32'),
            'size': v.get('size'),
            'changed_payload': df.get('decoded_changed_bytes'),
            'changed_file': df.get('file_changed_bytes'),
            'per_region': df.get('per_region'),
            'outside_bytes': df.get('outside_bytes'),
            'gate': v.get('checks', {}).get('gate'),
            'spec_index_scale': spec_p,
            'tone_spec': {str(i): d['tone'][i] for i in (0, 10, 22, 46, 69, 128, 255)},
            'tone_bin': {str(i): pk['tone'][i] for i in (0, 22, 46, 64, 128, 255)},
            'matrix9': mat,
            'cb_x': pk['chroma']['cb_x'], 'cb_y': pk['chroma']['cb_y'],
            'cr_x': pk['chroma']['cr_x'], 'cr_y': pk['chroma']['cr_y'],
            'gray18_tone': pk['tone'][int(IDX_18_CAM)],
            'gray18_display8': pk['tone'][int(IDX_18_CAM)] / DISP * 255.0,
            'e1h_baseline': summary[look]['e1h_baseline'],
            'e1m_float': summary[look]['e1m_float'],
            'e1m': summary[look]['e1m'],
            'e1m_quant_cost': summary[look].get('quant_cost'),
            'err_spec': metrics(sim_tc, ref),
            'err_spec_m': metrics(sim_m, ref),
            'err_bin_cam': metrics(sim_bin.astype(np.uint8), ref),
        }
        e_s, e_m, e_b = (rows[look]['err_spec'], rows[look]['err_spec_m'],
                         rows[look]['err_bin_cam'])
        log('  %-14s spec P=%.1f: lumRMS=%.2f rgbRMS=%.2f meanY=%.1f | +M: %.2f | '
            'BIN@P230: %.2f (meanY=%.1f) | gray18=%.2f' % (
                look, spec_p, e_s['lum_rms'], e_s['rgb_rms'], e_s['mean_y_look'],
                e_m['lum_rms'], e_b['lum_rms'], e_b['mean_y_look'],
                rows[look]['gray18_display8']))

    panels_ref = [('机内 JPG（stock，对比度 0）',
                   ref0 if ref0.shape == panels_tc[0][1].shape else
                   np.asarray(Image.open(JPG).convert('RGB').resize(
                       (panels_tc[0][1].shape[1], panels_tc[0][1].shape[0]), Image.LANCZOS)))]
    grid5(panels_ref + panels_tc, '.tmp/cmp_four_looks.jpg',
          'B22 四套 film look × 同一张实拍 ORF（PB110003，官方固件）',
          'tone + 色度块 —— BIN 中除 3×3 矩阵以外的全部改动；规格口径 P=255/STEP≈124.0')
    grid5(panels_ref + panels_m, '.tmp/cmp_four_looks_matrix.jpg',
          'B22 四套 film look（含 3×3 矩阵 m0）× 同一张实拍 ORF',
          'tone + 矩阵 + 色度块 = 交付包全量；矩阵按拟合口径 tone(M·lin) 前向，非硬件预测')

    rep = {'orf': ORF, 'reference_jpg': JPG,
           'caliber': {'spec_index_scale': 255.0 / float(
               json.load(open(os.path.join(SPEC_DIR, 'reala_ace.json'),
                              encoding='utf-8'))['step']),
               'camera_index_scale_rendered': CAM_P},
           'err_base_stock_repro': err_base, 'looks': rows,
           'note': 'err_spec = 规格口径（look 设计意图）；err_bin_cam = 交付表在相机口径 P=230 下；'
                   '两者差 ~11% 索引刻度，见脚本 docstring。'}
    with open('.tmp/cmp22_report.json', 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    log('report -> .tmp/cmp22_report.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
