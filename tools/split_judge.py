#!/usr/bin/env python3
"""Judge a tone-group split build against the official baseline.

Usage:
  python tools/split_judge.py vivid.JPG natural.JPG muted.JPG portrait.JPG

Prints each mode's luma ratio vs the stored stock baseline and says whether
that mode's tone group falls in the darkened half or not.

Baseline: docs/baselines/stock_desk_2026-11-14.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scene_ab import stats  # noqa: E402

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                    'docs', 'baselines', 'stock_desk_2026-11-14.json')
MODES = ['vivid', 'natural', 'muted', 'portrait']


def main(paths):
    ref = json.load(open(BASE, encoding='utf-8'))
    print(f'{"mode":<10}{"stock":>8}{"new":>8}{"ratio":>8}   verdict')
    for mode, p in zip(MODES, paths):
        s = stats(p)
        stock = ref[mode]['lum']
        r = s['lum'] / stock
        tag = 'DARKENED (group in changed half)' if r < 0.85 else 'unchanged'
        print(f'{mode:<10}{stock:>8.2f}{s["lum"]:>8.2f}{r:>8.3f}   {tag}')


if __name__ == '__main__':
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1:])
