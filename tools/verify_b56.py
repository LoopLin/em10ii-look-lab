#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b56.py —— B56 离线整链验证（改指针做第一级替换 + 真矩阵按档）

每个用例跑两次 libsim：
  1) 快照派发者 `0x604AA228` -> `0x604AA261`（真实路径，含 `call 0x604B9E61` 写原厂指针，
     `0x604AA25A` 的桩在这之后把 `ctx+0x1230` 换成我们那档的表）
  2) 矩阵钩子桩 `0x601ADE04` -> `0x604B6ADB`（d3 = 32 位 key）

断言
  A1  k∈1..4：`ctx+0x1230` == 槽 22–25 中该档那张表
  A2  k∉1..4：`ctx+0x1230` 保持 `0x604B9E61` 刚写进去的原厂值
  A3  桩尾回到 `0x604AA261`；`(8,a2)` / `(12,a2)` 与原厂路径逐字节一致；d0 == 0
  A4  EXIF：k∈1..4 时 `0x61894C90` == `"k=N\\0"`；否则不动
  B1  k∈1..4：矩阵指针 == 矩阵数据第 k 块；k∉1..4 == 原厂 `0x605ECECA`
  B2  两张表/块内容 == 期望；跑完 m0 与 bank2（`0x605DEF4E..` 2 KB）逐字节未变
  B3  tone 池槽 0..21 未改；槽 22–25 == 四套真 look 的 tone
  C   本次不碰的两处（`0x604B811D` SD 钩子、`0x60479A2B` SU 钩子）仍是原厂
