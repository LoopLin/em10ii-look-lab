#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b62.py —— B62 离线验证 = B61 的 verify_b61.py + 闸门翻转三条断言

## 做法

A. 先说 B61 那套（整链回归 + 色度双写 12 用例）用 `tools/verify_b61.py <B62>` 原样跑一遍，
   它会把 `chroma.closed` 与 sha256 写进 `build/B62_gateflip.BIN.sim.json`。
B. 本脚本再追加闸门断言（全部写进同一个 sim.json）：

  C22 静态：`0x604803AF` == `CA 1A`；`0x604803AD..AE` == `A0 01`（cmp 未动）；
            `0x604803C6..C8` == `CC 54 07`（jmp 0x60480B1A 未动）；
            位移按**指令起始地址**算 = 0x604803C9
  C23 libsim：从 `0x604803AF` 起跑、D0=0、PSW=0 → 停在 `0x604803C9`（bra 无条件跳）
  C24 libsim：把 PSW 调到"原 bne 不跳"的那一位（先用 stock 的 `C9 1A` 扫出来），
             B62 仍停在 `0x604803C9` —— 证明翻转真的把这条分支变成无条件
  C25 与 B61 的差异只有 1 字节（0x604803AF）

用法：
  python tools/verify_b62.py
  python tools/verify_b62.py build/B62_gateflip.BIN
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import time

sys.path.insert(0, 'tools')

import verify_b32_chain as V               # noqa: E402
import build_b61 as B61                    # noqa: E402
import build_b62 as B62                    # noqa: E402

BASE = B61.BASE
GATE = B62.GATE
TARGET = B62.GATE_TARGET


