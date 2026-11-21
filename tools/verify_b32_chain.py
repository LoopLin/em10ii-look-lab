#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b32_chain.py —— B32_looks_guard 离线全链验证：派发 → 算 k → 写 stash → 上传器读 stash → 选 tone 表。

链的设计（见 tools/build_beta_stash.py、docs/firmware/beta/B32-*.md）：
  派发器 0x604B8098 在 0x604B8107..0x604B811D 算判别量
      key = (u8(ctx+8)<<24) | (u8(ctx+9)<<16) | u16(ctx+0x0A)
  0x604B8129 的 5 字节被换成远跳 → SD 桩 0x605DED44：
      k = ((key>>24)&3)<<3 | (key&7)      （= ((b0&3)<<3) | (mode&7)）
      把 k 写进 stash 0x605DEDC0，然后跳回派发器续跑点
  上传器 0x60479960 在 0x60479A27 的 5 字节被换成远跳 → SU 桩 0x605DEDD0：
      读 stash 的 k；k 不在 1..4 → 原厂路径 a2 = *(ctx+0x1230)
      否则 a2 = 0x605D8344 + (slot_map[k]+0x15)*0x200 = tone 池 slot (21+k)
  上传器随后在 0x60479A3A.. 的循环里把 a2 指向的 256 个 16 bit 表项按 4 B 步长
  写进 dest(=A0)，所以"选没选对表"能从 dest 里量出来。

**为什么有一个页要映射成 r+w+x**：桩体与 stash（0x605DED44 / 0x605DEDD0 / 0x605DEDC0）都在
0x605DE000 这一页里。真机上 tone 池是 RAM——既可写、又会被人 jump 进去执行；而 libsim 的 code
段只读、rw/rwz 段不可执行。所以这一页必须用 permissions=7(r+w+x) 映射，才算等价于"真机的
可写页"。其余地址照原样：代码段 5(r+x)、栈/ctx 段 3(r+w)。这是**模拟器建模**，不改固件。

验收判据（每个用例都要全过才算"离线链闭合"）：
  C1 派发器真的走进 SD 桩          C2 SD 桩写出的 k == 期望 k
  C3 SD 桩回到派发器续跑点 0x604B8134
  C4 SU 桩的选表路径符合 k 的预期（k∈1..4 → look，否则 stock）
  C5 a2 落在预期的表上             C6 上传器拷出的表 == 预期表

用法：
  python tools/verify_b32_chain.py                     # mode=1..4 + mode=7（守卫用例）
  python tools/verify_b32_chain.py --case 3            # 只跑一个用例
  python tools/verify_b32_chain.py --json .tmp/b32.json
  python tools/verify_b32_chain.py --no-whatif         # 跳过"假如三处字节按意图改写"的对照实验
  python tools/verify_b32_chain.py --b0 0x01 --b1 0x01 # 换成真机那类 key 的高字节

环境：Windows；建议 PYTHONIOENCODING=utf-8。只读 build/ 里的包，不写固件、不刷机。
退出码：0 = 所有验收用例整链闭合；1 = 有闭合不了的用例（B32 当前就是 1）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import oly_container as oc  # noqa: E402

BASE = 0x60021020              # main 块解码后的加载地址
IMAGE_END = BASE + 0x1460000   # main 块长度（21,364,672 B）
ENGINE = ROOT / '.tmp' / 'olya' / 'toolchains' / 'mn103-gnu-build' / '.libs' / 'penf-mn103.exe'

REGS = ['D0', 'D1', 'D2', 'D3', 'A0', 'A1', 'A2', 'A3', 'SP', 'PC', 'PSW', 'MDR', 'LIR',
        'LAR', 'FAULT', 'Z', 'XY', 'R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7',
        'RAMB', 'RAR', 'U', 'VC', 'EXB', 'CRT', 'UNK']

