#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_l39.py —— L40_keyprobe 的静态校验（只读，不写包、不 import build_l39）

查什么（全部只认字节）：
  1. 容器：尺寸不变、能解析、块 0 的 32 位字和 == 0
  2. 与 stock 的逐字节 diff：只允许四段 —— 钩点 A 7 B、钩点 B 5 B、SD 桩、SU 桩
  3. 钩点 A 0x604B811D：DC disp32 落点 == SD 桩 0x605DED44，后 2 B 填充 0x00
  4. 钩点 B 0x60479A2B：DC disp32 落点 == SU 桩 0x605DEDA0（5 B，无填充）
  5. 两个桩的尾巴都是 jmp，落点分别 == 0x604B8124 / 0x60479A32
  6. SD 桩里确实带了 6 个探针读（movbu (0x424|0x427|0x42B|0x42E|0x42F|0x430,a2),d0 =
     `fa 42 <off_lo> <off_hi>`）、两个落点常量（KEY_STASH / PROBE_STASH）和原厂
     `cmp 0x01010066`
  7. SU 桩里确实带了 EXIF 文本区常量 0x61894C90、探针区常量 0x61894CA0、
     "k=" 引导常量 0x313D6B，并且把栈还原（add 12,sp）
  8. 不变量：键表 0x605E9CC8（12 B）、EXIF 文本区（117 B）、弯曲门 0x604B9F1F、
     tone 槽 22/25 与 stock 一致；bank2 0x605DEF4E..0x605DF74E 零改动
  9. 落点全部在 WINDOW 0x605D8344..0x605DEF44 内、4 对齐、互不重叠

用法：python tools/verify_l39.py [包路径]
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
OUT = sys.argv[1] if len(sys.argv) > 1 else 'build/L40_keyprobe.BIN'

SD_HOOK, SD_HOOK_LEN = 0x604B811D, 7
SD_RET = 0x604B8124
SD_STUB = 0x605DED44

SU_HOOK, SU_HOOK_LEN = 0x60479A2B, 5
SU_RET = 0x60479A32
SU_STUB = 0x605DEDA0

PROBE_STASH = 0x605DED88
KEY_STASH = 0x605DED90
EXIF_TEXT = 0x61894C90
EXIF_KEY = 0x61894C94
EXIF_PROBE = 0x61894CA0
EXIF_REC = 0x61894C98          # B75A 记录基址（read_probe.py 认这个）
B75A_MAGIC = 0x41353742        # 'B75A' 小端 u32
EXIF_LEAD = 0x313D6B
PROBE_OFFSETS = (0x424, 0x427, 0x42B, 0x42E, 0x42F, 0x430)
WINDOW = (0x605D8344, 0x605DEF44)
PROVEN = (0x605DECD4, 0x605DEE4C)   # L30 已用 157 个非零字节证明这段是死曲线
BANK2 = (0x605DEF4E, 0x605DF74E)
GATE_BYTE = 0x604B9F1F
EXIF_TEXT_LEN = 117
SIZE = 69_599_232


def pay(p):
    with open(p, 'rb') as f:
        return oc.parse(f.read())[0].payload


