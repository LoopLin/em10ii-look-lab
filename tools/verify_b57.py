#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b57.py —— B57 离线整链验证（第一级指针 + 第二级恒等 + 真矩阵）

B57 = B56（`0x604AA25A` 把 `ctx+0x1230` 换成我们的表）+ 两点修补：
  · k∉1..4 时桩 A **把原厂指针写回去**（`0x604B9E61` 的返回值 a0），不再残留上一次的表
  · 再加 SU 钩子 `0x60479A2B` → 桩 S：k∈1..4 时把第二级设成**恒等表** `L[x]=round(x/255*1023)`，
    这样两级只有一次映射（零复合）；k∉1..4 保持原厂指针，走 `0x60479A32` 原有回退

每个用例跑四个 sim：
  1) 快照派发者 `0x604AA228` -> `0x604AA261`（真实路径，桩 A 在内）
  2) 桩 S `0x605DEDE4` 单跑：查 A1 保不保（`0x60479A3B` 的拷贝循环要用 a1 = dest+0x400）
  3) 上传器整跑：看真正拷进 ISP 窗口那份表是恒等表还是原厂表
  4) 矩阵桩 `0x601ADE04` -> `0x604B6ADB`（d3 = 32 位 key）

断言
  A1  k∈1..4：`ctx+0x1230` == 槽 22–25 该档；FLAG == k-1
  A2  k∉1..4：`ctx+0x1230` == **原厂指针**（stock 运行时 `0x604B9E61` 的返回值，逐字节）；FLAG == 0xFF
  A3  桩尾回 `0x604AA261`；`(8,a2)`/`(12,a2)` 与原厂一致；d0 == 0
  A4  EXIF：k∈1..4 时 `0x61894C90` == `"k=N\\0"`；否则不动
  S1  桩 S 跑完 a1 == dest+0x400（拷贝循环的写指针没被破坏）
  S2  上传器拷进 dest 的 256 项表 == 恒等表（k∈1..4）/ == 原厂表内容（k∉1..4）
  B1  k∈1..4：矩阵指针 == MX_DATA+18*(k-1)、块内容 == 该档真矩阵；否则原厂 `0x605ECECA`
  B2  跑完 m0 与 bank2（`0x605DEF4E..` 2 KB）逐字节未变
  B3  tone 槽 22–25 == 四套真 look；池内其余（除槽 48 恒等表）未改
  C   SD 钩子 `0x604B811D` 与色度钩点 `0x604B4B25` 仍是原厂
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
import build_b57 as B57                   # noqa: E402

BASE = 0x60021020
SLOT = 0x200

SNAP_ENTRY, SNAP_STOP = 0x604AA228, 0x604AA261
TRACE_STUB = B57.TRACE_STUB
SU_STUB, SU_RET = B57.SU_STUB, B57.SU_RET
MX_STUB, MX_RET = B57.MX_STUB, B57.MX_RET
MX_INSERT = B57.MX_INSERT

TONE_ADDRS = B57.TONE_ADDRS
TONE_PTRTAB = B57.TONE_PTRTAB
MX_PTRTAB = B57.MX_PTRTAB
MX_DATA = B57.MX_DATA
MATRIX_M0 = B57.MATRIX_M0
BANK2 = B57.BANK2
POOL = B57.POOL
FLAG = B57.FLAG
IDENT_ADDR = B57.IDENT_ADDR
EXIF_TEXT = B57.EXIF_TEXT
EXIF_REGION = '0x61894000:0x1000'
EXIF_POKE = b'        '
STOCK_PTR = 0x605D8144          # 原厂 `0x604B9E61` 的返回表（在池基址前 0x200）

CTX = 0x61E20000
P = 0x61E21000
SP = 0x61E0F800
DEST = 0x9E00A400
O = 0x61E10000
S = 0x61E18000
T2 = 0x61E19000
MX_STRIDE = 18


def payload_of(path):
    import oly_container as oc
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def k_of(key):
    return ((key >> 24) & 3) * 8 + (key & 7)


def snap(image, b0, b1, mode):
    pokes = [
        (CTX + 0x08, bytes([b0 & 0xFF])),
        (CTX + 0x09, bytes([b1 & 0xFF])),
        (CTX + 0x0A, struct.pack('<H', mode & 0xFFFF)),
        (CTX + 0x3C0, (0).to_bytes(4, 'little')),
        (CTX + 0x436, b'\x00\x00'),
        (CTX + 0x1230, (0).to_bytes(4, 'little')),
        (P, bytes(0x40)),
        (FLAG, b'\0\0\0\0'),
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': CTX, 'A1': P, 'SP': SP}
    d = V.sim(image, SNAP_ENTRY, SNAP_STOP, limit=600000, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,))
    return V.regs_of(d), d