TONE = 0x605D8344              # tone 池基址
SLOT = 0x200                   # 每张 tone 表 512 B
SLOT_MAP = 0x605DB744          # slot_map[k]（B32 里是 00 01 02 03 04）
N_HALF = 256                   # 上传器要拷的表项数
DEST_SPAN = 0x400              # 拷贝循环写到 dest+0x800；tone 表项占前 1024 B

STASH = 0x605DEDC0             # 存 k 的一字节
SD_RAM = 0x605DED44            # SD 桩（派发器侧）
SU_RAM = 0x605DEDD0            # SU 桩（上传器侧）
WX_PAGE = 0x605DE000           # 桩 + stash 所在的 4 KiB 页（真机 = 可写 RAM）

DISPATCH = 0x604B8098          # 派发器入口
SD_INSERT = 0x604B8129         # 被 5 字节远跳覆盖处（原 cmp 0x2000096,d2）
DISP_CONT = 0x604B8134         # 桩之后原厂续跑点（原 bls 的目标）
DISP_EPILOGUE = 0x604B831D     # ret —— 只有正常走完才会到这里
DISP_EXIT = 0x604B8320         # key 不合闸时的去向（不是函数出口，是另一条业务分支）
BROKEN_TARGET = 0x604DED63     # B32 里 SD 桩尾 jmp（disp=-0x100000）的实际落点
SD_TAIL = 0x605DED63           # SD 桩最后一条 jmp

UPLOAD = 0x60479960            # 上传器入口
SU_INSERT = 0x60479A27         # 被 5 字节远跳覆盖处（原 add 0x1230,a0）
SU_LOOK = 0x605DEE06           # SU 桩：选表之后的 jmp（look 路径）
SU_STOCK = 0x605DEE15          # SU 桩：原厂 a2 之后的 jmp（守卫路径）
COPY_LOOP = 0x60479A3A         # 拷贝循环首条（此刻 a2 = 选中的表）
UPLOAD_MERGE = 0x60479A77      # 两段拷贝之后的汇合点

DEST = 0x9E00A400              # A0：tone 段的目的地
O = 0x61E10000                 # A1：(12,SP)
S = 0x61E18000                 # *(O+0x0C) = ctx（本文件统一把它当 ctx）
STOCK_LUT_SLOT = 21            # 守卫路径故意指向一张不在 22..25 里的表
STOCK_LUT = TONE + STOCK_LUT_SLOT * SLOT
T2 = 0x61E19000                # *(ctx+0x420)：第二段 1024 B 的来源（内容无关）
CTX = 0x61E20000               # 派发器 ctx（A0）
SCRATCH = 0x61E28000           # 派发器写返回值用的指针槽
SP = 0x61E0F800
HEAP = '0x61e00000:0x40000'

# 段：(起始, 长度, permissions)。5=r+x、3=r+w、7=r+w+x。
CODE_A = (BASE, WX_PAGE - BASE, 5)
WX = (WX_PAGE, 0x1000, 7)
CODE_B = (WX_PAGE + 0x1000, IMAGE_END - (WX_PAGE + 0x1000), 5)
UPLOAD_HEAP = ('0x9e00a000:0x3000', '0x8fe00000:0x1000', '0x8ff00000:0x8000',
               '0xa1c21000:0x1000', '0xa1c2a000:0x1000')

# ---- 只在模拟器内存里用的"假如按意图改写"字节（**不是**改包！）-------------------
# SD 桩尾 jmp 的 disp32：mn10300 的远跳位移是相对**指令地址**算的（不是 pc+5）。
# 反证：B32 里的 dc0000f0ff 实际落在 0x604DED63 = SD_TAIL + (-0x100000)；B31 里的
# dcc193edff 落在 0x604B8124 = SD_TAIL + (-0x126C3F)。
def _jmp32(target: int) -> bytes:
    return b'\xdc' + struct.pack('<i', target - SD_TAIL)


