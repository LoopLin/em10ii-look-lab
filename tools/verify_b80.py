#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b80.py — B80 = B79 + 置脏位 0x10000（强制走 0x9E00CC00 第二路）"""
import hashlib, json, struct, sys
sys.path.insert(0, 'tools')
import oly_container as oc, verify_b32_chain as V
BASE=0x60021020; B69='build/B69_slot1.BIN'
HOOK,STUB,STRUCT=0x604B974D,0x605DEE4C,0x605DEDF4
N4=0x605F0798; OFFS=[0x150,0x690,0x2D0,0x450,0x810,0x990,0x510,0xA50,0x5D0,0xB10]
EXIF=0x61894CA0; O,R,OUT=0x61E30000,0x61E33000,0x61E32000
def at(buf,a,n): o=a-BASE; return bytes(buf[o:o+n])
def main():
    path=sys.argv[1] if len(sys.argv)>1 else 'build/B80_hsl_cc00.BIN'
    img=oc.parse(open(path,'rb').read())[0].payload
    b69=oc.parse(open(B69,'rb').read())[0].payload
    exp='929921648004f081f8260871fce40000010061fcdca04c8961fccc42383000f81010fcddf4ed5d60dcf2a8edff'
    res={}
    res['hook']=at(img,HOOK,5).hex()=='dcff561200'
    res['stub']=at(img,STUB,45).hex()==exp
    res['structure']=at(img,STRUCT,20).hex()=='f4ed5d60'+'00'*12+'98075f60'
    res['sd_hook']=at(img,0x604B811D,5).hex()=='dc276c1200'
    res['su_stub']=at(img,0x605DECD4,8).hex()=='70fcde70ed5d6072'
    res['n4_zero']=all(at(img,N4+o,n)==b'\x00'*n for o,n in [(0x150,0x180),(0x2D0,0x180),(0x690,0x180),(0x810,0x180)])
    res['n4_mask']=at(img,N4+0xBD0,2).hex()=='df07'
    pokes=[(O+4,(0x61E31000).to_bytes(4,'little')),(O+8,R.to_bytes(4,'little')),
           (O+0x0C,OUT.to_bytes(4,'little')),(R,b'\x00'*4)]
    d=V.sim(img,HOOK,0x604B977C,limit=200000,pokes=pokes,regs={'A0':O,'A1':0x61E31000,'A3':O+4},extra_rwz=('0x61894000:0x2000',))
    regs=V.regs_of(d); res['fault']=d.get('fault')
    if not res['fault']:
        dw=struct.unpack('<I',V.mem_bytes(d,R,4))[0]
        res['dirty_0x10000']=bool(dw & 0x10000)
        res['bit2']=(V.mem_bytes(d,O+0x64,1)[0]&4)!=0
        res['exif']=V.mem_bytes(d,EXIF+16,4)==b'B80\x00'
        res['mask']=struct.unpack('<H',V.mem_bytes(d,OUT+0x3C4,2))[0]==0x7DF
        res['ptrs']=all(struct.unpack('<I',V.mem_bytes(d,OUT+0x1434+4*i,4))[0]==N4+o for i,o in enumerate(OFFS))
    allok=all(v is True for v in res.values() if v is not None)
    rec={'pkg':path,'sha256':hashlib.sha256(open(path,'rb').read()).hexdigest(),'verifier':'tools/verify_b80.py','closed':bool(allok),'checks':res,'base':B69}
    open(path+'.sim.json','w',encoding='utf-8').write(json.dumps(rec,ensure_ascii=False,indent=2))
    for k,v in res.items(): print('  %-14s %s'%(k,v))
if __name__=='__main__': main()
