#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b37_chain.py —— B37 / B38 的端到端离线验证（libsim）。

跑两段真实代码：

  段 A（派发）  PC = 0x604AA228，ctx 里摆好 (b0, b1, mode16)
      0x604AA228 -> call 0x604B8098（派发器）-> 0x604AA23E -> 0x604AA242（SD 钩子）
      桩跑完 -> 0x604AA248 原厂续跑

  段 B（上传）  PC = 0x60479960，A0 = dest，A1 = O
      入口 5 B 被 SU 钩子吃掉 -> 桩改 *(S+0x1230) -> 重放 -> 0x60479965
      -> 0x60479A3A 拷贝循环 -> 0x60479A77 汇合点

判据（每个用例 7 条）::

  C1 段 A 真的落进 SD 桩（PC == 0x605DED44）
  C2 SD 桩写出的 stash == 设计 k
  C3 段 A 回到 0x604AA248（原厂续跑点）
  C4 段 B 真的落进 SU 桩（PC == 0x605DEDD0）
  C5 过了 SU 桩之后 *(S+0x1230) == 期望表地址（k∈1..4 → 槽 21+k；否则原厂表）
  C6 上传器拷到 dest 的 256 项 == 期望表的内容（逐项比，值来自包内字节）
  C7 B38：m0 == 第 k 档矩阵；B37：m0 一个字节都没动（== 原厂）

用法::

    python tools/verify_b37_chain.py build/B37_looks_chain.BIN
    python tools/verify_b37_chain.py build/B38_looks_mx_chain.BIN --json .tmp/b38_sim.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import oly_container as oc  # noqa: E402

BASE = 0x60021020
IMAGE_END = BASE + 0x1460000
ENGINE = os.path.join(ROOT, '.tmp', 'olya', 'toolchains', 'mn103-gnu-build',
                      '.libs', 'penf-mn103.exe')

REGS = ['D0', 'D1', 'D2', 'D3', 'A0', 'A1', 'A2', 'A3', 'SP', 'PC', 'PSW', 'MDR',
        'LIR', 'LAR', 'FAULT', 'Z', 'XY', 'R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6',
        'R7', 'RAMB', 'RAR', 'U', 'VC', 'EXB', 'CRT', 'UNK']

TONE = 0x605D8344
SLOT = 0x200
SD_RAM = 0x605DED44
SU_RAM = 0x605DEDD0
STASH = 0x605DEDC0
MATRIX_RAM = 0x605ECECA
MATRIX_STOCK_END = MATRIX_RAM + 18
MX = 0x605DEE60
MX_STRIDE = 0x12

DISPATCH_ENTRY = 0x604AA228
SD_RET = 0x604AA248
UPLOAD = 0x60479960
AFTER_SU = 0x60479A27          # 过了 SU 钩子、进拷贝循环之前的原厂点
UPLOAD_MERGE = 0x60479A77      # 两段拷贝之后的汇合点

DEST = 0x9E00A400
O = 0x61E10000
S = 0x61E18000
T2 = 0x61E19000
CTX = 0x61E20000
SCRATCH = 0x61E28000
SP = 0x61E0F800
STOCK_LUT_SLOT = 21
STOCK_LUT = TONE + STOCK_LUT_SLOT * SLOT

# 段布局：0x605EC000..0x605ED000 这一段给 rwx（矩阵 m0 = 0x605ECECA 要在运行期被写），
# 其余代码段保持 r-x，桩所在的 0x605DE000 页本来就是可写 RAM。
REGIONS = [
    (BASE, 0x605DE000 - BASE, 5),
    (0x605DE000, 0x1000, 7),
    (0x605DF000, 0x605EC000 - 0x605DF000, 5),
    (0x605EC000, 0x1000, 7),
    (0x605ED000, IMAGE_END - 0x605ED000, 5),
]
HEAP = (0x61E00000, 0x40000, 3)
UPLOAD_HEAP = ('0x9e00a000:0x3000', '0x8fe00000:0x1000', '0x8ff00000:0x8000',
               '0xa1c21000:0x1000', '0xa1c2a000:0x1000')