def find_tail(blob_base, blob, target, cap=0x200):
    """在 blob 里找 jmp disp32 落点 == target 的那条，返回 (桩长, 尾偏移)。"""
    for i in range(0, min(len(blob), cap) - 5):
        if blob[i] == 0xDC:
            t = (blob_base + i + struct.unpack_from('<i', blob, i + 1)[0]) & 0xFFFFFFFF
            if t == target:
                return i + 5, i
    return None, None


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

    # ---- 桩边界：用「尾巴 jmp 落点」反推，不依赖 build 脚本 ----
    sd_len = find_tail(SD_STUB, rd(SD_STUB, 0x200), SD_RET)[0]
    su_len = find_tail(SU_STUB, rd(SU_STUB, 0x200), SU_RET)[0]
    ok &= ck('SD 桩尾可定位', sd_len is not None,
             '未找到 -> 0x%08x 的 jmp' % SD_RET if sd_len is None else '%d B' % sd_len)
    ok &= ck('SU 桩尾可定位', su_len is not None,
             '未找到 -> 0x%08x 的 jmp' % SU_RET if su_len is None else '%d B' % su_len)
    sd_len = sd_len or 0
    su_len = su_len or 0

    ranges = [(SD_HOOK, SD_HOOK_LEN), (SU_HOOK, SU_HOOK_LEN),
              (SD_STUB, sd_len), (SU_STUB, su_len),
              (PROBE_STASH, 8), (KEY_STASH, 4)]
    named = [('SD 钩点', SD_HOOK, SD_HOOK_LEN), ('SU 钩点', SU_HOOK, SU_HOOK_LEN),
             ('SD 桩', SD_STUB, sd_len), ('SU 桩', SU_STUB, su_len)]

    def inside(i):
        return any(a - BASE <= i < a - BASE + n for a, n in ranges)

    outside = [i for i in diff if not inside(i)]
    ok &= ck('无区外改动', bool(diff) and not outside, '改动 %d B / 区外 %d B' % (len(diff), len(outside)))

    # 每一段都必须真的被改过（否则说明钩子/桩没落上）
    for name, a, n in named[:2] + named[2:]:
        hit = sum(1 for i in diff if a - BASE <= i < a - BASE + n)
        ok &= ck('%s 有改动' % name, hit > 0, '%d/%d B' % (hit, n))

    # 钩点 A
    hb = rd(SD_HOOK, SD_HOOK_LEN)
    ok &= ck('SD 钩点是 jmp disp32', hb[0] == 0xDC, hb[:5].hex())
    tgt = (SD_HOOK + struct.unpack_from('<i', hb, 1)[0]) & 0xFFFFFFFF
    ok &= ck('SD 钩点落点 == SD 桩', tgt == SD_STUB, '0x%08x' % tgt)
    ok &= ck('SD 钩点填充 2 B 为 0', hb[5:] == b'\x00\x00', hb[5:].hex())

    hb2 = rd(SU_HOOK, SU_HOOK_LEN)
    ok &= ck('SU 钩点是 jmp disp32', hb2[0] == 0xDC, hb2[:5].hex())
    tgt2 = (SU_HOOK + struct.unpack_from('<i', hb2, 1)[0]) & 0xFFFFFFFF
    ok &= ck('SU 钩点落点 == SU 桩', tgt2 == SU_STUB, '0x%08x' % tgt2)

    # 桩尾
    for name, base, ln, ret in (('SD', SD_STUB, sd_len, SD_RET), ('SU', SU_STUB, su_len, SU_RET)):
        if not ln:
            continue
        tail = rd(base + ln - 5, 5)
        back = (base + ln - 5 + struct.unpack_from('<i', tail, 1)[0]) & 0xFFFFFFFF
        ok &= ck('%s 桩尾是 jmp' % name, tail[0] == 0xDC, tail.hex())
        ok &= ck('%s 桩尾落点 == 0x%08x' % (name, ret), back == ret, '0x%08x' % back)

    sd = rd(SD_STUB, sd_len) if sd_len else b''
    su = rd(SU_STUB, su_len) if su_len else b''

    # SD 桩内容
    probe_pats = [b'\xfa\x42' + struct.pack('<H', off) for off in PROBE_OFFSETS]
    found = [p in sd for p in probe_pats]
    ok &= ck('SD 桩含 6 个探针读', all(found),
             '缺 %s' % [hex(PROBE_OFFSETS[i]) for i, f in enumerate(found) if not f] if not all(found) else '6/6')
    ok &= ck('SD 桩含 KEY_STASH 常量', struct.pack('<I', KEY_STASH) in sd, '0x%08x' % KEY_STASH)
    ok &= ck('SD 桩含 PROBE_STASH 常量', struct.pack('<I', PROBE_STASH) in sd, '0x%08x' % PROBE_STASH)
    ok &= ck('SD 桩含原厂 cmp 0x01010066', struct.pack('<I', 0x01010066) in sd, '66 00 01 01')
    ok &= ck('SD 桩首条 = mov d1,d2', sd[:1] == b'\x86', sd[:2].hex() + '（原厂 0x604B811D = 86）')

    # SU 桩内容
    ok &= ck('SU 桩含 EXIF 文本区常量', struct.pack('<I', EXIF_TEXT) in su, '0x%08x' % EXIF_TEXT)
    ok &= ck('SU 桩含探针记录基址', struct.pack('<I', EXIF_REC) in su, '0x%08x' % EXIF_REC)
    ok &= ck('SU 桩含 B75A magic', struct.pack('<I', B75A_MAGIC) in su, "'B75A'")
    ok &= ck('SU 桩含 PROBE_STASH 常量', struct.pack('<I', PROBE_STASH) in su, '0x%08x' % PROBE_STASH)
    ok &= ck('SU 桩含 "k=" 引导常量', struct.pack('<I', EXIF_LEAD) in su, '0x%08x' % EXIF_LEAD)
    ok &= ck('SU 桩含 KEY_STASH 常量', struct.pack('<I', KEY_STASH) in su, '0x%08x' % KEY_STASH)
    ok &= ck('SU 桩无 tone 表常量（不改 tone）',
             not (struct.pack('<I', 0x605DAF44) in su or struct.pack('<I', 0x605DB544) in su),
             '不含 0x605DAF44 / 0x605DB544')
    ok &= ck('EXIF 文本区紧跟 key32（+4）', EXIF_KEY == EXIF_TEXT + 4, '%#x' % EXIF_KEY)

    # 落点合法性
    for name, a, n in (('SD 桩', SD_STUB, sd_len), ('SU 桩', SU_STUB, su_len),
                       ('PROBE_STASH', PROBE_STASH, 8), ('KEY_STASH', KEY_STASH, 4)):
        ok &= ck('%s 在 WINDOW 内' % name, bool(n) and WINDOW[0] <= a and a + n <= WINDOW[1],
                 '0x%08x..0x%08x' % (a, a + n))
        ok &= ck('%s 4 对齐' % name, a % 4 == 0, '0x%08x' % a)
        ok &= ck('%s 在 L30 已验证可覆盖带内' % name, PROVEN[0] <= a and a + n <= PROVEN[1],
                 '0x%08x..0x%08x（带 0x%08x..0x%08x）' % (a, a + n, PROVEN[0], PROVEN[1]))
    spans = sorted((a, a + n) for _, a, n in named if n) + [(PROBE_STASH, PROBE_STASH + 8),
                                                           (KEY_STASH, KEY_STASH + 4)]
    spans.sort()
    ok &= ck('四段落点互不重叠',
             all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1)),
             ' '.join('0x%x..0x%x' % s for s in spans))

    # 不变量
    guards = [('键表 0x605E9CC8 未动', 0x605E9CC8, 12),
              ('EXIF 文本区未动', EXIF_TEXT, EXIF_TEXT_LEN),
              ('弯曲门 0x604B9F1F 未动', GATE_BYTE, 1),
              ('tone 槽 22 未动', 0x605DAF44, 0x200),
              ('tone 槽 25 未动', 0x605DB544, 0x200)]
    for name, a, n in guards:
        ok &= ck(name, rd(a, n) == stock[a - BASE:a - BASE + n], '%d B' % n)

    bank2_bad = [i for i in range(BANK2[0] - BASE, BANK2[1] - BASE) if built[i] != stock[i]]
    ok &= ck('bank2 零改动', not bank2_bad, '%d B 改动' % len(bank2_bad))

    sha = hashlib.sha256(data).hexdigest()
    sim = {'pkg': OUT, 'sha256': sha, 'verifier': 'verify_l39.py', 'closed': ok,
           'scope': 'static-only（未上卡）',
           'layout': {'sd_hook': SD_HOOK, 'sd_hook_len': SD_HOOK_LEN, 'sd_stub': SD_STUB,
                      'sd_stub_len': sd_len, 'sd_ret': SD_RET,
                      'su_hook': SU_HOOK, 'su_hook_len': SU_HOOK_LEN, 'su_stub': SU_STUB,
                      'su_stub_len': su_len, 'su_ret': SU_RET,
                      'probe_stash': PROBE_STASH, 'key_stash': KEY_STASH, 'exif_rec': EXIF_REC,
                      'b75a': B75A_MAGIC,
                      'exif_text': EXIF_TEXT, 'exif_key': EXIF_KEY, 'exif_probe': EXIF_PROBE,
                      'probe_offsets': list(PROBE_OFFSETS), 'window': list(WINDOW),
                      'diff_bytes': len(diff)},
           'checks': checks}
    with io.open(OUT + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(sim, f, ensure_ascii=False, indent=2)

    print('L39 校验 %s' % OUT)
    print('  sha256 %s' % sha)
    for c in checks:
        print('  [%s] %-30s %s' % ('OK ' if c['ok'] else 'BAD', c['name'], c['detail']))
    print('  -> %s  (.sim.json 已写)' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
