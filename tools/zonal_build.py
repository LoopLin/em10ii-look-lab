#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zonal_build.py — 12 区色相参数 → 喂给 ISP 的十张表（+ 掩码）的合成器，
外加一个把库存数据集拆回去的读取器。

两个方向都在这个文件里：

  读   parse_dataset(buf) / read_stock(addr)      0xBD4 B → 10 张表 + 掩码 + 标志
  写   build_dataset(zones, **opts)               12 个段参数 → 0xBD4 B（或 0xA80 B 的纯表区）

格式依据：docs/map/zonal-feed.md（96 项/表、4 张 u32 + 6 张 u16、u32 槽 hi16==lo16、
中性 0x3F00、值域 0x0000..0x3FFF、槽序与掩码语义）。本文只在"数值怎么从 12 个段参数来"
这一层引入约定（标【约定】），格式层全部照抄上面那份文档的实测结论（标【铁证】/【实测】）。

用法：
    python tools/zonal_build.py --dump                     # 拆库存 HSL 数据集（默认）
    python tools/zonal_build.py --dump --at 0x605F5BB4     # 同一数据集，按 brief 里的地址给
    python tools/zonal_build.py --dump --at stock-flat     # 普通模式数据集（8 张全中性）
    python tools/zonal_build.py --params p.json --out out/ds.bin
    python tools/zonal_build.py --params p.json --out out/tables.bin --out-format banks
    python tools/zonal_build.py --neutral --out out/neutral.bin   # 全中性（=全 0x3F00）
    python tools/zonal_build.py --selftest                 # 自证（不改任何文件）

参数字典（--params 可以是文件路径，也可以直接给一段 JSON）：
    {
      "mapping": "h3",            # 12 区 ↔ 6 块：h3 / h1 / h2（见 docs/map/zonal-build.md §4）
      "layout":  "roles",         # roles：四个曲线族各管一参；mirror：十张全写同一份
      "hue_mode": "clamp",        # clamp：加偏移后钳位；wrap：按 0x4000 取模（相位旋转）
      "amp": 1.0,                 # 参考形状的幅度（<1 给增益留余量）
      "zones": [                  # 12 段；缺的按中性补
        {"hue_deg": 0.0, "sat": 1.1, "bright_pct": 0.0},
        ...
      ]
    }
    每段也接受简写 [hue_deg, sat, bright_pct]，以及 "hue_ticks"/"bright_ticks" 原始码值、
    "enabled": true/false 强制启用/停用。
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- 地址与常量
BASE = 0x60021020                     # 镜像加载基址（analysis/*.decoded.bin）
IMAGE = ROOT / 'analysis' / '00_0100_40300020.decoded.bin'

DATASET_HSL = 0x605F5A64              # 库存 HSL 数据集（0xBD4 B）
DATASET_FLAT = 0x605F0798             # 库存"普通模式"数据集（0xBD4 B）
BANKS_HSL = 0x605F5BB4                # = DATASET_HSL + 0x150，十张表的起点

# 简报里写的是 0x605F5BB4（3028 B）。实测：0x605F5BB4 - 0x605F5A64 = 0x150，
# 0x150 正是第 0 张表的偏移，而 0xBD4 这个长度是从 0x605F5A64 量出来的；
# 从 0x5BB4 起只有 0xBD4-0x150 = 0xA84 B。两个地址都接受，内部统一归一到数据集基址。
ADDR_ALIAS = {BANKS_HSL: DATASET_HSL}
NAMED_ADDR = {'stock-hsl': DATASET_HSL, 'hsl': DATASET_HSL,
              'stock-flat': DATASET_FLAT, 'flat': DATASET_FLAT}

DATASET_LEN = 0xBD4                   # 12×0x1C 描述符 + 十张表 + u16 掩码 + u8 标志
BANKS_LEN = 0xA80                     # 4×0x180 + 6×0xC0
N_SLOT, N_ITEMS, BLOCK, N_ZONES = 10, 96, 16, 12

NEUTRAL = 0x3F00                      # 中性/填充码【实测：库存 8/10 张整张都是它】
FULL = 0x4000                         # 14 位满刻度的算术值（本身不可表示）
VMAX = 0x3FFF
MASK_STOCK = 0x7DF                    # 普通数据集自带掩码 = 它的 10 条非空描述符位图

# 槽 → 数据集内偏移 / 单元宽度（【铁证】docs/map/zonal-feed.md §1.1）
SLOT_OFF = [0x150, 0x690, 0x2D0, 0x450, 0x810, 0x990, 0x510, 0xA50, 0x5D0, 0xB10]
SLOT_W = [4, 4, 4, 2, 4, 2, 2, 2, 2, 2]

# 槽 → 曲线族。A/B/C/D 的归属是从库存数据集的实测结构反推的【实测】：
#   slot3 == slot2、slot5 == slot4、slot8 == slot0（16 位槽是 u32 槽复本的低半），
#   而 slot1/6/9 在库存 HSL 里整张 0x3F00 ⇒ 它们是"恒等"槽。
SLOT_FAMILY = ['A', 'id', 'B', 'B', 'C', 'C', 'id', 'D', 'A', 'id']
FAMILY_SLOTS = {'A': [0, 8], 'B': [2, 3], 'C': [4, 5], 'D': [7], 'id': [1, 6, 9]}

