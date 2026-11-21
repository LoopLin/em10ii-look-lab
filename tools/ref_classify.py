#!/usr/bin/env python3
"""Classify reference sites as code/data and annotate the immediate instruction."""
import json, struct, sys
IMG='analysis/00_0100_40300020.decoded.bin'; BASE=0x60021020
CODE_END=0x605C0000   # heuristic: everything below is main-block code
d=open(IMG,'rb').read()
idx=json.load(open('docs/map/ref-index.json'))
def at(a,n=8): return d[a-BASE:a-BASE+n]
rows=[]
for fam,tabs in idx['families'].items():
    for name,info in tabs.items():
        for r in info['refs']:
            a=int(r,16)
            site=at(a,4)
            kind='code' if a<CODE_END else 'data'
            # look back up to 8 bytes for `fc dc/cd/dd <imm32>` style
            ctx=None
            for back in (0,1,2,3,4):
                p=a-back
                if back and d[p-BASE-1:p-BASE]==b'\xfc':
                    op=d[p-BASE:p-BASE+1].hex()
                    ctx={'at':hex(p-1),'op':'fc '+op}
                    break
            rows.append({'family':fam,'table':name,'table_addr':info['addr'],
                         'site':hex(a),'kind':kind,'ctx':ctx})
code=[r for r in rows if r['kind']=='code']
data=[r for r in rows if r['kind']=='data']
print('code refs %d / data refs %d' % (len(code), len(data)))
from collections import Counter
print('code refs by family:', Counter(r['family'] for r in code))
print()
for r in sorted(code,key=lambda x:int(x['site'],16))[:60]:
    c=r['ctx']['op'] if r['ctx'] else ''
    print('%s  %-12s %-10s %s %s' % (r['site'], r['family'], r['table'], r['table_addr'], c))
json.dump(rows, open('docs/map/ref-sites.json','w'), indent=1)
print('\nwrote docs/map/ref-sites.json')
