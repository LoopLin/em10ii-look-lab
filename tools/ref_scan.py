#!/usr/bin/env python3
"""Unaligned 4-byte pointer scan (catches immediates inside instructions)."""
import json, struct
IMG='analysis/00_0100_40300020.decoded.bin'; BASE=0x60021020; CODE_END=0x605C0000
d=open(IMG,'rb').read()
def find_all(addr):
    pat=struct.pack('<I',addr); out=[]; i=d.find(pat)
    while i!=-1:
        out.append(BASE+i); i=d.find(pat,i+1)
    return out
def families():
    f={}
    f.update({'tone_%02d'%i: 0x605D8344+i*0x200 for i in range(64)})
    f.update({'group_%d'%i: 0x60600920+i*0x20 for i in range(5)})
    f.update({'chroma_%02d'%i: 0x605D4829+i*0x64 for i in range(65)})
    f.update({'sixaxis':0x605ECF5A,'hsl_flat':0x605F0798,'hsl_real':0x605F5A64,
              'chroma_ptr':0x605FFDC8,'matrix_area':0x60DB0000,'lut_ident':0x605D8144})
    return f
res={}
for name,a in families().items():
    hs=find_all(a)
    if hs: res[name]={'addr':hex(a),'n':len(hs),'sites':[hex(h) for h in hs],
                      'code':[hex(h) for h in hs if h<CODE_END]}
json.dump(res, open('docs/map/ref-scan.json','w'), indent=1)
tot=sum(v['n'] for v in res.values()); cod=sum(len(v['code']) for v in res.values())
print('targets hit: %d, total sites: %d, code sites: %d' % (len(res), tot, cod))
for k in ('group_0','group_1','group_2','group_3','group_4','sixaxis','lut_ident','chroma_ptr','hsl_flat','hsl_real'):
    if k in res: print('%-10s %-10s total %2d  code %s' % (k, res[k]['addr'], res[k]['n'], res[k]['code']))
