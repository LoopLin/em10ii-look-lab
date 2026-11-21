# -*- coding: utf-8 -*-
"""读 B24b/B24c/B24d 模式探针的照片，解出每个档位的 k。

探针表（`build_beta_modeprobe.probe_table`）在灰块索引区间 48..112 上是平的：
    table[i] = 300 + 20*k        (48 <= i <= 112)
显示值 = table/1023*255  ⇒  display8 = 74.78 + 4.985*k

所以只要量到灰块的显示值，k 就唯一确定；索引只要落在 48..112（对着靶图拍一般都落这里）。

用法：
    python tools/read_modeprobe.py PB150002 PB150003 PB150004 PB150005
    python tools/read_modeprobe.py --dir I:/DCIM/100OLYMP PB150002 ...
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from read_b01_probe import patch_box  # 复用灰块定位

BAND_LO, BAND_HI = 48, 112
BAND_BASE, BAND_STEP = 300, 20


def model_display(k: int) -> float:
    return (BAND_BASE + BAND_STEP * k) / 1023.0 * 255.0


def measure(jpg_path: str, orf_path: str) -> dict:
    import rawpy

    jpg = np.asarray(Image.open(jpg_path).convert('RGB')).astype(np.float32)
    luma = 0.2126 * jpg[..., 0] + 0.7152 * jpg[..., 1] + 0.0722 * jpg[..., 2]
    # patch_box 返回的是**分数坐标**（0..1），不是像素
    fx0, fx1, fy0, fy1 = patch_box(luma)
    Hj, Wj = luma.shape

    # 灰块内部取子区（避开文字与边框）
    ix0, ix1 = fx0 + (fx1 - fx0) * 0.30, fx0 + (fx1 - fx0) * 0.75
    iy0, iy1 = fy0 + (fy1 - fy0) * 0.18, fy0 + (fy1 - fy0) * 0.58
    sub = luma[int(iy0 * Hj):int(iy1 * Hj), int(ix0 * Wj):int(ix1 * Wj)]
    disp = float(np.median(sub))

    # ORF 给线性亮度，用来确认索引落在平段内
    with rawpy.imread(orf_path) as raw:
        rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True,
                              output_bps=16, half_size=True)
    rgb = rgb.astype(np.float32) / 65535.0
    lin = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    H, W = lin.shape
    lin_sub = lin[int(iy0 * H):int(iy1 * H), int(ix0 * W):int(ix1 * W)]
    idx = float(np.median(lin_sub)) * 255.0

    k_est = (disp - model_display(0)) / (model_display(1) - model_display(0))
    return {'jpg': os.path.basename(jpg_path), 'display8': round(disp, 2),
            'idx': round(idx, 1), 'k': round(k_est, 2),
            'k_nearest': int(round(k_est)),
            'predicted': round(model_display(int(round(k_est))), 2),
            'in_band': BAND_LO <= idx <= BAND_HI}


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    d = 'I:/DCIM/100OLYMP'
    if '--dir' in argv:
        d = argv[argv.index('--dir') + 1]
        args = [a for a in args if a != d]
    if not args:
        print(__doc__)
        return 1
    print(f'{"name":12s} {"display8":>9s} {"idx":>6s} {"k":>6s} {"k~":>4s} {"pred":>7s} band')
    for name in args:
        stem = os.path.splitext(name)[0]
        try:
            r = measure(os.path.join(d, stem + '.JPG'), os.path.join(d, stem + '.ORF'))
        except Exception as e:  # noqa: BLE001
            print(f'{stem:12s}  ERROR {e}')
            continue
        print(f'{stem:12s} {r["display8"]:9.2f} {r["idx"]:6.1f} {r["k"]:6.2f} '
              f'{r["k_nearest"]:4d} {r["predicted"]:7.2f} {"yes" if r["in_band"] else "NO"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
