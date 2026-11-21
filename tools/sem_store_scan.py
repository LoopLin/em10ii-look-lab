#!/usr/bin/env python3
"""sem_store_scan.py — 全镜像线性扫描 (disp,An) 读写指令。
用法: python tools/sem_store_scan.py 0x30 0x1c 0x3c0 > out.json
注意: 这是线性扫描(不做控制流)，会有少量误报；每条命中都给了地址可复核。
"""
import bisect, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mn10300_dis as M

BASE = 0x60021020
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = open(os.path.join(HERE, 'analysis', '00_0100_40300020.decoded.bin'), 'rb').read()
D = M.Decoder()
PAT_ANY = re.compile(r'\((\d+),([ad][0-3])\)')

def load(p):
    return json.load(open(os.path.join(HERE, p), encoding='utf-8'))

def main(argv):
    offsets = {int(a, 0) for a in argv}
    seg = load('docs/map/segments.json')
    ranges = [(int(r['start'], 16), int(r['end'], 16)) for r in seg['code_ranges']]
    fn = sorted((int(r[0], 16), r[1]) for r in load('docs/map/functions-index.json')['rows'])
    fstart = [s for s, _ in fn]
    res = {hex(o): [] for o in offsets}
    for start, end in ranges:
        off = start
        while off < end:
            r = D.match(data, off - BASE, off)
            if not r:
                off += 1
                continue
            blen, mnem, ops, rule = r
            if mnem and ops:
                ms = list(PAT_ANY.finditer(ops))
                for m in ms:
                    d = int(m.group(1)); base = m.group(2)
                    if d not in offsets: continue
                    kind = 'load' if ops.lstrip().startswith('(') else 'store'
                    width = 'byte' if 'bu' in mnem else ('half' if 'hu' in mnem else 'word')
                    other = ops.replace(m.group(0), '').strip(' ,')
                    i = bisect.bisect_right(fstart, off) - 1
                    res[hex(d)].append({'addr': off, 'mnem': mnem, 'ops': ops, 'kind': kind,
                                        'width': width, 'base': base, 'other': other,
                                        'fn': fstart[i] if i >= 0 else None})
            off += blen
    print(json.dumps(res, indent=1, ensure_ascii=False))

if __name__ == '__main__':
    main(sys.argv[1:])
