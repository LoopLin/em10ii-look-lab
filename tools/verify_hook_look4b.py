# -*- coding: utf-8 -*-
"""B14 模拟器验证：上传器 0x60479960 → 0x60479A77。

四条判据（lead 指定）：
1. 四个目标 key 各跑一次，`dest+0x000` 的 256 个 u32 == 对应表的零扩展；
   四组两两不同；
2. 非目标 key（默认 0x0101006B）在**原厂指针**指向原厂槽 0..3 的四种现场下，
   `dest+0x000..0x3FF` 与**原厂镜像**逐值相同；
3. 执行后 `(S+0x1230)` 与原厂一致（B14 的桩不写任何内存，这就是回归判据）；
4. 停点寄存器 D1/D2/D3/A1/A2/A3/SP/PSW 与原厂对比（A2 允许不同；额外在
   0x60479A3A 这条“进循环前”的边界上也比一次）。

用法（从工作区根）：
    python tools/verify_hook_look4b.py
    python tools/verify_hook_look4b.py --bin build/B13_look4.BIN   # 对照（会炸/不符）
"""
import argparse
import os
import struct
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, 'reconfigure'):
        _s.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, 'tools')
sys.path.insert(0, '.tmp')

import oly_container     # noqa: E402
import simrun            # noqa: E402

BASE = 0x60021020
BIN = 'build/B14_look4.BIN'
STOCK_IMAGE = 'analysis/00_0100_40300020.decoded.bin'

SLOT = 0x200
STOCK_SLOT = 0x605D8344
POOL = 0x60600920
INSERT = 0x60479A27
STUBS = {'hole': 0x60DA4E08, 'slot53': 0x605DED44, 'slot71': 0x605E1144}
STUB = STUBS['hole']
LOOP_TAB = STUB + 0x58          # 桩 66 B 之后，8 字节对齐
LOOP_END = LOOP_TAB + 32
ENTRY = b'\xdc' + struct.pack('<i', STUB - 0x60479A27)
STOP = 0x60479A77
LOOP_HEAD = 0x60479A3A

O = 0x61E10000
S = 0x61E18000
CTX = 0x61E20000
DEST = 0x9E00A400
SP = 0x61E0F000
STOCK_LUT = 0x8FF07800      # 预置的原厂指针（斜坡表）

TABLESETS = {
    'lead': [('reala_ace', 0x01010079, 0x605DDD44),
             ('astia', 0x01020079, 0x605DDF44),
             ('eterna', 0x01030079, 0x605DE144),
             ('bleach_bypass', 0x01010066, 0x605DEB44)],
    'fftail': [('reala_ace', 0x01010079, 0x60663000),
               ('astia', 0x01020079, 0x60663200),
               ('eterna', 0x01030079, 0x60663400),
               ('bleach_bypass', 0x01010066, 0x60663600)],
    # B16 datapool：表在 0x605Dxxxx 固件数据区 slot 8/9/10/11
    'dpool': [('reala_ace', 0x01010079, 0x605D9344),
              ('astia', 0x01020079, 0x605D9544),
              ('eterna', 0x01030079, 0x605D9744),
              ('bleach_bypass', 0x01010066, 0x605D9944)],
    # B15 变量隔离：四档直接指原厂槽 0/2/4/6，不写任何新数据
    'stock': [('reala_ace', 0x01010079, 0x605D8344),
              ('astia', 0x01020079, 0x605D8744),
              ('eterna', 0x01030079, 0x605D8B44),
              ('bleach_bypass', 0x01010066, 0x605D8F44)],
}
GROUPS = TABLESETS['lead']
NON_TARGET = 0x0101006B

REGIONS = ['0x61e00000:0x20000', '0x61e20000:0x1000', '0x9e00a000:0x3000',
           '0x8fe00000:0x2000', '0x8ff00000:0x8000', '0xa1c21000:0x1000']
CODE = '0x60021020:0xd98940'      # 覆盖桩 0x60da4e08（镜像内）


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
    for i in range(256):
        p.append(h(STOCK_LUT + 2 * i, 0x100 + i))
    return p


def run(stop, key, image, lut=STOCK_LUT, slot=0):
    d = simrun.run(0x60479960, stop, limit=500000, codes=[CODE],
                   rws=REGIONS, pokes=pokes(key, slot, lut),
                   regs={'A0': DEST, 'A1': O, 'SP': SP}, image=image)
    if d.get('status') != 'returned':
        raise SystemExit('key=%08X image=%s status=%s fault=%s'
                         % (key, image, d.get('status'), d.get('fault')))
    return d


