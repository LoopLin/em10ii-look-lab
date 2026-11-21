# -*- coding: utf-8 -*-
"""B13 模拟器验证：上传器 0x60479960 → 0x60479A77，两件事。

证据 1（四档仍在）：四个目标 key 各跑一次，`dest+0x000` 的 256 个 u32 必须等于
FF 空间里对应表的零扩展，且 `(S+0x1230)` 被回写成该表地址。

证据 2（零副作用）：非目标 key（默认 0x0101006B）在**原厂指针**指向原厂槽 0..3
的四种现场下，`dest+0x000..0x3FF` 必须与**原厂镜像**逐值相同 —— 这证明四张表
放在 0x60E00000 之后，槽 0..3（12 个池指针的目标）没有被顺手改掉。

用法（从工作区根）：
    python tools/verify_hook_look4.py                 # B13
    python tools/verify_hook_look4.py --bin build/B12_look4.BIN   # 对照（应 FAIL 证据 2）
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, 'tools')
sys.path.insert(0, '.tmp')

import oly_container     # noqa: E402
import simrun            # noqa: E402

BASE = 0x60021020
BIN = 'build/B13_look4.BIN'
STOCK_IMAGE = 'analysis/00_0100_40300020.decoded.bin'
TABLE_RAM = 0x60E00000      # B13 的四张表
SLOT = 0x200
STOCK_SLOT = 0x605D8344     # 原厂槽 0（必须保持不动）
INSERT = 0x60479A27
RESUME = 0x60479A2C
STUB = 0x60DE0000
ENTRY = bytes.fromhex('dcd9659600')

O = 0x61E10000
S = 0x61E18000
CTX = 0x61E20000
DEST = 0x9E00A400
SP = 0x61E0F000
STOCK_LUT = 0x8FF07800      # 预置的原厂指针（斜坡表）

GROUPS = [('reala_ace', 0x01010079), ('astia', 0x01020079),
          ('eterna', 0x01030079), ('bleach_bypass', 0x01010066)]
NON_TARGET = 0x0101006B

REGIONS = ['0x61e00000:0x20000', '0x61e20000:0x1000', '0x9e00a000:0x3000',
           '0x60e00000:0x800',
           '0x8fe00000:0x2000', '0x8ff00000:0x8000', '0xa1c21000:0x1000']
CODE = '0x60021020:0xd98940'
STUB_CODE = '0x60de0000:0x100'


def w(addr, val):
    return (addr, struct.pack('<I', val & 0xFFFFFFFF))


def b(addr, val):
    return (addr, bytes([val & 0xFF]))


def h(addr, val):
    return (addr, struct.pack('<H', val & 0xFFFF))


def rd(d, addr, n):
    for r in d['memory']:
        buf = bytes.fromhex(r['hex'])
        a = r['address']
        if a <= addr and addr + n <= a + len(buf):
            return buf[addr - a:addr - a + n]
    raise SystemExit('不在 dump 区域里: %08X+%X' % (addr, n))


def words(d, addr, n):
    return list(struct.unpack('<%dI' % n, rd(d, addr, 4 * n)))


def ptr_of(d):
    return struct.unpack('<I', rd(d, S + 0x1230, 4))[0]


def pokes(key, slot, lut=STOCK_LUT):
    p = [w(O + 0x04, CTX), w(O + 0x0C, S), w(O + 0x30, key), w(O + 0x34, 0),
         b(O + 0xBD, 1), w(O + 0x64, 2),
         w(CTX + 0x08, key), w(CTX + 0x0C, slot << 16), b(CTX + 0x10, 0),
         h(CTX + 0x1C, 0x0100), h(CTX + 0x1E, 0x0100),
         w(S + 0x41C, 0x8FF00000), w(S + 0x420, 0x8FF01000),
         w(S + 0x1224, STOCK_SLOT), w(S + 0x1228, STOCK_SLOT + SLOT),
         w(S + 0x122C, STOCK_SLOT + 2 * SLOT),
         w(S + 0x1230, lut), w(S + 0x3C4, 1)]
    for k in range(10):
        p.append(w(S + 0x1434 + 4 * k, 0x8FF02000 + 0x400 * k))
    # 预置指针指向的表：0x100+i 的斜坡，与四张 look 表都不同
    for i in range(256):
        p.append(h(STOCK_LUT + 2 * i, 0x100 + i))
    return p


def run_case(key, slot, image, lut=STOCK_LUT):
    d = simrun.run(0x60479960, 0x60479A77, limit=500000, codes=[CODE, STUB_CODE],
                   rws=REGIONS, pokes=pokes(key, slot, lut),
                   regs={'A0': DEST, 'A1': O, 'SP': SP}, image=image)
    if d.get('status') != 'returned':
        raise SystemExit('key=%08X image=%s status=%s fault=%s'
                         % (key, image, d.get('status'), d.get('fault')))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bin', default=BIN)
    ap.add_argument('--payload', default=None)
    args = ap.parse_args()
    payload = args.payload or '.tmp/%s.payload.bin' % os.path.splitext(
        os.path.basename(args.bin))[0]

    if not os.path.exists(args.bin):
        raise SystemExit('缺 %s，先跑 tools/build_hook_look4.py' % args.bin)

    blocks = oly_container.parse(open(args.bin, 'rb').read())
    pay = blocks[0].payload
    open(payload, 'wb').write(pay)
    print('payload %s：%d 字节（来自 %s）' % (payload, len(pay), args.bin))

    stock = open(STOCK_IMAGE, 'rb').read()
    tables = []
    for i in range(len(GROUPS)):
        off = TABLE_RAM - BASE + i * SLOT
        buf = pay[off:off + SLOT]
        tables.append(list(struct.unpack('<256H', buf)))
    print('表 0..3 的 t[46] = ' + ' / '.join(str(t[46]) for t in tables))

    ok = True
    lines = []

    # ---------------- 证据 1：四个目标 key ----------------
    print('\n== 证据 1：四个目标 key（各走各的表）')
    results = []
    for idx, (name, key) in enumerate(GROUPS):
        d = run_case(key, idx, payload)
        got = words(d, DEST, 256)
        exp = tables[idx]
        ptr = ptr_of(d)
        want_ptr = TABLE_RAM + idx * SLOT
        good = (got == exp) and (ptr == want_ptr)
        ok = ok and good
        results.append(got)
        print('[%s] key=%08X 表 %d @%#x' % (name, key, idx, want_ptr))
        print('   dest+0x000 前 8：%s' % ' '.join('%d' % v for v in got[:8]))
        print('   dest+0x3F0 末 8：%s' % ' '.join('%d' % v for v in got[248:]))
        print('   %s 256 个 u32 == 表 %d 零扩展；%s (S+0x1230) = %#x'
              % ('✔' if got == exp else '✘', idx,
                 '✔' if ptr == want_ptr else '✘', ptr))
        lines.append('%-14s key=%08X tab=%d dest[0:8]=%s  %s'
                     % (name, key, idx, got[:8], 'PASS' if good else 'FAIL'))

    same = [(GROUPS[i][0], GROUPS[j][0])
            for i in range(len(results)) for j in range(i + 1, len(results))
            if results[i] == results[j]]
    if same:
        ok = False
        print('✘ 有完全相同的两组: %s' % same)
    else:
        print('✔ 四组两两不同（最小差异 %d 个位置）' % min(
            sum(1 for x, y in zip(results[i], results[j]) if x != y)
            for i in range(len(results)) for j in range(i + 1, len(results))))

    # 未列 key 且指针指向我们的斜坡表 → 必须原样不动
    d = run_case(NON_TARGET, 0, payload)
    got = words(d, DEST, 256)
    ptr = ptr_of(d)
    fb = (got == [0x100 + i for i in range(256)]) and ptr == STOCK_LUT
    ok = ok and fb
    print('[fallback] key=%08X dest 前 8：%s；(S+0x1230) = %#x  %s'
          % (NON_TARGET, ' '.join('%d' % v for v in got[:8]), ptr,
             '✔' if fb else '✘'))
    lines.append('%-14s key=%08X tab=- dest[0:8]=%s  %s'
                 % ('fallback', NON_TARGET, got[:8], 'PASS' if fb else 'FAIL'))

    # ---------------- 证据 2：零副作用 ----------------
    print('\n== 证据 2：零副作用（非目标 key + 原厂指针指向原厂槽 0..3）')
    print('   判据：dest+0x000..0x3FF 必须与**原厂镜像**逐值相同')
    for i in range(4):
        slot_addr = STOCK_SLOT + i * SLOT
        b13 = run_case(NON_TARGET, i, payload, lut=slot_addr)
        stk = run_case(NON_TARGET, i, STOCK_IMAGE, lut=slot_addr)
        v13 = words(b13, DEST, 256)
        vst = words(stk, DEST, 256)
        p13, pst = ptr_of(b13), ptr_of(stk)
        same_v = v13 == vst
        same_p = p13 == pst == slot_addr
        good = same_v and same_p
        ok = ok and good
        print('   指针 = 原厂槽 %d @%#x：dest 与原厂 %s；指针 %s'
              % (i, slot_addr, '逐值相同' if same_v else '不同 ✘',
                 '未被改' if same_p else '被改成 %#x ✘' % p13))
        print('     dest 前 8：B13 %s | 原厂 %s'
              % (' '.join('%d' % v for v in v13[:8]),
                 ' '.join('%d' % v for v in vst[:8])))
        lines.append('%-14s key=%08X lut=slot%d dest[0:8]=%s  %s'
                     % ('zero-side', NON_TARGET, i, v13[:8],
                        'PASS' if good else 'FAIL'))

    # 对照：B12（表在槽 0..3）在同一现场会改到别人 —— 这就是返工的理由
    if os.path.exists('build/B12_look4.BIN'):
        pay12 = oly_container.parse(open('build/B12_look4.BIN', 'rb').read())[0].payload
        p12 = '.tmp/B12.payload.bin'
        open(p12, 'wb').write(pay12)
        slot_addr = STOCK_SLOT
        v12 = words(run_case(NON_TARGET, 0, p12, lut=slot_addr), DEST, 256)
        vst = words(run_case(NON_TARGET, 0, STOCK_IMAGE, lut=slot_addr), DEST, 256)
        n = sum(1 for x, y in zip(v12, vst) if x != y)
        print('   [对照 B12] 同现场（指针=原厂槽 0）dest 与原厂差 %d/256 个值 —— '
              '改槽 0..3 会顺手改到这些模式' % n)
        lines.append('%-14s key=%08X lut=slot0 dest[0:8]=%s  %s (对照，应 FAIL)'
                     % ('B12-contrast', NON_TARGET, v12[:8], 'FAIL' if n else 'PASS'))
        if n == 0:
            ok = False
            print('   ✘ B12 对照没有差异，说明对照用例没生效')

    # ---------------- 寄存器守恒 ----------------
    d_hook = run_case(0x01010079, 0, payload)
    d_stock = run_case(0x01010079, 0, STOCK_IMAGE)
    rh = dict(zip(simrun.REGS, d_hook['registers']))
    rs = dict(zip(simrun.REGS, d_stock['registers']))
    must_same = ['D1', 'D2', 'D3', 'A1', 'A3', 'SP', 'PSW']
    diff = [n for n in must_same if rh[n] != rs[n]]
    ok = ok and not diff
    print('\n[寄存器守恒] %s 与原厂逐位相同 = %s；D0=%#x vs %#x、A2=%#x vs %#x、'
          'A0=%#x vs %#x'
          % ('/'.join(must_same), not diff, rh['D0'], rs['D0'],
             rh['A2'], rs['A2'], rh['A0'], rs['A0']))
    lines.append('%-14s d2=%#x/%#x a3=%#x/%#x SP=%#x  %s'
                 % ('regs', rh['D2'], rs['D2'], rh['A3'], rs['A3'], rh['SP'],
                    'PASS' if not diff else 'FAIL'))

    # ---------------- 镜像核对（直接读包，不经模拟器） ----------------
    print('\n== 镜像核对')
    ins = pay[INSERT - BASE:INSERT - BASE + 5]
    print('   插点 = %s  %s' % (ins.hex(), '✔' if ins == ENTRY else '✘'))
    ok = ok and ins == ENTRY
    for i in range(4):
        a = STOCK_SLOT + i * SLOT
        same = pay[a - BASE:a - BASE + SLOT] == stock[a - BASE:a - BASE + SLOT]
        print('   原厂槽 %d @%#x 与原厂逐字节相同  %s' % (i, a, '✔' if same else '✘'))
        ok = ok and same
    pool = pay[0x60600920 - BASE:0x60600920 - BASE + 672]
    pool_s = stock[0x60600920 - BASE:0x60600920 - BASE + 672]
    print('   168 个池指针与原厂相同  %s' % ('✔' if pool == pool_s else '✘'))
    ok = ok and pool == pool_s
    print('   stub 前 8 字节 = %s' % pay[STUB - BASE:STUB - BASE + 8].hex())

    open('.tmp/verify_look4.txt', 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('\nVERDICT  %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
