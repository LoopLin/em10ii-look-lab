#!/usr/bin/env python3
"""For each code site referencing a table, find enclosing function start + nearby calls."""
import json, struct
IMG='analysis/00_0100_40300020.decoded.bin'; BASE=0x60021020; CODE_END=0x605C0000
d=open(IMG,'rb').read()
def h(a,n): return d[a-BASE:a-BASE+n]
def func_start(a, back=0x400):
    """scan back for 'de' (retf) then skip padding to first non-ff/00 byte"""
    for p in range(a, max(BASE,a-back), -1):
        if d[p-BASE]==0xde:
            q=p+1
            # retf takes up to 2 operand bytes; skip them then padding
            while q<a and d[q-BASE] in (0x00,0xff): q+=1
            return q, a-q
    return None, None
idx=json.load(open('docs/map/ref-scan.json'))
rows=[]
for name,info in idx.items():
    for s in info['code']:
        a=int(s,16)
        st,off = func_start(a)
        rows.append({'table':name,'table_addr':info['addr'],'site':hex(a),
                     'func':hex(st) if st else None,'off_in_func':off,
                     'bytes_before':h(a-8,8).hex(),'bytes_after':h(a+4,12).hex()})
rows.sort(key=lambda r:int(r['site'],16))
for r in rows:
    print('%s %-12s func=%s +%s  pre=%s post=%s' % (r['site'], r['table'], r['func'], r['off_in_func'], r['bytes_before'], r['bytes_after']))
json.dump(rows, open('docs/map/code-sites.json','w'), indent=1)
print('\n%d sites -> docs/map/code-sites.json' % len(rows))
