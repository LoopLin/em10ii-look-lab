# -*- coding: utf-8 -*-
"""读 B20 探针包拍出来的灰卡照片：反推每个模式命中了哪张探针表（= 哪个 filter id）。

做法：
1. 在 JPG 里自动找"中央大灰块"（亮度阈值 + 行/列投影），取中心 40% 区域；
2. 用同一位置在 ORF 的线性亮度上取均值 → 索引 idx = L * P；
3. 对 9 张候选表（8 张探针 + 原厂 t0）算预测显示值，取最接近的那张；
4. 同一个模式在 EV+2 与 EV+1 两组里应当命中同一张表 —— 用它做自检。

用法：python tools/read_keyprobe.py <jpg,orf> [<jpg,orf> ...]
"""
from __future__ import annotations

import sys

import numpy as np
from PIL import Image

sys.path.insert(0, 'tools')
import rawpipe

BASE = 0x60021020
TONE = 0x605D8344
PROBE = {  # slot -> (s, key)
    8: (0.35, 0x01010066), 9: (0.45, 0x02000096), 10: (0.55, 0x01010067),
    11: (0.65, 0x01010079), 12: (0.75, 0x01020079), 13: (0.85, 0x0101006B),
    14: (0.95, 0x0102006B), 15: (1.05, 0x01030079),
}


def mirror():
    # 必须用 B20 自己的镜像，否则拿到的是原厂表
    import os
    p = os.environ.get('KEYPROBE_IMAGE', '.tmp/B20.decoded.bin')
    return np.memmap(p, dtype=np.uint8, mode='r')


def tables(m):
    out = {}
    for slot in PROBE:
        out[slot] = rawpipe.tone_table(slot, m)
    out['stock'] = rawpipe.tone_table(0, m)
    return out


def find_block(path):
    im = Image.open(path).convert('L')
    a = np.asarray(im, dtype=np.float32)
    h, w = a.shape
    thr = a.max() * 0.6
    mask = a > thr
    rows = mask.sum(1) / w
    cols = mask.sum(0) / h
    ry = np.where(rows > 0.30)[0]
    cx = np.where(cols > 0.25)[0]
    if len(ry) < 10 or len(cx) < 10:
        raise SystemExit(f'{path}: 找不到灰块（阈值 {thr:.0f}）')
    y0, y1 = ry.min(), ry.max()
    x0, x1 = cx.min(), cx.max()
    # 中心 40% 区域，避开左上角文字与底部参考块
    dy, dx = int((y1 - y0) * 0.30), int((x1 - x0) * 0.30)
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy), a


def main(pairs):
    m = mirror()
    tabs = tables(m)
    for jpg, orf in pairs:
        (x0, y0, x1, y1), a = find_block(jpg)
        g = float(a[y0:y1, x0:x1].mean())
        d = rawpipe.load_orfs(orf, wb='camera', half_size=False, color='raw')
        lin = rawpipe.crop_to_jpg_frame(d['lin'], d['meta'])
        L = rawpipe.luma(lin)
        h, w = L.shape
        sx, sy = w / a.shape[1], h / a.shape[0]
        sub = L[int(y0 * sy):int(y1 * sy), int(x0 * sx):int(x1 * sx)]
        Lm = float(sub.mean())
        line = [f'{jpg.split("/")[-1]:>14s} G={g:6.2f} L={Lm:.4f}']
        for P in (230.0, 255.0):
            idx = max(0.0, min(255.0, Lm * P))
            best = []
            for slot, tab in tabs.items():
                pred = float(tab[int(round(idx))]) / 1023.0 * 255.0
                best.append((abs(pred - g), slot, pred))
            best.sort()
            err, slot, pred = best[0]
            tag = f's={PROBE[slot][0]:.2f}/k={PROBE[slot][1]:#010x}' if slot in PROBE else 'stock'
            line.append(f'| P={P:.0f} idx={idx:5.1f} -> {slot if slot in PROBE else "t0"} {tag:24s} pred={pred:6.2f} err={err:5.2f}')
        print('  '.join(line))
    return 0


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args or len(args) % 2:
        print(__doc__)
        sys.exit(2)
    prs = [(args[i], args[i + 1]) for i in range(0, len(args), 2)]
    sys.exit(main(prs))