def p32(a, v):
    return (a, int(v).to_bytes(4, 'little'))


def sim(image, pc, stop, limit=400000, pokes=(), regs=None, extra_rwz=()):
    r = {n: 0 for n in REGS}
    r['PC'] = pc
    r['SP'] = SP
    r.update(regs or {})
    regions = list(REGIONS)
    for spec in extra_rwz:
        a, _, ln = spec.partition(':')
        regions.append((int(a, 0), int(ln, 0), 3))
    regions.append(HEAP)
    regions.sort()
    payload = bytearray(b'PFMN002\0' + struct.pack('<6I', limit, stop, 0,
                                                   len(regions), 0, 0))
    payload.extend(struct.pack('<32I', *[r[n] for n in REGS]))
    for start, length, mask in regions:
        data = bytearray(length) if mask == 3 else \
            bytearray(image[start - BASE:start - BASE + length])
        for pa, pb in pokes:
            if start <= pa and pa + len(pb) <= start + len(data):
                data[pa - start:pa - start + len(pb)] = pb
        payload.extend(struct.pack('<5I', start, len(data), mask, 1, 0xffffffff))
        payload.extend(bytes(data))
    p = subprocess.run([ENGINE], input=bytes(payload), capture_output=True,
                       timeout=900)
    if p.returncode:
        raise SystemExit('libsim rc=%d %s'
                         % (p.returncode, p.stderr.decode(errors='replace')[:900]))
    return json.loads(p.stdout)


def regs_of(d):
    return dict(zip(REGS, d['registers']))


def mem_bytes(d, addr, n):
    for mr in d['memory']:
        a = mr['address']
        if a <= addr and addr + n <= a + len(mr['hex']) // 2:
            b = bytes.fromhex(mr['hex'])
            return b[addr - a:addr - a + n]
    return b''


def describe(d):
    s = 'status=%s PC=%#x' % (d.get('status'), regs_of(d).get('PC', -1))
    if d.get('fault'):
        s += ' fault=%s' % (d['fault'],)
    return s


def table_of(image, addr):
    o = addr - BASE
    return bytes(image[o:o + SLOT])


def tone_of_dest(dest):
    return [struct.unpack_from('<H', dest, 4 * i)[0] for i in range(256)]


def k_of(b0, b1, mode16):
    return ((b0 & 3) << 3) | (mode16 & 7)


def want_slot(k):
    return ('look', 21 + k) if 1 <= k <= 4 else ('stock', STOCK_LUT_SLOT)


def dispatch(image, b0, b1, mode16, stop):
    key = (b0 << 24) | (b1 << 16) | mode16
    pokes = [
        p32(CTX + 0x3C0, 0),
        (CTX + 0x436, b'\x00\x00'),
        (CTX + 8, bytes([b0 & 0xFF])),
        (CTX + 9, bytes([b1 & 0xFF])),
        (CTX + 0x0A, struct.pack('<H', mode16)),
        (CTX + 0x42F, b'\x00'),          # 走默认分支（真机 4 档 picture mode 就走这里）
    ]
    regs = {'A0': CTX, 'A1': SCRATCH, 'D0': 0, 'D1': 0}
    return key, sim(image, DISPATCH_ENTRY, stop, pokes=pokes, regs=regs)


def upload(image, stash_k, stop, seg2=None):
    pokes = [
        p32(O + 0x0C, S),
        p32(O + 0x64, 4),
        p32(O + 0x34, 0),
        (O + 0xBD, b'\x01'),
        p32(S + 0x41C, 0),
        p32(S + 0x420, T2 if seg2 is None else seg2),
        p32(S + 0x1230, STOCK_LUT),
        (STASH, bytes([stash_k & 0xFF])),
    ]
    regs = {'A0': DEST, 'A1': O}
    return sim(image, UPLOAD, stop, pokes=pokes, regs=regs, extra_rwz=UPLOAD_HEAP)


def check(name, cases):
    return all(cases) if cases else False