"""
from __future__ import annotations

import hashlib
import io
import json
import struct
import sys
import time

sys.path.insert(0, 'tools')

import verify_b32_chain as V              # noqa: E402
import build_b56 as B56                   # noqa: E402

BASE = 0x60021020
SLOT = 0x200

SNAP_ENTRY, SNAP_STOP = 0x604AA228, 0x604AA261
SNAP_INSERT = B56.TRACE_INSERT
MX_STUB, MX_RET = B56.MX_STUB, B56.MX_RET
MX_INSERT = B56.MX_INSERT

TONE_ADDRS = B56.TONE_ADDRS
TONE_PTRTAB = B56.TONE_PTRTAB
MX_PTRTAB = B56.MX_PTRTAB
MX_DATA = B56.MX_DATA
MATRIX_M0 = B56.MATRIX_M0
BANK2 = B56.BANK2
POOL = B56.POOL
EXIF_TEXT = B56.EXIF_TEXT
EXIF_REGION = '0x61894000:0x1000'
EXIF_POKE = b'        '

CTX = 0x61E20000
P = 0x61E21000
SP = 0x61E0F800


def payload_of(path):
    import oly_container as oc
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def k_of(key):
    return ((key >> 24) & 3) * 8 + (key & 7)


def snap(image, b0, b1, mode):
    """从函数真入口跑到 0x604AA261（桩尾），返回 regs 与 sim。"""
    pokes = [
        (CTX + 0x08, bytes([b0 & 0xFF])),
        (CTX + 0x09, bytes([b1 & 0xFF])),
        (CTX + 0x0A, struct.pack('<H', mode & 0xFFFF)),
        (CTX + 0x3C0, (0).to_bytes(4, 'little')),
        (CTX + 0x436, b'\x00\x00'),
        (CTX + 0x1230, (0).to_bytes(4, 'little')),
        (P, bytes(0x40)),
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': CTX, 'A1': P, 'SP': SP}
    d = V.sim(image, SNAP_ENTRY, SNAP_STOP, limit=600000, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,))
    return V.regs_of(d), d


def mx_pick(image, key):
    pokes = []
    regs = {'D3': key, 'A2': CTX, 'A3': SP + 8, 'D1': 0x01010064}
    d = V.sim(image, MX_STUB, MX_RET, limit=300, pokes=pokes, regs=regs)
    return V.regs_of(d), d


def read_mem(d, addr, n):
    b = V.mem_bytes(d, addr, n)
    if b:
        return b
    if n <= 1:
        return b''
    h = n // 2
    lo = read_mem(d, addr, h)
    hi = read_mem(d, addr + h, n - h)
    if not lo or not hi:
        return b''
    return lo + hi


def span_diff(d, ref, span):
    got = read_mem(d, span[0], span[1] - span[0])
    if not got or len(got) != len(ref):
        return None
    return [i for i in range(len(ref)) if got[i] != ref[i]]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else B56.OUT
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    stock = payload_of(B56.SRC)

    def at(img, addr, n):
        return bytes(img[addr - BASE: addr - BASE + n])

    # ---- 静态前提 ----
    tones = B56.load_tones()
    mats = B56.load_matrices()
    pre_tones = all(at(image, a, SLOT) == t[3] for a, t in zip(TONE_ADDRS, tones))
    pre_pool = at(image, POOL[0], TONE_ADDRS[0] - POOL[0]) == at(stock, POOL[0], TONE_ADDRS[0] - POOL[0])
    pre_toneptr = [int.from_bytes(at(image, TONE_PTRTAB + 4 * i, 4), 'little') for i in range(4)]
    pre_mxptr = [int.from_bytes(at(image, MX_PTRTAB + 4 * i, 4), 'little') for i in range(4)]
    pre_mxdata = at(image, MX_DATA, 72) == b''.join(m[3] for m in mats)
    pre_bank2 = at(image, BANK2[0], BANK2[1] - BANK2[0]) == at(stock, BANK2[0], BANK2[1] - BANK2[0])
    pre_m0 = at(image, MATRIX_M0, 18) == at(stock, MATRIX_M0, 18)
    pre_no_sd = at(image, 0x604B811D, 7) == at(stock, 0x604B811D, 7)
    pre_no_su = at(image, 0x60479A2B, 5) == at(stock, 0x60479A2B, 5)
    pre_hook_a = at(image, SNAP_INSERT, 5) == B56.b46.jmp_bytes(SNAP_INSERT, B56.TRACE_STUB)
    pre_hook_b = at(image, MX_INSERT, 5) == B56.b46.jmp_bytes(MX_INSERT, MX_STUB)
    pre_ok = all([pre_tones, pre_pool, pre_toneptr == TONE_ADDRS,
                  pre_mxptr == [MX_DATA + i * 18 for i in range(4)], pre_mxdata,
                  pre_bank2, pre_m0, pre_no_sd, pre_no_su, pre_hook_a, pre_hook_b])
    print('静态前提：tone 四槽=%s 池槽0..21 未改=%s tone 指针表=%s 矩阵指针表=%s 矩阵数据=%s'
          % (pre_tones, pre_pool, pre_toneptr == TONE_ADDRS,
             pre_mxptr == [MX_DATA + i * 18 for i in range(4)], pre_mxdata))
    print('          bank2 未改=%s m0 未改=%s 未碰 SD 钩子=%s 未碰 SU 钩子=%s 两钩子=%s/%s'
          % (pre_bank2, pre_m0, pre_no_sd, pre_no_su, pre_hook_a, pre_hook_b))
    print('          tone 指针表 = %s' % ['0x%X' % v for v in pre_toneptr])

    allok = pre_ok
    details = []
    for b0, b1, mode in ([(0x00, 0x01, m) for m in (1, 2, 3, 4)]
                         + [(0x00, 0x01, 0), (0x00, 0x01, 5), (0x00, 0x01, 7)]):
        key = (b0 << 24) | (b1 << 16) | mode
        k_exp = k_of(key)
        in_range = 1 <= k_exp <= 4

        sregs, sd = snap(image, b0, b1, mode)
        stregs, std = snap(stock, b0, b1, mode)
        got_ctx1230 = int.from_bytes(read_mem(sd, CTX + 0x1230, 4) or b'\0' * 4, 'little')
        stock_ctx1230 = int.from_bytes(read_mem(std, CTX + 0x1230, 4) or b'\0' * 4, 'little')
        exp_ctx = TONE_ADDRS[k_exp - 1] if in_range else stock_ctx1230

        p8 = read_mem(sd, P + 8, 4)
        p12 = read_mem(sd, P + 12, 4)
        sp8 = read_mem(std, P + 8, 4)
        sp12 = read_mem(std, P + 12, 4)

        mregs, md = mx_pick(image, key)
        a0 = mregs['A0']
        exp_a0 = (MX_DATA + (k_exp - 1) * MX_STRIDE) if in_range else MATRIX_M0
        got_mx = read_mem(md, a0, MX_STRIDE) or b''
        exp_mx = mats[k_exp - 1][3] if in_range else at(stock, MATRIX_M0, 18)

        got_text = read_mem(sd, EXIF_TEXT, 4) or b''
        exp_text = (b'k=%d\x00' % k_exp) if in_range else EXIF_POKE[:4]

        a1 = (sregs['PC'] == SNAP_STOP and got_ctx1230 == exp_ctx)
        a2 = (got_ctx1230 == stock_ctx1230) if not in_range else True
        a3 = (p8 == sp8 and p12 == sp12 and sregs['D0'] == 0)
        a4 = (got_text == exp_text)
        b1ok = (a0 == exp_a0) and (got_mx == exp_mx)
        m0_ok = all((read_mem(d, MATRIX_M0, 18) or b'') == at(image, MATRIX_M0, 18) for d in (sd, md))
        bank2_ref = at(image, BANK2[0], BANK2[1] - BANK2[0])
        bank2_ok = True
        for d in (sd, md):
            bd = span_diff(d, bank2_ref, BANK2)
            if bd is None or bd:
                bank2_ok = False
                break

        ok = all([pre_ok, a1, a2, a3, a4, b1ok, m0_ok, bank2_ok])
        allok &= ok
        print('== b0=%02X b1=%02X mode=0x%04X  key=0x%08X  k=%d（在 1..4=%s）'
              % (b0, b1, mode, key, k_exp, in_range))
        print('   snap=%s' % V.describe(sd))
        print('   ctx+0x1230=0x%08X（期望 0x%08X，原厂 0x%08X）  P+8=%s P+12=%s（原厂 %s / %s）'
              % (got_ctx1230, exp_ctx, stock_ctx1230, p8.hex(), p12.hex(), sp8.hex(), sp12.hex()))
        print('   mtx : a0=0x%08X（期望 0x%08X） %s'
              % (a0, exp_a0, list(struct.unpack('<9h', got_mx)) if len(got_mx) == 18 else 'n/a'))
        print('   exif: %r（期望 %r）' % (got_text, exp_text))
        for name, v in (('A1 ctx+0x1230 == 该档 tone 槽', a1),
                        ('A2 越界时保持原厂指针', a2),
                        ('A3 尾回 0x604AA261 且 (8,a2)/(12,a2) 同原厂、d0==0', a3),
                        ('A4 EXIF k=N（仅 1..4）', a4),
                        ('B1 矩阵指针与块内容', b1ok),
                        ('B2 m0 与 bank2 未变', m0_ok and bank2_ok),
                        ('B3 tone 槽 22–25 == 四套真 look', pre_tones),
                        ('C  未碰 SD/SU 钩子', pre_no_sd and pre_no_su)):
            print('     %s  %s' % ('PASS' if v else 'FAIL', name))
        details.append({'b0': b0, 'b1': b1, 'mode': mode, 'key': '0x%08X' % key, 'k': k_exp,
                        'ctx1230': '0x%08X' % got_ctx1230, 'stock_ctx1230': '0x%08X' % stock_ctx1230,
                        'mx': '0x%08X' % a0, 'exif': got_text.hex(), 'ok': bool(ok)})

    rec = {'pkg': path, 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b56.py', 'cases': len(details), 'closed': bool(allok),
           'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'checks': ['A1 ctx+0x1230 == 该档 tone 槽（k=1..4）',
                      'A2 越界时 ctx+0x1230 保持原厂', 'A3 尾回 0x604AA261、(8,a2)/(12,a2) 同原厂、d0==0',
                      'A4 EXIF k=N（仅 1..4）', 'B1 矩阵指针与块内容按 k',
                      'B2 m0 与 bank2 未变', 'B3 tone 槽 22–25 == 四套真 look',
                      'C  未碰 SD/SU 钩子'],
           'static': {'tones': pre_tones, 'pool_0_21_untouched': pre_pool,
                      'tone_ptrtab': ['0x%X' % v for v in pre_toneptr],
                      'matrix_ptrtab': ['0x%X' % v for v in pre_mxptr],
                      'matrix_data': pre_mxdata, 'bank2_untouched': pre_bank2,
                      'm0_stock': pre_m0, 'sd_hook_stock': pre_no_sd, 'su_hook_stock': pre_no_su,
                      'hook_a': pre_hook_a, 'hook_b': pre_hook_b},
           'details': details}
    with io.open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print('\n-> ' + ('全部闭合' if allok else '有不闭合的用例'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, allok))
    sys.exit(0 if allok else 1)


MX_STRIDE = 18

if __name__ == '__main__':
    main()
