# -*- coding: utf-8 -*-
"""L31 静态校验：L30 全量 + N 档槽位 1 字节（0x605E9CCC: 01 -> 02）。

只做一件事：证明"相对 L30 只改了那 1 个解码字节，其余一字未动"。
不做 ISP 仿真（本包不改曲线形状，只改一个枚举值）。
"""
import sys, json, hashlib
sys.path.insert(0, 'tools')
import oly_container as oc

BASE  = 0x60021020
PREV  = 'build/L30_newton.BIN'
OUT   = 'build/L31.BIN'
NSLOT = 0x605E9CCC          # 空位假设：Natural 槽
HOOKS = (0x604B811D, 0x60479A2B, 0x604B6AD6, 0x604B4CAD)


def pay(p):
    with open(p, 'rb') as f:
        return oc.parse(f.read())[0].payload


def main():
    a, b = pay(PREV), pay(OUT)
    checks = []

    def chk(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    diff = [i for i in range(len(a)) if a[i] != b[i]]
    o = NSLOT - BASE
    chk('exactly one decoded byte differs', len(diff) == 1, 'diff=%d %r' % (len(diff), [hex(i) for i in diff[:4]]))
    chk('the byte is the N slot', diff == [o], 'expected %s got %s' % (hex(o), [hex(i) for i in diff]))
    chk('old value is 01', a[o] == 1, 'old=%02x' % a[o])
    chk('new value is 02', b[o] == 2, 'new=%02x' % b[o])
    for addr in HOOKS:
        i = addr - BASE
        chk('hook 0x%X unchanged' % addr, a[i:i + 5] == b[i:i + 5])
    # tone 表四张保持不变（与 L30 相同）
    for addr in (0x605DAF44, 0x605DB144, 0x605DB344, 0x605DB544):
        i = addr - BASE
        chk('tone 0x%X unchanged' % addr, a[i:i + 512] == b[i:i + 512])
    chk('size unchanged', len(a) == len(b), '%d vs %d' % (len(a), len(b)))

    ok = all(c['ok'] for c in checks)
    rec = {'pkg': OUT, 'sha256': hashlib.sha256(open(OUT, 'rb').read()).hexdigest(),
           'verifier': 'tools/verify_l31.py', 'closed': ok,
           'scope': 'static: L30 payload + 1 byte at 0x605E9CCC (01->02); no ISP simulation',
           'checks': checks}
    open(OUT + '.sim.json', 'w', encoding='utf-8').write(json.dumps(rec, ensure_ascii=False, indent=1))
    for c in checks:
        print(('PASS ' if c['ok'] else 'FAIL ') + c['name'] + ('  ' + c['detail'] if c['detail'] else ''))
    print('closed =', ok)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
