# -*- coding: utf-8 -*-
"""L33 静态校验：L31 全量 + tone bend 门一个字节（0x604B9F1F: C8 -> CA）。

依据 docs/analysis/tone-control-bend.md：该字节是 `beq`（跳过弯曲）改成 `bra`，
无条件跳过「拷贝→弯曲→换指针」，于是上传的是我们写的原表。
"""
import sys, json, hashlib
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
PREV = 'build/L31.BIN'
OUT = 'build/L33_nobend.BIN'
GATE = 0x604B9F1F
HOOKS = (0x604B811D, 0x60479A2B, 0x604B6AD6, 0x604B4CAD)
TABS = (0x605DAF44, 0x605DB144, 0x605DB344, 0x605DB544)


def pay(p):
    with open(p, 'rb') as f:
        return oc.parse(f.read())[0].payload


def main():
    a, b = pay(PREV), pay(OUT)
    checks = []

    def chk(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    diff = [i for i in range(len(a)) if a[i] != b[i]]
    o = GATE - BASE
    chk('exactly one decoded byte differs', diff == [o], 'diff=%s' % [hex(i) for i in diff[:6]])
    chk('gate byte C8 -> CA', a[o] == 0xC8 and b[o] == 0xCA, 'old=%02x new=%02x' % (a[o], b[o]))
    for addr in TABS:
        i = addr - BASE
        chk('tone 0x%X unchanged' % addr, a[i:i + 512] == b[i:i + 512])
    for addr in HOOKS:
        i = addr - BASE
        chk('hook 0x%X unchanged' % addr, a[i:i + 5] == b[i:i + 5])
    chk('N slot still 02', b[0x605E9CCC - BASE] == 2, '%02x' % b[0x605E9CCC - BASE])
    chk('size unchanged', len(a) == len(b), '%d' % len(a))

    ok = all(c['ok'] for c in checks)
    rec = {'pkg': OUT, 'sha256': hashlib.sha256(open(OUT, 'rb').read()).hexdigest(),
           'verifier': 'tools/verify_l33.py', 'closed': ok,
           'scope': 'static: L31 + tone-bend gate bypass (0x604B9F1F C8->CA); no ISP simulation',
           'checks': checks}
    open(OUT + '.sim.json', 'w', encoding='utf-8').write(json.dumps(rec, ensure_ascii=False, indent=1))
    for c in checks:
        print(('PASS ' if c['ok'] else 'FAIL ') + c['name'] + ('  ' + c['detail'] if c['detail'] else ''))
    print('closed =', ok)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
