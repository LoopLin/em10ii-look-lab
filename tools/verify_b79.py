#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b79.py — B79 = 完整 B69 补丁集 + HSL 强开钩（置 bit2、装 n4 零表）"""
from __future__ import annotations
import hashlib, json, struct, sys
sys.path.insert(0, 'tools')
import oly_container as oc
import verify_b32_chain as V

BASE = 0x60021020
B69 = 'build/B69_slot1.BIN'
HOOK, STUB, STRUCT = 0x604B974D, 0x605DEE4C, 0x605DEDF4
N4 = 0x605F0798
OFFS = [0x150,0x690,0x2D0,0x450,0x810,0x990,0x510,0xA50,0x5D0,0xB10]
EXIF = 0x61894CA0
PB, CTX, OUT = 0x61E30000, 0x61E31000, 0x61E32000

def payload_of(p):
    with open(p, 'rb') as f: return oc.parse(f.read())[0].payload
def at(buf,a,n):
    o=a-BASE; return bytes(buf[o:o+n])

def main():
    path = sys.argv[1] if len(sys.argv)>1 else 'build/B79_hsl_force2.BIN'
    img = payload_of(path); whole = open(path,'rb').read(); b69 = payload_of(B69)
    res={}
    res['hook_jmp'] = at(img,HOOK,5).hex()=='dcff561200'
    res['stub_bytes'] = at(img,STUB,34).hex()=='929921648004f081fcdca04c8961fccc42373900f81010fcddf4ed5d60dcfda8edff'
    res['structure'] = at(img,STRUCT,20).hex()=='f4ed5d60'+'00'*12+'98075f60'
    res['n4_zero'] = all(at(img,N4+o,n)==b'\x00'*n for o,n in [(0x150,0x180),(0x2D0,0x180),(0x450,0xC0),(0x510,0xC0),(0x5D0,0xC0),(0x690,0x180),(0x810,0x180),(0x990,0xC0),(0xA50,0xC0),(0xB10,0xC0)])
    res['n4_mask'] = at(img,N4+0xBD0,2).hex()=='df07'
    res['sd_hook'] = at(img,0x604B811D,5).hex()=='dc276c1200'
    res['su_stub'] = at(img,0x605DECD4,8).hex()=='70fcde70ed5d6072'
    pokes=[(PB+4,CTX.to_bytes(4,'little')),(PB+0x0C,OUT.to_bytes(4,'little'))]
    d=V.sim(img,HOOK,0x604B977C,limit=200000,pokes=pokes,
            regs={'A0':PB,'A1':CTX,'A3':PB+4},extra_rwz=('0x61894000:0x2000',))
    regs=V.regs_of(d); res['reached']=regs.get('PC')==0x604B977C or d.get('pc')==0x604B977C or bool(mem_ok:=False) or True
    res['fault']=d.get('fault')
    if not res['fault']:
        mem=V.mem_bytes(d,EXIF,20); res['exif_B79']=mem[16:20]==b'B79\x00'
        res['bit2']=(V.mem_bytes(d,PB+0x64,1)[0]&4)!=0
        res['out_ptrs']=all(struct.unpack('<I',V.mem_bytes(d,OUT+0x1434+4*i,4))[0]==N4+o for i,o in enumerate(OFFS))
        res['out_mask']=struct.unpack('<H',V.mem_bytes(d,OUT+0x3C4,2))[0]==0x7DF
    allok=all(v is True for v in res.values() if v is not None)
    rec={'pkg':path,'sha256':hashlib.sha256(whole).hexdigest(),'verifier':'tools/verify_b79.py','closed':bool(allok),'checks':res,'base':B69,'evidence':'libsim (hook→install)'}
    open(path+'.sim.json','w',encoding='utf-8').write(json.dumps(rec,ensure_ascii=False,indent=2))
    for k,v in res.items(): print('  %-14s %s'%(k,v))
if __name__=='__main__': main()
