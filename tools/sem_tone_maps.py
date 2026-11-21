#!/usr/bin/env python3
"""sem_tone_maps.py — 两张哈希派发表 + bank2 曲线族的机械映射。
输出 JSON 到 .tmp/semantics/tone_maps.json，方便复核。"""
import json, os, sys, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mn10300_dis as M

BASE = 0x60021020
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(HERE, 'analysis', '00_0100_40300020.decoded.bin')
data = open(IMG, 'rb').read()
D = M.Decoder()

def u16(a): return int.from_bytes(data[a-BASE:a-BASE+2], 'little')
def u32(a): return int.from_bytes(data[a-BASE:a-BASE+4], 'little')
def raw(a, n): return data[a-BASE:a-BASE+n]

def dis_from(a, n=0x60, maxins=40):
    """线性反汇编一段，返回 [(addr, text, size)]"""
    out = []; off0 = a - BASE; off = 0
    while off < n and len(out) < maxins:
        r = D.match(data, off0+off, a+off)
        if not r: break
        blen, mnem, ops, rule = r
        out.append((a+off, (mnem + (' ' + ','.join(ops) if ops else '')), blen))
        off += blen
    return out

TONE1 = 0x60600920           # 21x8 组指针表
BANK1 = 0x605D8344
BANK2 = 0x605DEF4E
BANK1_N = 168                # 保守：只按指针表算

def bank2_index(addr):
    if BANK2 <= addr < BANK2 + 0x200*64 and (addr - BANK2) % 0x200 == 0:
        return (addr - BANK2)//0x200
    return None
def bank1_index(addr):
    if BANK1 <= addr and (addr - BANK1) % 0x200 == 0:
        return (addr - BANK1)//0x200
    return None

def scan_movs(a, n=0x60):
    """从 a 起线性扫到 ret，收集 mov imm,a0 / mov imm,d0 的立即数"""
    imms = []
    for addr, ins, size in dis_from(a, n):
        t = ins
        m = re.search(r'0x([0-9a-f]{5,8})', t)
        if t.startswith('ret'): imms.append(('RET', t)); break
        if m: imms.append((addr, t))
    return imms

def dump_table(tab, slots, stride=8, keybase=0x01010065):
    ents = []
    for i in range(slots):
        k = u32(tab + stride*i); h = u32(tab + stride*i + 4)
        if k == 0 and h == 0: continue
        d1 = k + keybase
        ents.append({'slot': i, 'stored': k, 'd1': d1,
                     'b0': (d1 >> 24) & 0xFF, 'b1': (d1 >> 16) & 0xFF, 'hw': d1 & 0xFFFF,
                     'handler': h})
    return ents

if __name__ == '__main__':
    out = {}
    out['tableA'] = dump_table(0x60601C44, 16, 8, 0x01010065)
    out['tableB'] = dump_table(0x606020E8, 128, 8, 0x01010063)
    # handler -> 返回的曲线
    for name in ('tableA', 'tableB'):
        for e in out[name]:
            h = e['handler']
            imms = scan_movs(h, 0x50)
            e['trace'] = [t for _, t in imms if isinstance(_, int)][:6]
            curves = []
            for a, t in imms:
                m = re.search(r'0x([0-9a-f]{8})', t)
                if not m: continue
                v = int(m.group(1), 16)
                if bank2_index(v) is not None: curves.append(('bank2', bank2_index(v), v))
                elif bank1_index(v) is not None: curves.append(('bank1', bank1_index(v), v))
                elif 0x60600920 <= v <= 0x60600A10: curves.append(('ptrtab', (v-0x60600920)//8, v))
            e['curves'] = curves
    print(json.dumps(out, indent=1)[:200])
    os.makedirs(os.path.join(HERE, '.tmp', 'semantics'), exist_ok=True)
    with open(os.path.join(HERE, '.tmp', 'semantics', 'tone_maps.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print('wrote .tmp/semantics/tone_maps.json')
