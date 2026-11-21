#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b61.py —— B61 离线整链验证（B59 全链 + 色度双写）

分两段：

A. **B59 回归**：跑 `tools/verify_b59.py <包>`（派发器 → SD 桩 → SU 桩 → 上传器 → MX 桩，
   11 条断言 × 7 用例）。B61 必须在那套断言上仍然全闭合。

B. **色度双写（新增）**：PC 从真钩点 `0x604AA297` 起跑，走 钩子 → 桩 → 回跳 → 100 B 拷贝：

  C12  停在 0x604AA29E，a0 == 指针表[k-1]（k∈1..4）/ 原厂返回值（否则）
  C13  a0 处 100 B == 该档极端块（含 z 自洽 / pad=0）
  C14  **新鲜记录 dest+0x1d 的 100 B == 该档块**（写 #1，原路径）
  C19  **slot1 记录 *(ctx+0x37d8)+0x1d 的 100 B == 该档块**（写 #2，B61 新增）；
       且 slot1 记录的其它字节不动；k∉1..4 时 slot1 整块 0xEE 不动
  C15  桩保住 d2/d3/a2/a3
  C16  ctx+0x1230 与 m0 均未被写
  C17  k∉1..4 时与**原厂固件同点同参数**逐字节一致（等价性）
  C18  静态（指针表 / 桩 / 四块 / bank2）
  C20  两处写的 100 B 逐字节相同（双写同一份数据）
  C21  `*(ctx+0x37d8)==0` 时 cmp 0 保护生效：不崩、a0 仍是块指针、slot1 不写

用法：
  python tools/verify_b61.py
  python tools/verify_b61.py build/B61_chroma2.BIN
