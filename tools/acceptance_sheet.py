"""acceptance_sheet.py -- 22-scene acceptance contact sheet + numbers.

Usage:
  python tools/acceptance_sheet.py <out.jpg> <stem1> <stem2> ...

Prints, per stem: EXIF k (our probe field), mean L*, mean C*, and the gray
ramp; then builds one contact sheet with a label per tile so the scenes can be
eyeballed side by side.
"""
import io
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, '.')
import tools.judge_chart as jc

LOOK = {0: 'iE/unknown', 1: 'Ektachrome', 2: 'Elite', 3: 'Chrome', 4: 'ProNegHi',
        5: 'i-Enhance'}


def probe_k(path):
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('rp', 'tools/read_probe.py')
        rp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rp)
        uc = rp.get_user_comment(path)
        if uc and b'k=' in uc:
            s = uc.split(b'k=')[1][:1]
            return int(s)
    except Exception:
        pass
    return 0


def tile(path, w=420):
    im = Image.open(path).convert('RGB')
    h = int(im.height * w / im.width)
    return im.resize((w, h))


def main():
    out = sys.argv[1]
    stems = sys.argv[2:]
    root = r'I:\DCIM\100OLYMP'
    tiles = []
    print('%-12s %-11s %6s %6s  gray' % ('stem', 'look', 'L*', 'C*'))
    for s in stems:
        p = os.path.join(root, s + '.JPG')
        if not os.path.exists(p):
            print('%-12s MISSING' % s)
            continue
        k = probe_k(p)
        rgb = jc.sample(p)
        lab = np.array([jc._lab(x) for x in rgb])
        C = np.hypot(lab[:, 1], lab[:, 2])
        gray = [round(float(lab[i, 0]), 1) for i in (18, 19, 20, 21, 22, 23)]
        print('%-12s %-11s %6.1f %6.1f  %s' % (s, LOOK.get(k, k), lab[:, 0].mean(),
                                               C.mean(), gray))
        tiles.append((s, k, tile(p)))
    if not tiles:
        raise SystemExit('no tiles')
    cols = 4
    rows = (len(tiles) + cols - 1) // cols
    tw, th = tiles[0][2].size
    pad, lab_h = 8, 22
    sheet = Image.new('RGB', (cols * (tw + pad) + pad, rows * (th + lab_h + pad) + pad),
                      (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    for i, (s, k, im) in enumerate(tiles):
        r, c = divmod(i, cols)
        x = pad + c * (tw + pad)
        y = pad + r * (th + lab_h + pad)
        sheet.paste(im, (x, y))
        d.text((x + 4, y + th + 4), '%s  %s' % (s, LOOK.get(k, k)), fill=(235, 235, 235))
    sheet.save(out, quality=92)
    print('sheet ->', out)


if __name__ == '__main__':
    main()
