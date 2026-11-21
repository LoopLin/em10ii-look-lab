#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b60.py —— B60 离线整链验证（B59 全链 + 色度钩子）

分两段：

A. **B59 回归**：直接跑 `tools/verify_b59.py <B60 包>`（同一个 libsim 整链：派发器 → SD 桩 →
   SU 桩 → 上传器 → MX 桩，11 条断言 × 7 用例）。B60 必须在 B59 那套断言上仍然全闭合 ——
   这证明"B59 的东西一个字节都没动"在运行期也成立。

B. **色度钩子（新增）**：每个用例跑一次 libsim，PC 从**真钩点** `0x604AA297` 起，
   一路跑到拷贝循环结束 `0x604AA31D`（也就是真的走了 钩子 → 桩 → 原厂函数 → 回跳 → 拷贝）：

  C12  回到 0x604AA31D，a0 == 指针表[k-1]（k∈1..4）/ 原厂返回值（否则）
  C13  a0 处 100 B == 该档色度块（含 pad=0、z 自洽）
  C14  **dest+0x1d 的 100 B == 该档色度块**（端到端：色度块真的进了 ctx 工作区）
  C15  桩保住调用点的 d2/d3/a2/a3（call reglist [d2,d3,a2,a3] 的语义）
  C16  ctx+0x1230 未被写；原厂 m0 未被写
  C17  k∉1..4 时与**原厂固件同点同参数**的 a0/dest 完全一致（等价性，不是猜的期望值）
  C18  指针表 / 四张块 / 钱包（bank2）静态一致

用法：
  python tools/verify_b60.py                    # 默认 build/B60_chroma.BIN
  python tools/verify_b60.py build/B60_chroma.BIN
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
import build_b60 as B60                   # noqa: E402

BASE = B60.BASE
STASH = B60.STASH
CH_INSERT = B60.CH_INSERT
CH_STUB = B60.CH_STUB
CH_PTRTAB = B60.CH_PTRTAB
CH_ADDRS = B60.CH_ADDRS
CH_STRIDE = B60.CH_STRIDE
CH_STOCK = B60.CH_STOCK
COPY_END = 0x604AA31D                     # 拷贝循环之后（movhu (28,a3),d0）
RET_POINT = B60.CH_RET                    # 0x604AA29E：call 的下一条真实指令（cmp 0x0,a0）
DEST = 0x61E10000


def payload_of(path):
    import oly_container as oc
    with open(path, 'rb') as f:
        return oc.parse(f.read())[0].payload


def k_of(key):
    return ((key >> 24) & 3) * 8 + (key & 7)


def chroma_run(image, key, *, mode=0xA400, ctx3c0=0, stop=None):
    """从真钩点 0x604AA297 起跑：钩子→桩→原厂→回跳。
    stop=RET_POINT 只看返回值（a0）；stop=COPY_END 再走完 100 B 拷贝。"""
    stop = COPY_END if stop is None else stop
    CTX = W.CTX
    pokes = [
        (CTX + 0x3C0, struct.pack('<I', ctx3c0)),
        (CTX + 0x1071, b'\x02'),
        (CTX + 0x0C, struct.pack('<H', mode)),
        (CTX + 0x1230, (0xC0FFEE00).to_bytes(4, 'little')),
        (STASH, struct.pack('<I', int(key))),
        (DEST, b'\xEE' * 0x100),
    ]
    regs = {'A0': CTX, 'A1': 0x61E11000, 'A2': DEST, 'A3': 0x61E1A000,
            'D2': 0x0000D2D2, 'D3': 0x0000D3D3}
    d = V.sim(image, CH_INSERT, stop, limit=400000, pokes=pokes, regs=regs)
    return V.regs_of(d), d


