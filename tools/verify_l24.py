# -*- coding: utf-8 -*-
"""L23 静态校验：四张 tone 表逐点等于 (D_ref-b)/a（逐档实测仿射求逆）；其余与 L20 相同。"""
import sys, json, hashlib, io, struct
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
TONE = [0x605DAF44, 0x605DB144, 0x605DB344, 0x605DB544]
PREV, OUT = 'build/L20.BIN', 'build/L24_probe.BIN'


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
    for addr in TONE:
        o = addr - BASE
        span.update(range(o, o + 512))
    chk('diff only inside the four tone tables', all(i in span for i in diff),
        '%d bytes outside spans' % sum(1 for i in diff if i not in span))
    import struct
    AFF = [(0.4764, -8.71), (0.6310, 7.70), (0.4504, 2.22), (0.4248, 25.95)]
    LOOKS = ['ektachrome_100_vs', 'elite_color_200', 'classic_chrome', 'pro_neg_hi']
    o=TONE[0]-BASE
    got=list(struct.unpack_from('<256H', b, o))
    chk('k1 identity table', got == [4*i for i in range(256)])
    for k, name in enumerate(LOOKS[1:], start=1):
        d = json.load(io.open('build/looks_final/%s.json' % name, encoding='utf-8'))
        a_, b_ = AFF[k]
        want = [min(1023, max(0, int(round(((v / 1023.0 * 255.0) - b_) / a_)))) for v in d['tone256']]
        o = TONE[k] - BASE
        got = list(struct.unpack_from('<256H', b, o))
        chk('k%d table equals affine inverse' % (k + 1), got == want,
            'maxdiff=%d' % max(abs(x - y) for x, y in zip(got, want)))
        chk('k%d monotonic' % (k + 1), all(y >= x for x, y in zip(got, got[1:])))
    for addr in (0x604B811D, 0x60479A2B, 0x604B6AD6, 0x604B4CAD):
        o = addr - BASE
        chk('hook 0x%X unchanged' % addr, a[o:o + 5] == b[o:o + 5])
    chk('size unchanged', len(a) == len(b))

    ok = all(c['ok'] for c in checks)
    rec = {'pkg': OUT, 'sha256': hashlib.sha256(open(OUT, 'rb').read()).hexdigest(),
           'verifier': 'tools/verify_l24.py', 'closed': ok,
           'scope': 'static: k1 identity table (T=4i), k2-k4 affine inverse', 'checks': checks}
    open(OUT + '.sim.json', 'w', encoding='utf-8').write(json.dumps(rec, ensure_ascii=False, indent=1))
    for c in checks:
        print(('PASS ' if c['ok'] else 'FAIL ') + c['name'] + ('  ' + c['detail'] if c['detail'] else ''))
    print('closed =', ok)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