# 每个族吃哪几个参数（【约定】——物理含义没有固件证据，见 docs/map/zonal-build.md §5）
FAMILY_PARAM = {'A': ('hue',), 'B': ('sat',), 'C': ('bright',),
                'D': ('hue', 'sat', 'bright')}

# 库存 HSL 数据集的四族参考形状（16 项一块，逐字节取自 0x605F5A64 的 slot0/2/4/7）【实测】。
# 6 块共用同一条参考形状：A/B 在库存里本来就是 6 块逐项相同的 16 项周期。
SHAPE = {
    'A': [0x0008, 0x0808, 0x1008, 0x1808, 0x2007, 0x2708, 0x2F08, 0x3708,
          0x3FF8, 0x37F8, 0x2FF8, 0x27F9, 0x20F8, 0x18F8, 0x10F8, 0x08F8],
    'B': [0x0010, 0x1010, 0x2010, 0x300F, 0x3FFB, 0x3AFA, 0x34FB, 0x2FFB,
          0x2AFB, 0x25FA, 0x1FFB, 0x1AFB, 0x15FB, 0x10FA, 0x0AFB, 0x05FB],
    'C': [0x0014, 0x1413, 0x2714, 0x3B00, 0x3BFB, 0x36FB, 0x31FB, 0x2CFB,
          0x27FB, 0x22FC, 0x1EFB, 0x19FB, 0x14FB, 0x0FFB, 0x0AFB, 0x05FB],
    'D': [0x3F00, 0x3F00, 0x3F00, 0x3F00, 0x3FFB, 0x3AFB, 0x35FA, 0x2FFB,
          0x2AFB, 0x25FB, 0x20FA, 0x1AFB, 0x15FB, 0x10FB, 0x0BFB, 0x05FB],
}

# B35 包用的"排列码"（tools/build_beta_zonalmap.py 同源），本工具能原样复现它，
# 用来跟一张真构建出来的镜像做逐字节对照。
DROPS = [6, 9, 4, 2, 8, 0, 3, 10, 7, 1, 5, 11]

MAPPINGS = ('h3', 'h1', 'h2')
LAYOUTS = ('roles', 'mirror')


# ---------------------------------------------------------------- 读
def load_image(path=None) -> bytes:
    p = Path(path) if path else IMAGE
    if not p.exists():
        raise SystemExit('找不到镜像：%s' % p)
    return p.read_bytes()


def norm_addr(addr) -> tuple[int, int]:
    """把外部地址归一到数据集基址；返回 (基址, 被解释掉的偏移)。"""
    if addr in ADDR_ALIAS:
        return ADDR_ALIAS[addr], addr - ADDR_ALIAS[addr]
    return addr, 0


def read_stock(addr=DATASET_HSL, image=None) -> dict:
    """从镜像里抠一块 0xBD4 B 并按格式拆开。addr 可以是 0x605F5BB4（bank 区起点）。"""
    img = image if image is not None else load_image()
    base, shifted = norm_addr(addr)
    off = base - BASE
    if off < 0 or off + DATASET_LEN > len(img):
        raise SystemExit('地址 %#x 超出镜像范围' % addr)
    ds = parse_dataset(img[off:off + DATASET_LEN])
    ds['addr'] = base
    ds['addr_asked'] = addr
    ds['shifted'] = shifted
    return ds


def parse_dataset(buf: bytes) -> dict:
    """0xBD4 B → {'desc':[…12×0x1C…], 'tables':[…10×96 个 16 位值…],
    'wide':[…10 份原始 32 位单元…], 'mask', 'flag', 'raw'}。

    u32 槽：取低半字当规范值，另存整 dword 以便验 hi16==lo16。
    u16 槽：上传器 movhu 读出再 mov 成 32 位 ⇒ ISP 侧低半=值、高半=0。
    """
    if len(buf) < DATASET_LEN:
        raise ValueError('数据集短了：%d < %d' % (len(buf), DATASET_LEN))
    buf = bytes(buf[:DATASET_LEN])
    desc = [buf[i * 0x1C:(i + 1) * 0x1C] for i in range(N_ZONES)]
    tables, wide = [], []
    for off, w in zip(SLOT_OFF, SLOT_W):
        if w == 4:
            dw = list(struct.unpack_from('<%dI' % N_ITEMS, buf, off))
            tables.append([x & 0xFFFF for x in dw])
            wide.append(dw)
        else:
            tables.append(list(struct.unpack_from('<%dH' % N_ITEMS, buf, off)))
            wide.append(None)
    mask, flag = struct.unpack_from('<HB', buf, DATASET_LEN - 4)
    return {'desc': desc, 'tables': tables, 'wide': wide,
            'mask': mask, 'flag': flag, 'raw': buf}


def desc_bitmap(desc) -> int:
    """12 条描述符里"非全零"的位图 —— 与库存普通数据集的掩码逐位相等【实测】。"""
    return sum(1 << i for i, d in enumerate(desc) if any(d))


