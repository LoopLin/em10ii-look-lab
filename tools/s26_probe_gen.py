#!/usr/bin/env python3
"""S26：给 33 张 tone 表各一个不同的亮度增益，用来一次性测出「模式 -> 表」的映射。

原理：所有表都乘一个正数 g 仍是单调曲线，画面上表现为整体亮度变化。
给第 i 张表一个唯一的 g_i，刷一次，四个模式各拍一张，测出每张照片的亮度
偏移量，就能反推每个模式读的是哪一张表。

用法：
  python tools/s26_probe_gen.py --out-patch .tmp/s26_probe.patch \
      --report .tmp/s26_probe.json
"""
import argparse
import json
import struct
from pathlib import Path

IMAGE = Path('analysis/00_0100_40300020.decoded.bin')
BASE = 0x60021020


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tables', default='.tmp/baddrs.txt')
    ap.add_argument('--out-patch', default='.tmp/s26_probe.patch')
    ap.add_argument('--report', default='.tmp/s26_probe.json')
    args = ap.parse_args()

    img = IMAGE.read_bytes()
    addrs = sorted({int(x, 16) for x in Path(args.tables).read_text().split()})
    lines, report = [], {}
    for i, a in enumerate(addrs):
        g = 0.70 + 0.03 * i                       # 0.70 .. 1.66，每张唯一
        t = struct.unpack_from('<256H', img, a - BASE)
        out = [min(1023, int(round(v * g))) for v in t]
        # 保持单调（四舍五入可能平掉，但不会回退）
        for k in range(1, 256):
            if out[k] < out[k - 1]:
                out[k] = out[k - 1]
        raw = struct.pack('<256H', *out)
        for off in range(0, len(raw), 64):
            lines.append('ram 0x%08x=%s' % (a + off, raw[off:off + 64].hex()))
        report['%08x' % a] = {'index': i, 'gain': round(g, 3),
                              'before': [t[0], t[128], t[255]],
                              'after': [out[0], out[128], out[255]]}

    Path(args.out_patch).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    Path(args.report).write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(f'tables={len(addrs)} lines={len(lines)} bytes={sum(len(l.split("=")[1])//2 for l in lines)}')


if __name__ == '__main__':
    main()