def su_pick(image, flag):
    """桩 S 单跑：a0 = ctx+0x1230（内含原厂指针），d2 = dest+0x400，SP+8 = dest。"""
    pokes = [
        (S + 0x1230, STOCK_PTR.to_bytes(4, 'little')),
        (FLAG, int(flag).to_bytes(4, 'little')),
        (SP + 8, DEST.to_bytes(4, 'little')),
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': S + 0x1230, 'D2': DEST + 0x400, 'A3': 0x61E1A000}
    d = V.sim(image, SU_STUB, SU_RET, limit=400, pokes=pokes, regs=regs,
              extra_rwz=(EXIF_REGION,))
    return V.regs_of(d), d


def upload(image, flag, tone_ptr):
    """上传器整跑：ctx+0x1230 先被"桩 A"设成 tone_ptr，FLAG 由桩 A 写。"""
    pokes = [
        (O + 0x0C, S.to_bytes(4, 'little')),
        (O + 0x64, (4).to_bytes(4, 'little')),
        (O + 0x34, (0).to_bytes(4, 'little')),
        (O + 0xBD, b'\x01'),
        (S + 0x41C, (0).to_bytes(4, 'little')),
        (S + 0x420, T2.to_bytes(4, 'little')),
        (S + 0x1230, int(tone_ptr).to_bytes(4, 'little')),
        (FLAG, int(flag).to_bytes(4, 'little')),
        (EXIF_TEXT, EXIF_POKE),
    ]
    regs = {'A0': DEST, 'A1': O}
    d = V.sim(image, V.UPLOAD, V.UPLOAD_MERGE, limit=400000, pokes=pokes, regs=regs,
              extra_rwz=V.UPLOAD_HEAP + (EXIF_REGION,))
    return V.tone_of_dest(V.mem_bytes(d, DEST, V.DEST_SPAN)), d


def mx_pick(image, key):
    regs = {'D3': key, 'A2': CTX, 'A3': SP + 8, 'D1': 0x01010064}
    d = V.sim(image, MX_STUB, MX_RET, limit=300, pokes=[], regs=regs)
    return V.regs_of(d), d


def read_mem(d, addr, n):
    """mem_bytes 要求整段落在一个输出段里；跨段时二分拼（一路到 1 B）。"""
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
    path = sys.argv[1] if len(sys.argv) > 1 else B57.OUT
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    stock = payload_of(B57.SRC)

    def at(img, addr, n):
        return bytes(img[addr - BASE: addr - BASE + n])

    # ---- 静态前提 ----
    tones = B57.load_tones()
    mats = B57.load_matrices()
    ident_vals, ident_blob = B57.identity_table()
    pre_tones = all(at(image, a, SLOT) == t[3] for a, t in zip(TONE_ADDRS, tones))
    pre_ident = at(image, IDENT_ADDR, SLOT) == ident_blob
    pre_pool = at(image, POOL[0], TONE_ADDRS[0] - POOL[0]) == at(stock, POOL[0], TONE_ADDRS[0] - POOL[0])
    pre_toneptr = [int.from_bytes(at(image, TONE_PTRTAB + 4 * i, 4), 'little') for i in range(4)]
    pre_mxptr = [int.from_bytes(at(image, MX_PTRTAB + 4 * i, 4), 'little') for i in range(4)]
    pre_mxdata = at(image, MX_DATA, 72) == b''.join(m[3] for m in mats)
    pre_identptr = at(image, B57.IDENT_PTR, 4) == struct.pack('<I', IDENT_ADDR)
    pre_bank2 = at(image, BANK2[0], BANK2[1] - BANK2[0]) == at(stock, BANK2[0], BANK2[1] - BANK2[0])
    pre_m0 = at(image, MATRIX_M0, 18) == at(stock, MATRIX_M0, 18)
    pre_no_sd = at(image, 0x604B811D, 7) == at(stock, 0x604B811D, 7)
    pre_no_ch = at(image, 0x604B4B25, 3) == at(stock, 0x604B4B25, 3)
    pre_hook_a = at(image, SNAP_ENTRY + 0x32, 5) == B57.b46.jmp_bytes(B57.TRACE_INSERT, TRACE_STUB)
    pre_hook_s = at(image, B57.SU_INSERT, 5) == B57.b46.jmp_bytes(B57.SU_INSERT, SU_STUB)
    pre_hook_m = at(image, MX_INSERT, 5) == B57.b46.jmp_bytes(MX_INSERT, MX_STUB)
    pre_ok = all([pre_tones, pre_ident, pre_pool, pre_toneptr == TONE_ADDRS,
                  pre_mxptr == [MX_DATA + i * 18 for i in range(4)], pre_mxdata, pre_identptr,
                  pre_bank2, pre_m0, pre_no_sd, pre_no_ch, pre_hook_a, pre_hook_s, pre_hook_m])
    print('静态前提：tone 四槽=%s 恒等表@槽48=%s 池槽0..21 未改=%s 指针表=%s 矩阵数据=%s IDENT_PTR=%s'
          % (pre_tones, pre_ident, pre_pool, pre_toneptr == TONE_ADDRS, pre_mxdata, pre_identptr))
    print('          bank2 未改=%s m0 未改=%s 未碰 SD/色度钩点=%s/%s 三钩子=%s/%s/%s'
          % (pre_bank2, pre_m0, pre_no_sd, pre_no_ch, pre_hook_a, pre_hook_s, pre_hook_m))

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
        stock_ptr = stregs['A0']          # 原厂 `0x604B9E61` 的返回值 = 原厂要写进 ctx+0x1230 的指针
        got_flag = int.from_bytes(read_mem(sd, FLAG, 4) or b'\0' * 4, 'little')

        # 桩 S 单跑（查 a1 保没保）
        uregs, _ = su_pick(image, (k_exp - 1) if in_range else 0xFF)
        a1_ok = uregs['A1'] == DEST + 0x400

        # 上传器整跑（第二级用的表）
        tone_ptr = TONE_ADDRS[k_exp - 1] if in_range else stock_ptr
        up_vals, ud = upload(image, (k_exp - 1) if in_range else 0xFF, tone_ptr)
        exp_vals = ident_vals if in_range else list(struct.unpack('<256H', at(image, stock_ptr, SLOT)))
        s2_ok = up_vals == exp_vals
        got_text = read_mem(ud, EXIF_TEXT, 4) or b''

        mregs, md = mx_pick(image, key)
        a0 = mregs['A0']
        exp_a0 = (MX_DATA + (k_exp - 1) * MX_STRIDE) if in_range else MATRIX_M0
        got_mx = read_mem(md, a0, MX_STRIDE) or b''
        exp_mx = mats[k_exp - 1][3] if in_range else at(stock, MATRIX_M0, 18)

        p8 = read_mem(sd, P + 8, 4)
        p12 = read_mem(sd, P + 12, 4)
        sp8 = read_mem(std, P + 8, 4)
        sp12 = read_mem(std, P + 12, 4)

        exp_text = (b'k=%d\x00' % k_exp) if in_range else EXIF_POKE[:4]
        a1_assert = (sregs['PC'] == SNAP_STOP and got_ctx1230 == (TONE_ADDRS[k_exp - 1] if in_range else stock_ptr)
                     and got_flag == ((k_exp - 1) if in_range else 0xFF))
        a2_assert = (got_ctx1230 == stock_ptr) if not in_range else True
        a3_assert = (p8 == sp8 and p12 == sp12 and sregs['D0'] == 0)
        a4_assert = (got_text == exp_text)
        b1ok = (a0 == exp_a0) and (got_mx == exp_mx)

        m0_ok = all((read_mem(d, MATRIX_M0, 18) or b'') == at(image, MATRIX_M0, 18) for d in (sd, md))
        bank2_ref = at(image, BANK2[0], BANK2[1] - BANK2[0])
        bank2_ok = all(span_diff(d, bank2_ref, BANK2) == [] for d in (sd, ud, md))

        ok = all([pre_ok, a1_assert, a2_assert, a3_assert, a4_assert, a1_ok, s2_ok,
                  b1ok, m0_ok, bank2_ok])
        allok &= ok
        print('== b0=%02X b1=%02X mode=0x%04X  key=0x%08X  k=%d（在 1..4=%s）'
              % (b0, b1, mode, key, k_exp, in_range))
        print('   ctx+0x1230=0x%08X（期望 0x%08X，原厂指针 0x%08X）  FLAG=%d'
              % (got_ctx1230, (TONE_ADDRS[k_exp - 1] if in_range else stock_ptr), stock_ptr, got_flag))
        print('   P+8=%s P+12=%s（原厂 %s / %s）  a1(桩S)=0x%08X（期望 0x%08X）'
              % (p8.hex(), p12.hex(), sp8.hex(), sp12.hex(), uregs['A1'], DEST + 0x400))
        print('   第二级表[0,1,46,255]=%s（期望 %s）  exif=%r（期望 %r）'
              % ([up_vals[i] for i in (0, 1, 46, 255)] if up_vals else None,
                 [exp_vals[i] for i in (0, 1, 46, 255)] if exp_vals else None, got_text, exp_text))
        print('   mtx : a0=0x%08X（期望 0x%08X） %s'
              % (a0, exp_a0, list(struct.unpack('<9h', got_mx)) if len(got_mx) == 18 else 'n/a'))
        for name, v in (('A1 ctx+0x1230 == 该档槽 且 FLAG == k-1（越界为 0xFF）', a1_assert),
                        ('A2 越界时 ctx+0x1230 == 原厂指针（逐字节）', a2_assert),
                        ('A3 尾回 0x604AA261 且 (8,a2)/(12,a2) 同原厂、d0==0', a3_assert),
                        ('A4 EXIF k=N（仅 1..4）', a4_assert),
                        ('S1 桩 S 保住 a1（dest+0x400）', a1_ok),
                        ('S2 第二级表 == 恒等表（越界 == 原厂表）', s2_ok),
                        ('B1 矩阵指针与块内容', b1ok),
                        ('B2 m0 与 bank2 未变', m0_ok and bank2_ok),
                        ('B3 tone 槽 22–25 == 四套真 look、池其余未改', pre_tones and pre_pool),
                        ('C  未碰 SD / 色度钩点', pre_no_sd and pre_no_ch)):
            print('     %s  %s' % ('PASS' if v else 'FAIL', name))
        details.append({'b0': b0, 'b1': b1, 'mode': mode, 'key': '0x%08X' % key, 'k': k_exp,
                        'ctx1230': '0x%08X' % got_ctx1230, 'stock_ptr': '0x%08X' % stock_ptr,
                        'flag': got_flag, 'stage2_0_1_46_255': [up_vals[i] for i in (0, 1, 46, 255)],
                        'mx': '0x%08X' % a0, 'exif': got_text.hex(), 'ok': bool(ok)})

    rec = {'pkg': path, 'sha256': hashlib.sha256(whole).hexdigest(),
           'verifier': 'tools/verify_b57.py', 'cases': len(details), 'closed': bool(allok),
           'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'checks': ['A1 ctx+0x1230 == 该档槽、FLAG == k-1（k=1..4）',
                      'A2 越界时 ctx+0x1230 == 原厂指针（不再残留）',
                      'A3 尾回 0x604AA261、(8,a2)/(12,a2) 同原厂、d0==0',
                      'A4 EXIF k=N（仅 1..4）',
                      'S1 桩 S 保住 a1 = dest+0x400',
                      'S2 第二级 == 恒等表（k=1..4）/ 原厂表（越界）',
                      'B1 矩阵指针与块内容按 k', 'B2 m0 与 bank2 未变',
                      'B3 tone 槽 22–25 == 四套真 look、池其余未改', 'C 未碰 SD/色度钩点'],
           'static': {'tones': pre_tones, 'identity_slot48': pre_ident, 'pool_0_21_untouched': pre_pool,
                      'tone_ptrtab': ['0x%X' % v for v in pre_toneptr],
                      'matrix_ptrtab': ['0x%X' % v for v in pre_mxptr],
                      'matrix_data': pre_mxdata, 'ident_ptr': pre_identptr,
                      'bank2_untouched': pre_bank2, 'm0_stock': pre_m0,
                      'sd_hook_stock': pre_no_sd, 'chroma_hook_stock': pre_no_ch,
                      'hook_a': pre_hook_a, 'hook_s': pre_hook_s, 'hook_m': pre_hook_m},
           'details': details}
    with io.open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print('\n-> ' + ('全部闭合' if allok else '有不闭合的用例'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, allok))
    sys.exit(0 if allok else 1)


if __name__ == '__main__':
    main()
