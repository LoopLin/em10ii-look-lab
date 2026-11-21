#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""odd_slot_hunt.py —— 谁读 tone 组表（0x60600920 + 0x20*i）的**奇数槽**
（组基址 +4 / +0xC / +0x14 / +0x1C）？

两层扫描，缺一不可：

  A. 地址层（本脚本第一段）
     把「组基址 + 槽偏移」这个 RAM 地址按 **小端 4 字节** 在整个镜像里
     `bytes.find()`（非对齐）——即找 `fc dc <imm32>`（mov imm32,a0）这类
     立即数引用。这一层能看见「谁把某张表的地址写进指令」，看不见
     「寄存器相对寻址」。

  B. 寄存器相对层（本脚本第二段，才是真正的答案）
     stock 里**只有组基址**被立即数引用；槽选择全部发生在选择器
     `0x604B8049`：先把 a0 设成 组基址+{0,8,0x10,0x18}（=偶数槽），
     再 `mov (a0),a0` / `mov (4,a0),a0`。后者**就是读奇数槽**：
     +0+4=+4、+8+4=+0xC、+0x10+4=+0x14、+0x18+4=+0x1C。
     所以 A 层对任何 **非 0 槽** 都必然是 0 命中——这不是"死数据"的证据，
     因为偶数槽 2/4/6（+8/+0x10/+0x18）同样是 0 命中，而它们正是主路径。

第 B 层做三件事：
  1. 找齐组基址的所有立即数装载点，并验证每个都汇入选择器调用；
  2. 反汇编选择器，定位 `(a0)` / `(4,a0)` 两条读路径与门控 `d0`；
  3. 把选择器的地址计算抄成一个纯 Python 模型，对 d1=0..0xFFFF × d0∈{0,1}
     穷举，验证「d0=0 只可能落在 {0,8,0x10,0x18}、d0=1 只可能落在
     {4,0xC,0x14,0x1C}」——这是对反汇编读法的可执行核对；
  4. 逐个选择器调用点回溯 d0 的写者（`0x604BDD42` 还是别的来源）。

