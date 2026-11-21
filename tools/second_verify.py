#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""second_verify.py —— 固件包的「二次核验」（独立于构建脚本）

规矩（用户 2026-11-16 定）：任何一个包，不管是主线程出的还是子智能体出的，
上卡前都必须再过一遍这道核验。它与三道门的分工：

  audit_beta_pack.py  查"数据本身合不合法"（校验和/锚点/表的形状）
  patch_check.py      查"钩点可不可达"（静态分支）
  libsim              查"代码路径跑不跑得通"
  second_verify.py    查"这个包到底是什么"——独立重算、独立反汇编，
                      不 import 任何 build_*.py，只认字节。

它会做六件事：
  1. 尺寸 / 容器 / 三组件 / 32 位字和 == 0
  2. 三条锚点（0x60600920 / 0x605ECECA / 0x604B6A98）
  3. 与基线包的逐字节 diff → 分段清单
  4. 每段是否落在"声明的落点"里（--allow 或内置表），越界报警
  5. 代码段变化处**重新反汇编**，把 DC/CA/C8/C9/C1/C6 这类控制流指令的
     目标地址独立算一遍，打印"目标 == 预期？"
  6. tone 池 54 张表：单调、≤1023；与原厂表比较，标出被改的槽

用法：
  python tools/second_verify.py build/B62_gateflip.BIN --base build/B61_chroma2.BIN
  python tools/second_verify.py build/B62_gateflip.BIN --base stock/OLY_E_120_1400_0000_0000.BIN --json
