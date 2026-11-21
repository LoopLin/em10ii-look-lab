# -*- coding: utf-8 -*-
"""L43 静态校验：L39 全量 + 四张 tone 表全部乘 0.6。

用途：N 档对 k2 单表 ×0.6 完全无反应（149 与 145 差 ≤1 码），
本包把四张表一起压低，判断 N 到底读不读我们这组表。
  - N 明显变暗 → N 读其中某张，只是映射错位（继续定位）
  - N 毫无变化 → N 的 tone 来自另一条通路（换策略：钩上传器抓指针）
"""
import sys, json, hashlib
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
PREV = 'build/L39_tone_trim.BIN'
OUT = 'build/L43_alltone.BIN'
GATE = 0x604B9F1F
HOOKS = (0x604B811D, 0x60479A2B, 0x604B6AD6, 0x604B4CAD)
TABS = ((0x605DAF44, 'k1'), (0x605DB144, 'k2'), (0x605DB344, 'k3'), (0x605DB544, 'k4'))


def pay(p):
    with open(p, 'rb') as f:
        return oc.parse(f.read())[0].payload


def main():
    a, b = pay(PREV), pay(OUT)
    checks = []

    def chk(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    diff = [i for i in range(len(a)) if a[i] != b[i]]
    lo = min(x - BASE for x, _ in TABS)
    hi = max(x - BASE for x, _ in TABS) + 512
    chk('diff confined to the four tone tables', diff and diff[0] >= lo and diff[-1] < hi,
        'n=%d first=%s last=%s' % (len(diff), hex(diff[0] + BASE) if diff else '-',
                                   hex(diff[-1] + BASE) if diff else '-'))
    for addr, name in TABS:
        i = addr - BASE
        old = [int.from_bytes(a[i + 2 * j:i + 2 * j + 2], 'little') for j in range(256)]
        new = [int.from_bytes(b[i + 2 * j:i + 2 * j + 2], 'little') for j in range(256)]
        chk('%s scaled ~0.6' % name, all(abs(n - round(o * 0.6)) <= 2 for o, n in zip(old, new)),
            'old[128]=%d new[128]=%d' % (old[128], new[128]))
        chk('%s monotonic' % name, all(x <= y for x, y in zip(new, new[1:])))
    for addr in HOOKS:
        i = addr - BASE
        chk('hook 0x%X unchanged' % addr, a[i:i + 5] == b[i:i + 5])
    chk('bend gate still CA', b[GATE - BASE] == 0xCA, '%02x' % b[GATE - BASE])
    chk('N slot still 02', b[0x605E9CCC - BASE] == 2, '%02x' % b[0x605E9CCC - BASE])
    chk('size unchanged', len(a) == len(b), '%d' % len(a))

    ok = all(c['ok'] for c in checks)
    rec = {'pkg': OUT, 'sha256': hashlib.sha256(open(OUT, 'rb').read()).hexdigest(),
           'verifier': 'tools/verify_l43.py', 'closed': ok,
           'scope': 'static: L39 + all four tone tables x0.6 (Natural read-path probe); no ISP simulation',
           'checks': checks}
    open(OUT + '.sim.json', 'w', encoding='utf-8').write(json.dumps(rec, ensure_ascii=False, indent=1))
    for c in checks:
        print(('PASS ' if c['ok'] else 'FAIL ') + c['name'] + ('  ' + c['detail'] if c['detail'] else ''))
    print('closed =', ok)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
