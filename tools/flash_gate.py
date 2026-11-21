# -*- coding: utf-8 -*-
"""flash_gate.py —— 上卡前的硬门（2026-11-16 立规）。

规则：**任何一个固件包，没有离线模拟记录就不许上卡。**
本脚本是唯一允许把包写到相机卡的可执行入口：
  1. 尺寸必须是 69,599,232 B；
  2. 容器能被 oly_container 解析（签名/校验和）；
  3. 必须有同名 sidecar `<包名>.sim.json`，且 `closed: true`、其中的 sha256 与包一致；
  4. `audit_beta_pack.py` 必须报 [OK]；
四条全过才允许 --copy 写 `DCOLYMP/E1209999.BIN`，并记一行到 `docs/firmware/FLASH-LOG.md`。

用法：
  python tools/flash_gate.py build/B48_stash_extreme.BIN          # 只判定，不动卡
  python tools/flash_gate.py build/B48_stash_extreme.BIN --copy   # 判定通过后写卡
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import oly_container as oc

SIZE = 69_599_232
OFFICIAL = 'ff9d302318ecf6e06c0b31c181fc6a185e93623e819749db31cec7977fa92052'


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def find_card():
    # 优先认“真相机卡”：同时有 DCOLYMP 与 DCIM\100OLYMP 的盘
    cands = ['%s:\\DCOLYMP' % c for c in 'DEFGHIJKLMNOPQRSTUVWXYZ'
             if os.path.isdir('%s:\\DCOLYMP' % c)]
    for p in cands:
        if os.path.isdir(os.path.join(os.path.dirname(p), 'DCIM', '100OLYMP')):
            return p
    return cands[0] if cands else None


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    pkg = sys.argv[1]
    do_copy = '--copy' in sys.argv

    rows = []
    ok = True

    size = os.path.getsize(pkg)
    rows.append(('尺寸 %d B' % size, size == SIZE))
    ok &= size == SIZE

    try:
        comps = oc.parse(open(pkg, 'rb').read())
        rows.append(('容器解析（%d 个组件）' % len(comps), True))
    except Exception as e:                                    # noqa: BLE001
        rows.append(('容器解析失败：%s' % e, False))
        ok = False

    digest = sha256_of(pkg)
    sim_path = pkg + '.sim.json'
    if os.path.exists(sim_path):
        with open(sim_path, encoding='utf-8') as f:
            sim = json.load(f)
        same = sim.get('sha256') == digest
        closed = bool(sim.get('closed'))
        rows.append(('模拟记录 closed=%s、sha 一致=%s（%s，%d 例）'
                     % (closed, same, sim.get('verifier'), sim.get('cases', 0)),
                     closed and same))
        ok &= closed and same
    else:
        rows.append(('缺模拟记录 %s' % os.path.basename(sim_path), False))
        ok = False

    r = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'audit_beta_pack.py'), pkg],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    audit_ok = '[OK ]' in (r.stdout or '')
    rows.append(('audit_beta_pack [OK]', audit_ok))
    ok &= audit_ok

    print('== 上卡硬门：%s' % pkg)
    for name, good in rows:
        print('   %s  %s' % ('PASS' if good else 'FAIL', name))
    print('   sha256 = %s' % digest)

    if not ok:
        print('\n结论：不许上卡。')
        sys.exit(1)

    print('\n结论：可以上卡。')
    if not do_copy:
        print('（未给 --copy，未动卡）')
        return

    card = find_card()
    if not card:
        print('找不到 DCOLYMP（卡没插？）——未写卡。')
        sys.exit(1)
    off = os.path.join(card, 'E1200001.BIN')
    if not os.path.exists(off) or sha256_of(off) != OFFICIAL:
        print('警告：卡上官方 E1200001.BIN 缺失或被改！仍未写卡，请先恢复官方包。')
        sys.exit(1)

    dst = os.path.join(card, 'E1209999.BIN')
    shutil.copyfile(pkg, dst)
    got = sha256_of(dst)
    print('已写卡：%s  sha=%s  %s' % (dst, got[:16], 'OK' if got == digest else 'MISMATCH'))
    log = os.path.join(ROOT, 'docs', 'firmware', 'FLASH-LOG.md')
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, 'a', encoding='utf-8') as f:
        f.write('| %s | %s | %s | 模拟 closed |\n'
                % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), os.path.basename(pkg), digest[:16]))
    print('已记录到 docs/firmware/FLASH-LOG.md')


if __name__ == '__main__':
    main()