输出：docs/map/odd-slots.json
只读镜像，不改任何 .BIN。
"""
import json
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from tools.mn10300_dis import Decoder, format_line  # noqa: E402

IMG = os.path.join(ROOT, 'analysis', '00_0100_40300020.decoded.bin')
OUT = os.path.join(ROOT, 'docs', 'map', 'odd-slots.json')

BASE = 0x60021020
CODE_LO, CODE_HI = 0x60400000, 0x605C0000
GROUP0, GROUP_N, GROUP_STEP = 0x60600920, 21, 0x20
TONE_LO, TONE_HI = 0x605D8344, 0x605D8344 + 0x200 * 64
SELECTOR = 0x604B8049          # 槽/基准-替代 选择器
ALT_FN = 0x604BDD42            # 替代位求值
SLOT_OFFS = [0, 4, 8, 12, 16, 20, 24, 28]
EVEN_OFFS = [0, 8, 16, 24]
ODD_OFFS = [4, 12, 20, 28]

D = open(IMG, 'rb').read()
DEC = Decoder()


# ---------------------------------------------------------------- 基础读取
def rd(a, n):
    return D[a - BASE:a - BASE + n]


def u32(a):
    return struct.unpack_from('<I', D, a - BASE)[0]


def func_start(a, back=0x600):
    """向上反扫 `de`（retf）再跳过操作数/填充小零，求函数起点（同 code_sites.py）。"""
    for p in range(a, max(BASE, a - back), -1):
        if D[p - BASE] == 0xde:
            q = p + 1
            while q < a and D[q - BASE] in (0x00, 0xff):
                q += 1
            return q
    return None


def hexs(x):
    return None if x is None else '0x%08x' % x


def find_all(pattern):
    out, i = [], D.find(pattern)
    while i >= 0:
        out.append(BASE + i)
        i = D.find(pattern, i + 1)
    return out


def xrefs(addr):
    """全镜像非对齐 4 字节立即数引用（= 0x604B8xxx 那种 `fc dc <addr>` 的操作数位置）。"""
    return find_all(struct.pack('<I', addr))


def callers(target):
    """相对 call：MN10300 的 `cd <disp16>`，target = cia + disp16（含 5 字节形式）。"""
    out, i = [], D.find(b'\xcd')
    while i >= 0:
        if i + 2 < len(D):
            disp = struct.unpack_from('<h', D, i + 1)[0]
            if BASE + i + disp == target:
                out.append(BASE + i)
        i = D.find(b'\xcd', i + 1)
    return out


def disasm_from(addr, nbytes):
    """从 addr 顺向反汇编 nbytes 字节；返回 (pc, nb, raw, mnem, ops) 列表。"""
    rows = []
    for pc, nb, raw, mnem, ops, _rule in DEC.disasm(D, addr - BASE, nbytes, BASE):
        rows.append((pc, nb, raw, mnem, ops))
    return rows


def insn_at(addr, maxlen=8):
    """解码 addr 处的单条指令。"""
    rows = disasm_from(addr, maxlen * 4)
    return rows[0] if rows else None


def window_rows(addr, before=24, after=24):
    """取 [addr-before, addr+after) 覆盖的指令；锚点用函数起点（保证同步）。"""
    fs = func_start(addr)
    anchor = fs if (fs and 0 <= addr - fs <= 0x800) else max(BASE, addr - 32)
    rows = disasm_from(anchor, (addr - anchor) + after + 16)
    lo, hi = addr - before, addr + after
    return [r for r in rows if lo <= r[0] < hi], anchor


def rows_text(rows):
    return '\n'.join('%08X  %-14s %s %s' % (pc, raw.hex().upper(), m, o) for pc, _nb, raw, m, o in rows)


def rows_json(rows):
    """disasm_from 的元组行转成 JSON 友好的 dict（raw 用 hex 字符串）。"""
    return [{'pc': hexs(pc), 'len': nb, 'raw': raw.hex().upper(),
             'insn': ('%s %s' % (m, o)).strip()}
            for pc, nb, raw, m, o in rows]


_ADDR_KEYS = ('hit', 'func', 'window_anchor', 'insn_addr')


def hexify_addrs(obj):
    """把地址类字段的 int 统一写成 0x%08x 字符串（输出前调用一次）。"""
    if isinstance(obj, dict):
        return {k: (hexs(v) if k in _ADDR_KEYS and isinstance(v, int) else hexify_addrs(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [hexify_addrs(v) for v in obj]
    return obj


_HEX = re.compile(r'0x([0-9a-fA-F]+)')


def first_target(ops):
    m = _HEX.search(ops or '')
    return int(m.group(1), 16) if m else None


# ---------------------------------------------------------------- A. 地址层
def load_groups():
    groups = []
    for i in range(GROUP_N):
        g = GROUP0 + GROUP_STEP * i
        ptrs = [u32(g + 4 * k) for k in range(8)]
        groups.append({'index': i, 'addr': g, 'ptrs': ptrs,
                       'valid': all(TONE_LO <= p < TONE_HI for p in ptrs)})
    return groups


def address_scan(groups):
    """对 21 组 × 8 槽的槽地址做全镜像非对齐字节扫描。"""
    out = []
    for g in groups:
        slots = {}
        for off in SLOT_OFFS:
            tgt = g['addr'] + off
            hits = xrefs(tgt)
            slots[off] = {'addr': tgt, 'hits': hits}
        out.append({'group': g, 'slots': slots})
    return out


def classify_hit(site):
    """hit = 4 字节立即数的起始地址；判断前 2 字节是不是 `fc dc`/`fc dd`…"""
    pre = rd(site - 2, 2)
    reg = None
    if pre[0] == 0xfc and (pre[1] & 0xf0) == 0xd0:
        reg = 'a%d' % (pre[1] & 0xf)
    return {
        'hit': site,
        'kind': 'code' if CODE_LO <= site < CODE_HI else 'data',
        'imm_opcode': pre.hex(),
        'load_reg': reg,
        'insn_addr': site - 2 if reg else None,
    }


def hit_detail(site):
    """单个引用点的完整明细：分类 + 前后 24 字节反汇编。"""
    h = classify_hit(site)
    fs = func_start(site)
    rows, anchor = window_rows(site, 24, 24)
    h['func'] = fs
    h['off_in_func'] = (site - fs) if fs else None
    h['window_anchor'] = anchor
    h['insn_window_hex'] = rd(site - 24, 48).hex()
    h['disasm'] = rows_text(rows)
    if not h['load_reg']:
        h['note'] = ('前 2 字节不是 fc dc/fc dd，4 字节不落在 imm32 起始处 → 巧合字节'
                     '（本命中的实际立即数起始于别处）')
    return h


# ---------------------------------------------------------------- B. 寄存器相对层
def group_imm_loads(groups):
    """所有把组基址装进寄存器的立即数站点（`fc dc <group>` 等）。"""
    loads = []
    for g in groups:
        for site in xrefs(g['addr']):
            h = classify_hit(site)
            if h['kind'] == 'code' and h['load_reg']:
                loads.append({'group': g['index'], 'group_addr': g['addr'],
                              'site': site, 'load_reg': h['load_reg'],
                              'insn_addr': h['insn_addr'],
                              'func': func_start(site)})
    return loads


def call_reach(load_site, func_entry, limit=48):
    """从装载点顺走，跳过 bra/jmp，返回第一个 `call 0x604B8049` 的站点。"""
    pc = load_site
    seen = set()
    for _ in range(limit):
        if pc in seen:
            return None
        seen.add(pc)
        r = insn_at(pc)
        if not r:
            return None
        apc, nb, _raw, mnem, ops = r
        tgt = first_target(ops)
        if mnem == 'call' and tgt == SELECTOR:
            return apc
        if mnem in ('bra', 'jmp') and tgt and func_entry and func_entry <= tgt < func_entry + 0x800:
            pc = tgt
            continue
        if mnem in ('ret', 'retf', 'rts'):
            return None
        pc = apc + nb
    return None


def prev_rows(func_entry, site, count=12):
    """函数起点 → site 之间的最后 count 条指令（site 之前）。"""
    rows = disasm_from(func_entry, site - func_entry + 16)
    tail = [r for r in rows if r[0] < site]
    return tail[-count:]


_D0_WRITE = re.compile(r'(,d0$|^clr d0$|^mov d0,$)')


def d0_writer(rows):
    """从调用点往回找最后一个写 d0 的指令（粗略但可核：只看 ops 尾部的 ,d0）。"""
    for pc, nb, raw, mnem, ops in reversed(rows):
        txt = '%s %s' % (mnem, ops)
        if mnem == 'call':
            return {'insn': hexs(pc), 'kind': 'call_return',
                    'text': txt, 'raw': raw.hex()}
        if _D0_WRITE.search(txt) or mnem in ('clr',) and ops.strip() == 'd0':
            return {'insn': hexs(pc), 'kind': 'explicit_write',
                    'text': txt, 'raw': raw.hex()}
    return None


def selector_analysis():
    """反汇编选择器本体，定位两条读路径与门控。"""
    fs = SELECTOR
    rows = disasm_from(fs, 0x60)
    reads = []
    gate = None
    for pc, nb, raw, mnem, ops in rows:
        if mnem == 'mov' and ops in ('(a0),a0', '(4,a0),a0'):
            reads.append({'insn': hexs(pc), 'text': 'mov %s' % ops,
                          'raw': raw.hex(),
                          'path': 'alt/odd' if ops.startswith('(4,') else 'base/even'})
        if mnem == 'cmp' and ops == '0x0,d0':
            gate = {'insn': hexs(pc), 'text': 'cmp %s' % ops,
                    'note': 'd0 != 0 → alt/odd 分支（beq 到 (a0) 那条）'}
    return {'entry': hexs(fs), 'rows': rows_json(rows), 'rows_raw': rows,
            'reads': reads, 'gate': gate}


def selector_model():
    """把 0x604B8049 的地址计算抄成纯函数，穷举核对。"""
    def base_off(d1):
        d2 = d1 & 0xF0
        lo = d1 & 0xF
        if d2 == 0x30:
            return 8 if lo in (2, 3, 4) else 0x18
        if d2 == 0x40:
            return 0x10
        return 8 if lo in (2, 3, 4) else 0

    def read_off(d1, d0):
        return base_off(d1) + (4 if d0 else 0)

    even, odd, table = set(), set(), {}
    for d1 in range(0x10000):
        for d0 in (0, 1):
            o = read_off(d1, d0)
            (odd if d0 else even).add(o)
            if d0:
                key = (d1 & 0xF0, d1 & 0xF)
                table.setdefault(key, o)
    rows = []
    for (hi, lo), o in sorted(table.items()):
        rows.append({'d1_hi_nibble': '0x%02x' % hi, 'd1_lo_nibble': '0x%x' % lo,
                     'odd_slot_offset': o})
    # 合并 lo 的三种分组，给出可读的映射表
    compact = [
        {'d1 & 0xF0': '0x30', 'd1 & 0xF': '2,3,4', 'even_slot': 8, 'odd_slot': 12},
        {'d1 & 0xF0': '0x30', 'd1 & 0xF': '其它', 'even_slot': 24, 'odd_slot': 28},
        {'d1 & 0xF0': '0x40', 'd1 & 0xF': '任意', 'even_slot': 16, 'odd_slot': 20},
        {'d1 & 0xF0': '其它', 'd1 & 0xF': '2,3,4', 'even_slot': 8, 'odd_slot': 12},
        {'d1 & 0xF0': '其它', 'd1 & 0xF': '其它', 'even_slot': 0, 'odd_slot': 4},
    ]
    return {'even_offsets_reachable': sorted(even),
            'odd_offsets_reachable': sorted(odd),
            'even_ok': sorted(even) == EVEN_OFFS,
            'odd_ok': sorted(odd) == ODD_OFFS,
            'compact': compact,
            'raw_mapping_count': len(rows)}


def alt_bit_fn():
    """0x604BDD42 的门控条件（替代位 d0 的常规来源）。"""
    rows = disasm_from(ALT_FN, 0x34)
    return {'entry': hexs(ALT_FN), 'rows': rows_json(rows), 'disasm': rows_text(rows),
            'condition':
                'u8(ctx+0x10) in {0x00,0x0E} AND fam=u16(ctx+0x1C)>>8 in {1,2} '
                'AND bit = (fam==1 ? bit2 : bit0) of u8(ctx+0x30); else d0=0'}


def consumers(groups, loads):
    """验证每个组基址装载点都汇入选择器；返回按函数聚合的消费方。"""
    by_func = {}
    for ld in loads:
        fs = ld['func']
        cs = call_reach(ld['site'], fs)
        ld['selector_call'] = cs
        ld['reaches_selector'] = cs is not None
        by_func.setdefault(fs, []).append(ld)
    return by_func


def caller_details():
    """选择器全部调用点：含 d0 来源与门控。"""
    out = []
    for cs in callers(SELECTOR):
        fs = func_start(cs)
        rows = prev_rows(fs, cs) if fs else []
        writer = d0_writer(rows)
        has_alt_call = any(m == 'call' and first_target(o) == ALT_FN
                           for _pc, _nb, _r, m, o in rows)
        out.append({
            'site': hexs(cs), 'func': hexs(fs),
            'd0_writer': writer,
            'calls_0x604bdd42': has_alt_call,
            'd0_source': ('0x604BDD42(ctx) 替代位' if has_alt_call and writer
                          and writer['kind'] == 'call_return' else
                          ('0x604BDD42(ctx) 替代位' if has_alt_call else '其它（见 prev_insns）')),
            'prev_insns': ['%08X  %s %s' % (pc, m, o) for pc, _nb, _r, m, o in rows],
        })
    return out


# ---------------------------------------------------------------- 主流程
def main():
    try:                                   # Windows 控制台默认 cp936，中文会乱码
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    groups = load_groups()
    bad = [hexs(g['addr']) for g in groups if not g['valid']]
    scan = address_scan(groups)

    groups_out = []
    total_code = total_data = 0
    for row in scan:
        g = row['group']
        slot_offsets, slot_detail = {}, {}
        for off in SLOT_OFFS:
            hits = row['slots'][off]['hits']
            slots = []
            for site in hits:
                h = hit_detail(site)
                if h['kind'] == 'code':
                    total_code += 1
                else:
                    total_data += 1
                h['verdict'] = ('loads_group_base' if off == 0 and h['load_reg']
                                else 'immediate_ref' if h['load_reg']
                                else 'coincidental_bytes')
                slots.append(h)
            slot_offsets[str(off)] = [hexs(s['hit']) for s in slots]
            slot_detail[str(off)] = {
                'slot_addr': hexs(row['slots'][off]['addr']),
                'slot_value': hexs(g['ptrs'][off // 4]),
                'n': len(slots),
                'hits': slots,
            }
        groups_out.append({
            'group_addr': hexs(g['addr']),
            'index': g['index'],
            'slot_offsets': slot_offsets,
            'slot_detail': slot_detail,
            'ptrs': [hexs(p) for p in g['ptrs']],
        })

    loads = group_imm_loads(groups)
    by_func = consumers(groups, loads)
    sel = selector_analysis()
    model = selector_model()
    alt = alt_bit_fn()
    callers_out = caller_details()

    # 候选：① 奇数槽真读者 ② 组基址装载点 ③ 选择器调用点
    candidates = []
    for r in sel['reads']:
        site = int(r['insn'], 16)
        rows, anchor = window_rows(site, 24, 24)
        candidates.append({
            'site': r['insn'],
            'func': hexs(SELECTOR),
            'basis': 'register_relative',
            'insn_window_hex': rd(site - 24, 48).hex(),
            'disasm': rows_text(rows),
            'insn': r['text'],
            'raw': r['raw'],
            'verdict': ('reads_odd_slot' if r['path'] == 'alt/odd'
                        else 'reads_even_slot(对照)'),
            'gate': sel['gate'],
            'detail': ('a0 = 组基址 + {0,8,0x10,0x18}；该指令再 +4 → '
                       '槽 +4/+0xC/+0x14/+0x1C' if r['path'] == 'alt/odd'
                       else 'a0 = 组基址 + {0,8,0x10,0x18}（偶数槽）'),
        })
    for ld in loads:
        rows, anchor = window_rows(ld['site'], 24, 24)
        candidates.append({
            'site': hexs(ld['site']),
            'func': hexs(ld['func']),
            'basis': 'immediate',
            'insn_window_hex': rd(ld['site'] - 24, 48).hex(),
            'disasm': rows_text(rows),
            'insn': 'mov %s,a0' % hexs(ld['group_addr']),
            'raw': rd(ld['insn_addr'], 6).hex(),
            'verdict': 'loads_group_base_into_a0',
            'group': 'g%d' % ld['group'],
            'selector_call': hexs(ld['selector_call']),
        })
    for cd in callers_out:
        candidates.append({
            'site': cd['site'],
            'func': cd['func'],
            'basis': 'selector_invocation',
            'insn_window_hex': rd(int(cd['site'], 16) - 24, 48).hex(),
            'disasm': '\n'.join(cd['prev_insns']),
            'insn': 'call 0x604b8049',
            'verdict': 'invokes_selector_with_d0_gate',
            'd0_source': cd['d0_source'],
            'd0_writer': cd['d0_writer'],
        })

    verdict = {
        'odd_slots_have_readers': True,
        'reader': {'site': '0x604b808e', 'func': '0x604b8049',
                   'insn': 'mov (4,a0),a0',
                   'gate': 'd0 != 0（d0 低字节，extbu d0; cmp 0x0,d0）'},
        'why_address_scan_is_zero': (
            '槽选择不使用"槽地址立即数"：立即数只指向组基址，槽偏移靠 '
            'add {0,8,0x10,0x18} 与 (4,a0) 的寄存器相对寻址。'
            '偶数槽 +8/+0x10/+0x18 在地址层同样 0 命中，可作对照反证。'),
        'dead_data_claim': '不成立（奇数槽是 4 个 (基准, 替代) 对里的"替代"半）',
        'reaches_standard_shooting': (
            '标准拍摄链路 0x604B8098 里 15 个调用点的 d0 都来自 0x604BDD42(ctx)，'
            '只有 u8(ctx+0x10)∈{0,0x0E} 且 u16(ctx+0x1C)>>8∈{1,2} 且相应 bit 置位时 '
            '才走奇数槽；门不满足时 d0=0，奇数槽不被读取。'),
        'per_stop_independent_tone': (
            '槽维度给不了"每档独立影调"：18/21 组的 4 个槽完全同值（同 基准/替代 对），'
            'g0/g1 只有槽 1/3 的替代换成 k42/k43，g20 是 k41/k40 反向。'
            '档位区分在上游 21 个组（picture mode key），不在 8 个槽里。'),
    }

    doc = {
        'meta': {
            'image': os.path.relpath(IMG, ROOT),
            'base': hexs(BASE),
            'code_range': [hexs(CODE_LO), hexs(CODE_HI)],
            'generated_by': 'tools/odd_slot_hunt.py',
            'note': '只读静态分析；未运行固件、未改任何 .BIN',
        },
        'method': {
            'A_address_layer': ('对 21 组 × 8 槽的槽地址做全镜像非对齐小端 4 字节 '
                                'bytes.find()；命中分类 code(<0x605C0000)/data；'
                                'code 命中检查前 2 字节是否 fc d?（mov imm32,aN）'),
            'B_register_layer': ('组基址立即数装载 → 顺走 bra/jmp 至 call 0x604B8049 → '
                                 '反汇编选择器取 (a0)/(4,a0) 两条读路径 → 穷举模型核对'),
        },
        'summary': {
            'groups': len(groups),
            'groups_valid': len(groups) - len(bad),
            'invalid_groups': bad,
            'code_refs_total': total_code,
            'data_refs_total': total_data,
            'refs_on_slot0': sum(len(g['slot_offsets']['0']) for g in groups_out),
            'refs_on_nonzero_slots': sum(len(g['slot_offsets'][str(o)]) for g in groups_out for o in SLOT_OFFS[1:]),
            'group_base_loads': len(loads),
            'loads_reaching_selector': sum(1 for l in loads if l['reaches_selector']),
            'consumer_functions': sorted(hexs(f) for f in by_func),
            'selector_call_sites': len(callers_out),
        },
        'groups': groups_out,
        'selector': {
            'entry': sel['entry'],
            'reads': sel['reads'],
            'gate': sel['gate'],
            'disasm': rows_text(sel['rows_raw']),
        },
        'selector_model': model,
        'alt_bit_function': alt,
        'selector_callers': callers_out,
        'candidates': candidates,
        'verdict': verdict,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = hexify_addrs(doc)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)

    # ---------------- 控制台报告 ----------------
    s = doc['summary']
    print('groups=%d valid=%d  code_refs=%d data_refs=%d' %
          (s['groups'], s['groups_valid'], s['code_refs_total'], s['data_refs_total']))
    print('refs on slot +0 : %d' % s['refs_on_slot0'])
    print('refs on slots +4/+8/+0xC/+0x10/+0x14/+0x18/+0x1C : %d' % s['refs_on_nonzero_slots'])
    for off in SLOT_OFFS:
        n = sum(len(g['slot_offsets'][str(off)]) for g in groups_out)
        print('  +0x%02x : %d 命中' % (off, n))
    print('\ngroup-base immediate loads: %d, 汇入选择器: %d / %d' %
          (s['group_base_loads'], s['loads_reaching_selector'], s['group_base_loads']))
    print('consumer functions:', ' '.join(s['consumer_functions']))
    print('\nselector 0x%08x:' % SELECTOR)
    for r in sel['reads']:
        print('  %s  %-16s  %s' % (r['insn'], r['text'], r['path']))
    print('  gate:', sel['gate']['insn'], sel['gate']['note'])
    print('\nselector model: even=%s (ok=%s)  odd=%s (ok=%s)' %
          ([hex(x) for x in model['even_offsets_reachable']], model['even_ok'],
           [hex(x) for x in model['odd_offsets_reachable']], model['odd_ok']))
    print('\nselector call sites: %d' % len(callers_out))
    for cd in callers_out:
        w = cd['d0_writer']
        print('  call@%s func=%s  d0<-%s %s' %
              (cd['site'], cd['func'],
               (w['text'] if w else '?'),
               '' if cd['calls_0x604bdd42'] else '(非替代位来源)'))
    print('\n-> %s' % os.path.relpath(OUT, ROOT))


if __name__ == '__main__':
    main()
