#!/usr/bin/env python3
import hashlib, json, struct, sys
sys.path.insert(0,'tools')
import oly_container as oc, verify_b32_chain as V
BASE=0x60021020
path = sys.argv[1] if len(sys.argv)>1 else 'build/B81_hsl_probe.BIN'
whole=open(path,'rb').read(); img=oc.parse(whole)[0].payload
def at(a,n): o=a-BASE; return img[o:o+n]
HOOK=0x604B97F9; STUB=0x601ADE04
res={}
res['hook']=at(HOOK,7).hex()=='dc0b46cfffcbcb'
res['stub_magic']=at(STUB+14,6).hex()=='fccc42383100'
res['stub_tail']=at(STUB+52,5).hex()=='dcc8b93000'
# diff vs stock
stock=open('stock/OLY_E_120_1400_0000_0000.BIN','rb').read()
simg=oc.parse(stock)[0].payload
diffs=[i for i in range(len(img)) if img[i]!=simg[i]]
res['diff_only_patch']=all(HOOK-BASE<=d<HOOK-BASE+7 or STUB-BASE<=d<STUB-BASE+57 for d in diffs)
PB,CTX=0x61E10000,0x61E11000
def run(flag):
    pokes=[(PB+4,CTX.to_bytes(4,'little')),(PB+0x30,(0x0101006C).to_bytes(4,'little')),
           (CTX+0x0C,(0x0201).to_bytes(4,'little')),(CTX+0x24A,bytes([flag]))]
    d=V.sim(img,0x604B97F8,0x604B9800,limit=5000,pokes=pokes,regs={'A0':PB},extra_rwz=('0x61894000:0x2000',))
    e=V.mem_bytes(d,0x61894CA0,20)
    return d.get('fault'), e
f1,e1=run(1); f0,e0=run(0)
res['sim_fault']=f1 or f0
res['magic']=e1[:4]==b'B81\x00'
res['flag1']=e1[4:8]==(1).to_bytes(4,'little')
res['mode']=struct.unpack('<I',e1[8:12])[0]==0x0201
res['key']=struct.unpack('<I',e1[12:16])[0]==0x0101006C
res['counter']=struct.unpack('<I',e1[16:20])[0]>0
res['flag0']=e0[4:8]==(0).to_bytes(4,'little')
allok=all(v is True for k,v in res.items() if k!='sim_fault')
rec={'pkg':path,'sha256':hashlib.sha256(whole).hexdigest(),'verifier':'tools/verify_b81.py','closed':bool(allok),'checks':res,'evidence':'libsim 单次（选择器入口探针）'}
open(path+'.sim.json','w',encoding='utf-8').write(json.dumps(rec,ensure_ascii=False,indent=2))
for k,v in res.items(): print('  %-14s %s'%(k,v))
print('closed',bool(allok))
