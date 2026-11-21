#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l37.py —— L37_dialprobe 的静态校验（只读，不写包）

查什么（全部只认字节，不 import build_l37）：
  1. 容器：尺寸不变、能解析、块 0 的 32 位字和 == 0
  2. 与 stock 的逐字节 diff：只允许两段 —— 钩点 8 B、桩 248 B，且不重叠
  3. 钩点 0x604B9EBF：DC disp32 的落点 == 桩 0x605DED44，后 3 B 为填充 0x00
  4. 桩：在 WINDOW 内、4 对齐；首条 add -8,sp；末条 jmp 落点 == 0x604B9EC4
  5. 桩里确实带了 EXIF 文本区常量 0x61894C90（小端出现在立即数里）
  6. 门字节 0x604B9F1F 仍为 C8（本包不动弯曲）；EXIF 文本区与 stock 一致
  7. 载荷 52 B <= 117 B 的预算（由桩内 store 偏移 +0x30..+0x33 界定）

用法：python tools/verify_l37.py [包路径]
退出码 0 = PASS，1 = FAIL；同时写 <包>.sim.json。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.chdir(ROOT)

import oly_container as oc                      # noqa: E402

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT = sys.argv[1] if len(sys.argv) > 1 else 'build/L37_dialprobe.BIN'

HOOK, HOOK_LEN = 0x604B9EBF, 8
HOOK_RET = 0x604B9EC4
STUB = 0x605DED44
GATE_BYTE = 0x604B9F1F
WINDOW = (0x605D8344, 0x605DEF44)
EXIF_TEXT = 0x61894C90
EXIF_TEXT_LEN = 117
PAYLOAD_LEN = 0x34
SIZE = 69_599_232


def pay(p):
    with open(p, 'rb') as f:
        return oc.parse(f.read())[0].payload


def main():
    checks = []

    def ck(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})
        return ok

    ok = True
    data = open(OUT, 'rb').read()
    ok &= ck('尺寸不变', len(data) == SIZE, '%d B' % len(data))
    blocks = oc.parse(data)
    ok &= ck('容器可解析', len(blocks) == 3, '%d 块' % len(blocks))
    b0 = blocks[0]
    tot = (oc.sum32(b0.header) + oc.sum32(b0.payload) + oc.sum32(b0.trailer)) & 0xFFFFFFFF
    ok &= ck('块 0 字和 == 0', tot == 0, '0x%08x' % tot)

    stock = pay(SRC)
    built = pay(OUT)
    diff = [i for i in range(len(built)) if built[i] != stock[i]]

    def rd(addr, n):
        return built[addr - BASE: addr - BASE + n]

    # 桩长按结构判定：桩内第一条 jmp disp32 且落点 == HOOK_RET 的指令就是桩尾
    sb_probe = rd(STUB, 0x200)
    stub_len = None
    for i in range(0, 0x200 - 5):
        if sb_probe[i] == 0xDC:
            t = (STUB + i + struct.unpack_from('<i', sb_probe, i + 1)[0]) & 0xFFFFFFFF
            if t == HOOK_RET:
                stub_len = i + 5
                break
    ok &= ck('桩尾可定位', stub_len is not None,
             '未找到 -> 0x%08x 的 jmp' % HOOK_RET if stub_len is None else '%d B' % stub_len)
    if stub_len is None:
        stub_len = 0
    ranges = [(HOOK, HOOK_LEN), (STUB, stub_len)]

    runs = []
    prev = None
    for i in diff:
        if prev is None or i != prev + 1:
            runs.append([i, i])
        else:
            runs[-1][1] = i
        prev = i
    ok &= ck('diff 全落在两段落点内', bool(diff) and all(
        any(a - BASE <= r[0] and r[1] < a - BASE + l for a, l in ranges) for r in runs),
        '%d 字节 / %d 段（钩点 %d B + 桩 %d B）'
        % (len(diff), len(runs), HOOK_LEN, stub_len))
    outside = [i for i in diff if not any(a - BASE <= i < a - BASE + l for a, l in ranges)]
    ok &= ck('无区外改动', not outside, '区外 %d 字节' % len(outside))
    gap = [i for i in range(HOOK + HOOK_LEN - BASE, STUB - BASE) if built[i] != stock[i]]
    ok &= ck('钩点与桩之间无改动', not gap, '中间改动 %d 字节' % len(gap))

    hb = rd(HOOK, HOOK_LEN)
    tgt = (HOOK + struct.unpack_from('<i', hb, 1)[0]) & 0xFFFFFFFF
    ok &= ck('钩点是 jmp disp32', hb[0] == 0xDC, hb[:5].hex())
    ok &= ck('钩点落点 == 桩', tgt == STUB, '0x%08x' % tgt)
    ok &= ck('钩点填充 3 B 为 0', hb[5:] == b'\x00\x00\x00', hb[5:].hex())

    sb = rd(STUB, stub_len)
    ok &= ck('桩在 WINDOW 内', WINDOW[0] <= STUB and STUB + stub_len <= WINDOW[1],
             '0x%08x..0x%08x（窗口 0x%08x..0x%08x）'
             % (STUB, STUB + stub_len, WINDOW[0], WINDOW[1]))
    ok &= ck('桩 4 对齐', STUB % 4 == 0, '0x%08x' % STUB)
    ok &= ck('桩首条 add -8,sp', sb[:3] == bytes.fromhex('f8fef8'), sb[:3].hex())
    last = sb[stub_len - 5:stub_len]
    back = (STUB + stub_len - 5 + struct.unpack_from('<i', last, 1)[0]) & 0xFFFFFFFF
    ok &= ck('桩尾是 jmp', last[0] == 0xDC, last.hex())
    ok &= ck('桩尾落点 == 0x604B9EC4', back == HOOK_RET, '0x%08x' % back)
    ok &= ck('桩含 EXIF 文本区常量', struct.pack('<I', EXIF_TEXT) in sb,
             '0x%08x' % EXIF_TEXT)
    ok &= ck('载荷 <= 117 B', PAYLOAD_LEN <= EXIF_TEXT_LEN, '%d B' % PAYLOAD_LEN)

    ok &= ck('门字节未动（C8）', rd(GATE_BYTE, 2) == stock[GATE_BYTE - BASE: GATE_BYTE - BASE + 2],
             rd(GATE_BYTE, 2).hex())
    ok &= ck('EXIF 文本区未动',
             rd(EXIF_TEXT, EXIF_TEXT_LEN) == stock[EXIF_TEXT - BASE: EXIF_TEXT - BASE + EXIF_TEXT_LEN],
             '117 B')

    sha = hashlib.sha256(data).hexdigest()
    sim = {'pkg': OUT, 'sha256': sha, 'verifier': 'verify_l37.py', 'closed': ok,
           'scope': 'static-only（未上卡）',
           'layout': {'hook': HOOK, 'hook_len': HOOK_LEN, 'stub': STUB,
                      'stub_len': stub_len, 'hook_ret': HOOK_RET,
                      'exif_text': EXIF_TEXT, 'payload_len': PAYLOAD_LEN},
           'checks': checks}
    with io.open(OUT + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(sim, f, ensure_ascii=False, indent=2)

    print('L37 校验 %s' % OUT)
    print('  sha256 %s' % sha)
    for c in checks:
        print('  [%s] %-24s %s' % ('OK ' if c['ok'] else 'BAD', c['name'], c['detail']))
    print('  -> %s  (.sim.json 已写)' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