SD_FLAGS_OLD = bytes.fromhex('fcca66000101')     # cmp 0x1010066,d2（B32 里的常数）
SD_FLAGS_NEW = bytes.fromhex('fcca96000002')     # cmp 0x2000096,d2（原厂那条被覆盖的 cmp）
# SU 守卫：`cmp 0x1,d0 / bcc guard` 里 C=(1>d0)，要的是"k==0 才守" ⇒ 应是 bcs(0xC4)，
# 而不是 bcc(0xC6)。一个字节的差：0x605DEDE1: C6 -> C4（位移 0x2A 双方都是 0x605DEE0B）。
SU_GUARD_ADDR = 0x605DEDE1
SU_GUARD_OLD = b'\xc6'
SU_GUARD_NEW = b'\xc4'


def p32(addr: int, val: int):
    return (addr, val.to_bytes(4, 'little'))


def patch_image(image: bytes, items) -> bytes:
    """在内存副本上打字节（仅用于对照实验，不落盘）。"""
    b = bytearray(image)
    for addr, data in items:
        b[addr - BASE:addr - BASE + len(data)] = data
    return bytes(b)


def sim(image: bytes, pc: int, stop: int, *, limit: int = 400000, bps=(), pokes=(),
        regs=None, extra_rwz=()) -> dict:
    """跑一次 libsim（PFMN002 协议，permissions 可自定义）。"""
    r = {n: 0 for n in REGS}
    r['PC'] = pc
    r['SP'] = SP
    r.update(regs or {})
    regions = [CODE_A, WX, CODE_B]
    for spec in (HEAP,) + tuple(extra_rwz):
        a, _, ln = spec.partition(':')
        regions.append((int(a, 0), int(ln, 0), 3))
    regions.sort()
    payload = bytearray(b'PFMN002\0' + struct.pack('<6I', limit, stop, 0, len(regions),
                                                   len(bps), 0))
    payload.extend(struct.pack('<%dI' % len(bps) + '32I', *bps, *[r[n] for n in REGS]))
    for start, length, mask in regions:
        data = bytearray(length) if mask == 3 else \
            bytearray(image[start - BASE:start - BASE + length])
        for pa, pb in pokes:
            if start <= pa and pa + len(pb) <= start + len(data):
                data[pa - start:pa - start + len(pb)] = pb
        payload.extend(struct.pack('<5I', start, len(data), mask, 1, 0xffffffff))
        payload.extend(bytes(data))
    p = subprocess.run([str(ENGINE)], input=payload, capture_output=True, timeout=600)
    if p.returncode:
        raise SystemExit('engine rc=%d %s' % (p.returncode,
                                              p.stderr.decode(errors='replace')[:800]))
    return json.loads(p.stdout)


def regs_of(d: dict) -> dict:
    return dict(zip(REGS, d['registers']))


def mem_bytes(d: dict, addr: int, n: int) -> bytes:
    for mr in d['memory']:
        a = mr['address']
        if a <= addr and addr + n <= a + len(mr['hex']) // 2:
            b = bytes.fromhex(mr['hex'])
            return b[addr - a:addr - a + n]
    return b''


def table_of(image: bytes, slot: int) -> bytes:
    o = TONE + slot * SLOT - BASE
    return bytes(image[o:o + SLOT])


def tone_of_dest(dest: bytes) -> list[int]:
    """上传器把 256 个 16 bit 表项按 4 B 步长写进 dest（dest[4i:4i+2] = table[i]）。"""
    if len(dest) < DEST_SPAN:
        return []
    return [struct.unpack_from('<H', dest, 4 * i)[0] for i in range(N_HALF)]


def describe(d: dict) -> str:
    r = regs_of(d)
    f = d.get('fault') or {}
    s = f'{d["status"]} PC=0x{r["PC"]:08x}'
    if d['status'] in ('memory_fault', 'instruction_fault'):
        s += f' kind={f.get("kind")} fault@0x{(f.get("address") or 0):08x}'
    return s