def slot_of_item(slot: int, item: int) -> tuple[int, int]:
    """(槽, 项) → 数据集字节区间 [start, end)。"""
    s = SLOT_OFF[slot] + item * SLOT_W[slot]
    return s, s + SLOT_W[slot]


# ---------------------------------------------------------------- 打印
def fmt_tables(ds, title='', head=True) -> str:
    lines = []
    if title:
        lines.append(title)
    if 'addr' in ds:
        note = ''
        if ds.get('shifted'):
            note = '（给的 %#x 是数据集+0x150=表区起点，已归一）' % ds['addr_asked']
        if ds.get('addr') is None:
            lines.append('  生成本体  长度 %d B (0x%X)  掩码 %#05X  标志 %d'
                         % (DATASET_LEN, DATASET_LEN, ds['mask'], ds['flag']))
        else:
            lines.append('  基址 %#x%s  长度 %d B (0x%X)  掩码 %#05X  标志 %d'
                         % (ds['addr'], note, DATASET_LEN, DATASET_LEN,
                            ds['mask'], ds['flag']))
            lines.append('  描述符非空的区：%s'
                         % [i for i, d in enumerate(ds['desc']) if any(d)])
    for slot, (vals, w, fam) in enumerate(zip(ds['tables'], SLOT_W, SLOT_FAMILY)):
        blocks = [vals[k:k + BLOCK] for k in range(0, N_ITEMS, BLOCK)]
        per16 = all(b == blocks[0] for b in blocks)
        wide = ds['wide'][slot]
        hi_ok = None if wide is None else all((x >> 16) == (x & 0xFFFF) for x in wide)
        lines.append('  slot%d  w%d  fam %-2s  off +0x%03X  min %04X max %04X  '
                     '全中性 %-5s 16周期 %-5s hi==lo %s'
                     % (slot, w, fam, SLOT_OFF[slot], min(vals), max(vals),
                        'yes' if all(v == NEUTRAL for v in vals) else 'no',
                        'yes' if per16 else 'no',
                        'n/a' if hi_ok is None else ('yes' if hi_ok else 'NO')))
        if head:
            lines.append('        前16项：' + ' '.join('%04X' % v for v in vals[:BLOCK]))
    return '\n'.join(lines)


# ---------------------------------------------------------------- 参数
def _f(x, d=0.0):
    return d if x is None else float(x)


class Zone:
    """一个色相段的参数。三种物理量，各自带一个"中性值"：

    hue    色相位移：hue_deg（度，360° ↔ 0x4000 码）或 hue_ticks（原始码单位）
    sat    饱和度增益：1.0 = 中性（≥0 的实数）
    bright 亮度偏移：bright_pct（满刻度 0x4000 的 %）或 bright_ticks（原始码单位）
    """

    __slots__ = ('hue_deg', 'sat', 'bright_pct', 'enabled')

    def __init__(self, hue_deg=0.0, sat=1.0, bright_pct=0.0, enabled=None):
        self.hue_deg = float(hue_deg)
        self.sat = float(sat)
        self.bright_pct = float(bright_pct)
        self.enabled = enabled

    # --- 码值换算
    @property
    def hue_ticks(self) -> int:
        return int(round(self.hue_deg / 360.0 * FULL))

    @property
    def bright_ticks(self) -> int:
        return int(round(self.bright_pct / 100.0 * FULL))

    # --- 中性判定
    def is_neutral(self, params) -> bool:
        if self.enabled is False:
            return True
        if self.enabled is True:
            return False
        for p in params:
            if p == 'hue' and self.hue_ticks != 0:
                return False
            if p == 'sat' and abs(self.sat - 1.0) > 1e-9:
                return False
            if p == 'bright' and self.bright_ticks != 0:
                return False
        return True

    def as_dict(self):
        return {'hue_deg': self.hue_deg, 'sat': self.sat,
                'bright_pct': self.bright_pct, 'enabled': self.enabled}

    def __repr__(self):
        return 'Zone(hue=%+g°, sat=%.4f, bright=%+g%%)' % (
            self.hue_deg, self.sat, self.bright_pct)


def zone_from_json(obj) -> Zone:
    if obj is None:
        return Zone()
    if isinstance(obj, (list, tuple)):
        v = list(obj) + [None] * (3 - len(obj))
        return Zone(_f(v[0]), _f(v[1], 1.0), _f(v[2]))
    if isinstance(obj, dict):
        tick_hue = obj.get('hue_ticks')
        tick_br = obj.get('bright_ticks')
        hue_deg = _f(obj.get('hue_deg'))
        br_pct = _f(obj.get('bright_pct'))
        if tick_hue is not None:
            hue_deg = float(tick_hue) * 360.0 / FULL
        if tick_br is not None:
            br_pct = float(tick_br) * 100.0 / FULL
        return Zone(hue_deg, _f(obj.get('sat'), 1.0), br_pct, obj.get('enabled'))
    raise ValueError('看不懂的段参数：%r' % (obj,))


def zones_from_json(zones) -> list:
    zs = [zone_from_json(z) for z in (zones or [])]
    zs += [Zone() for _ in range(N_ZONES - len(zs))]
    if len(zs) > N_ZONES:
        raise ValueError('只接受 12 段，给了 %d' % len(zs))
    return zs


