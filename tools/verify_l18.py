#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l18.py -- L18 = stock + 六轴探针（0/1024/2048/3072）静态校验"""
import json, sys, os
sys.path.insert(0, 'tools')
import oly_container as oc
import build_l18 as b18

STOCK = 'stock/OLY_E_120_1400_0000_0000.BIN'

def payload(p):
    return bytearray(oc.parse(open(p, 'rb').read())[0].payload)

def main():
    st = payload(STOCK); bl = payload(b18.OUT)
    problems = []
    if len(st) != len(bl):
        problems.append('payload size differs')
    diffs = [i for i in range(len(st)) if st[i] != bl[i]]
    spans = []
    for i in diffs:
        if spans and i == spans[-1][1]:
            spans[-1][1] = i + 1
        else:
            spans.append([i, i + 1])
    hook_ok = bl[b18.HOOK - b18.HOOK + 0x60021020 - 0x60021020] is not None  # placeholder
    lo = b18.HOOK - 0x60021020
    if bl[lo] != 0xDC:
        problems.append('hook not DC')
    target = lo + int.from_bytes(bl[lo+1:lo+5], 'little', signed=True)
    if target != b18.STUB - 0x60021020:
        problems.append('hook target wrong')
    so = b18.STUB - 0x60021020
    if bl[so] == 0xFF:
        problems.append('stub empty')
    exp = [[lo, lo+5], [so, so+60]]
    if spans != [ [a,b] for a,b in exp ]:
        # allow stub length variance
        if len(spans) != 2 or spans[0] != exp[0]:
            problems.append('unexpected diff spans %r' % spans)
    out = {'closed': not problems, 'problems': problems,
           'note': 'static only: hook + stub bytes; runtime not modelled',
           'spans': spans}
    open(b18.OUT + '.sim.json', 'w').write(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))

if __name__ == '__main__':
    main()
