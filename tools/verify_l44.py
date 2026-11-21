# -*- coding: utf-8 -*-
"""L44 静态校验：四张 tone 表全部替换为 8 级阶梯，其余与 L39 一致。"""
import sys, json, io, hashlib, struct
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
PREV = 'build/L39_tone_trim.BIN'
OUT = 'build/L44_stair.BIN'
TABS = ((0x605DAF44, 'k1'), (0x605DB144, 'k2'), (0x605DB344, 'k3'), (0x605DB544, 'k4'))


def pay(p):
    with open(p, 'rb') as f:
        return oc.parse(f.read())[0].payload


def main():
    a, b = pay(PREV), pay(OUT)
    checks = []

    def chk(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    chk('same payload size', len(a) == len(b), '%d vs %d' % (len(a), len(b)))
    diff = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    lo = min(addr for addr, _ in TABS) - BASE - 0x20
    hi = max(addr for addr, _ in TABS) - BASE - 0x20 + 512
    chk('diff confined to four tone tables', diff and diff[0] >= lo and diff[-1] < hi,
        '%#x..%#x' % (diff[0] + BASE + 0x20, diff[-1] + BASE + 0x20) if diff else 'no diff')
    allok = True
    for addr, name in TABS:
        off = addr - BASE - 0x20
        for i in range(256):
            want = 1023 if ((i // 32) % 2) else 0
            got = struct.unpack_from('<H', b, off + 2 * i)[0]
            if got != want:
                allok = False
                chk('stair %s[%d]' % (name, i), False, '%d != %d' % (got, want))
                break
    chk('all four tables are 8-step staircase', allok)
    for h in (0x604B811D, 0x60479A2B, 0x604B6AD6, 0x604B4CAD):
        off = h - BASE - 0x20
        chk('hook %#x unchanged' % h, a[off:off + 8] == b[off:off + 8])
    ok = all(c['ok'] for c in checks)
    doc = {'pack': OUT, 'sha256': hashlib.sha256(open(OUT, 'rb').read()).hexdigest(),
           'scope': '静态：四张 tone 表替换为阶梯；无 ISP 仿真', 'closed': ok, 'checks': checks}
    io.open(OUT + '.sim.json', 'w', encoding='utf-8').write(json.dumps(doc, indent=1, ensure_ascii=True))
    for c in checks:
        print(('PASS' if c['ok'] else 'FAIL'), c['name'], c['detail'])
    print('closed =', ok)
    return 0 if ok else 1


sys.exit(main())
