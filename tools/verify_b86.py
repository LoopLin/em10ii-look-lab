#!/usr/bin/env python3
import hashlib, json, struct, sys
sys.path.insert(0,'tools')
import oly_container as oc, verify_b32_chain as V
BASE=0x60021020
path = sys.argv[1] if len(sys.argv)>1 else 'build/B86_hsl_fields.BIN'
whole=open(path,'rb').read(); img=oc.parse(whole)[0].payload
def at(a,n): o=a-BASE; return img[o:o+n]
HOOK=0x604B974D; STUB=0x605DEE4C
stub=bytes.fromhex('92f82e0425c003f17dfcdca04c8961fccc4238360060fa434a02f81004f84310f81005f8630cf81006f80308f81008fa03c003f8100cf83810f8020cf81014dccba8edff')
res={}
res['hook']=at(HOOK,5).hex()=='dcff561200'
res['stub']=at(STUB,len(stub))==stub
stock=open('stock/OLY_E_120_1400_0000_0000.BIN','rb').read(); simg=oc.parse(stock)[0].payload
diffs=[i for i in range(len(img)) if img[i]!=simg[i]]
res['diff_only_patch']=all(HOOK-BASE<=d<HOOK-BASE+5 or STUB-BASE<=d<STUB-BASE+len(stub) for d in diffs)
PB,CTX=0x61E10000,0x61E11000
pokes=[(PB+4,CTX.to_bytes(4,'little')),(PB+0x0C,(0x61E20000).to_bytes(4,'little')),
       (CTX+0x0C,(0xF00C).to_bytes(2,'little')),(CTX+0x10,bytes([3])),
       (CTX+0x24A,bytes([1])),(CTX+8,(0x01010067).to_bytes(4,'little')),
       (CTX+0x3C0,(0x605F0000).to_bytes(4,'little'))]
d=V.sim(img,HOOK,0x604B9756,limit=200,pokes=pokes,regs={'A0':PB},extra_rwz=('0x61894000:0x2000',))
e=V.mem_bytes(d,0x61894CA0,24)
res['sim_fault']=d.get('fault')
res['magic']=e[:4]==b'B86\x00'
res['flag']=e[4]==1
res['family']=e[5]==3
res['mode']=struct.unpack('<H',e[6:8])[0]==0xF00C
res['key']=struct.unpack('<I',e[8:12])[0]==0x01010067
res['gate']=struct.unpack('<I',e[12:16])[0]==0x605F0000
res['param']=struct.unpack('<I',e[16:20])[0]==PB
res['out']=struct.unpack('<I',e[20:24])[0]==0x61E20000
allok=all(v is True for k,v in res.items() if k!='sim_fault') and res['sim_fault'] is None
rec={'pkg':path,'sha256':hashlib.sha256(whole).hexdigest(),'verifier':'tools/verify_b86.py','closed':bool(allok),'checks':res,'evidence':'libsim 单次（HSL 入口 0x604B974D，停在 0x604B9756）'}
open(path+'.sim.json','w',encoding='utf-8').write(json.dumps(rec,ensure_ascii=False,indent=2))
for k,v in res.items(): print('  %-14s %s'%(k,v))
print('closed',bool(allok))
