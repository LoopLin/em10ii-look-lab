# -*- coding: utf-8 -*-
"""verify_release.py -- 成品包的静态审计：从 stock 起的每一处改动都必须落在白名单里"""
import os, sys, json, hashlib
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
STOCK = 'stock/OLY_E_120_1400_0000_0000.BIN'
REL = os.environ.get('RELEASE_BIN', 'build/RELEASE_v1.BIN')
REF = 'build/L55_newton3.BIN'

TONE = [0x605DAF44, 0x605DB144, 0x605DB344, 0x605DB544]
MATRIX = [0x605DEEE0, 0x605DEEF2, 0x605DEF04, 0x605DEF16]
CHROMA = [0x605DEB44, 0x605DEBA8, 0x605DEC0C, 0x605DEC70]
HOOKS = [(0x604B811D, 7), (0x60479A2B, 5), (0x604B6AD6, 5), (0x604B4CAD, 6)]
STUBS = [(0x605DECD4, 69), (0x605DED1C, 38), (0x605DED44, 56),
         (0x605DEDD4, 16), (0x605DEDE4, 24), (0x605DEE3C, 16),
         (0x601ADE04, 50)]
POOL = (0x605DEEE0, 0x605DEF28)
SINGLE = [0x605E9CCC, 0x604B9F1F, 0x604B9D1A, 0x604B9D1B]

ok = []


def chk(name, cond):
    ok.append((name, bool(cond)))


def pay(p):
    return oc.parse(open(p, 'rb').read())[0].payload


s, r, ref = pay(STOCK), pay(REL), pay(REF)
chk('尺寸与 stock 相同', len(s) == len(r))
chk('block0 与 L55 逐字节相同', r == ref)

white = set()
for a in TONE + MATRIX + CHROMA:
    n = 512 if a in TONE else (18 if a in MATRIX else 100)
    white |= set(range(a - BASE, a - BASE + n))
for a, n in HOOKS + STUBS:
    white |= set(range(a - BASE, a - BASE + n))
white |= set(range(POOL[0] - BASE, POOL[1] - BASE))
white |= {x - BASE for x in SINGLE}

diff = [i for i in range(len(s)) if s[i] != r[i]]
stray = [i for i in diff if i not in white]
chk('改动总数 %d、其中白名单外 %d 字节' % (len(diff), len(stray)), not stray)
if stray:
    print('   白名单外示例:', [hex(i + BASE) for i in stray[:12]])

chk('nslot==0x02', r[0x605E9CCC - BASE] == 0x02)
chk('弯曲门==0xCA', r[0x604B9F1F - BASE] == 0xCA)
chk('兜底表指向 0x605DB144', r[0x604B9D1A - BASE:0x604B9D1C - BASE] == bytes([0x44, 0xB1]))
chk('四张 tone 表 === L55', all(r[a - BASE:a - BASE + 512] == ref[a - BASE:a - BASE + 512] for a in TONE))
chk('四套矩阵 === L55', all(r[a - BASE:a - BASE + 18] == ref[a - BASE:a - BASE + 18] for a in MATRIX))
chk('四块色度 === L55', all(r[a - BASE:a - BASE + 100] == ref[a - BASE:a - BASE + 100] for a in CHROMA))

b1 = oc.parse(open(REL, 'rb').read())[1].payload
UI_EXPECT = [x.strip() for x in os.environ.get(
    'UI_EXPECT', 'Ektachrome 100 VS,Elite Color 200,Classic Chrome,Pro Neg Hi').split(',') if x.strip()]
for nm in UI_EXPECT:
    chk('UI 名 %s' % nm, nm.encode('utf-16-le') in b1)

bad = [n for n, c in ok if not c]
for n, c in ok:
    print(('[OK ] ' if c else '[ERR] ') + n)
sha = hashlib.sha256(open(REL, 'rb').read()).hexdigest()
json.dump({'verifier': 'verify_release.py', 'sha256': sha, 'closed': not bad,
           'scope': '静态：从 stock 起的全量字节白名单 + tone/矩阵/色度与 L55 一致 + UI 名存在；无 ISP 仿真'},
          open(REL + '.sim.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('closed =', not bad, ' sha256', sha)
sys.exit(1 if bad else 0)
