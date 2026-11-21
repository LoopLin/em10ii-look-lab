#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b78.py — B78 标准档强开 HSL（自造数据集 = n4 清零 + 钩 0x604B974D）"""
import hashlib, json, struct, sys
sys.path.insert(0,'tools')
import oly_container as oc, verify_b32_chain as V
BASE=0x60021020
path = sys.argv[1] if len(sys.argv)>1 else 'build/B78_hsl_force.BIN'
whole=open(path,'rb').read(); img=oc.parse(whole)[0].payload
def at(a,n): o=a-BASE; return img[o:o+n]
HOOK=0x604B974D; STUB=0x605DEE4C; STRUCT=0x605DEDF4; N4=0x605F0798
res={}
res['hook_jmp']=at(HOOK,5).hex()=='dcff561200'
res['stub_head']=at(STUB,3).hex()=='92fcdc'
res['stub_tail']=at(STUB+27-5,5).hex()=='dc04a9edff'
res['struct_self']=struct.unpack('<I',at(STRUCT,4))[0]==STRUCT
res['struct_ds']=struct.unpack('<I',at(STRUCT+16,4))[0]==N4
sizes=[0x180,0x180,0xC0,0xC0,0xC0,0x180,0x180,0xC0,0xC0,0xC0]
offs=[0x150,0x2D0,0x450,0x510,0x5D0,0x690,0x810,0x990,0xA50,0xB10]
res['tables_zero']=all(set(at(N4+o,n))=={0} for o,n in zip(offs,sizes))
res['mask_07df']=at(N4+0xBD0,2).hex()=='df07'
PB,PL,OUT=0x61E10000,0x61E11000,0x61E12000
pokes=[(PB+4,PL.to_bytes(4,'little')),(PB+12,OUT.to_bytes(4,'little'))]
d=V.sim(img,HOOK,0x604B977C,limit=30000,pokes=pokes,regs={'A0':PB},extra_rwz=('0x61894000:0x2000',))
res['sim_fault']=d.get('fault')
m=V.mem_bytes(d,OUT+0x1434,0x28)
ptrs=[struct.unpack('<I',m[i:i+4])[0] for i in range(0,0x28,4)]
res['sim_ds_n4']=all(N4<=p<N4+0xBD4 for p in ptrs)
res['sim_mask']=struct.unpack('<I',V.mem_bytes(d,OUT+0x3C4,4))[0]==0x7DF
allok=all(v is True for k,v in res.items() if k!='sim_fault')
rec={'pkg':path,'sha256':hashlib.sha256(whole).hexdigest(),'verifier':'tools/verify_b78.py','closed':bool(allok),'checks':res,'evidence':'libsim 单次（钩子→安装）'}
open(path+'.sim.json','w',encoding='utf-8').write(json.dumps(rec,ensure_ascii=False,indent=2))
for k,v in res.items(): print('  %-14s %s'%(k,v))
print('closed',bool(allok))