def stub_report(image: bytes) -> dict:
    def blob(a, n):
        return bytes(image[a - BASE:a - BASE + n])

    sd_tail = blob(SD_TAIL, 5)
    disp = int.from_bytes(sd_tail[1:5], 'little', signed=True)
    return {
        'sd_tail_addr': SD_TAIL,
        'sd_tail': sd_tail.hex(),
        'sd_disp': disp,
        'sd_target': (SD_TAIL + 5 + disp) & 0xFFFFFFFF,
        'su_guard_addr': 0x605DEDDC,
        'su_guard': blob(0x605DEDDC, 11).hex(),
        'sd_flags_addr': 0x605DED5D,
        'sd_flags': blob(0x605DED5D, 6).hex(),
        'slot_map': blob(SLOT_MAP, 8).hex(),
    }


# ---------------------------------------------------------------- 派发器
def dispatch(image: bytes, b0: int, b1: int, mode16: int, full: bool = True) -> dict:
    key = (b0 << 24) | (b1 << 16) | (mode16 & 0xFFFF)
    k_exp = ((key >> 24) & 3) << 3 | (key & 7)
    pokes = [
        p32(CTX + 0x3C0, 0),                      # 0x604B80CF: 0 -> 直接去算 key
        (CTX + 0x436, b'\x00\x00'),               # 0x604B809E: u16(ctx+0x436) != 0x101
        (CTX + 8, bytes([b0 & 0xFF])),
        (CTX + 9, bytes([b1 & 0xFF])),
        (CTX + 0x0A, struct.pack('<H', mode16 & 0xFFFF)),
    ]
    regs = {'A0': CTX, 'A1': SCRATCH, 'D0': 0, 'D1': 0}

    d = sim(image, DISPATCH, DISP_EPILOGUE, limit=200000, pokes=pokes, regs=regs)
    stash = mem_bytes(d, STASH, 1)
    ds = sim(image, DISPATCH, 0xFFFFFFFF, limit=200000, bps=[SD_RAM], pokes=pokes, regs=regs)
    dc = sim(image, DISPATCH, 0xFFFFFFFF, limit=200000, bps=[DISP_CONT], pokes=pokes, regs=regs)
    db = sim(image, DISPATCH, 0xFFFFFFFF, limit=200000, bps=[BROKEN_TARGET],
             pokes=pokes, regs=regs) if full else None
    dl = sim(image, DISPATCH, 0xFFFFFFFF, limit=20000, pokes=pokes, regs=regs) if full else None
    return {
        'b0': b0, 'b1': b1, 'mode16': mode16, 'key': key, 'k_exp': k_exp,
        'stash': stash[0] if stash else None,
        'stub_hit': ds['status'] == 'breakpoint' and regs_of(ds)['PC'] == SD_RAM,
        'broken_hit': bool(db) and db['status'] == 'breakpoint'
                      and regs_of(db)['PC'] == BROKEN_TARGET,
        'cont_hit': dc['status'] == 'breakpoint' and regs_of(dc)['PC'] == DISP_CONT,
        'loop': bool(dl) and dl['status'] == 'instruction_limit',
        'run': d,
    }


# ---------------------------------------------------------------- SU 桩（单独跑）
def su_probe(image: bytes, k: int) -> dict:
    pokes = [p32(S + 0x1230, STOCK_LUT), (STASH, bytes([k & 0xFF])),
             (SP + 8, DEST.to_bytes(4, 'little'))]
    regs = {'A0': S, 'D2': DEST + 0x400}
    dl = sim(image, SU_RAM, SU_LOOK, limit=200, pokes=pokes, regs=regs)
    ds = sim(image, SU_RAM, SU_STOCK, limit=200, pokes=pokes, regs=regs)
    look = dl['status'] == 'returned'
    a2 = regs_of(dl)['A2'] if look else regs_of(ds)['A2']
    slot = (a2 - TONE) // SLOT
    return {'k': k & 0xFF, 'path': 'look' if look else 'stock', 'a2': a2,
            'slot': slot, 'slot_ok': TONE <= a2 < TONE + 64 * SLOT,
            'stock_hit': ds['status'] == 'returned'}


