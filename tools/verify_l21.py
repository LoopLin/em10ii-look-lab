"""L21 static check: four tone tables = f^-1(OETF(L20 tables)); everything else == L20."""
import sys, json, hashlib, numpy as np
sys.path.insert(0, 'tools')
import oly_container as oc

SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
PREV = 'build/L20.BIN'
OUT = 'build/L21.BIN'
BASE = 0x60021020
TABLES = [0x605DAF44, 0x605DB144, 0x605DB344, 0x605DB544]
CODES = np.array([0, 5, 15, 33, 61, 100, 188, 1023], float)
DISP = np.array([0, 15.7, 40.7, 68.7, 91.8, 111.3, 137.4, 255], float)


def payload(path):
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def oetf(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def finv(d):
    return np.interp(np.clip(d, 0, 255), DISP, CODES)


def main():
    prev = payload(PREV)
    cur = payload(OUT)
    src = payload(SRC)
    checks = []

    def chk(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    spans = []
    for a in TABLES:
        o = a - BASE
        t1 = np.frombuffer(prev[o:o + 512], dtype='<u2').astype(float)
        want = np.clip(np.round(finv(oetf(t1 / 1023.0) * 255.0) / 255.0 * 1023.0), 0, 1023).astype('<u2')
        got = np.frombuffer(cur[o:o + 512], dtype='<u2')
        chk('table 0x%X == f^-1(OETF(prev))' % a, np.array_equal(want, got),
            '%d/%d bytes differ' % (int((want.view(np.uint8) != got.view(np.uint8)).sum()), 512))
        spans.append((o, o + 512))

    diff = np.nonzero(np.frombuffer(prev, dtype=np.uint8) != np.frombuffer(cur, dtype=np.uint8))[0]
    inside = np.zeros(len(prev), bool)
    for s, e in spans:
        inside[s:e] = True
    chk('payload diff only inside four tone spans', bool(inside[diff].all()) and len(diff) > 0,
        '%d bytes, outside=%d' % (len(diff), int((~inside[diff]).sum())))

    hooks = [0x604B811D, 0x60479A2B, 0x604B6AD6, 0x604B4CAD]
    for a in hooks:
        o = a - BASE
        chk('hook 0x%X unchanged vs L20' % a, prev[o:o + 5] == cur[o:o + 5])

    chk('size == stock size', len(cur) == len(src))

    allok = all(c['ok'] for c in checks)
    rec = {'pkg': OUT, 'sha256': hashlib.sha256(open(OUT, 'rb').read()).hexdigest(),
           'verifier': 'tools/verify_l21.py', 'closed': allok,
           'scope': 'static: tone-table transform + payload diff; no ISP simulation',
           'checks': checks}
    with open(OUT + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    for c in checks:
        print(('PASS ' if c['ok'] else 'FAIL ') + c['name'] + ('  ' + c['detail'] if c['detail'] else ''))
    print('closed =', allok)
    return 0 if allok else 1


if __name__ == '__main__':
    sys.exit(main())
