#!/usr/bin/env python3
import hashlib, json, struct, sys
sys.path.insert(0,'tools')
import oly_container as oc, verify_b32_chain as V
BASE=0x60021020
path=sys.argv[1] if len(sys.argv)>1 else 'build/B82_hsl_gateprobe.BIN'
whole=open(path,'rb').read(); img=oc.parse(whole)[0].payload
def at(a,n): o=a-BASE; return img[o:o+n]
HOOK=0x604B9756; STUB=0x601ADE04
res={}
res['hook']=at(HOOK,5).hex()=='dcae46cfff'
res['stub_magic']=at(STUB+8,6).hex()=='fccc42383200'
res['diff_only_patch']=True
simg=oc.parse(open('stock/OLY_E_120_1400_0000_0000.BIN','rb').read())[0].payload
res['diff_only_patch']=all(HOOK-BASE<=i<HOOK-BASE+5 or STUB-BASE<=i<STUB-BASE+52
    for i in range(len(img)) if img[i]!=simg[i])
PB,CTX,PTR=0x61E10000,0x61E11000,0x61E12000
def run(gate):
    pokes=[(PB+4,CTX.to_bytes(4,'little')),(PB+0x38,(0x10000000).to_bytes(4,'little')),
           (CTX+0x0C,(0x0201).to_bytes(4,'little')),
           (CTX+0x3C0,(PTR if gate else 0).to_bytes(4,'little')),
           (PTR,(1).to_bytes(4,'little'))]
    bp=0x604B975B if gate else 0x604B9786
    d=V.sim(img,0x604B974D,bp,limit=3000,pokes=pokes,regs={'A0':PB},extra_rwz=('0x61894000:0x2000',))
    e=V.mem_bytes(d,0x61894CA0,24)
    return d.get('fault'),e
f1,e1=run(True); f0,e0=run(False)
res['sim_fault']=f1 or f0
res['magic']=e1[:4]==b'B82\x00'
res['gate_val']=struct.unpack('<I',e1[4:8])[0]==PTR
res['pb']=struct.unpack('<I',e1[8:12])[0]==PB
res['ctx']=struct.unpack('<I',e1[12:16])[0]==CTX
res['mode']=struct.unpack('<H',e1[16:18])[0]==0x0201
res['pb38']=struct.unpack('<I',e1[20:24])[0]==0x10000000
res['gate0']=struct.unpack('<I',e0[4:8])[0]==0
allok=all(v is True for k,v in res.items() if k!='sim_fault')
rec={'pkg':path,'sha256':hashlib.sha256(whole).hexdigest(),'verifier':'tools/verify_b82.py',
     'closed':bool(allok),'checks':res,'evidence':'libsim 两态（门开/门关）'}
open(path+'.sim.json','w',encoding='utf-8').write(json.dumps(rec,ensure_ascii=False,indent=2))
for k,v in res.items(): print('  %-16s %s'%(k,v))
print('closed',bool(allok))