def payload_of(path):
    import oly_container as oc
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def regs_after(image, start, stop, regs, limit=12):
    d = V.sim(image, start, stop, limit=limit, regs=regs)
    return V.regs_of(d), d


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    path = sys.argv[1] if len(sys.argv) > 1 else B62.OUT
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    stock = payload_of(B61.SRC)
    b61 = payload_of(B61.OUT)

    def at(img, addr, n):
        return bytes(img[addr - BASE:addr - BASE + n])

    print('== A. 先跑 B61 全套验证（tools/verify_b61.py %s）' % path)
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    r = subprocess.run([sys.executable, 'tools/verify_b61.py', path],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', env=env)
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    for l in lines[-6:]:
        print('   ' + l)
    a_ok = (r.returncode == 0)
    if not a_ok:
        print(r.stdout[-4000:])

    print('\n== B. 闸门翻转断言')
    ok = a_ok

    gate_now = at(image, GATE, 2)
    c22a = gate_now == B62.GATE_NEW
    c22b = at(image, B62.CMP_SITE, 2) == B62.CMP_BYTES
    c22c = at(image, B62.JMP_SITE, 3) == B62.JMP_BYTES
    disp = struct.unpack('b', B62.GATE_NEW[1:2])[0]
    c22d = (GATE + disp) == TARGET
    print('   闸门 %s == %s : %s' % (hex(GATE), B62.GATE_NEW.hex(), c22a))
    print('   cmp @0x%X == %s : %s' % (B62.CMP_SITE, B62.CMP_BYTES.hex(), c22b))
    print('   jmp @0x%X == %s (->0x60480B1A) : %s'
          % (B62.JMP_SITE, B62.JMP_BYTES.hex(), c22c))
    print('   位移基准=起始 0x%X + (%+d) = 0x%X : %s' % (GATE, disp, GATE + disp, c22d))
    c22 = all([c22a, c22b, c22c, c22d])
    ok &= c22

    r0, d0 = regs_after(image, GATE, TARGET, {'D0': 0, 'PSW': 0})
    c23 = (r0['PC'] == TARGET and d0.get('status') not in ('memory_fault', 'instruction_fault'))
    print('   C23 D0=0 PSW=0 -> PC=0x%X（期望 0x%X）: %s' % (r0['PC'], TARGET, c23))
    ok &= c23

    # C24：从 cmp 那条指令起跑，用真算出来的标志位比 "bne 会不跳" 与 "bra 恒跳"
    #   D0=1 -> cmp 相等 -> Z=1 -> 原 bne 不跳（落到 0x604803B1 的拷贝循环）
    #   D0=0 -> Z=0 -> 原 bne 跳；两种 D0 下 B62 的 bra 都跳
    CMP = B62.CMP_SITE
    NEXT = GATE + 2
    rs_eq, _ = regs_after(stock, CMP, NEXT, {'D0': 1})
    rs_ne, _ = regs_after(stock, CMP, TARGET, {'D0': 0})
    rb_eq, dbe = regs_after(image, CMP, TARGET, {'D0': 1})
    rb_ne, _ = regs_after(image, CMP, TARGET, {'D0': 0})
    c24 = (rs_eq['PC'] == NEXT and rs_ne['PC'] == TARGET
           and rb_eq['PC'] == TARGET and rb_ne['PC'] == TARGET
           and dbe.get('status') not in ('memory_fault', 'instruction_fault'))
    print('   C24 stock: D0=1(Z=1) bne 不跳 -> PC=0x%X（期望 0x%X，落进拷贝循环）'
          % (rs_eq['PC'], NEXT))
    print('       stock: D0=0 bne 跳 -> PC=0x%X（期望 0x%X）' % (rs_ne['PC'], TARGET))
    print('       B62  : D0=1 -> PC=0x%X / D0=0 -> PC=0x%X（bra 无条件，均期望 0x%X）'
          % (rb_eq['PC'], rb_ne['PC'], TARGET))
    print('       -> C24 %s' % c24)
    ok &= c24

    d61 = [i for i in range(len(image)) if image[i] != b61[i]]
    c25 = d61 == [GATE - BASE]
    print('   C25 vs B61 差异 %d 字节 %s : %s'
          % (len(d61), ['0x%X' % (BASE + i) for i in d61[:6]], c25))
    ok &= c25

    rec = {}
    try:
        with io.open(path + '.sim.json', encoding='utf-8') as f:
            rec = json.load(f)
    except Exception:
        pass
    prev_closed = bool(rec.get('closed', False))
    if rec.get('verifier'):
        rec['b61_verifier'] = rec['verifier']
    rec['verifier'] = 'tools/verify_b62.py'
    rec['sha256'] = hashlib.sha256(whole).hexdigest()
    rec['gateflip'] = {
        'site': '0x%X' % GATE, 'old': B62.GATE_OLD.hex(), 'new': B62.GATE_NEW.hex(),
        'disp': disp, 'disp_base': 'instruction start address',
        'target': '0x%X' % TARGET,
        'cmp_intact': c22b, 'jmp_intact': c22c,
        'note': '任务书里的 0x604803AD 是 cmp 的地址；C9 1A 实际在 0x604803AF',
        'sim_c23_bra_uncond': bool(c23),
        'sim_c24_bne_vs_bra': bool(c24),
        'c24_note': ('从 0x604803AD 的 cmp 起跑：stock D0=1(Z=1) 时 bne 不跳 → 0x604803B1；'
                     'D0=0 时跳 → 0x604803C9；B62 的 bra 两种 D0 都跳 → 0x604803C9'),
        'diff_vs_b61_bytes': len(d61), 'diff_vs_b61_ok': bool(c25),
    }
    rec['closed'] = bool(prev_closed and ok)
    rec['time'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    with io.open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

    print('\nA. B61 全套: %s' % ('闭合' if a_ok else '未闭合'))
    print('B. 闸门翻转: C22=%s C23=%s C24=%s C25=%s' % (c22, c23, c24, c25))
    print('-> ' + ('全部闭合' if ok else 'FAIL'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, rec['closed']))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