def run_case(image, label, b0, b1, mode16, has_matrix, seg2=None):
    k = k_of(b0, b1, mode16)
    which, slot = want_slot(k)
    want_addr = TONE + slot * SLOT
    want_tab = table_of(image, want_addr)

    out = {'label': label, 'b0': b0, 'b1': b1, 'mode': mode16, 'k': k,
           'path': which, 'slot': slot, 'want_addr': want_addr, 'checks': {}}

    key, d1 = dispatch(image, b0, b1, mode16, SD_RAM)
    out['key'] = key
    out['dispatch_enter'] = describe(d1)
    out['checks']['C1'] = d1.get('status') == 'returned' and \
        regs_of(d1).get('PC') == SD_RAM

    _, d2 = dispatch(image, b0, b1, mode16, SD_RET)
    out['dispatch_return'] = describe(d2)
    stash = mem_bytes(d2, STASH, 1)
    out['stash'] = stash[0] if stash else None
    out['checks']['C2'] = out['stash'] == k
    out['checks']['C3'] = d2.get('status') == 'returned' and \
        regs_of(d2).get('PC') == SD_RET

    stash_k = out['stash'] if out['stash'] is not None else 0xFF
    d3 = upload(image, stash_k, SU_RAM, seg2=seg2)
    out['upload_enter'] = describe(d3)
    out['checks']['C4'] = d3.get('status') == 'returned' and \
        regs_of(d3).get('PC') == SU_RAM

    # C5 的观测点：融合路径在 0x60479A27（进拷贝前），只拷 tone 的那条路（*(S+0x420)==0）
    # 根本不经 0x60479A27（0x60479A0A beq 0x60479A59），所以改在汇合点 0x60479A77 观察。
    stop_c5 = AFTER_SU if seg2 is None else UPLOAD_MERGE
    d4 = upload(image, stash_k, stop_c5, seg2=seg2)
    got_ptr = struct.unpack('<I', mem_bytes(d4, S + 0x1230, 4))[0] \
        if mem_bytes(d4, S + 0x1230, 4) else None
    out['ptr'] = got_ptr
    out['ptr_at'] = stop_c5
    out['after_su'] = describe(d4)
    out['checks']['C5'] = got_ptr == want_addr and d4.get('status') == 'returned'

    d5 = upload(image, stash_k, UPLOAD_MERGE, seg2=seg2)
    dest = mem_bytes(d5, DEST, 0x400)
    got_tab = tone_of_dest(dest)
    want_vals = [struct.unpack_from('<H', want_tab, 2 * i)[0] for i in range(256)]
    out['dest'] = describe(d5)
    out['tone_head'] = got_tab[:6]
    out['checks']['C6'] = got_tab == want_vals and d5.get('status') == 'returned'

    if has_matrix:
        want_mx = bytes(image[MX + MX_STRIDE * (k - 1) - BASE:
                              MX + MX_STRIDE * (k - 1) - BASE + MX_STRIDE]) \
            if 1 <= k <= 4 else bytes(image[MATRIX_RAM - BASE:
                                             MATRIX_RAM - BASE + MX_STRIDE])
        got_mx = mem_bytes(d5, MATRIX_RAM, MX_STRIDE)
        out['m0'] = list(struct.unpack('<9h', got_mx)) if len(got_mx) == 18 else None
        out['checks']['C7'] = got_mx == want_mx
    else:
        got_mx = mem_bytes(d5, MATRIX_RAM, MX_STRIDE)
        stock_mx = bytes(image[MATRIX_RAM - BASE:MATRIX_RAM - BASE + MX_STRIDE])
        out['m0'] = list(struct.unpack('<9h', got_mx)) if len(got_mx) == 18 else None
        out['checks']['C7'] = got_mx == stock_mx

    out['pass'] = all(out['checks'].values())
    return out


