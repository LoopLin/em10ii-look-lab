#!/usr/bin/env python3
"""Assemble docs/map/ATLAS.md from the scan results."""
import json, collections
scan=json.load(open('docs/map/ref-scan.json'))
sites=json.load(open('docs/map/code-sites.json'))
byfunc=collections.defaultdict(list)
for r in sites:
    byfunc[r['func'] or 'unknown'].append(r)
lines=[]
A=lines.append
A('# E-M10 II 固件地图（atlas 骨架）\n')
A('生成时间：2026-11-15 · 镜像 `analysis/00_0100_40300020.decoded.bin`（21,364,672 B，基址 `0x60021020`）\n')
A('## 1. 段与地址约定\n')
A('| 项 | 值 |\n|---|---|')
A('| 镜像基址 | `0x60021020` |')
A('| 文件偏移 → RAM | `RAM = 0x60021020 + offset` |')
A('| 代码段（经验划分） | `< 0x605C0000` |')
A('| 表/数据区 | `0x605C0000 .. 0x61000000` |')
A('\n## 2. 表家族（已定位）\n')
A('| 家族 | 起始地址 | 步长 | 数量 | 被引用的表数 | 引用总数 | 代码站点 |\n|---|---|---|---|---|---|---|')
fam_meta={'tone_':'tone 曲线系数表 256×u16','group_':'tone 组表 168×u32','chroma_':'色度块 100 B','sixaxis':'六轴记录 24 B','hsl_flat':'HSL 平表数据集','hsl_real':'HSL 真表缓冲','chroma_ptr':'色度指针表 8×12','matrix_area':'矩阵区','lut_ident':'恒等 LUT'}
fam_desc={'tone_':'tone 曲线表 256×u16','group_':'tone 组表 168×u32','chroma_':'色度块 100 B','sixaxis':'六轴记录 24 B','hsl_flat':'HSL 平表数据集','hsl_real':'HSL 真表缓冲','chroma_ptr':'色度指针表 8×12','matrix_area':'矩阵区','lut_ident':'恒等 LUT'}
import re
def famof(n):
    for k in ('tone_','group_','chroma_'):
        if n.startswith(k) and not n.startswith('chroma_ptr'): return k
    return n
groups=collections.defaultdict(list)
for n,v in scan.items(): groups[famof(n)].append((n,v))
for fam in ('tone_','group_','chroma_','sixaxis','hsl_flat','hsl_real','chroma_ptr','lut_ident','matrix_area'):
    if fam not in groups: continue
    items=groups[fam]
    if fam=='group_':
        for n,v in sorted(items):
            A('| %s | `%s` | 0x20 | 1 | 1 | %d | %s |' % (n, v['addr'], v['n'], ', '.join('`%s`'%c for c in v['code']) or '—'))
        continue
    tgt=sum(v['n'] for _,v in items); cod=sum(len(v['code']) for _,v in items)
    addr0=sorted(items, key=lambda x:int(x[1]['addr'],16))[0][1]['addr']
    stride={'tone_':0x200,'chroma_':0x64}.get(fam,'—')
    A('| %s | `%s` | %s | %d | %d | %d | %d |' % (fam_desc.get(fam,fam), addr0, hex(stride) if isinstance(stride,int) else stride, len(items), len(items), tgt, cod))
A('\n## 3. 代码站点 → 所属函数\n')
A('| 函数起点 | 站点数 | 涉及的家族 | 首个站点 |\n|---|---|---|---|')
for f,rs in sorted(byfunc.items(), key=lambda kv: int(kv[0],16) if kv[0].startswith('0x') else 1<<62):
    fams=collections.Counter(r['family'] if 'family' in r else r['table'] for r in rs)
    names=collections.Counter()
    for r in rs:
        n=r['table']
        names[famof(n) if n.startswith(('tone_','group_','chroma_')) else n]+=1
    A('| `%s` | %d | %s | `%s` |' % (f, len(rs), ', '.join('%s×%d'%(k,v) for k,v in names.items()), rs[0]['site']))
A('\n## 4. 各表引用明细\n')
for fam in ('group_','sixaxis','chroma_ptr','hsl_flat','hsl_real','lut_ident'):
    if fam not in groups: continue
    for n,v in sorted(groups[fam]):
        A('- **%s** `%s` — %d 处：%s' % (n, v['addr'], v['n'], ', '.join('`%s`'%x for x in v['sites'])))
A('\n## 5. 已知空缺\n')
A('- 45 个代码站点里 9 个没定位到函数起点（`func=None`），它们的 `retf` 更远或走 `ret`。')
A('- 组表 `0x60600920` 有 3 处代码引用（`0x604b835a`/`0x604baa7e`/`0x604bae47`），分别落在 `0x604b8096`、`0x604ba8a2` 与未定位函数，尚未确认哪个对应"标准拍摄"。')
A('- 色度块 `chroma_05..64` 有 60 块无直接代码引用：只被 `0x605FFDC8` 指针表间接选取，`chroma_00..04` 才有硬编码立即数。')
A('- 运行时写者清单仍为空：需修 `tools/em10_run.py` 的区段映射后，用 `--rw/--dump` 监听 store。')
open('docs/map/ATLAS.md','w',encoding='utf-8').write('\n'.join(lines)+'\n')
print('wrote docs/map/ATLAS.md  (%d lines)' % len(lines))
