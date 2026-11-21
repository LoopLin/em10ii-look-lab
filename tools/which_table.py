#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""哪张 tone 表被用上了？—— 用 ORF 反推，跟 JPG 的亮度比。

思路：ORF 线性亮度 → 索引 → 候选表映射 → 8bit，与机内 JPG 的亮度比 RMS。
候选 = 原厂几张常用表 + B48 的四张极端表。
只读，不改任何文件；输出一张排名表。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rawpipe as rp  # noqa: E402
import build_b48 as b48  # noqa: E402


def table_of(k):
    d = rp.tone_table(k)
    return np.asarray(d, dtype=float)


def extreme(kind):
    b = b48.table_bytes(kind)
    return np.frombuffer(b, dtype='<u2').astype(float)


def probe(orf, jpg, half=True):
    d = rp.load_orfs(orf, half_size=half)
    lin = rp.crop_to_jpg_frame(d['lin'], d['meta'])
    y = rp.luma(lin)
    idx = rp.tone_index(y)
    ref = rp.load_reference_jpg(jpg)
    refy = np.asarray(ref, dtype=float)
    if refy.ndim == 3:
        refy = rp.luma(refy / 255.0) * 255.0
    if refy.shape != idx.shape:
        from PIL import Image
        refy = np.asarray(Image.fromarray(refy.astype(np.uint8)).resize(
            (idx.shape[1], idx.shape[0]))).astype(float)
    h, w = idx.shape
    sl = (slice(h // 4, 3 * h // 4), slice(w // 4, 3 * w // 4))
    out = []
    for name, tab in NAME_TAB.items():
        disp = tab[np.clip(idx.astype(int), 0, 255)] / 1023.0 * 255.0
        rms = float(np.sqrt(np.mean((disp[sl] - refy[sl]) ** 2)))
        lv = len(np.unique(np.round(disp[sl]).astype(int)))
        out.append((rms, name, lv))
    out.sort()
    return out


NAME_TAB = {}


def main():
    for k in (0, 1, 2, 3, 7):
        NAME_TAB['stock t%d' % k] = table_of(k)
    for kind in ('bright', 'dark', 'poster', 'invert'):
        NAME_TAB['B48 ' + kind] = extreme(kind)
    base = 'photos/B48_extreme_mapping'
    pairs = sys.argv[1:] or ['PB160057', 'PB160058', 'PB160059', 'PB160060', 'PB160001']
    for n in pairs:
        orf = os.path.join(base, n + '.ORF')
        jpg = os.path.join(base, n + '.JPG')
        if not os.path.exists(orf):
            print('%s: ORF 缺失，跳过' % n)
            continue
        r = probe(orf, jpg)
        print('\n== %s  最优三个：' % n)
        for rms, name, lv in r[:3]:
            print('   %-14s RMS %5.2f   级数 %3d' % (name, rms, lv))


if __name__ == '__main__':
    main()