"""
from __future__ import annotations

import hashlib
import io
import json
import struct
import subprocess
import sys
import time

sys.path.insert(0, 'tools')

import verify_b32_chain as V              # noqa: E402
import verify_b59 as W                    # noqa: E402
import build_b54 as B54                   # noqa: E402
import build_b61 as B61                   # noqa: E402

BASE = B61.BASE
STASH = B61.STASH
CH_INSERT = B61.CH_INSERT
CH_STUB = B61.CH_STUB
CH_PTRTAB = B61.CH_PTRTAB
CH_ADDRS = B61.CH_ADDRS
CH_STRIDE = B61.CH_STRIDE
CH_STOCK = B61.CH_STOCK
SLOT1_OFF = B61.SLOT1_OFF
REC_OFF = B61.REC_OFF
COPY_END = 0x604AA31D
RET_POINT = B61.CH_RET
DEST = 0x61E10000
SLOT1 = 0x61E30000                         # HEAP 0x61e00000..0x61e40000 内
SLOT_FILL = 0x200


def payload_of(path):
    import oly_container as oc
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def k_of(key):
    return ((key >> 24) & 3) * 8 + (key & 7)


def chroma_run(image, key, *, mode=0xA400, ctx3c0=0, slot1=SLOT1, stop=None):
    """从真钩点 0x604AA297 起跑；slot1 是预置的 *(ctx+0x37d8)。"""
    stop = COPY_END if stop is None else stop
    CTX = W.CTX
    pokes = [
        (CTX + 0x3C0, struct.pack('<I', ctx3c0)),
        (CTX + 0x1071, b'\x02'),
        (CTX + 0x0C, struct.pack('<H', mode)),
        (CTX + 0x1230, (0xC0FFEE00).to_bytes(4, 'little')),
        (STASH, struct.pack('<I', int(key))),
        (DEST, b'\xEE' * 0x100),
        (CTX + SLOT1_OFF, struct.pack('<I', slot1)),
        (SLOT1, b'\xEE' * SLOT_FILL),
    ]
    regs = {'A0': CTX, 'A1': 0x61E11000, 'A2': DEST, 'A3': 0x61E1A000,
            'D2': 0x0000D2D2, 'D3': 0x0000D3D3}
    d = V.sim(image, CH_INSERT, stop, limit=400000, pokes=pokes, regs=regs)
    return V.regs_of(d), d


def fault_free(d):
    return d.get('status') not in ('memory_fault', 'instruction_fault')


def audit_block(blk, name):
    errs = []
    if len(blk) != CH_STRIDE:
        return ['%s 长度 %d != 100' % (name, len(blk))]
    for tag, xo, yo, zo in (('Cb', 0x00, 0x10, 0x20), ('Cr', 0x32, 0x42, 0x52)):
        x, y, z = list(blk[xo:xo + 16]), list(blk[yo:yo + 16]), list(blk[zo:zo + 17])
        if any(a > b for a, b in zip(x, x[1:])):
            errs.append('%s %s x 非单调' % (name, tag))
        prev = 0
        for i in range(1, 17):
            dx = x[i] - x[i - 1] if i < 16 else 0
            want = prev if dx == 0 else (round((y[i] - y[i - 1]) * 128 / dx) & 0xFF)
            if abs(want - z[i]) > 1:
                errs.append('%s %s z[%d]=%d want %d' % (name, tag, i, z[i], want))
                break
            prev = want
    if blk[0x31] or blk[0x63]:
        errs.append('%s pad 非 0: 0x%02x/0x%02x' % (name, blk[0x31], blk[0x63]))
    return errs


def run_b59_regression(path):
    import os
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    r = subprocess.run([sys.executable, 'tools/verify_b59.py', path],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', env=env)
    tail = [l for l in r.stdout.splitlines() if l.strip()][-2:]
    return r.returncode == 0, tail, r.stdout


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    path = sys.argv[1] if len(sys.argv) > 1 else 'build/B61_chroma2.BIN'
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    stock_img = payload_of(B61.SRC)

    print('== A. B59 整链回归（tools/verify_b59.py %s）' % path)
    ok59, tail59, out59 = run_b59_regression(path)
    for l in tail59:
        print('   ' + l)
    if not ok59:
        print(out59[-3000:])

    def at(img, addr, n):
        return bytes(img[addr - BASE:addr - BASE + n])

    ptr = [int.from_bytes(at(image, CH_PTRTAB + 4 * i, 4), 'little') for i in range(4)]
    pre_ptr = ptr == list(CH_ADDRS)
    blocks = [at(image, a, CH_STRIDE) for a in CH_ADDRS]
    stock_block = at(stock_img, CH_STOCK, CH_STRIDE)
    want_blocks = [B61.extreme_block(cb, cr, stock_block) for cb, cr in B61.BLOCK_SPEC]
    pre_blk = blocks == want_blocks
    pre_audit = all(not audit_block(b, 'k=%d' % (i + 1)) for i, b in enumerate(blocks))
    pre_distinct = len(set(blocks)) == 4 and all(b != stock_block for b in blocks)
    pre_yspec = all(list(b[0x10:0x20]) == [B61.BLOCK_SPEC[i][0]] * 16
                    and list(b[0x42:0x52]) == [B61.BLOCK_SPEC[i][1]] * 16
                    for i, b in enumerate(blocks))
    pre_xstock = all(at(image, CH_ADDRS[i] + xo, 16) == at(stock_img, CH_STOCK + xo, 16)
                     for i in range(4) for xo in (0x00, 0x32))
    pre_hook = at(image, CH_INSERT, 5) == B61.b46.jmp_bytes(CH_INSERT, CH_STUB)
    stub_now, _, _ = B61.assemble_stub()
    pre_stub = at(image, CH_STUB, len(stub_now)) == stub_now
    pre_tail = at(image, CH_INSERT + 5, 2) == b'\xF0\x18' == at(stock_img, CH_INSERT + 5, 2)
    n2 = B54.BANK2[1] - B54.BANK2[0]
    pre_bank2 = at(image, B54.BANK2[0], n2) == at(stock_img, B54.BANK2[0], n2)
    pre_m0 = at(image, B54.MATRIX_M0, 18) == at(stock_img, B54.MATRIX_M0, 18)
    pre_pal = at(image, CH_STOCK, CH_STRIDE) == stock_block
    print('\n静态前提：指针表=%s 四块==极值块=%s 自洽=%s 互异=%s y极值=%s x==库存=%s '
          '钩子=%s 钩后死字节=%s 桩==现汇编=%s bank2==原厂=%s m0==原厂=%s 原厂块==stock=%s'
          % (pre_ptr, pre_blk, pre_audit, pre_distinct, pre_yspec, pre_xstock, pre_hook,
             pre_tail, pre_stub, pre_bank2, pre_m0, pre_pal))
    pre_ok = all([pre_ptr, pre_blk, pre_audit, pre_distinct, pre_yspec, pre_xstock,
                  pre_hook, pre_tail, pre_stub, pre_bank2, pre_m0, pre_pal])

    cases = [
        (0x00000001, 'k=1 reala_ace（Cb y=00 / Cr y=00）', 0xA400, 0, SLOT1),
        (0x00000002, 'k=2 astia（Cb/Cr y=FF）', 0xA400, 0, SLOT1),
        (0x00000003, 'k=3 eterna（Cb y=00 / Cr y=FF）', 0xA800, 0, SLOT1),
        (0x00000004, 'k=4 bleach_bypass（Cb y=FF / Cr y=00）', 0xA900, 0, SLOT1),
        (0x01010064, 'k=12（越界）', 0xA400, 0, SLOT1),
        (0x00000000, 'k=0 越界', 0xA400, 0, SLOT1),
        (0x00000005, 'k=5 越界', 0xA400, 0, SLOT1),
        (0x000000FF, 'k=7 越界', 0xA400, 0, SLOT1),
        (0xFFFFFFFF, 'k=31 越界', 0xA400, 0, SLOT1),
        (0x03010003, 'k=27 越界（高位=3）', 0xA400, 0, SLOT1),
        (0x00000002, 'k=2 且 ctx+0x3c0 非空（标准档 key 优先）', 0xA400, 0x61E28000, SLOT1),
        (0x00000002, 'k=2 且 *(ctx+0x37d8)==0（cmp 0 保护）', 0xA400, 0, 0),
    ]

    allok = pre_ok
    details = []
    for key, label, mode, ctx3c0, slot1 in cases:
        k = k_of(key)
        in_range = 1 <= k <= 4
        rregs, rd = chroma_run(image, key, mode=mode, ctx3c0=ctx3c0, slot1=slot1, stop=RET_POINT)
        sregs, sd = chroma_run(stock_img, key, mode=mode, ctx3c0=ctx3c0, slot1=slot1, stop=RET_POINT)
        cregs, cd = chroma_run(image, key, mode=mode, ctx3c0=ctx3c0, slot1=slot1, stop=COPY_END)
        _, scd = chroma_run(stock_img, key, mode=mode, ctx3c0=ctx3c0, slot1=slot1, stop=COPY_END)

        exp_a0 = CH_ADDRS[k - 1] if in_range else sregs['A0']
        exp_blk = blocks[k - 1] if in_range else stock_block
        a0 = rregs['A0']
        got_blk = V.mem_bytes(rd, a0, CH_STRIDE) or b''
        got_dest = V.mem_bytes(cd, DEST + REC_OFF, CH_STRIDE) or b''
        sl1 = V.mem_bytes(cd, SLOT1, SLOT_FILL) or b''
        ee = b'\xEE' * SLOT_FILL

        c12 = (rregs['PC'] == RET_POINT and a0 == exp_a0 and fault_free(rd) and fault_free(cd))
        c13 = got_blk == exp_blk and not audit_block(got_blk, 'a0')
        c14 = got_dest == exp_blk
        if in_range and slot1:
            c19 = (sl1[REC_OFF:REC_OFF + CH_STRIDE] == exp_blk
                   and sl1[:REC_OFF] == b'\xEE' * REC_OFF
                   and sl1[REC_OFF + CH_STRIDE:] == b'\xEE' * (SLOT_FILL - REC_OFF - CH_STRIDE)
                   and cregs['PC'] == COPY_END)
        else:
            c19 = sl1 == ee                    # 不写（越界 或 slot1 为空）
        c15 = (rregs['A2'] == DEST and rregs['A3'] == 0x61E1A000
               and rregs['D2'] == 0x0000D2D2 and rregs['D3'] == 0x0000D3D3
               and cregs['A2'] == DEST and cregs['D2'] == 0x0000D2D2
               and cregs['D3'] == 0x0000D3D3)
        ctx_ok = all((V.mem_bytes(d, W.CTX + 0x1230, 4) or b'') == (0xC0FFEE00).to_bytes(4, 'little')
                     for d in (rd, cd))
        m0_ok = all((V.mem_bytes(d, B54.MATRIX_M0, 18) or b'') == at(stock_img, B54.MATRIX_M0, 18)
                    for d in (rd, cd))
        sl_ptr_ok = all((V.mem_bytes(d, W.CTX + SLOT1_OFF, 4) or b'') == struct.pack('<I', slot1)
                        for d in (rd, cd))
        c16 = ctx_ok and m0_ok and sl_ptr_ok
        if in_range:
            c17 = True
        else:
            c17 = (a0 == sregs['A0']
                   and (V.mem_bytes(sd, a0, CH_STRIDE) or b'') == got_blk
                   and got_dest == (V.mem_bytes(scd, DEST + REC_OFF, CH_STRIDE) or b''))
        c18 = pre_ptr and pre_stub and pre_bank2 and pre_audit
        two = in_range and bool(slot1)
        c20 = (got_dest == sl1[REC_OFF:REC_OFF + CH_STRIDE]) if two else True
        c21 = True
        if in_range and not slot1:
            c21 = (a0 == CH_ADDRS[k - 1] and got_dest == exp_blk and sl1 == ee
                   and fault_free(rd) and fault_free(cd))

        ok = all([c12, c13, c14, c19, c15, c16, c17, c18, c20, c21])
        allok &= ok
        print('== key=0x%08X %s（k=%d，在 1..4=%s）' % (key, label, k, in_range))
        print('   a0=0x%08X ; 写#1 dest+0x1d==块=%s ; 写#2 slot1+0x1d==块=%s'
              % (a0, c14, ('%s' % c19) if two else 'n/a（不该写）'))
        for name, v in (('C12 停在 0x604AA29E 且 a0 == 指针表[k-1] / 原厂返回值', c12),
                        ('C13 a0 处 100 B == 该档块（z 自洽 / pad=0）', c13),
                        ('C14 写 #1：新鲜记录 dest+0x1d == 该档块', c14),
                        ('C19 写 #2：slot1 *(ctx+0x37d8)+0x1d == 该档块（越界/空指针时不写）', c19),
                        ('C15 桩保住 d2/d3/a2/a3', c15),
                        ('C16 ctx+0x1230 / m0 / *(ctx+0x37d8) 未被写', c16),
                        ('C17 k∉1..4 与原厂同点同参数逐字节一致', c17),
                        ('C18 静态（指针表/桩/四块/bank2）', c18),
                        ('C20 两处写的 100 B 逐字节相同', c20),
                        ('C21 空指针保护路径（a0 仍为块指针、不崩、不写）', c21)):
            print('     %s  %s' % ('PASS' if v else 'FAIL', name))
        details.append({'key': '0x%08X' % key, 'label': label, 'k': k,
                        'in_range': in_range, 'mode': '0x%04X' % mode,
                        'ctx3c0': '0x%08X' % ctx3c0, 'slot1': '0x%08X' % slot1,
                        'a0': '0x%08X' % a0, 'exp_a0': '0x%08X' % exp_a0,
                        'fresh_record_match': bool(c14), 'slot1_match': bool(c19),
                        'ok': bool(ok)})

    rec = {}
    try:
        with io.open(path + '.sim.json', encoding='utf-8') as f:
            rec = json.load(f)
    except Exception:
        pass
    rec.setdefault('checks', [])
    rec['verifier'] = 'tools/verify_b61.py'
    rec['sha256'] = hashlib.sha256(whole).hexdigest()
    rec['b59_regression'] = {'closed': bool(ok59), 'verifier': 'tools/verify_b59.py'}
    rec['chroma'] = {
        'hook': '0x%X' % CH_INSERT, 'stub': '0x%X' % CH_STUB,
        'stub_len': len(stub_now), 'ptrtab': '0x%X' % CH_PTRTAB,
        'blocks': ['0x%X' % a for a in CH_ADDRS], 'stock_block': '0x%X' % CH_STOCK,
        'block_spec': [{'k': i + 1, 'look': B61.LOOKS[i],
                        'cb_y': '0x%02X' % B61.BLOCK_SPEC[i][0],
                        'cr_y': '0x%02X' % B61.BLOCK_SPEC[i][1]} for i in range(4)],
        'write1': 'fresh record *(O+0x3940)+0x1d（原路径拷贝循环 0x604AA30E..）',
        'write2': 'slot1 record *(ctx+0x%X)+0x%X（桩内 100 次 movbu，cmp 0 保护）' % (SLOT1_OFF, REC_OFF),
        'looks': B61.LOOKS, 'cases': len(cases),
        'checks': ['C12 返回值', 'C13 a0 处 100 B', 'C14 写#1 新鲜记录',
                   'C19 写#2 slot1', 'C15 d2/d3/a2/a3 保全', 'C16 ctx+0x1230/m0/0x37d8 未写',
                   'C17 越界档与原厂等价', 'C18 静态', 'C20 两处内容相同', 'C21 空指针保护'],
        'static': {'ptrtab': pre_ptr, 'blocks_from_spec': pre_blk, 'audit': pre_audit,
                   'distinct': pre_distinct, 'y_extreme': pre_yspec, 'x_stock': pre_xstock,
                   'hook': pre_hook, 'hook_tail_stock': pre_tail, 'bank2': pre_bank2,
                   'm0': pre_m0, 'stock_block': pre_pal},
        'details': details, 'closed': bool(allok),
    }
    rec['closed'] = bool(rec.get('closed', False) and allok and ok59)
    rec['time'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    with io.open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

    print('\nA. B59 回归: %s' % ('闭合' if ok59 else '未闭合'))
    print('B. 色度双写: %d 用例 -> %s' % (len(cases), '全闭合' if allok else '有不闭合的用例'))
    print('-> ' + ('全部闭合' if (allok and ok59) else 'FAIL'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, rec['closed']))
    sys.exit(0 if (allok and ok59) else 1)


if __name__ == '__main__':
    main()