def regs(d):
    return dict(zip(simrun.REGS, d['registers']))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bin', default=None)
    ap.add_argument('--stub', choices=sorted(STUBS), default='hole')
    ap.add_argument('--tables', choices=['lead', 'fftail', 'stock', 'dpool'], default='lead')
    args = ap.parse_args()
    global GROUPS, STUB, LOOP_TAB, LOOP_END, ENTRY
    GROUPS = TABLESETS[args.tables]
    STUB = STUBS[args.stub]
    LOOP_TAB = STUB + 0x58
    LOOP_END = LOOP_TAB + 32
    ENTRY = b'\xdc' + struct.pack('<i', STUB - 0x60479A27)
    if args.bin is None and args.tables == 'dpool' and args.stub == 'slot53':
        args.bin = 'build/B16_hook_datapool.BIN'
    elif args.bin is None and args.tables == 'stock' and args.stub == 'slot53':
        args.bin = 'build/B16_hook_slot53.BIN'
    elif args.bin is None and args.tables == 'stock' and args.stub == 'slot71':
        args.bin = 'build/B16b_hook_slot71.BIN'
    elif args.bin is None:
        args.bin = {'lead': 'build/B14_look4.BIN',
                    'fftail': 'build/B14b_look4.BIN',
                    'stock': 'build/B15_hook_stocktables.BIN'}[args.tables]
    if not os.path.exists(args.bin):
        raise SystemExit('缺 %s，先跑 tools/build_hook_look4b.py' % args.bin)

    pay = oly_container.parse(open(args.bin, 'rb').read())[0].payload
    payload = '.tmp/%s.payload.bin' % os.path.splitext(
        os.path.basename(args.bin))[0]
    open(payload, 'wb').write(pay)
    print('payload %s：%d 字节（来自 %s）' % (payload, len(pay), args.bin))

    stock = open(STOCK_IMAGE, 'rb').read()
    tables = []
    for name, key, tab in GROUPS:
        off = tab - BASE
        t = list(struct.unpack('<256H', pay[off:off + SLOT]))
        if pay[off:off + SLOT] == b'\xff' * SLOT:
            print('  ✘ 表 %s @%#x 还是 FF' % (name, tab))
        if pay[off:off + SLOT] != bytes(struct.pack('<256H', *t)):
            print('  ✘ 表 %s @%#x 读回来不一致' % (name, tab))
        tables.append(t)
    print('表 t[46] = ' + ' / '.join('%d' % t[46] for t in tables))

    ok = True
    lines = []

    print('\n== 判据 1：四个目标 key（各走各的表）')
    res = []
    for i, (name, key, tab) in enumerate(GROUPS):
        d = run(STOP, key, payload)
        got = words(d, DEST, 256)
        exp = tables[i]
        ptr = ptr_of(d)
        good = (got == exp) and (ptr == STOCK_LUT)
        ok &= good
        res.append(got)
        print('[%s] key=%08X 表 @%#x' % (name, key, tab))
        print('   dest+0x000 前 8：%s' % ' '.join('%d' % v for v in got[:8]))
        print('   %s 256 个 u32 == 该表零扩展；%s (S+0x1230) = %#x（应为预置 %#x，未写）'
              % ('✔' if got == exp else '✘',
                 '✔' if ptr == STOCK_LUT else '✘', ptr, STOCK_LUT))
        lines.append('%-14s key=%08X tab=%#x dest[0:8]=%s  %s'
                     % (name, key, tab, got[:8], 'PASS' if good else 'FAIL'))
    pairs = [(GROUPS[i][0], GROUPS[j][0]) for i in range(len(res))
             for j in range(i + 1, len(res)) if res[i] == res[j]]
    if pairs:
        ok = False
        print('✘ 有完全相同的两组：%s' % pairs)
    else:
        print('✔ 四组两两不同（最小差异 %d 个位置）' % min(
            sum(1 for x, y in zip(res[i], res[j]) if x != y)
            for i in range(len(res)) for j in range(i + 1, len(res))))

    print('\n== 判据 1b（未命中）：非目标 key 必须原厂原样')
    d = run(STOP, NON_TARGET, payload)
    got = words(d, DEST, 256)
    ptr = ptr_of(d)
    fb = (got == [0x100 + i for i in range(256)]) and ptr == STOCK_LUT
    ok &= fb
    print('[fallback] key=%08X dest 前 8：%s；(S+0x1230) = %#x  %s'
          % (NON_TARGET, ' '.join('%d' % v for v in got[:8]), ptr,
             '✔' if fb else '✘'))
    lines.append('%-14s key=%08X tab=- dest[0:8]=%s  %s'
                 % ('fallback', NON_TARGET, got[:8], 'PASS' if fb else 'FAIL'))

    print('\n== 判据 2：零副作用（非目标 key + 原厂指针指向原厂槽 0..3）')
    print('   判据：dest+0x000..0x3FF 必须与**原厂镜像**逐值相同')
    for i in range(4):
        slot_addr = STOCK_SLOT + i * SLOT
        dh = run(STOP, NON_TARGET, payload, lut=slot_addr)
        ds = run(STOP, NON_TARGET, STOCK_IMAGE, lut=slot_addr)
        vh, vs = words(dh, DEST, 256), words(ds, DEST, 256)
        good = (vh == vs) and ptr_of(dh) == ptr_of(ds) == slot_addr
        ok &= good
        print('   指针 = 原厂槽 %d @%#x：dest 与原厂 %s；指针 %s'
              % (i, slot_addr, '逐值相同' if vh == vs else '不同 ✘',
                 '未被改' if ptr_of(dh) == slot_addr else '被改成 %#x ✘' % ptr_of(dh)))
        print('     dest 前 8：B14 %s | 原厂 %s'
              % (' '.join('%d' % v for v in vh[:8]),
                 ' '.join('%d' % v for v in vs[:8])))
        lines.append('%-14s key=%08X lut=slot%d dest[0:8]=%s  %s'
                     % ('zero-side', NON_TARGET, i, vh[:8],
                        'PASS' if good else 'FAIL'))

    print('\n== 判据 3：执行后 (S+0x1230) 与原厂一致（我们根本不写它）')
    for i, (name, key, tab) in enumerate(GROUPS):
        dh = run(STOP, key, payload)
        ds = run(STOP, key, STOCK_IMAGE)
        same = ptr_of(dh) == ptr_of(ds)
        ok &= same
        print('   %-14s key=%08X  hook %#x / 原厂 %#x  %s'
              % (name, key, ptr_of(dh), ptr_of(ds), '✔' if same else '✘'))
        lines.append('%-14s key=%08X s1230=%#x/%#x  %s'
                     % ('state', key, ptr_of(dh), ptr_of(ds),
                        'PASS' if same else 'FAIL'))

    print('\n== 判据 4：寄存器守恒（插点边界 0x60479A3A 与停点 0x60479A77）')
    for stop, label in ((LOOP_HEAD, '进循环前'), (STOP, '停点')):
        dh = regs(run(stop, 0x01010079, payload))
        ds = regs(run(stop, 0x01010079, STOCK_IMAGE))
        must = ['D1', 'D2', 'D3', 'A1', 'A3', 'SP', 'PSW']
        diff = [n for n in must if dh[n] != ds[n]]
        ok &= not diff
        print('   %s@%#x：%s %s；A2 %#x vs %#x（允许不同）、D0 %#x vs %#x'
              % (label, stop, '/'.join(must), '✔ 相同' if not diff else '✘ ' + str(diff),
                 dh['A2'], ds['A2'], dh['D0'], ds['D0']))
        lines.append('%-14s stop=%#x %s  %s'
                     % ('regs', stop, '/'.join(must), 'PASS' if not diff else 'FAIL'))

    print('\n== 镜像核对（直接读包，不经模拟器）')
    ins = pay[INSERT - BASE:INSERT - BASE + 5]
    print('   插点 = %s  %s' % (ins.hex(), '✔' if ins == ENTRY else '✘'))
    ok &= ins == ENTRY
    stub = pay[STUB - BASE:STUB - BASE + 66]
    print('   桩 @%#x = %d B，前 8 字节 %s' % (STUB, len(stub), stub[:8].hex()))
    loop = pay[LOOP_TAB - BASE:LOOP_TAB - BASE + 32]
    exp_loop = b''.join(struct.pack('<II', k, t) for _, k, t in GROUPS)
    print('   对数组 @%#x %s' % (LOOP_TAB, '✔' if loop == exp_loop else '✘'))
    ok &= loop == exp_loop
    for i in range(4):
        a = STOCK_SLOT + i * SLOT
        same = pay[a - BASE:a - BASE + SLOT] == stock[a - BASE:a - BASE + SLOT]
        ok &= same
        print('   原厂槽 %d @%#x 与原厂逐字节相同  %s' % (i, a, '✔' if same else '✘'))
    pool = pay[POOL - BASE:POOL - BASE + 672]
    same = pool == stock[POOL - BASE:POOL - BASE + 672]
    ok &= same
    print('   168 个池指针与原厂相同  %s' % ('✔' if same else '✘'))

    open('.tmp/verify_look4b.txt', 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('\nVERDICT  %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