def audit_block(blk, name):
    """独立色度自洽检查（与 audit_beta_pack.py 同一套规则）。"""
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
    """B59 那一套整链断言必须在 B60 上依旧全闭合（原样跑它的 main）。"""
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
    path = sys.argv[1] if len(sys.argv) > 1 else 'build/B60_chroma.BIN'
    image = payload_of(path)
    with open(path, 'rb') as f:
        whole = f.read()
    stock_img = payload_of(B60.SRC)

    print('== A. B59 整链回归（tools/verify_b59.py %s）' % path)
    ok59, tail59, out59 = run_b59_regression(path)
    for l in tail59:
        print('   ' + l)
    if not ok59:
        print(out59[-3000:])

    # ---- 静态前提 ----
    def at(img, addr, n):
        return bytes(img[addr - BASE:addr - BASE + n])

    ptr = [int.from_bytes(at(image, CH_PTRTAB + 4 * i, 4), 'little') for i in range(4)]
    pre_ptr = ptr == list(CH_ADDRS)
    blocks = [at(image, a, CH_STRIDE) for a in CH_ADDRS]
    want_blocks = [B60.chroma_block(l, at(stock_img, CH_STOCK, CH_STRIDE)) for l in B60.LOOKS]
    pre_blk = blocks == want_blocks
    pre_audit = all(not audit_block(b, 'k=%d' % (i + 1)) for i, b in enumerate(blocks))
    pre_distinct = len(set(blocks)) == 4 and all(b != at(stock_img, CH_STOCK, CH_STRIDE) for b in blocks)
    pre_hook = at(image, CH_INSERT, 5) == B60.b46.jmp_bytes(CH_INSERT, CH_STUB)
    stub_now, _, _ = B60.assemble_stub()          # 现汇编一遍，确认包里的桩就是这份源码出的
    pre_stub = at(image, CH_STUB, len(stub_now)) == stub_now
    pre_tail = at(image, CH_INSERT + 5, 2) == b'\xF0\x18' == at(stock_img, CH_INSERT + 5, 2)
    n2 = B54.BANK2[1] - B54.BANK2[0]
    pre_bank2 = at(image, B54.BANK2[0], n2) == at(stock_img, B54.BANK2[0], n2)
    pre_m0 = at(image, B54.MATRIX_M0, 18) == at(stock_img, B54.MATRIX_M0, 18)
    print('\n静态前提：指针表=%s 四块==JSON 期望=%s 自洽=%s 四块互异且异于原厂=%s '
          '钩子=%s 钩后死字节=%s 桩==现汇编=%s bank2==原厂=%s m0==原厂=%s'
          % (pre_ptr, pre_blk, pre_audit, pre_distinct, pre_hook, pre_tail, pre_stub,
             pre_bank2, pre_m0))
    pre_ok = all([pre_ptr, pre_blk, pre_audit, pre_distinct, pre_hook, pre_tail, pre_stub,
                  pre_bank2, pre_m0])

    # ---- 用例 ----
    cases = [
        (0x00000001, 'k=1 reala_ace', 0xA400, 0),
        (0x00000002, 'k=2 astia', 0xA400, 0),
        (0x00000003, 'k=3 eterna', 0xA800, 0),
        (0x00000004, 'k=4 bleach_bypass', 0xA900, 0),
        (0x01010064, 'k=12（B51 实测过的 key 0x01010064，越界）', 0xA400, 0),
        (0x00000000, 'k=0 越界', 0xA400, 0),
        (0x00000005, 'k=5 越界', 0xA400, 0),
        (0x000000FF, 'k=7 越界', 0xA400, 0),
        (0xFFFFFFFF, 'k=31 越界', 0xA400, 0),
        (0x03010003, 'k=27 越界（高位=3）', 0xA400, 0),
        (0x00000002, 'k=2 且 ctx+0x3c0 非空（标准档 key 优先）', 0xA400, 0x61E28000),
    ]

    stock_block = at(stock_img, CH_STOCK, CH_STRIDE)
    allok = pre_ok
    details = []
    for key, label, mode, ctx3c0 in cases:
        k = k_of(key)
        in_range = 1 <= k <= 4
        # (1) 只看返回值：PC 停在 0x604AA29E，a0 就是 0x604B4B24 的（可替换的）返回值
        rregs, rd = chroma_run(image, key, mode=mode, ctx3c0=ctx3c0, stop=RET_POINT)
        sregs, sd = chroma_run(stock_img, key, mode=mode, ctx3c0=ctx3c0, stop=RET_POINT)
        # (2) 再走完拷贝循环：dest+0x1d 的 100 B 就是最终进 ctx 工作区的东西
        cregs, cd = chroma_run(image, key, mode=mode, ctx3c0=ctx3c0, stop=COPY_END)
        _, scd = chroma_run(stock_img, key, mode=mode, ctx3c0=ctx3c0, stop=COPY_END)

        exp_a0 = CH_ADDRS[k - 1] if in_range else sregs['A0']
        exp_blk = blocks[k - 1] if in_range else stock_block
        a0 = rregs['A0']
        got_blk = V.mem_bytes(rd, a0, CH_STRIDE) or b''
        got_dest = V.mem_bytes(cd, DEST + 0x1D, CH_STRIDE) or ''

        c12 = (rregs['PC'] == RET_POINT and a0 == exp_a0)
        c13 = got_blk == exp_blk and not audit_block(got_blk, 'a0')
        c14 = got_dest == exp_blk
        c15 = (rregs['A2'] == DEST and rregs['A3'] == 0x61E1A000
               and rregs['D2'] == 0x0000D2D2 and rregs['D3'] == 0x0000D3D3
               and cregs['A2'] == DEST and cregs['D2'] == 0x0000D2D2
               and cregs['D3'] == 0x0000D3D3)
        ctx_ok = all((V.mem_bytes(d, W.CTX + 0x1230, 4) or b'') == (0xC0FFEE00).to_bytes(4, 'little')
                     for d in (rd, cd))
        m0_ok = all((V.mem_bytes(d, B54.MATRIX_M0, 18) or b'') == at(stock_img, B54.MATRIX_M0, 18)
                    for d in (rd, cd))
        c16 = ctx_ok and m0_ok
        if in_range:
            c17 = True                    # 不适用：由 C12/C13 覆盖
        else:
            c17 = (a0 == sregs['A0']
                   and (V.mem_bytes(sd, a0, CH_STRIDE) or b'') == got_blk
                   and got_dest == (V.mem_bytes(scd, DEST + 0x1D, CH_STRIDE) or b''))
        c18 = pre_ptr and pre_stub and pre_bank2 and pre_audit

        ok = all([c12, c13, c14, c15, c16, c17, c18])
        allok &= ok
        print('== key=0x%08X %s（k=%d，在 1..4=%s）' % (key, label, k, in_range))
        print('   a0=0x%08X（期望 0x%08X） 原厂同参数 a0=0x%08X ; 拷贝后 a0=0x%08X'
              % (a0, exp_a0, sregs['A0'], cregs['A0']))
        print('   a0 处 100 B == 期望=%s ; dest+0x1d 100 B == 期望=%s'
              % (c13, c14))
        for name, v in (('C12 停在 0x604AA29E 且 a0 == 指针表[k-1] / 原厂返回值', c12),
                        ('C13 a0 处 100 B == 该档块（含 z 自洽 / pad=0）', c13),
                        ('C14 dest+0x1d 100 B == 该档块（端到端进 ctx 工作区）', c14),
                        ('C15 桩保住 d2/d3/a2/a3', c15),
                        ('C16 ctx+0x1230 与 m0 均未被写', c16),
                        ('C17 k∉1..4 与原厂同点同参数逐字节一致（在 1..4 时不适用）', c17),
                        ('C18 静态（指针表/桩/四块/bank2）', c18)):
            print('     %s  %s' % ('PASS' if v else 'FAIL', name))
        details.append({'key': '0x%08X' % key, 'label': label, 'k': k,
                        'in_range': in_range, 'mode': '0x%04X' % mode,
                        'ctx3c0': '0x%08X' % ctx3c0,
                        'a0': '0x%08X' % a0, 'exp_a0': '0x%08X' % exp_a0,
                        'stock_a0': '0x%08X' % sregs['A0'],
                        'a0_after_copy': '0x%08X' % cregs['A0'],
                        'dest_match': bool(c14), 'ok': bool(ok)})

    # ---- 写回 sidecar（在 verify_b59 写的那份上追加色度段） ----
    rec = {}
    try:
        with io.open(path + '.sim.json', encoding='utf-8') as f:
            rec = json.load(f)
    except Exception:
        pass
    rec.setdefault('checks', [])
    rec['verifier'] = 'tools/verify_b60.py'
    rec['sha256'] = hashlib.sha256(whole).hexdigest()
    rec['b59_regression'] = {'closed': bool(ok59), 'verifier': 'tools/verify_b59.py'}
    rec['chroma'] = {
        'hook': '0x%X' % CH_INSERT, 'stub': '0x%X' % CH_STUB,
        'stub_len': 49, 'ptrtab': '0x%X' % CH_PTRTAB,
        'blocks': ['0x%X' % a for a in CH_ADDRS], 'stock_block': '0x%X' % CH_STOCK,
        'looks': B60.LOOKS, 'cases': len(cases),
        'checks': ['C12 a0 == 指针表[k-1] / 原厂返回值', 'C13 a0 处 100 B == 该档块',
                   'C14 dest+0x1d == 该档块', 'C15 d2/d3/a2/a3 保全',
                   'C16 ctx+0x1230 与 m0 未写', 'C17 越界档与原厂等价', 'C18 静态'],
        'static': {'ptrtab': pre_ptr, 'blocks_from_json': pre_blk, 'audit': pre_audit,
                   'distinct': pre_distinct, 'hook': pre_hook,
                   'hook_tail_stock': pre_tail, 'bank2': pre_bank2, 'm0': pre_m0},
        'details': details, 'closed': bool(allok),
    }
    rec['closed'] = bool(rec.get('closed', False) and allok and ok59)
    rec['time'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    with io.open(path + '.sim.json', 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

    print('\nA. B59 回归: %s' % ('闭合' if ok59 else '未闭合'))
    print('B. 色度钩子: %d 用例 -> %s' % (len(cases), '全闭合' if allok else '有不闭合的用例'))
    print('-> ' + ('全部闭合' if (allok and ok59) else 'FAIL'))
    print('   sim 记录: %s.sim.json  closed=%s' % (path, rec['closed']))
    sys.exit(0 if (allok and ok59) else 1)


if __name__ == '__main__':
    main()