def all_neutral_zones() -> list:
    return [Zone() for _ in range(N_ZONES)]


# ---------------------------------------------------------------- 12 区 ↔ 6 块
def item_zones(mapping: str, block: int, item: int) -> tuple:
    """某块某项目前由哪几个区共用。

    h3  一块 16 项 = 区 2k、2k+1 共用（96 = 6×16 的字面读法）
    h1  一块 16 项 = 前 8 项区 2k、后 8 项区 2k+1（12 段连续覆盖 360°）
    h2  一块 16 项 = 前 8 项区 k、后 8 项区 k+6（(k,k+6) 交错）
    三种都还只是假说（B35 包已出、未上机判读），见 docs/map/zonal-build.md §4。
    """
    if mapping == 'h3':
        return (2 * block, 2 * block + 1)
    half = 0 if item < 8 else 1
    if mapping == 'h1':
        return (2 * block + half,)
    if mapping == 'h2':
        return (block, block + 6)[half:half + 1]
    raise ValueError('未知 mapping：%s' % mapping)


def _combine(zones, idxs, params):
    """把共用一块的几个区的参数合成一个；全中性则返回 None（→ 填 0x3F00）。"""
    active = [zones[i] for i in idxs if not zones[i].is_neutral(params)]
    if not active:
        return None
    n = len(active)
    out = {'hue': 0.0, 'sat': 0.0, 'bright': 0.0}
    for z in active:
        out['hue'] += z.hue_ticks / n
        out['sat'] += (z.sat - 1.0) / n
        out['bright'] += z.bright_ticks / n
    out['sat'] += 1.0            # 回到"1.0 = 中性"的绝对刻度
    return out


def _clamp(v: int) -> int:
    return 0 if v < 0 else (VMAX if v > VMAX else v)


