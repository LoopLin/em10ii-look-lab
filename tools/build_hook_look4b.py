# -*- coding: utf-8 -*-
"""B14_look4：按 picture mode 选 tone 曲线的代码 hook（E-M10 II / MN10300）。

B13 在真机上炸了（开机自检循环 / 取景全黑）：它的桩在 0x60DE0000、表在
0x60E00000，而段加载器只把字节拷到 0x60DA5000 为止 —— 那两个地址的字节
**根本不进 RAM**，跳过去就是执行未映射内存。B14 的重做：

1. 桩 → 段 3 最后一个 FF 洞 `0x60DA4E08`（原厂 504 B 全 FF，到 0x60DA4FF0，
   段边界 0x60DA5000）；54 B 的桩 + 32 B 的 (key,表) 对数组都塞在这个洞里；
2. 四张 tone 表 → tone 池里**没有任何指针引用**的死槽（全镜像扫描 u32 引用
   为 0），必然已加载、必然可写：
       reala_ace     -> 0x605DDD44 (slot 45)
       astia         -> 0x605DDF44 (slot 46)
       eterna        -> 0x605DE144 (slot 47)
       bleach_bypass -> 0x605DEB44 (slot 52)
   原厂槽 0..3（12 个池指针的目标）**一个字节都不动**；
3. `0x60600920` 起 168 个池指针 **保持原样**（本包不碰它们）；
4. 插点 `0x60479A27` 的 5 字节（原 `FA D0 30 12 70`）换成 5 字节远跳
   `DC <disp32>` → 桩 `0x60DA4E08`；
5. 桩只替换 `a2`（tone 表基址）后 `jmp 0x60479A3A`，**不写任何内存** ——
   尤其不写 `*(S+0x1230)`（原厂只读它，另有一条登记路径会消费/清零它）。
   未命中时退回原厂语义：`a0=S+0x1230`、`d0=*(a0)`、`jmp 0x60479A32`。

桩的汇编源在 `tools/hooks/look4b_stub.s`；本脚本用项目自带的
`tools/mn10300_dis.py` 反汇编自产字节逐条核对落点，再出包，再用
`oly_container.parse` 独立回读，最后逐字节 diff 确认**只动了 7 段**。

用法（从工作区根）：
    python tools/build_hook_look4b.py --out build/B14_look4.BIN
    python tools/build_hook_look4b.py --patch-only
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import struct
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, 'reconfigure'):
        _s.reconfigure(encoding='utf-8', errors='replace')

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
STOCK_PAYLOAD = 'analysis/00_0100_40300020.decoded.bin'

SLOT_SZ = 0x200
STOCK_SLOT = 0x605D8344            # 原厂槽 0（必须保持不动）
POOL_RAM = 0x60600920
POOL_N = 168

INSERT_RAM = 0x60479A27
INSERT_ORIG = bytes.fromhex('fad0301270')   # add 0x1230,a0 / mov (a0),d0
RESUME_LUT = 0x60479A3A                      # setlb：拷贝循环首
RESUME_STOCK = 0x60479A32                    # cmp 0x0,d0：原厂判空

# 桩位置档案：hole = B14/B15（段 3 末尾 FF 洞）；slot53/slot71 = 搬进 tone 池
PROFILES = {
    'hole':   dict(STUB_RAM=0x60DA4E08, HOLE_END=0x60DA5000,
                   HOLE_KIND='ff',  HOLE_NAME='段 3 末尾 FF 洞'),
    'slot53': dict(STUB_RAM=0x605DED44, HOLE_END=0x605DEF44,
                   HOLE_KIND='slot', HOLE_NAME='tone 池 slot 53'),
    'slot71': dict(STUB_RAM=0x605E1144, HOLE_END=0x605E1344,
                   HOLE_KIND='slot', HOLE_NAME='tone 池 slot 71'),
}
for _p in PROFILES.values():          # 对数组统一放在桩后 0x58（8 字节对齐）
    _p['LOOP_TAB'] = _p['STUB_RAM'] + 0x58
STUB_RAM = PROFILES['hole']['STUB_RAM']
HOLE_END = PROFILES['hole']['HOLE_END']
HOLE_KIND = PROFILES['hole']['HOLE_KIND']
HOLE_NAME = PROFILES['hole']['HOLE_NAME']
BEQ_TGT = STUB_RAM + 0x30          # 命中：mov (4,a2),a2
BNE_TGT = STUB_RAM + 0x13          # 循环首：mov (a2),d3
LOOP_TAB = PROFILES['hole']['LOOP_TAB']   # 桩里 4 个 (key, 表地址) 对
LOOP_END = LOOP_TAB + 8 * 4
STUB_SRC = 'tools/hooks/look4b_stub.s'
WORK = '.tmp/look4b'
TC = '.tmp/olya/toolchains/mn10300-elf/bin'
LOOK_DIR = '.tmp/looks_beta'
PATCH_FILE = '.tmp/B14_look4.patch'

# 表落点两套（同一份 stub / 同一个桩地址，只换表放哪）：
#  lead   = lead 指定的 tone 池槽 45/46/47/52
#  fftail = 段 1 尾部 0x60661FCC..0x60673000 那段 FF 填充里（0x60663000 起）
TABLESETS = {
    'lead': [('reala_ace', 0x01010079, 0x605DDD44),
             ('astia', 0x01020079, 0x605DDF44),
             ('eterna', 0x01030079, 0x605DE144),
             ('bleach_bypass', 0x01010066, 0x605DEB44)],
    'fftail': [('reala_ace', 0x01010079, 0x60663000),
               ('astia', 0x01020079, 0x60663200),
               ('eterna', 0x01030079, 0x60663400),
               ('bleach_bypass', 0x01010066, 0x60663600)],
    # B16 datapool：四张表写进 0x605Dxxxx 固件数据区，slot 8/9/10/11（ART 用）
    'dpool': [('reala_ace', 0x01010079, 0x605D9344),
              ('astia', 0x01020079, 0x605D9544),
              ('eterna', 0x01030079, 0x605D9744),
              ('bleach_bypass', 0x01010066, 0x605D9944)],
    # B15 变量隔离：一个新数据区都不写，四档直接指原厂已有槽 0/2/4/6
    'stock': [('reala_ace', 0x01010079, 0x605D8344),
              ('astia', 0x01020079, 0x605D8744),
              ('eterna', 0x01030079, 0x605D8B44),
              ('bleach_bypass', 0x01010066, 0x605D8F44)],
}
GROUPS = TABLESETS['lead']

# 反证用：lead 认为“无引用”的四个槽，实际被这些代码立即数引用
LEAD_SLOTS = [('reala_ace', 0x605DDD44), ('astia', 0x605DDF44),
              ('eterna', 0x605DE144), ('bleach_bypass', 0x605DEB44)]


# ---------------------------------------------------------------- stub 汇编
def assemble_stub(force: bool = False) -> bytes:
    os.makedirs(WORK, exist_ok=True)
    tag = '%08x' % STUB_RAM
    obj = os.path.join(WORK, 'look4b_stub_%s.o' % tag)
    elf = os.path.join(WORK, 'look4b_stub_%s.elf' % tag)
    bin_ = os.path.join(WORK, 'look4b_stub_%s.bin' % tag)
    need = force or not os.path.exists(bin_)
    if not need:
        need = os.path.getmtime(bin_) < os.path.getmtime(STUB_SRC)
    if need:
        asm = os.path.join(TC, 'mn10300-elf-as.exe')
        ld = os.path.join(TC, 'mn10300-elf-ld.exe')
        oc = os.path.join(TC, 'mn10300-elf-objcopy.exe')
        for exe in (asm, ld, oc):
            if not os.path.exists(exe):
                raise SystemExit('缺工具链：' + exe)
        env = dict(os.environ)
        env.update(TMP='.tmp/gtmp', TEMP='.tmp/gtmp', TMPDIR='.tmp/gtmp')
        os.makedirs('.tmp/gtmp', exist_ok=True)
        runs = [
            [asm, '-alh=' + os.path.join(WORK, 'look4b_stub_%s.lst' % tag),
             '-o', obj, STUB_SRC],
            [ld, '-Ttext', hex(STUB_RAM), '-e', 'look4b_entry', '-o', elf, obj],
            [oc, '-O', 'binary', elf, bin_],
        ]
        for cmd in runs:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
            if r.returncode:
                sys.stderr.write(r.stdout or '')
                sys.stderr.write(r.stderr or '')
                raise SystemExit('汇编失败：' + cmd[0])
    b = open(bin_, 'rb').read()
    # .s 里对数组的地址是字面量：按 STUB 档案改写两个 imm32
    pat_tab = struct.pack('<I', 0x60DA4E60)
    pat_end = struct.pack('<I', 0x60DA4E80)
    if LOOP_TAB != 0x60DA4E60:
        if b.count(pat_tab) != 1 or b.count(pat_end) != 1:
            raise SystemExit('桩里对数组立即数不是各 1 处，拒改')
        b = b.replace(pat_tab, struct.pack('<I', LOOP_TAB))
        b = b.replace(pat_end, struct.pack('<I', LOOP_END))
    return b


def disassemble(path: str, base: int, length: int):
    """用项目自带的反汇编器（tools/mn10300_dis.py）列一遍。"""
    r = subprocess.run([sys.executable, 'tools/mn10300_dis.py', path,
                        '--base', hex(base), '--len', str(length)],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    if r.returncode:
        raise SystemExit('mn10300_dis 失败：' + (r.stderr or '')[:400])
    out = []
    for ln in r.stdout.splitlines():
        m = re.match(r'\s*([0-9A-F]{8})\s+((?:[0-9A-F]{2} ?)+?)\s{2,}(\S.*)$', ln)
        if m:
            out.append((int(m.group(1), 16), m.group(3).strip()))
    return out


def check_stub(stub: bytes) -> None:
    """逐条核对桩：读 key、算 a1/d1/d2、查对数组、未命中退回原厂、命中进循环。"""
    path = os.path.join(WORK, 'look4b_stub_check_%08x.bin' % STUB_RAM)
    open(path, 'wb').write(stub)
    lines = disassemble(path, STUB_RAM, len(stub))
    if not lines:
        raise SystemExit('反汇编为空')
    print('  --- 桩反汇编（tools/mn10300_dis.py @ %#x，%d 字节）---'
          % (STUB_RAM, len(stub)))
    for a, t in lines:
        print('   %08X  %s' % (a, t))
    if b'\xff' * 6 in stub:
        raise SystemExit('桩里有 0xFF 空洞，链接地址可能不对')
    want = [
        'mov (12,SP),a2',                 # a2 = O
        'mov (48,a2),d0',                 # d0 = u32 filter id (O+0x30)
        'mov d3,a1',                      # a1 = saved d3（a1 在此处已死）
        'mov 0x%x,a2' % LOOP_TAB,
        'mov 0x%x,a0' % LOOP_END,
        'mov (a2),d3',
        'cmp d3,d0',
        'beq (%#x,PC)' % BEQ_TGT,
        'add 0x8,a2',
        'cmp a0,a2',
        'bne (%#x,PC)' % BNE_TGT,
        # 未命中：原厂语义 + 复原 d3/a1/d2
        'mov (4,SP),a0',
        'add 0x1230,a0',
        'mov (a0),d0',
        'mov a1,d3',
        'mov d2,a1',
        'add 0x400,d2',
        'jmp 0x%x' % RESUME_STOCK,
        # 命中：换 a2 后进拷贝循环
        'mov (4,a2),a2',
        'mov a1,d3',
        'mov d2,a1',
        'add 0x400,d2',
        'mov (8,SP),d1',
        'jmp 0x%x' % RESUME_LUT,
    ]
    got = [t for _, t in lines]
    got[7] = 'beq (%#x,PC)' % int(re.search(r'0x([0-9a-f]+)', got[7]).group(1), 16)
    got[10] = 'bne (%#x,PC)' % int(re.search(r'0x([0-9a-f]+)', got[10]).group(1), 16)
    if got != want:
        for i in range(max(len(got), len(want))):
            g = got[i] if i < len(got) else '<缺>'
            w = want[i] if i < len(want) else '<多>'
            if g != w:
                raise SystemExit('桩第 %d 条不符：得 %r，期望 %r' % (i, g, w))
    # 两条远跳的位移必须相对指令自身地址
    for a, t in lines:
        if not t.startswith('jmp 0x'):
            continue
        off = a - STUB_RAM
        disp = struct.unpack('<i', stub[off + 1:off + 5])[0]
        tgt = int(t.split('0x')[1], 16)
        if a + disp != tgt:
            raise SystemExit('远跳位移算错：%#x + %d != %#x' % (a, disp, tgt))
    if STUB_RAM + len(stub) > LOOP_TAB:
        raise SystemExit('桩长 %d B 压到对数组 0x%x 了' % (len(stub), LOOP_TAB))
    print('  桩核对：%d 字节 / %d 条，两条远跳落点 %#x、%#x ✔，'
          '对数组 @%#x 未越界 ✔'
          % (len(stub), len(lines), RESUME_STOCK, RESUME_LUT, LOOP_TAB))


def entry_bytes() -> bytes:
    """插点 5 字节：DC + disp32，位移基准 = 指令自身地址 0x60479A27。"""
    disp = STUB_RAM - INSERT_RAM
    eb = b'\xdc' + struct.pack('<i', disp)
    p = os.path.join(WORK, 'entry_probe.bin')
    open(p, 'wb').write(eb)
    lines = disassemble(p, INSERT_RAM, len(eb))
    if not lines or lines[0][1] != 'jmp 0x%x' % STUB_RAM:
        raise SystemExit('插点远跳落点不对：%r' % (lines,))
    print('  插点 %#x：%s → %s（%s）'
          % (INSERT_RAM, eb.hex(), lines[0][1], 'disp=%+#x' % disp))
    return eb


def lookup_bytes() -> bytes:
    """桩里查的数组：4 × (key u32, tone 表 u32)，小端。"""
    out = b''
    for look, key, tab in GROUPS:
        out += struct.pack('<II', key, tab)
    assert len(out) == LOOP_END - LOOP_TAB
    return out


# ---------------------------------------------------------------- tone 表
def load_tone(look: str) -> list:
    p = os.path.join(LOOK_DIR, look + '.json')
    if not os.path.exists(p):
        raise SystemExit('缺 look 文件：' + p)
    with io.open(p, encoding='utf-8') as f:
        spec = json.load(f)
    tone = [int(v) for v in spec['tone']]
    if len(tone) != 256:
        raise SystemExit('%s: tone 长度 %d != 256' % (look, len(tone)))
    if any(v < 0 or v > 1023 for v in tone):
        raise SystemExit('%s: tone 越界（0..1023）' % look)
    if any(b < a for a, b in zip(tone, tone[1:])):
        raise SystemExit('%s: tone 非单调' % look)
    return tone


def tone_bytes(tone) -> bytes:
    return b''.join(int(v).to_bytes(2, 'little') for v in tone)


def ref_addrs(payload: bytes, addr: int):
    """整镜像扫这个 u32 地址被引用的所有位置（立即数也算，判死槽用）。"""
    pat = struct.pack('<I', addr)
    out, i = [], payload.find(pat)
    while i >= 0:
        out.append(BASE + i)
        i = payload.find(pat, i + 1)
    return out


def ref_count(payload: bytes, addr: int) -> int:
    return len(ref_addrs(payload, addr))


# ---------------------------------------------------------------- 出包
def build_patch(tables) -> str:
    L = ['# B14 look4: picture-mode tone hook -- stock-safe stub + 4 tables in dead tone slots']
    L.append('# stub 0x%08x (%d B) + (key,table) pairs 0x%08x (%d B), inside the FF hole'
             % (STUB_RAM, len(STUB), LOOP_TAB, LOOP_END - LOOP_TAB))
    L.append('# hole 0x60da4e08..0x60da5000 is stock FF; segment loader stops at 0x60da5000')
    L.append('# tables go to unreferenced tone slots (u32 reference scan = 0):')
    for look, key, tab in GROUPS:
        L.append('#   %-14s key=%08x -> 0x%08x' % (look, key, tab))
    L.append('# stock tone slots 0x605d8344+i*0x200 are NOT written; 168 pool pointers untouched')
    if tables:
        for i, ((look, key, tab), t_b) in enumerate(zip(GROUPS, tables)):
            L.append('# table %d <- %s' % (i, look))
            L.append('ram 0x%x=%s' % (tab, t_b.hex()))
    else:
        L.append('# NO tone table is written: the four groups point at stock slots 0/2/4/6')
        for look, key, tab in GROUPS:
            L.append('#   %-14s key=%08x -> stock slot 0x%08x (left untouched)'
                     % (look, key, tab))
    L.append('# (key,table) pairs @ 0x%x' % LOOP_TAB)
    L.append('ram 0x%x=%s' % (LOOP_TAB, LOOKUP.hex()))
    L.append('# insert point 0x60479a27: fad0301270 -> dc <disp32> (stub 0x%x)' % STUB_RAM)
    L.append('ram 0x%x=%s' % (INSERT_RAM, ENTRY.hex()))
    L.append('# stub @ 0x%x (%d bytes)' % (STUB_RAM, len(STUB)))
    L.append('ram 0x%x=%s' % (STUB_RAM, STUB.hex()))
    txt = '\n'.join(L) + '\n'
    txt.encode('ascii')            # patch 文件注释必须是 ASCII
    return txt


def diff_offsets(a: bytes, b: bytes):
    n = min(len(a), len(b))
    return [i for i in range(n) if a[i] != b[i]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--stub', choices=sorted(PROFILES), default='hole',
                    help='桩位置档案：hole=段3末尾FF洞, slot53/slot71=tone 池槽')
    ap.add_argument('--tables', choices=['lead', 'fftail', 'stock', 'dpool'], default='lead',
                    help='lead = tone 池槽 45/46/47/52；fftail = 段 1 尾部 FF 填充')
    ap.add_argument('--out', default=None)
    ap.add_argument('--patch-only', action='store_true')
    ap.add_argument('--force-asm', action='store_true')
    args = ap.parse_args()

    global STUB, ENTRY, LOOKUP, GROUPS, PATCH_FILE
    GROUPS = TABLESETS[args.tables]
    if args.out is None:
        args.out = {'lead': 'build/B14_look4.BIN',
                    'fftail': 'build/B14b_look4.BIN',
                    'stock': 'build/B15_hook_stocktables.BIN',
                    'dpool': 'build/B16_hook_datapool.BIN'}[args.tables]
        if args.stub == 'slot53' and args.tables == 'dpool':
            args.out = 'build/B16_hook_datapool.BIN'
        elif args.stub == 'slot53' and args.tables == 'stock':
            args.out = 'build/B16_hook_slot53.BIN'
        elif args.stub == 'slot71' and args.tables == 'stock':
            args.out = 'build/B16b_hook_slot71.BIN'
    globals().update(PROFILES[args.stub])
    globals()['LOOP_END'] = LOOP_TAB + 8 * 4
    globals()['BEQ_TGT'] = STUB_RAM + 0x30
    globals()['BNE_TGT'] = STUB_RAM + 0x13
    PATCH_FILE = '.tmp/%s.patch' % os.path.splitext(os.path.basename(args.out))[0]
    print('表落点集合 = %s -> %s（patch %s）' % (args.tables, args.out, PATCH_FILE))
    print('== 1. 桩（0x%x，%s）' % (STUB_RAM, HOLE_NAME))
    STUB = assemble_stub(args.force_asm)
    check_stub(STUB)
    ENTRY = entry_bytes()
    LOOKUP = lookup_bytes()
    if LOOP_END > HOLE_END:
        raise SystemExit('桩+对数组越过段边界 0x%x' % HOLE_END)
    print('  桩 %d B + 对数组 %d B = %d B，洞 0x%x..0x%x（%d B）✔'
          % (len(STUB), len(LOOKUP), len(STUB) + len(LOOKUP), STUB_RAM,
             HOLE_END, HOLE_END - STUB_RAM))

    stock_bytes = open(STOCK_PAYLOAD, 'rb').read()
    if args.tables == 'stock':
        print('== 2. 四档指向原厂已有槽（本包不写任何 tone 表）')
        tables = []
        for look, key, tab in GROUPS:
            t = list(struct.unpack('<256H', stock_bytes[tab - BASE:tab - BASE + SLOT_SZ]))
            print('  %-14s key=%08X -> %#x  t22=%-4d t46=%-4d t64=%-4d t128=%-4d t255=%-4d'
                  % (look, key, tab, t[22], t[46], t[64], t[128], t[255]))
            tables.append(t)
    else:
        print('== 2. 四张 tone 表（落到选定区域）')
        tones = []
        for look, key, tab in GROUPS:
            t = load_tone(look)
            tones.append(t)
            print('  %-14s key=%08X -> %#x  t22=%-4d t46=%-4d t64=%-4d t128=%-4d t255=%-4d'
                  % (look, key, tab, t[22], t[46], t[64], t[128], t[255]))
        tables = tones
    for i in range(len(tables)):
        for j in range(i + 1, len(tables)):
            if tables[i] == tables[j]:
                raise SystemExit('组 %d 与组 %d 的表完全相同' % (i, j))
    if args.tables != 'stock':
        tables = [tone_bytes(t) for t in tables]

    print('== 3. 原厂 payload 预检')
    stock = open(STOCK_PAYLOAD, 'rb').read()
    ok = True
    off_ins = INSERT_RAM - BASE
    got = stock[off_ins:off_ins + 5]
    print('  插点原字节 %s（期望 %s）%s' % (got.hex(), INSERT_ORIG.hex(),
                                          '✔' if got == INSERT_ORIG else '✘'))
    ok &= got == INSERT_ORIG
    region = stock[STUB_RAM - BASE:HOLE_END - BASE]
    if HOLE_KIND == 'ff':
        print('  FF 洞 0x%x..0x%x：%d B 全 FF %s'
              % (STUB_RAM, HOLE_END, len(region),
                 '✔' if region == b'\xff' * len(region) else '✘'))
        ok &= region == b'\xff' * len(region)
    else:
        first_nz = next((i for i, v in enumerate(region) if v), None)
        print('  tone 槽 0x%x..0x%x：%d B，首个非零字节 +%s，槽内引用 %s'
              % (STUB_RAM, HOLE_END, len(region),
                 ('0x%x' % first_nz) if first_nz is not None else '（全零）',
                 ', '.join('%#x' % a for a in ref_addrs(stock, STUB_RAM)) or '无'))
    print('  表落点体检：')
    if args.tables == 'stock':
        for look, key, tab in GROUPS:
            hits = ref_addrs(stock, tab)
            inbank = 0x605D8344 <= tab < 0x605D8344 + 44 * SLOT_SZ
            good = bool(hits) and inbank
            print('    %-14s %#x -> 原厂槽、引用 %s、在 44 张被引用的槽内 %s  %s'
                  % (look, tab, ', '.join('%#x' % h for h in hits),
                     '是' if inbank else '否', '✔' if good else '✘'))
            ok &= good
    elif args.tables == 'dpool':
        POOL_LO, POOL_HI = 0x60600920, 0x60600920 + 4 * 168
        for look, key, tab in GROUPS:
            hits = ref_addrs(stock, tab)
            from_pool = bool(hits) and all(POOL_LO <= h < POOL_HI for h in hits)
            in_data = tab < 0x60662000
            good = from_pool and in_data
            print('    %-14s %#x -> 引用 %d 处（全在指针池 %s）、在固件数据区 %s  %s'
                  % (look, tab, len(hits), '是' if from_pool else '否',
                     '是' if in_data else '否', '✔' if good else '✘'))
            ok &= good
    else:
        for look, key, tab in GROUPS:
            hits = ref_addrs(stock, tab)
            ff = stock[tab - BASE:tab - BASE + SLOT_SZ] == b'\xff' * SLOT_SZ
            if args.tables == 'fftail':
                good = (not hits) and ff
                print('    %-14s %#x -> 引用 %d 处、原厂 FF %s  %s'
                      % (look, tab, len(hits), '是' if ff else '否',
                         '✔' if good else '✘'))
            else:
                good = (len(hits) == 1 and 0x604B8A00 <= hits[0] < 0x604B8B40)
                print('    %-14s %#x -> 引用 %s  %s（该槽被 tone 登记路径使用）'
                      % (look, tab, ', '.join('%#x' % h for h in hits) or '无',
                         '与预期一致' if good else '✘'))
            ok &= good
    for i in range(3):
        a = STOCK_SLOT + i * SLOT_SZ
        n = ref_count(stock, a)
        print('    [对照] 原厂槽 %d %#x -> %d 处引用（应 >0）%s'
              % (i, a, n, '✔' if n > 0 else '✘'))
        ok &= n > 0
    if not ok:
        return 1

    print('== 4. patch 文件（注释全 ASCII）')
    txt = build_patch(None if args.tables == 'stock' else tables)
    with io.open(PATCH_FILE, 'w', encoding='utf-8', newline='\n') as f:
        f.write(txt)
    print('  %s（%d 行，%d 字节）' % (PATCH_FILE, txt.count('\n'), len(txt)))
    if args.patch_only:
        return 0
    if os.path.exists(args.out):
        raise SystemExit('拒绝覆盖已有文件：' + args.out)

    print('== 5. mkpatch')
    cmd = [sys.executable, 'tools/mkpatch.py', SRC, '--out', args.out,
           '--patch-file', PATCH_FILE]
    print('  ' + ' '.join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    print('\n'.join('   ' + s for s in (r.stdout or '').strip().splitlines()[-12:]))
    if r.returncode:
        sys.stderr.write(r.stderr or '')
        return r.returncode

    print('== 6. 独立回读（oly_container.parse）')
    sys.path.insert(0, 'tools')
    import oly_container  # noqa: E402

    b0 = oly_container.parse(open(args.out, 'rb').read())[0].payload
    ok = True
    for (look, key, tab), t_b in zip(GROUPS, tables):
        tb = t_b if isinstance(t_b, (bytes, bytearray)) else tone_bytes(t_b)
        got = b0[tab - BASE:tab - BASE + SLOT_SZ]
        good = got == tb
        ok &= good
        print('  表 %-14s @%#x %s  (t46=%d)'
              % (look, tab, '✔' if good else '✘', int.from_bytes(tb[92:94], 'little')))
    got = b0[LOOP_TAB - BASE:LOOP_TAB - BASE + len(LOOKUP)]
    print('  对数组 @%#x %s' % (LOOP_TAB, '✔' if got == LOOKUP else '✘'))
    ok &= got == LOOKUP
    got = b0[off_ins:off_ins + 5]
    print('  插点 @%#x = %s %s' % (INSERT_RAM, got.hex(), '✔' if got == ENTRY else '✘'))
    ok &= got == ENTRY
    got = b0[STUB_RAM - BASE:STUB_RAM - BASE + len(STUB)]
    print('  桩 @%#x = %d 字节 %s' % (STUB_RAM, len(STUB), '✔' if got == STUB else '✘'))
    ok &= got == STUB
    for i in range(4):
        a = STOCK_SLOT + i * SLOT_SZ
        same = b0[a - BASE:a - BASE + SLOT_SZ] == stock[a - BASE:a - BASE + SLOT_SZ]
        ok &= same
        print('  原厂槽 %d @%#x 与原厂逐字节相同 %s' % (i, a, '✔' if same else '✘'))
    if args.tables == 'stock':
        for look, key, tab in GROUPS:
            same = b0[tab - BASE:tab - BASE + SLOT_SZ] == stock[tab - BASE:tab - BASE + SLOT_SZ]
            ok &= same
            print('  目标原厂槽 %-14s @%#x 未改动 %s' % (look, tab, '✔' if same else '✘'))
    got_pool = b0[POOL_RAM - BASE:POOL_RAM - BASE + 4 * POOL_N]
    same = got_pool == stock[POOL_RAM - BASE:POOL_RAM - BASE + 4 * POOL_N]
    print('  168 个池指针原样未动 %s' % ('✔' if same else '✘'))
    ok &= same

    print('== 7. 与官方包逐字节对比（差异只许落在预期段）')
    want = ([] if args.tables == 'stock' else
            [(tab - BASE, SLOT_SZ, '表 ' + look) for (look, _, tab) in GROUPS])
    want.append((INSERT_RAM - BASE, 5, '插点 0x60479a27'))
    want.append((STUB_RAM - BASE, len(STUB), '桩 0x%x' % STUB_RAM))
    want.append((LOOP_TAB - BASE, len(LOOKUP), '对数组 0x%x' % LOOP_TAB))
    diffs = diff_offsets(stock, b0)
    outside = []
    for (s, n, nm) in want:
        cnt = sum(1 for o in diffs if s <= o < s + n)
        print('  %-22s @%#x +%#x：%3d/%3d 字节与库存不同' % (nm, s + BASE, n, cnt, n))
    for o in diffs:
        if not any(s <= o < s + n for (s, n, _) in want):
            outside.append(o)
    if outside:
        print('  ✘ 有 %d 个差异字节落在预期之外，首个 @%#x'
              % (len(outside), outside[0] + BASE))
        ok = False
    else:
        print('  ✔ 全部 %d 个差异字节都在这 7 段里，其余 %d 字节与原厂一致'
              % (len(diffs), len(stock) - len(diffs)))
    size = os.path.getsize(args.out)
    same = size == os.path.getsize(SRC)
    print('== 产物 %s  %d B（等于官方包大小：%s）' % (args.out, size, same))
    return 0 if (ok and same) else 1


STUB = b''
ENTRY = b''
LOOKUP = b''

if __name__ == '__main__':
    sys.exit(main())
