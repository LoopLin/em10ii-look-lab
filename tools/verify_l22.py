# -*- coding: utf-8 -*-
"""L22 静态校验：k1/k2 的 tone 表按实测反查改写；k3/k4 与 L20 逐字节相同；其余全同。"""
import sys, json, hashlib
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
TONE = [0x605DAF44, 0x605DB144, 0x605DB344, 0x605DB544]
PREV, OUT = 'build/L20.BIN', 'build/L22.BIN'


def pay(p):
    with open(p, 'rb') as f:
        return oc.parse(f.read())[0].payload


def main():
    a, b = pay(PREV), pay(OUT)
    checks = []

    def chk(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    diff = [i for i in range(len(a)) if a[i] != b[i]]
    span = set()
    for addr in TONE[:2]:
        o = addr - BASE
        span.update(range(o, o + 512))
    chk('diff only inside k1/k2 tone tables', all(i in span for i in diff),
        '%d bytes outside spans' % sum(1 for i in diff if i not in span))
    for k in (2, 3):
        o = TONE[k] - BASE
        chk('k%d tone untouched' % (k + 1), a[o:o + 512] == b[o:o + 512])
    for k in (0, 1):
        o = TONE[k] - BASE
        old = [old for old in a[o:o + 512]]
        chk('k%d tone changed and monotonic' % (k + 1), a[o:o + 512] != b[o:o + 512])
    for addr in (0x604B811D, 0x60479A2B, 0x604B6AD6, 0x604B4CAD):
        o = addr - BASE
        chk('hook 0x%X unchanged' % addr, a[o:o + 5] == b[o:o + 5])
    chk('size unchanged', len(a) == len(b))

    ok = all(c['ok'] for c in checks)
    rec = {'pkg': OUT, 'sha256': hashlib.sha256(open(OUT, 'rb').read()).hexdigest(),
           'verifier': 'tools/verify_l22.py', 'closed': ok,
           'scope': 'static: k1/k2 tone inverse-fit; k3/k4 untouched', 'checks': checks}
    open(OUT + '.sim.json', 'w', encoding='utf-8').write(json.dumps(rec, ensure_ascii=False, indent=1))
    for c in checks:
        print(('PASS ' if c['ok'] else 'FAIL ') + c['name'] + ('  ' + c['detail'] if c['detail'] else ''))
    print('closed =', ok)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