def build_table(fam: str, zones, mapping='h3', hue_mode='clamp', amp=1.0,
                shape=None) -> tuple[list, dict]:
    """生成一张 96 项表。返回 (值表, 统计)。

    值模型（本工具的【约定】C1，见 docs/map/zonal-build.md §3）：

        M   = round(mean(形状里 ≠ 0x3F00 的项))     # 块内参考电平
        v   = M + amp·sat·(shape[i] − M) + hue + bright   （钳到 0..0x3FFF）
        hue_mode=wrap 时 hue 这一步按 0x4000 取模

    参数没被这一族用到时取中性值（A 只看 hue、B 只看 sat、C 只看 bright、D 三个都看），
    于是"某族中性"就等价于"这一族整块填 0x3F00"。
    """
    shape = SHAPE[fam] if shape is None else shape
    params = FAMILY_PARAM[fam]
    curve = [v for v in shape if v != NEUTRAL] or list(shape)
    M = int(round(sum(curve) / float(len(curve))))
    out, clipped, blocks_on = [0] * N_ITEMS, 0, []
    for k in range(N_ITEMS // BLOCK):
        any_on = False
        for i in range(BLOCK):
            p = _combine(zones, item_zones(mapping, k, i), params)
            if p is None:
                out[k * BLOCK + i] = NEUTRAL
                continue
            any_on = True
            sat = p['sat'] if 'sat' in params else 1.0
            hue = int(round(p['hue'])) if 'hue' in params else 0
            br = int(round(p['bright'])) if 'bright' in params else 0
            # 参考形状里"本来就是 0x3F00"的项是中性宽度：中性点上没有色偏移可放大，
            # 所以增益这一步不动它（否则 D 段那段 0x3F00 前缀会被放大成 0x3FFF）。
            if shape[i] == NEUTRAL:
                v = NEUTRAL
            else:
                v = int(round(M + amp * sat * (shape[i] - M)))
            if hue_mode == 'wrap' and 'hue' in params:
                v = (v + hue) % FULL              # 相位旋转：超过 0x4000 就折回
            elif 'hue' in params:
                v = v + hue
            v = v + br
            if v != _clamp(v):
                clipped += 1
            out[k * BLOCK + i] = _clamp(v)
        if any_on:
            blocks_on.append(k)
    # 全表都没启用时，仍然返回 10 张 0x3F00（中性集）；这是"不变色"的正确字节。
    return out, {'mean': M, 'clipped': clipped, 'blocks': blocks_on}


def build_tables(zones, mapping='h3', layout='roles', hue_mode='clamp', amp=1.0) -> list:
    """12 段参数 → 十张表（每张 96 个 16 位规范值）。"""
    fams = {}
    if layout == 'roles':
        for fam in ('A', 'B', 'C', 'D'):
            fams[fam], _ = build_table(fam, zones, mapping, hue_mode, amp)
        fams['id'] = [NEUTRAL] * N_ITEMS
    elif layout == 'mirror':
        # 十张全写同一份"合成"曲线（B35 的形态；表若是冗余副本时最稳）
        one, _ = build_table('D', zones, mapping, hue_mode, amp)
        for fam in ('A', 'B', 'C', 'D', 'id'):
            fams[fam] = list(one)
    else:
        raise ValueError('未知 layout：%s' % layout)
    return [list(fams[SLOT_FAMILY[s]]) for s in range(N_SLOT)]


def build_mask(zones, explicit=None, force_nonzero=False) -> int:
    """自动掩码 = 启用区的位图；全中性时得 0（上传器见到 0 会整块跳过，见 §6）。"""
    if explicit is not None:
        return int(explicit) & 0xFFFF
    bits = 0
    for i, z in enumerate(zones):
        if not z.is_neutral(('hue', 'sat', 'bright')):
            bits |= 1 << i
    if bits == 0 and force_nonzero:
        bits = MASK_STOCK
    return bits


def build_dataset(zones, mapping='h3', layout='roles', hue_mode='clamp', amp=1.0,
                  mask=None, force_mask=False, desc='stock', stock=None) -> dict:
    """12 段参数 → 完整 0xBD4 数据集（含 12 条描述符 / 掩码 / 标志）。"""
    tables = build_tables(zones, mapping, layout, hue_mode, amp)
    if desc == 'stock':
        src = stock if stock is not None else read_stock(DATASET_HSL)
        descs = [bytes(d) for d in src['desc']]
    elif desc == 'zero':
        descs = [b'\x00' * 0x1C for _ in range(N_ZONES)]
    else:
        raise ValueError('未知 desc：%s' % desc)
    raw = bytearray(DATASET_LEN)
    for i, d in enumerate(descs):
        raw[i * 0x1C:(i + 1) * 0x1C] = d
    for slot, (vals, w) in enumerate(zip(tables, SLOT_W)):
        if len(vals) != N_ITEMS:
            raise ValueError('槽 %d 项数不对：%d' % (slot, len(vals)))
        if w == 4:
            for i, v in enumerate(vals):
                struct.pack_into('<I', raw, SLOT_OFF[slot] + 4 * i, (v & 0xFFFF) * 0x10001)
        else:
            for i, v in enumerate(vals):
                struct.pack_into('<H', raw, SLOT_OFF[slot] + 2 * i, v & 0xFFFF)
    m = build_mask(zones, mask, force_mask)
    struct.pack_into('<HB', raw, DATASET_LEN - 4, m, 0)
    ds = parse_dataset(bytes(raw))
    ds['addr'] = None
    ds['mask_auto'] = build_mask(zones)
    return ds


def serialize(ds, fmt='dataset') -> bytes:
    """把 parse 过的结构写回字节；fmt='banks' 只写 0xA80 B 的表区。"""
    raw = bytearray(ds['raw'])
    if fmt == 'dataset':
        return bytes(raw)
    if fmt == 'banks':
        return bytes(raw[0x150:0xBD0])
    raise ValueError('未知 out-format：%s' % fmt)


def code_tables(drops=DROPS) -> list:
    """B35 的排列码：每 8 项一段，段 c 的值 = 0x3F00 − drop(c)·0x200，十张表同码。"""
    one = []
    for i in range(N_ITEMS):
        one.append(NEUTRAL - drops[i // 8] * 0x200)
    return [list(one) for _ in range(N_SLOT)]


# ---------------------------------------------------------------- 差异分析
def diff_items(a: dict, b: dict) -> list:
    """两份数据集逐字节比，返回 [(槽, 项, 旧值, 新值)]（描述符/掩码/标志不算在内）。"""
    out = []
    for slot, (w, va, vb) in enumerate(zip(SLOT_W, a['tables'], b['tables'])):
        for i, (x, y) in enumerate(zip(va, vb)):
            if x != y:
                out.append((slot, i, x, y))
    return out


def diff_bytes(a: dict, b: dict) -> list:
    """逐字节差异，返回 [(start, end)] 连续区间（只针对整份 0xBD4）。"""
    ra, rb = a['raw'], b['raw']
    ranges, start = [], None
    for i, (x, y) in enumerate(zip(ra, rb)):
        if x != y:
            if start is None:
                start = i
        elif start is not None:
            ranges.append((start, i))
            start = None
    if start is not None:
        ranges.append((start, len(ra)))
    return ranges


def expected_items(mapping: str, zones_idx, role_slots) -> set:
    """给定"只动这几个区"，预期会变的 (槽, 项) 全集。"""
    want = set()
    for slot in role_slots:
        for k in range(N_ITEMS // BLOCK):
            for i in range(BLOCK):
                zs = item_zones(mapping, k, i)
                if any(z in zones_idx for z in zs):
                    want.add((slot, k * BLOCK + i))
    return want


def role_slots_of(zones, layout='roles') -> set:
    """这些区用到了哪些参数 → 会碰哪几个槽（与 mapping 无关：mapping 只改项号，不改槽）。"""
    fams, slots = set(), set()
    for i, z in enumerate(zones):
        for fam, params in FAMILY_PARAM.items():
            if not z.is_neutral(params):
                fams.add(fam)
    if layout == 'mirror':
        return set(range(N_SLOT))
    for fam in fams:
        slots.update(FAMILY_SLOTS[fam])
    return slots


# ---------------------------------------------------------------- 自证
def _fmt_ranges(ranges, base_label='+0x%03X') -> str:
    return ', '.join(base_label % s + '-0x%03X' % e for s, e in ranges)


def selftest(verbose=True) -> int:
    """自证：格式基线（库存）→ 全中性 → 单区差分 → 序列化往返 → B35 复现。

    返回失败数（0 = 全过）。只读镜像与 .tmp 里已有的构建产物，不写任何文件。
    """
    fails, log = [], []
    stat = {'n': 0}

    def chk(ok, name, detail=''):
        stat['n'] += 1
        log.append('%s %s%s' % ('PASS' if ok else 'FAIL', name,
                                ('  ' + detail) if detail else ''))
        if not ok:
            fails.append(name)

    img = load_image()

    # --- 1. 库存 HSL 数据集：格式基线
    hsl = read_stock(DATASET_HSL, img)
    chk(len(hsl['raw']) == DATASET_LEN, '数据集长度 = 0xBD4 (3028 B)')
    chk(hsl['mask'] == 0 and hsl['flag'] == 0, 'HSL 数据集掩码/标志 = 0x0000/0')
    chk([i for i, d in enumerate(hsl['desc']) if any(d)] == [3, 4, 5, 9, 10, 11],
        'HSL 描述符非空区 = 3,4,5,9,10,11')
    hi_ok = all((x >> 16) == (x & 0xFFFF) for s, w in enumerate(SLOT_W) if w == 4
                for x in hsl['wide'][s])
    chk(hi_ok, '4 张 u32 槽 384 项全部 hi16 == lo16')
    dup = all(hsl['tables'][d] == hsl['tables'][s]
              for d, s in ((3, 2), (5, 4), (8, 0)))
    chk(dup, 'u16 复本槽 3==2、5==4、8==0')
    rng = all(0 <= v <= VMAX for t in hsl['tables'] for v in t)
    chk(rng, '全部 960 个值落在 0x0000..0x3FFF')
    neut = [s for s in range(N_SLOT) if all(v == NEUTRAL for v in hsl['tables'][s])]
    chk(neut == [1, 6, 9], 'HSL 里整张全中性的槽 = 1,6,9', str(neut))

    # --- 2. 库存普通数据集：8 张全中性 + 掩码 = 描述符位图
    flat = read_stock(DATASET_FLAT, img)
    neut_f = [s for s in range(N_SLOT) if all(v == NEUTRAL for v in flat['tables'][s])]
    chk(neut_f == [0, 1, 2, 3, 4, 5, 8, 9], '普通数据集 8/10 张全中性', str(neut_f))
    chk(flat['mask'] == desc_bitmap(flat['desc']) == MASK_STOCK,
        '普通数据集掩码 0x7DF == 非空描述符位图', '%#x' % flat['mask'])
    chk(flat['tables'][6] != flat['tables'][0] and flat['tables'][7] != flat['tables'][0],
        '普通数据集只在 slot6/slot7 有曲线')

    # --- 3. 全中性生成 = 十张 0x3F00
    zones0 = all_neutral_zones()
    ds0 = build_dataset(zones0, desc='stock', stock=hsl)
    chk(all(v == NEUTRAL for t in ds0['tables'] for v in t),
        '全中性 → 960 个值全 = 0x3F00')
    chk(ds0['mask'] == 0, '全中性 → 自动掩码 0（上传器整块跳过=不变）', '%#x' % ds0['mask'])
    chk(all(ds0['tables'][s] == flat['tables'][s] for s in neut_f),
        '全中性 → 与库存那 8 张全中性表逐项相等')
    # 与一份独立脚本写出来的全中性表区对照（build_beta_zonalfeed.py 的产物）
    b28 = ROOT / '.tmp' / 'B28.decoded.bin'
    if b28.exists():
        d28 = b28.read_bytes()
        o = DATASET_HSL - BASE
        chk(d28[o + 0x150:o + 0xBD0] == serialize(ds0)[0x150:0xBD0],
            '全中性表区与 .tmp/B28.decoded.bin 的 0xA80 B 逐字节相同')
        chk(d28[o:o + DATASET_LEN] == serialize(ds0),
            '全中性整份 0xBD4 与 .tmp/B28.decoded.bin 逐字节相同')
    else:
        log.append('SKIP 全中性对照 .tmp/B28.decoded.bin（不存在）')

    # --- 4. 只把第 0 段饱和度 +10%
    zones1 = all_neutral_zones()
    zones1[0] = Zone(0.0, 1.1, 0.0)
    ds1 = build_dataset(zones1, desc='stock', stock=hsl)
    items = diff_items(ds0, ds1)          # [(槽, 项, 旧, 新)]
    pairs = {(t[0], t[1]) for t in items}
    slots_hit = sorted({s for s, _ in pairs})
    want_slots = sorted(role_slots_of(zones1))
    chk(slots_hit == want_slots, '第 0 段 sat+10% 只动预期槽', '%s vs 预期 %s' % (slots_hit, want_slots))
    want = expected_items('h3', {0}, want_slots)
    chk(pairs <= want, '差异项 ⊆ 第 0 块（块↔区 h3）',
        '%d 项，越界 %d 项' % (len(pairs), len(pairs - want)))
    chk(all(0 <= t[1] < BLOCK for t in items), '差异项号全部落在 0..15（块 0）',
        '项号 %s' % sorted({t[1] for t in items}))
    br = diff_bytes(ds0, ds1)
    tables_only = all(0x150 <= s and e <= 0xBD0 for s, e in br)
    mask_only = [r for r in br if not (0x150 <= r[0] and r[1] <= 0xBD0)]
    chk(tables_only or all(s >= DATASET_LEN - 4 for s, _ in mask_only),
        '差异字节只在表区 0x150..0xBD0（掩码另算）',
        _fmt_ranges(br) + '  ← 末尾 +0xBD0 是掩码 u16（0x000→0x001）')
    chk(build_mask(zones1) == 0x001, '第 0 段启用 → 掩码 = 0x001', '%#x' % build_mask(zones1))
    st = build_table('B', zones1, 'h3')[1]
    log.append('     ↳ 块 0 削顶 %d/16 项（库存形状已吃满 0..0x3FFF，+10%% 会顶格）' % st['clipped'])

    # --- 5. 换一个区、换一个参数、换一种 mapping，仍块内闭环
    for mapping, zi, z, params in (('h1', 0, Zone(0, 1.1, 0), ('sat',)),
                                   ('h2', 6, Zone(30, 1, 0), ('hue',)),
                                   ('h3', 11, Zone(0, 1, 5.0), ('bright',))):
        zs2 = all_neutral_zones()
        zs2[zi] = z
        a = build_dataset(all_neutral_zones(), mapping=mapping, desc='zero')
        b = build_dataset(zs2, mapping=mapping, desc='zero')
        got = {(t[0], t[1]) for t in diff_items(a, b)}
        want = expected_items(mapping, {zi}, role_slots_of(zs2))
        chk(got and got <= want, 'mapping=%s 区%d %s → 差异块内闭环' % (mapping, zi, params),
            '动 %d 项，越界 %d' % (len(got), len(got - want)))

    # --- 6. 序列化往返
    rt = parse_dataset(serialize(ds1))
    chk(rt['tables'] == ds1['tables'] and rt['mask'] == ds1['mask']
        and rt['raw'] == ds1['raw'], 'parse(serialize(x)) == x')
    chk(all((x >> 16) == (x & 0xFFFF) for s, w in enumerate(SLOT_W) if w == 4
            for x in rt['wide'][s]), '生成侧 u32 槽 hi16 == lo16')

    # --- 7. 复现真实构建产物 B35 的十张表
    b35 = ROOT / '.tmp' / 'B35.decoded.bin'
    if b35.exists():
        d = b35.read_bytes()
        o = DATASET_HSL - BASE
        mine = bytearray(DATASET_LEN)
        for slot, (vals, w) in enumerate(zip(code_tables(), SLOT_W)):
            for i, v in enumerate(vals):
                if w == 4:
                    struct.pack_into('<I', mine, SLOT_OFF[slot] + 4 * i, v * 0x10001)
                else:
                    struct.pack_into('<H', mine, SLOT_OFF[slot] + 2 * i, v)
        chk(bytes(mine[0x150:0xBD0]) == d[o + 0x150:o + 0xBD0],
            'B35 排列码十张表与 .tmp/B35.decoded.bin 逐字节相同（0xA80 B）')
    else:
        log.append('SKIP B35 对照（.tmp/B35.decoded.bin 不存在）')

    if verbose:
        print('自证：%d 项检查，%d 失败' % (stat['n'], len(fails)))
        for line in log:
            print('  ' + line)
        if fails:
            print('\n失败项：' + ', '.join(fails))
    return len(fails)


# ---------------------------------------------------------------- CLI
def _parse_addr(s):
    if s is None:
        return DATASET_HSL
    t = str(s).strip().lower()
    if t in NAMED_ADDR:
        return NAMED_ADDR[t]
    return int(t, 16) if t.startswith('0x') else int(t, 0)


def _load_params(arg):
    txt = arg
    if arg and not arg.lstrip().startswith('{'):
        p = Path(arg)
        if p.exists():
            txt = p.read_text(encoding='utf-8')
        else:
            raise SystemExit('参数文件不存在：%s' % arg)
    try:
        return json.loads(txt)
    except Exception as e:
        raise SystemExit('参数 JSON 解析失败：%s' % e)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='12 区参数 ↔ 十张 ISP 表（0xBD4 数据集）的合成与拆解',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dump', action='store_true', help='拆开库存数据集并打印')
    ap.add_argument('--at', default=None,
                    help='数据集地址：0x605F5A64 / 0x605F5BB4 / stock-hsl / stock-flat')
    ap.add_argument('--params', default=None, help='段参数 JSON（文件路径或内联 JSON）')
    ap.add_argument('--neutral', action='store_true', help='全中性参数（十张 0x3F00）')
    ap.add_argument('--out', default=None, help='输出 bin')
    ap.add_argument('--out-format', choices=['dataset', 'banks'], default='dataset',
                    help='dataset=0xBD4 整份；banks=0xA80 纯表区')
    ap.add_argument('--map', choices=MAPPINGS, default=None, help='12 区 ↔ 6 块假说')
    ap.add_argument('--layout', choices=LAYOUTS, default=None, help='roles / mirror')
    ap.add_argument('--hue-mode', choices=['clamp', 'wrap'], default=None)
    ap.add_argument('--amp', type=float, default=None, help='参考形状幅度，默认 1.0')
    ap.add_argument('--mask', default=None, help='掩码覆盖（hex），默认自动=启用区位图')
    ap.add_argument('--force-mask', action='store_true',
                    help='全中性时也把掩码给成 0x7DF（强行过上传器的非零门）')
    ap.add_argument('--desc', choices=['stock', 'zero'], default=None,
                    help='12 条描述符照抄库存还是清零')
    ap.add_argument('--print', dest='print_tables', action='store_true',
                    help='每张表连前 16 项一起打（默认只打每槽一行）')
    ap.add_argument('--json', dest='json_out', default=None, help='把摘要写成 JSON')
    ap.add_argument('--selftest', action='store_true', help='自证')
    a = ap.parse_args(argv)

    if a.selftest:
        return 1 if selftest() else 0

    if a.dump and not (a.params or a.neutral):
        addr = _parse_addr(a.at)
        ds = read_stock(addr)
        print(fmt_tables(ds, '库存数据集 %#x' % ds['addr']))
        print('  u16 复本核验：slot3==slot2 %s  slot5==slot4 %s  slot8==slot0 %s'
              % tuple(('是' if ds['tables'][d] == ds['tables'][s] else '否')
                      for d, s in ((3, 2), (5, 4), (8, 0))))
        print('  描述符位图 %#05X，数据集自带掩码 %#05X，标志 %d'
              % (desc_bitmap(ds['desc']), ds['mask'], ds['flag']))
        if ds['shifted']:
            print('  注：%#x = 数据集基址 + 0x150（十张表的起点），已归一到 %#x'
                  % (ds['addr_asked'], ds['addr']))
        return 0

    cfg = {}
    if a.params:
        cfg = _load_params(a.params)
        if not isinstance(cfg, dict):
            cfg = {'zones': cfg}
    zones = all_neutral_zones() if (a.neutral or not a.params) else \
        zones_from_json(cfg.get('zones'))
    if a.neutral:
        zones = all_neutral_zones()

    mapping = a.map or cfg.get('mapping') or 'h3'
    layout = a.layout or cfg.get('layout') or 'roles'
    hue_mode = a.hue_mode or cfg.get('hue_mode') or 'clamp'
    amp = a.amp if a.amp is not None else float(cfg.get('amp', 1.0))
    desc = a.desc or cfg.get('desc') or 'stock'
    mask = a.mask if a.mask is not None else cfg.get('mask')
    if mask is not None:
        mask = int(mask, 0) if isinstance(mask, str) else int(mask)
    if mapping not in MAPPINGS:
        raise SystemExit('mapping 只能是 %s' % (MAPPINGS,))
    if layout not in LAYOUTS:
        raise SystemExit('layout 只能是 %s' % (LAYOUTS,))

    ds = build_dataset(zones, mapping=mapping, layout=layout, hue_mode=hue_mode,
                       amp=amp, mask=mask, force_mask=a.force_mask, desc=desc)

    print('生成：mapping=%s layout=%s hue_mode=%s amp=%.3f desc=%s'
          % (mapping, layout, hue_mode, amp, desc))
    for i, z in enumerate(zones):
        if not z.is_neutral(('hue', 'sat', 'bright')):
            print('  区%-2d %s  → hue %+d 码 / bright %+d 码'
                  % (i, z, z.hue_ticks, z.bright_ticks))
    print(fmt_tables(ds, '生成的十张表', head=a.print_tables))
    print('  掩码 %#05X（自动位图 %#05X）%s'
          % (ds['mask'], ds['mask_auto'],
             '  ⚠ 为 0：上传器会整块跳过，等于不变' if ds['mask'] == 0 else ''))
    for slot, w in zip(range(N_SLOT), SLOT_W):
        vals = ds['tables'][slot]
        if all(v == NEUTRAL for v in vals):
            continue
        bad = sum(1 for v in vals if v in (0, VMAX))
        print('  slot%d 触顶/触底项 %d/96' % (slot, bad))

    if a.out:
        data = serialize(ds, a.out_format)
        p = Path(a.out)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        print('写 %s（%d B, 0x%X）' % (p, len(data), len(data)))

    if a.json_out:
        p = Path(a.json_out)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            'mapping': mapping, 'layout': layout, 'hue_mode': hue_mode, 'amp': amp,
            'desc': desc, 'mask': ds['mask'], 'mask_auto': ds['mask_auto'],
            'zones': [z.as_dict() for z in zones],
            'tables': ds['tables'],
            'ranges': {str(s): [min(ds['tables'][s]), max(ds['tables'][s])]
                       for s in range(N_SLOT)},
        }, ensure_ascii=False, indent=1), encoding='utf-8')
        print('写 %s' % p)
    return 0


if __name__ == '__main__':
    try:                                  # Windows 控制台默认 GBK，中文会炸；统一改 utf-8
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
    sys.exit(main())
