#!/usr/bin/env python3
"""Walk pointer ancestry upward: who points at X, and at that, up to N levels."""
import json, struct, sys
IMG='analysis/00_0100_40300020.decoded.bin'; BASE=0x60021020
CODE_END=0x605C0000
d=open(IMG,'rb').read()
n=len(d)//4
dw=struct.unpack('<%dI'%n, d[:n*4])
pos={}
for i,v in enumerate(dw): pos.setdefault(v,[]).append(BASE+i*4)

def refs(a): return pos.get(a,[])

def walk(a, depth, seen=None, path=None, out=None):
    seen = seen if seen is not None else set()
    out  = out if out is not None else []
    path = path or []
    if depth==0 or a in seen: return out
    seen.add(a)
    for r in refs(a):
        kind='code' if r<CODE_END else 'data'
        out.append({'from':hex(r),'kind':kind,'level':depth,'target':hex(a),'path':path+[hex(a)]})
        if kind=='data':
            walk(r, depth-1, seen, path+[hex(a)], out)
    return out

def main():
    targets = {'group_0':0x60600920,'group_1':0x60600940,'group_3':0x60600980,
               'chroma_00':0x605D4829,'sixaxis':0x605ECF5A}
    res={}
    for k,a in targets.items():
        res[k]={'addr':hex(a),'chain':walk(a,3)}
    json.dump(res, open('docs/map/ptr-chains.json','w'), indent=1)
    for k,v in res.items():
        ch=v['chain']
        coden=[c for c in ch if c['kind']=='code']
        print('%-10s %s : %d ancestors, %d code sites' % (k, v['addr'], len(ch), len(coden)))
        for c in coden[:6]: print('    code', c['from'], 'level', c['level'], '<-', ' -> '.join(c['path']))
if __name__=='__main__': main()