# ---------------------------------------------------------------- 上传器
def upload(image: bytes, stash_k: int) -> dict:
    pokes = [
        p32(O + 0x0C, S),          # ctx = *(O+0x0C)
        p32(O + 0x64, 4),          # bit2 -> 走 tone 段
        p32(O + 0x34, 0),
        (O + 0xBD, b'\x01'),
        p32(S + 0x41C, 0),         # 跳过第一段（1024 B）
        p32(S + 0x420, T2),        # 第二段非 0
        p32(S + 0x1230, STOCK_LUT),
        (STASH, bytes([stash_k & 0xFF])),
    ]
    regs = {'A0': DEST, 'A1': O}
    d = sim(image, UPLOAD, UPLOAD_MERGE, limit=400000, pokes=pokes, regs=regs,
            extra_rwz=UPLOAD_HEAP)
    tone = tone_of_dest(mem_bytes(d, DEST, DEST_SPAN))
    return {'run': d, 'tone': tone}


def want_slot(k: int) -> tuple[str, int]:
    """按设计 k 应该怎么走：k∈1..4 → look 到 slot 21+k；否则 stock 到 slot 21。"""
    if 1 <= k <= 4:
        return 'look', 21 + k
    return 'stock', STOCK_LUT_SLOT


def run_case(image: bytes, name: str, b0: int, b1: int, mode: int, full: bool = True) -> dict:
    dp = dispatch(image, b0, b1, mode, full=full)
    stash = dp['stash'] if dp['stash'] is not None else 0xFF
    su = su_probe(image, stash)
    up = upload(image, stash)
    exp_path, exp_slot = want_slot(dp['k_exp'])
    exp_table = list(struct.unpack('<256H', table_of(image, exp_slot)))
    checks = {
        'C1 派发进入 SD 桩': dp['stub_hit'],
        'C2 SD 桩写出期望的 k': dp['stash'] == dp['k_exp'],
        'C3 SD 桩回到续跑点 0x604B8134': dp['cont_hit'],
        f'C4 SU 走 {exp_path} 路径': su['path'] == exp_path,
        f'C5 a2 = slot{exp_slot}': su['a2'] == TONE + exp_slot * SLOT,
        f'C6 拷出的表 = slot{exp_slot}': up['tone'] == exp_table,
    }
    data_closed = bool(checks['C2 SD 桩写出期望的 k'] and checks['C4 SU 走 %s 路径' % exp_path]
                       and checks['C5 a2 = slot%d' % exp_slot]
                       and checks['C6 拷出的表 = slot%d' % exp_slot])
    ctl_closed = bool(checks['C1 派发进入 SD 桩']
                      and checks['C3 SD 桩回到续跑点 0x604B8134'])
    return {'name': name, 'dispatch': dp, 'su': su, 'upload': up,
            'exp_path': exp_path, 'exp_slot': exp_slot, 'checks': checks,
            'exp_t46': exp_table[46], 'data_closed': data_closed,
            'ctl_closed': ctl_closed, 'closed': data_closed and ctl_closed}


