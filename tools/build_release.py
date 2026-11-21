# -*- coding: utf-8 -*-
"""build_release.py -- 从原厂 stock 一路构建成品固件（不基于任何旧包）

组成（每一块都来自 stock + 明确补丁）：
  1. `.tmp/b90.patch`  —— 基础设施：四个钩子/桩、矩阵四套、色度四块（stock-based）
  2. 四张 tone 表      —— 用 L55 的最终值（512 B ×4，直接覆盖 b90 的初始表）
  3. 三处单字节/双字节 —— N 槽 0x605E9CCC=02、弯曲门 0x604B9F1F=CA、
                          兜底表立即数 0x604B9D1A=44b1（指向我们的 k2 表）
  4. UI 名             —— .tmp/ui_names.blk.patch（主菜单）+ .tmp/ui_names2.blk.patch（SCP 条）

输出 build/RELEASE_v1.BIN；所有校验在同一次运行里做完。
"""
import sys, subprocess, hashlib, os
sys.path.insert(0, 'tools')
import oly_container as oc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK = os.path.join(ROOT, 'stock', 'OLY_E_120_1400_0000_0000.BIN')
L55 = os.path.join(ROOT, 'build', 'L55_newton3.BIN')
OUT = os.environ.get('RELEASE_OUT', os.path.join(ROOT, 'build', 'RELEASE_v1.BIN'))
BASE = 0x60021020
TONE = [0x605DAF44, 0x605DB144, 0x605DB344, 0x605DB544]
MATRIX = [0x605DEEE0, 0x605DEEF2, 0x605DEF04, 0x605DEF16]
CHROMA = [0x605DEB44, 0x605DEBA8, 0x605DEC0C, 0x605DEC70]
SINGLE = {0x605E9CCC: '02', 0x604B9F1F: 'ca'}
PAIR = {0x604B9D1A: '44b1'}


def pay(path):
    return oc.parse(open(path, 'rb').read())[0].payload


def run(*args):
    print('$', ' '.join(args))
    r = subprocess.run([sys.executable] + list(args), cwd=ROOT,
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    tail = (r.stdout or '').strip().splitlines()[-2:]
    for line in tail:
        print('   ', line)
    if r.returncode:
        raise SystemExit('step failed: %s\n%s' % (args[1], r.stderr[-800:]))


def mk(src, out, patch):
    work = os.path.join(ROOT, 'build', '_rel')
    os.makedirs(work, exist_ok=True)
    tmp = os.path.join(work, 'step_%s.bin' % os.path.basename(out))
    open(tmp, 'wb').write(open(src, 'rb').read())
    run(os.path.join('tools', 'mkpatch.py'), tmp, '--patch-file', patch, '--out', out)


# ---- 2/3: 生成"tone 表 + 三处字节"补丁（都对 stock 的 RAM 地址） ----
lines = ['# release: final tone tables (from L55) + 3 byte edits']
L = pay(L55)
for addr in TONE:
    o = addr - BASE
    lines.append('ram 0x%x=%s  # tone table %d' % (addr, L[o:o + 512].hex(), TONE.index(addr) + 1))
for addr in MATRIX:
    o = addr - BASE
    lines.append('ram 0x%x=%s  # matrix %d' % (addr, L[o:o + 18].hex(), MATRIX.index(addr) + 1))
for addr in CHROMA:
    o = addr - BASE
    lines.append('ram 0x%x=%s  # chroma %d' % (addr, L[o:o + 100].hex(), CHROMA.index(addr) + 1))
for addr, hexv in SINGLE.items():
    lines.append('ram 0x%x=%s' % (addr, hexv))
for addr, hexv in PAIR.items():
    lines.append('ram 0x%x=%s' % (addr, hexv))
patch_tone = os.path.join(ROOT, '.tmp', 'release_tone.patch')
open(patch_tone, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

# ---- 组装 ----
step = [
    (STOCK, os.path.join(ROOT, '.tmp', 'rel1.BIN'), os.path.join(ROOT, '.tmp', 'b90.patch')),
    (os.path.join(ROOT, '.tmp', 'rel1.BIN'), os.path.join(ROOT, '.tmp', 'rel2.BIN'), patch_tone),
    (os.path.join(ROOT, '.tmp', 'rel2.BIN'), os.path.join(ROOT, '.tmp', 'rel3.BIN'),
     os.environ.get('UI_NAMES_PATCH', os.path.join(ROOT, '.tmp', 'ui_names.blk.patch'))),
    (os.path.join(ROOT, '.tmp', 'rel3.BIN'), OUT,
     os.environ.get('UI_NAMES2_PATCH', os.path.join(ROOT, '.tmp', 'ui_names2.blk.patch'))),
]
for src, out, patch in step:
    mk(src, out, patch)

# ---- 校验：与 L55 比对（除 UI 两个块外应逐字节相同） ----
rel = oc.parse(open(OUT, 'rb').read())
ref = oc.parse(open(L55, 'rb').read())
print('\n== 校验 ==')
for i, (a, b) in enumerate(zip(ref, rel)):
    d = [k for k in range(len(a.payload)) if a.payload[k] != b.payload[k]]
    print('block %d: 与 L55 差异 %d 字节%s' % (i, len(d), '' if not d else ' (UI 期望只动 block 1)'))
print('tone 表一致:', all(L[addr - BASE:addr - BASE + 512] == rel[0].payload[addr - BASE:addr - BASE + 512]
                        for addr in TONE))
print('nslot/gate/funnel:', hex(rel[0].payload[0x605E9CCC - BASE]),
      hex(rel[0].payload[0x604B9F1F - BASE]),
      rel[0].payload[0x604B9D1A - BASE:0x604B9D1C - BASE].hex())
u = rel[1].payload
print('UI 名（菜单）:', 'Look 1'.encode('utf-16-le') in u or 'Ektachrome 100 VS'.encode('utf-16-le') in u,
      'Look 2'.encode('utf-16-le') in u or 'Elite Color 200'.encode('utf-16-le') in u)
print('矩阵/色度一致:', all(rel[0].payload[a - BASE:a - BASE + 18] == L[a - BASE:a - BASE + 18] for a in MATRIX),
      all(rel[0].payload[a - BASE:a - BASE + 100] == L[a - BASE:a - BASE + 100] for a in CHROMA))
print('sha256', hashlib.sha256(open(OUT, 'rb').read()).hexdigest())
