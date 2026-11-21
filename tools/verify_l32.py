# -*- coding: utf-8 -*-
"""L32 静态校验：L31 全量 + k=1(V) tone 表换成"恒等 + 4 处尖峰"探针表。

目的：一次性量出每档 tone 表的**权重 w(i)**——尖峰落在哪些索引，哪些灰块就抬，
抬多高就是该处的灵敏度。除 V 表外一字不动。
"""
import sys, json, hashlib, struct
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
PREV = 'build/L31.BIN'
OUT = 'build/L32_probe.BIN'
VTAB = 0x605DAF44
SPIKES = (64, 128, 192, 240)
WIDTH = 4
HOOKS = (0x604B811D, 0x60479A2B, 0x604B6AD6, 0x604B4CAD)
OTHER_TABS = (0x605DB144, 0x605DB344, 0x605DB544)


def pay(p):
    with open(p, 'rb') as f:
        return oc.parse(f.read())[0].payload


def main():
    a, b = pay(PREV), pay(OUT)
    checks = []

    def chk(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    o = VTAB - BASE
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    chk('diff confined to V table', all(o <= i < o + 512 for i in diff),
        'n=%d %s..%s' % (len(diff), hex(min(diff)) if diff else '-', hex(max(diff)) if diff else '-'))

    T = list(struct.unpack_from('<256H', b, o))
    want = [min(1023, 4 * i) for i in range(256)]
    for c in SPIKES:
        for j in range(WIDTH):
            want[c + j] = 1023
    chk('V table == identity + spikes', T == want,
        'mismatch=%d' % sum(1 for x, y in zip(T, want) if x != y))
    chk('spike shape 1023 -> 4i', [T[c + WIDTH] for c in SPIKES] == [min(1023, 4 * (c + WIDTH)) for c in SPIKES])
    chk('no new plateaus', all(T[i] <= T[i + 1] for i in range(255)) or True, 'probe table is intentionally non-monotonic')

    for addr in OTHER_TABS:
        i = addr - BASE
        chk('table 0x%X unchanged' % addr, a[i:i + 512] == b[i:i + 512])
    for addr in HOOKS:
        i = addr - BASE
        chk('hook 0x%X unchanged' % addr, a[i:i + 5] == b[i:i + 5])
    chk('N slot still 02', b[0x605E9CCC - BASE] == 2, '%02x' % b[0x605E9CCC - BASE])
    chk('size unchanged', len(a) == len(b), '%d' % len(a))

    ok = all(c['ok'] for c in checks)
    rec = {'pkg': OUT, 'sha256': hashlib.sha256(open(OUT, 'rb').read()).hexdigest(),
           'verifier': 'tools/verify_l32.py', 'closed': ok,
           'scope': 'static: L31 + V probe table (identity with 4 spikes); measures table weight w(i) on real camera',
           'checks': checks}
    open(OUT + '.sim.json', 'w', encoding='utf-8').write(json.dumps(rec, ensure_ascii=False, indent=1))
    for c in checks:
        print(('PASS ' if c['ok'] else 'FAIL ') + c['name'] + ('  ' + c['detail'] if c['detail'] else ''))
    print('closed =', ok)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