def print_case(res: dict) -> None:
    dp, su, up = res['dispatch'], res['su'], res['upload']
    tone = up['tone']
    print(f"== {res['name']}")
    print(f"  ctx+8=0x{dp['b0']:02X}  ctx+9=0x{dp['b1']:02X}  u16(ctx+0xA)=0x{dp['mode16']:04X}"
          f"  ->  key=0x{dp['key']:08X}  k=0x{dp['k_exp']:02X}")
    print(f"  派发  stash=0x{(dp['stash'] if dp['stash'] is not None else -1) & 0xFF:02X}"
          f"  SD桩入口={dp['stub_hit']}  SD桩尾落点0x{BROKEN_TARGET:08X}={dp['broken_hit']}"
          f"  续跑点0x{DISP_CONT:08X}={dp['cont_hit']}")
    print(f"        实跑: {describe(dp['run'])}")
    print(f"  SU    走 {su['path']:5s}（设计应为 {res['exp_path']:5s}）  a2=0x{su['a2']:08X}"
          f"  slot={su['slot'] if su['slot_ok'] else 'n/a'}（设计应为 slot{res['exp_slot']}）")
    print(f"  上传  T[46]: 拷出={tone[46] if tone else None}"
          f"  slot{res['exp_slot']} 表内={res['exp_t46']}")
    for k, v in res['checks'].items():
        print(f"    {'PASS' if v else 'FAIL'}  {k}")
    print(f"  => 控制流闭合={res['ctl_closed']}  数据链闭合={res['data_closed']}"
          f"  整链闭合={res['closed']}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--pack', default='build/B32_looks_guard.BIN')
    ap.add_argument('--b0', default='0x04', help='ctx+8（key 高字节）；默认 0x04（见 docs §3.2）')
    ap.add_argument('--b1', default='0x00', help='ctx+9；默认 0x00')
    ap.add_argument('--case', type=int, default=None, help='只跑某个 mode')
    ap.add_argument('--json', default=None)
    ap.add_argument('--no-whatif', action='store_true', help='跳过对照实验')
    args = ap.parse_args()

    b0, b1 = int(args.b0, 0), int(args.b1, 0)
    pack = (ROOT / args.pack).resolve()
    blob = pack.read_bytes()
    image = oc.parse(blob)[0].payload
    print(f'pack   {pack.name}  {len(blob):,} B')
    print(f'image  payload {len(image):,} B @ 0x{BASE:08X}  '
          f'sha256={hashlib.sha256(image).hexdigest()[:16]}')
    print(f'建模   可写页 0x{WX_PAGE:08X} 用 permissions=7（真机 RAM 页 = 可写且可执行）\n')

    sr = stub_report(image)
    print('桩字节体检：')
    print(f"  SD 桩尾  0x{sr['sd_tail_addr']:08X}: {sr['sd_tail']}"
          f"  disp={sr['sd_disp']:+#x}  -> 0x{sr['sd_target']:08X}")
    print(f"  SD 恢复标志 0x{sr['sd_flags_addr']:08X}: {sr['sd_flags']}  (cmp 0x1010066,d2)")
    print(f"  SU 守卫  0x{sr['su_guard_addr']:08X}: {sr['su_guard']}"
          f"  (and 0x1f / cmp 0x1 / bcc / cmp 0x4 / bhi)")
    print(f"  slot_map 0x{SLOT_MAP:08X}: {sr['slot_map']}\n")

    variants = [('mode=%d' % m, b0, b1, m) for m in (1, 2, 3, 4, 7)]
    if args.case is not None:
        variants = [v for v in variants if v[3] == args.case]
    rows = []
    for name, c8, c9, mode in variants:
        res = run_case(image, name, c8, c9, mode)
        print_case(res)
        rows.append(res)

    print('---- 诊断用例（不是验收项，用来说明"四个模式"要满足什么才能走到桩）----')
    for name, c8, c9, mode in [('ctx+8=0x00（父任务假设的 b0=0）', 0x00, 0x01, 1),
                              ('ctx+8=0x01（真机标准模式 key 0x01010079 那类）', 0x01, 0x01, 0x79)]:
        res = run_case(image, name, c8, c9, mode)
        dp, su = res['dispatch'], res['su']
        print(f"== {name}")
        print(f"  key=0x{dp['key']:08X} k=0x{dp['k_exp']:02X}  派发: "
              f"stash=0x{(dp['stash'] if dp['stash'] is not None else -1) & 0xFF:02X}"
              f"  SD桩入口={dp['stub_hit']}  实跑: {describe(dp['run'])}")
        print(f"  SU: {su['path']} a2=0x{su['a2']:08X} slot={su['slot']}\n")
        rows.append(res)

    if not args.no_whatif:
        print('---- 对照实验：只在模拟器内存里按意图改写字节（**未改包**）----')
        fixes = {
            'W1 尾跳→0x604B8124（父任务说法）': [(SD_TAIL, _jmp32(0x604B8124))],
            'W2 尾跳→0x604B812F（build_beta_stash.py 说法）': [(SD_TAIL, _jmp32(0x604B812F))],
            'W3 W2 + 标志常数改成 cmp 0x2000096': [(SD_TAIL, _jmp32(0x604B812F)),
                                                (0x605DED5D, SD_FLAGS_NEW)],
            'W4 W3 + SU 守卫 c6->c4（k==0 才走原厂）': [(SD_TAIL, _jmp32(0x604B812F)),
                                                    (0x605DED5D, SD_FLAGS_NEW),
                                                    (SU_GUARD_ADDR, SU_GUARD_NEW)],
        }
        wrows = []
        for label, items in fixes.items():
            img = patch_image(image, items)
            print(f'== {label}')
            for name, c8, c9, mode in variants:
                res = run_case(img, name + ' ' + label.split()[0], c8, c9, mode, full=False)
                dp, su, up = res['dispatch'], res['su'], res['upload']
                print(f"  {name:6s} k=0x{dp['k_exp']:02X}  stash="
                      f"0x{(dp['stash'] if dp['stash'] is not None else -1) & 0xFF:02X}"
                      f"  续跑点={dp['cont_hit']}  派发={describe(dp['run'])}"
                      f"  | SU {su['path']} a2=0x{su['a2']:08X} slot={su['slot']}"
                      f"  | 表一致={up['tone'] == list(struct.unpack('<256H', table_of(img, res['exp_slot'])))}"
                      f"  数据链={res['data_closed']} 控制流={res['ctl_closed']}")
                wrows.append(res)
            print()
        img = patch_image(image, fixes['W4 W3 + SU 守卫 c6->c4（k==0 才走原厂）'])
        for name, c8, c9, mode in [('W5 全修 + 原厂范围 key(k=9)', 0x01, 0x01, 0x79)]:
            res = run_case(img, name, c8, c9, mode, full=False)
            dp, su, up = res['dispatch'], res['su'], res['upload']
            print(f"== {name}")
            print(f"  key=0x{dp['key']:08X} k=0x{dp['k_exp']:02X}  stash="
                  f"0x{(dp['stash'] if dp['stash'] is not None else -1) & 0xFF:02X}"
                  f"  续跑点={dp['cont_hit']}  派发={describe(dp['run'])}")
            print(f"  SU {su['path']} a2=0x{su['a2']:08X} slot={su['slot']}"
                  f"  (设计: k=9 不在 1..4 -> 原厂 slot21)"
                  f"  表一致={up['tone'] == list(struct.unpack('<256H', table_of(img, 21)))}"
                  f"  数据链={res['data_closed']} 控制流={res['ctl_closed']}\n")
            wrows.append(res)
        rows.extend(wrows)

    if args.json:
        out = []
        for r in rows:
            dp = r['dispatch']
            out.append({
                'name': r['name'], 'b0': dp['b0'], 'b1': dp['b1'], 'mode16': dp['mode16'],
                'key': dp['key'], 'k': dp['k_exp'], 'stash': dp['stash'],
                'stub_hit': dp['stub_hit'], 'broken_hit': dp['broken_hit'],
                'cont_hit': dp['cont_hit'], 'dispatch_run': describe(dp['run']),
                'su_path': r['su']['path'], 'a2': r['su']['a2'], 'slot': r['su']['slot'],
                'exp_path': r['exp_path'], 'exp_slot': r['exp_slot'],
                'copied_t46': r['upload']['tone'][46] if r['upload']['tone'] else None,
                'upload_run': describe(r['upload']['run']),
                'checks': r['checks'], 'data_closed': r['data_closed'],
                'ctl_closed': r['ctl_closed'], 'closed': r['closed'],
            })
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'JSON -> {args.json}')
    n_ok = sum(1 for r in rows[:len(variants)] if r['closed'])
    print(f'验收 {n_ok}/{len(variants)} 个用例整条链闭合')
    return 0 if n_ok == len(variants) else 1


if __name__ == '__main__':
    raise SystemExit(main())