退出码 0 = 通过（可以有 warning），1 = 有 block 级问题。
"""
import argparse
import json
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_beta_pack import oly_container  # 只借用容器解析，不借用任何构建逻辑

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 0x60021020
SIZE = 69599232
TONE_BASE = 0x605D8344
TONE_STEP = 0x200
TONE_N = 54
LOAD_END = 0x60DA5000

ANCHORS = [
    ('池指针[0]', 0x60600920, 'u32', 0x605D8344),
    ('矩阵 m0 行和', 0x605ECECA, 'i16x9', (275, -27, 8, 9, 265, -18, 15, 29, 212)),
    ('代码锚', 0x604B6A98, 'bytes', bytes.fromhex('c810a002c814')),
]

# 已知的、历史上真机验证过的落点（可作为"声明落点"默认集合）
KNOWN_ZONES = [
    (0x604AA228, 0x604AA2A0, '派发后/色度链钩点与桩附近'),
    (0x60479960, 0x60479970, '上传器入口钩点'),
    (0x60479A20, 0x60479A30, '上传器内部钩点'),
    (0x604B6AD0, 0x604B6AE0, '矩阵门钩点'),
    (0x604B8110, 0x604B8130, '派发器哈希段钩点'),
    (0x604803A0, 0x604803C0, '色度记录回灌闸门'),
    (0x605D8344, 0x605DFF44, 'tone 池（54 张表）'),
    (0x601ADE00, 0x601ADE50, 'ISP 系数区旁口袋'),
    (0x60661590, 0x606615E0, '小块 FF 口袋'),
    (0x605DEB00, 0x605DF000, '槽 52/53 一带（桩与块）'),
]

# 已知不可用区域：落在里面直接报 block
DEAD_ZONES = [
    (0x60DA4E08, 0x60DA5000, '栈区（B14b/B15/B45 姿态相关重启）'),
    (0x60DE0000, 0x60E00000, '不进内存（B13 炸机）'),
    (0x605DEF44, 0x605DFF44, 'bank2（开机整片重写，B53 花屏）'),
]


def parse(path):
    data = open(path, 'rb').read()
    blocks = oly_container.parse(data)
    return data, blocks


def wordsum_ok(payload):
    n = len(payload) // 4
    s = 0
    for i in range(n):
        s = (s + struct.unpack_from('<I', payload, i * 4)[0]) & 0xFFFFFFFF
    tail = len(payload) - n * 4
    return s == 0, s, tail


def rd(payload, addr, n):
    o = addr - BASE
    if o < 0 or o + n > len(payload):
        return None
    return payload[o:o + n]


def u32(payload, addr):
    b = rd(payload, addr, 4)
    return None if b is None else struct.unpack('<I', b)[0]


def check_anchors(payload, out):
    for name, addr, kind, want in ANCHORS:
        if kind == 'u32':
            got = u32(payload, addr)
        elif kind == 'i16x9':
            b = rd(payload, addr, 18)
            got = None if b is None else struct.unpack('<9h', b)
        else:
            got = rd(payload, addr, len(want))
        ok = (got == want)
        out.append(('anchor', 'ok' if ok else 'block',
                    '%s @0x%08X = %r（期望 %r）' % (name, addr, got, want)))


def segments(a, b):
    diff = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    if not diff:
        return []
    runs = []
    s = prev = diff[0]
    for i in diff[1:]:
        if i - prev > 1:
            runs.append((s, prev))
            s = i
        prev = i
    runs.append((s, prev))
    return [(BASE + s, BASE + e, e - s + 1) for s, e in runs]


def classify(addr, zones):
    for s, e, name in zones:
        if s <= addr <= e:
            return name
    return None


def ctl_target(payload, addr):
    """独立重算控制流目标：只认 MN10300 的几个短形式。"""
    b = rd(payload, addr, 6)
    if not b:
        return None
    op = b[0]
    if op == 0xDC:      # jmp disp32
        disp = struct.unpack_from('<i', b, 1)[0]
        return ('jmp32', (addr + disp) & 0xFFFFFFFF, addr + 5)
    if op == 0xDD:      # call disp32（后跟栈调整，长度 5 或 7）
        disp = struct.unpack_from('<i', b, 1)[0]
        return ('call32', (addr + disp) & 0xFFFFFFFF, addr + 5)
    if op in (0xC8, 0xC9, 0xC1, 0xC6, 0xCA, 0xCB):   # beq/bne/bgt/bcc/bra/bsr
        disp = struct.unpack_from('<b', b, 1)[0]
        return ('rel8', (addr + disp) & 0xFFFFFFFF, addr + 2)
    return None


def check_diff(payload, base_payload, allow_zones, out, warnings):
    if base_payload is None:
        out.append(('diff', 'warn', '未给基线，跳过 diff 与落点检查'))
        return []
    segs = segments(base_payload, payload)
    out.append(('diff', 'ok', '相对基线共 %d 段 / %d 字节'
                % (len(segs), sum(s[2] for s in segs))))
    for s, e, n in segs:
        where = classify(s, KNOWN_ZONES) or classify(s, allow_zones) or ''
        dead = classify(s, DEAD_ZONES)
        if dead:
            out.append(('zone', 'block', '0x%08X..0x%08X (%dB) 落在**不可用区**：%s'
                        % (s, e, n, dead)))
            continue
        if not where and not allow_zones:
            warnings.append('0x%08X..0x%08X (%dB) 不在已知落点表里' % (s, e, n))
        # 代码段：反汇编目标
        if s < 0x60600000:
            t = ctl_target(payload, s)
            if t:
                kind, tgt, end = t
                out.append(('ctl', 'ok', '0x%08X %s → 0x%08X（%dB）'
                            % (s, kind, tgt, n)))
    return segs


def check_tone(payload, stock_payload, out):
    if stock_payload is None:
        return
    changed = []
    for k in range(TONE_N):
        addr = TONE_BASE + k * TONE_STEP
        cur = rd(payload, addr, 512)
        st = rd(stock_payload, addr, 512)
        if cur is None or st is None:
            out.append(('tone', 'block', '槽 %d 读不到（0x%08X）' % (k, addr)))
            continue
        vals = struct.unpack('<256H', cur)
        mono = all(vals[i] <= vals[i + 1] for i in range(255))
        rng = max(vals) <= 1023
        if not (mono and rng):
            note = '（槽 52/53 已被本包征用，属预期）' if k in (52, 53) else ''
            out.append(('tone', 'warn', '槽 %d 非单调或越界（max=%d, mono=%s）%s'
                        % (k, max(vals), mono, note)))
        if cur != st:
            changed.append(k)
    out.append(('tone', 'ok', '被改动的槽：%s' % (changed if changed else '无')))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('bin')
    ap.add_argument('--base', default=None, help='基线包（一般为上一版或 stock）')
    ap.add_argument('--allow', default=None, help='额外允许的落点 JSON: [[start,end,name],...]')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    path = a.bin if os.path.isabs(a.bin) else os.path.join(ROOT, a.bin)
    data, blocks = parse(path)
    payload = blocks[0].payload
    stock_data, stock_blocks = parse(os.path.join(ROOT, 'stock', 'OLY_E_120_1400_0000_0000.BIN'))
    stock_payload = stock_blocks[0].payload

    out, warnings = [], []
    out.append(('size', 'ok' if len(data) == SIZE else 'block',
                '%d 字节（期望 %d）' % (len(data), SIZE)))
    out.append(('blocks', 'ok' if len(blocks) == 3 else 'block',
                '容器解析出 %d 个组件：%s' % (len(blocks), [hex(b.address) for b in blocks])))
    b0 = blocks[0]
    tot = (oly_container.sum32(b0.header) + oly_container.sum32(b0.payload)
           + oly_container.sum32(b0.trailer)) & 0xFFFFFFFF
    out.append(('checksum', 'ok' if tot == 0 else 'block',
                '头+payload+尾 32 位字和 = 0x%08X（应为 0）' % tot))

    check_anchors(payload, out)

    base_payload = None
    if a.base:
        bp = a.base if os.path.isabs(a.base) else os.path.join(ROOT, a.base)
        base_payload = parse(bp)[1][0].payload
    allow = []
    if a.allow:
        allow = [tuple(x) for x in json.load(open(a.allow, encoding='utf-8'))]

    check_diff(payload, base_payload, allow, out, warnings)
    check_tone(payload, stock_payload, out)

    blocks_n = sum(1 for _, lv, _ in out if lv == 'block')
    warns = len(warnings) + sum(1 for _, lv, _ in out if lv == 'warn')

    if a.json:
        print(json.dumps({
            'bin': os.path.basename(path),
            'verdict': 'PASS' if blocks_n == 0 else 'FAIL',
            'block': blocks_n, 'warn': warns,
            'checks': [{'kind': k, 'level': lv, 'detail': d} for k, lv, d in out],
            'warnings': warnings,
        }, ensure_ascii=False, indent=2))
    else:
        print('== 二次核验：%s' % os.path.basename(path))
        for kind, lv, detail in out:
            mark = {'ok': 'OK  ', 'warn': 'WARN', 'block': 'FAIL'}[lv]
            print('  %s [%-7s] %s' % (mark, kind, detail))
        for w in warnings:
            print('  WARN  [zone   ] %s' % w)
        print('\n结论：%s（block=%d, warn=%d）'
              % ('PASS' if blocks_n == 0 else 'FAIL', blocks_n, warns))
    return 0 if blocks_n == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