CASES = [
    ('k=0 回落（原厂路径）', 0x04, 0x01, 0x00, None),
    ('k=1 reala_ace', 0x04, 0x01, 0x01, None),
    ('k=2 astia', 0x04, 0x01, 0x02, None),
    ('k=3 eterna', 0x04, 0x01, 0x03, None),
    ('k=4 bleach_bypass', 0x04, 0x01, 0x04, None),
    ('k=1 且 *(S+0x420)==0（只拷 tone 的那条路）', 0x04, 0x01, 0x01, 0),
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('pack')
    ap.add_argument('--json', default=None)
    args = ap.parse_args(argv)

    with open(args.pack, 'rb') as f:
        blocks = oc.parse(f.read())
    image = blocks[0].payload
    has_matrix = 'B38' in os.path.basename(args.pack).upper()
    print('pack   : %s' % args.pack)
    print('payload: %d B  base=%#x  variant=%s'
          % (len(image), BASE, 'B38 (带矩阵)' if has_matrix else 'B37 (只 tone)'))
    if len(image) != IMAGE_END - BASE:
        print('  注意：payload 比 IMAGE_END 短 %d B（CODE_B 区段短一截，无害）'
              % (IMAGE_END - BASE - len(image)))

    results = []
    for label, b0, b1, mode, seg2 in CASES:
        res = run_case(image, label, b0, b1, mode, has_matrix, seg2=seg2)
        results.append(res)
        print('\n--- %s ---' % label)
        print('  b0=%#04x b1=%#04x mode=%#06x -> key=%#010x k=%d  走 %s 槽 %d @%#x'
              % (res['b0'], res['b1'], res['mode'], res['key'], res['k'],
                 res['path'], res['slot'], res['want_addr']))
        print('  段 A 进桩  : %s' % res['dispatch_enter'])
        print('  段 A 回续跑: %s   stash=%s（期望 %d）'
              % (res['dispatch_return'], res['stash'], res['k']))
        print('  段 B 进桩  : %s' % res['upload_enter'])
        print('  过桩后指针 : %s（期望 %#x）' % (
            ('%#x' % res['ptr']) if res['ptr'] else 'None', res['want_addr']))
        print('  段 B 汇合  : %s' % res['dest'])
        print('  dest 前 6 项: %s' % res['tone_head'])
        print('  m0        : %s' % res['m0'])
        print('  %s' % ' '.join('%s=%s' % (k, 'OK' if v else 'FAIL')
                                for k, v in sorted(res['checks'].items())))

    print('\n================ 汇总 ================')
    hdr = 'case'.ljust(44) + 'k  ' + 'C1 C2 C3 C4 C5 C6 C7  verdict'
    print(hdr)
    all_ok = True
    for res in results:
        row = res['label'].ljust(44) + ('%-3d' % res['k'])
        row += ' '.join(' Y' if res['checks'][c] else ' N'
                        for c in ('C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7'))
        row += '   ' + ('PASS' if res['pass'] else 'FAIL')
        print(row)
        all_ok &= res['pass']

    data_closed = all(r['checks']['C2'] and r['checks']['C5'] and r['checks']['C6']
                      and r['checks']['C7'] for r in results)
    ctl_closed = all(r['checks']['C1'] and r['checks']['C3'] and r['checks']['C4']
                     for r in results)
    print('\n数据链（k -> stash -> 选表 -> 上传器拷贝 -> m0）闭合: %s' % data_closed)
    print('控制流（派发 -> SD 桩 -> 续跑；入口 -> SU 桩 -> 重放 -> 汇合）闭合: %s'
          % ctl_closed)
    print('VERDICT %s' % ('PASS' if all_ok else 'FAIL'))

    log = {'pack': args.pack, 'variant': 'b38' if has_matrix else 'b37',
           'results': results, 'data_closed': data_closed,
           'ctl_closed': ctl_closed, 'pass': all_ok}
    out = args.json or ('.tmp/%s_sim.json' % ('b38' if has_matrix else 'b37'))
    io.open(out, 'w', encoding='utf-8', newline='\n').write(
        json.dumps(log, ensure_ascii=False, indent=2) + '\n')
    print('（结构化结果另存 %s）' % out)
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
