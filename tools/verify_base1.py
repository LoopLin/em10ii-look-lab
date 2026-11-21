#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_base1.py -- BASE1 静态 + libsim 桩行为"""
import hashlib, io, json, struct, sys
sys.path.insert(0,'tools')
import verify_b32_chain as V
import verify_b59 as W
import oly_container as OC

STOCK='stock/OLY_E_120_1400_0000_0000.BIN'
OUT='build/BASE1.BIN'; SPEC='.tmp/base1.patch'
HOOK=0x60479A2B; STUB=0x605DECD4; RET=0x60479A32
EXIF_TEXT=0x61894C90; EXIF_REGION=W.EXIF_REGION
STASH=0x605DED70; S=0x61E18000; DEST=0x61E1C000

def payload(p):
    return bytearray(OC.parse(open(p,'rb').read())[0].payload)

def mem(d,addr,n): return V.mem_bytes(d,addr,n)

def main():
    checks=[]; problems=[]
    img=payload(OUT); st=payload(STOCK)
    checks.append(('size',len(img)==len(st),len(img)))
    # static: diff spans exactly hook+stub
    spans=[]
    for i in range(len(st)):
        if st[i]!=img[i]:
            if spans and i==spans[-1][1]: spans[-1][1]=i+1
            else: spans.append([i,i+1])
    base=0x60021020
    want=[(HOOK-base,HOOK-base+5),(STUB-base,STUB-base+74)]
    checks.append(('diff_spans',[tuple(s) for s in spans]==want,spans))
    checks.append(('hook',bytes(img[HOOK-base:HOOK-base+5])==V_hex('dca9521600'),None))
    # dynamic: run stub for k=1..4
    ok=True
    for k in (1,2,3,4):
        pokes=[(STASH,struct.pack('<I',k)),(S+0x1230,struct.pack('<I',0x605DAF44)),
               (EXIF_TEXT,bytes([0x20])*0x18)]
        regs={'A0':S+0x1230,'D2':DEST+0x400}
        d=V.sim(bytes(img),STUB,RET,limit=4000,pokes=pokes,regs=regs,extra_rwz=(EXIF_REGION,))
        m=mem(d,EXIF_TEXT,0x14)
        good=(m[0:3]==bytes([0x6B,0x3D,0x30+k]) and m[3]==0
              and struct.unpack('<I',m[4:8])[0]==k
              and struct.unpack('<I',m[8:12])[0]==S+0x1230
              and struct.unpack('<I',m[12:16])[0]==0x605DAF44
              and m[16:20]==b'BAS1')
        ok = ok and good
        if not good: problems.append('k=%d exif=%s'%(k,m.hex()))
    checks.append(('stub_k1_4',ok,None))
    res={'pkg':OUT,'sha256':hashlib.sha256(open(OUT,'rb').read()).hexdigest(),
         'verifier':'tools/verify_base1.py','closed':not problems,'problems':problems,
         'checks':[{'name':n,'ok':bool(o),'value':v} for n,o,v in checks],
         'note':'stock-only diagnostics; EXIF write path exercised in libsim'}
    io.open(OUT+'.sim.json','w',encoding='utf-8').write(json.dumps(res,ensure_ascii=False,indent=1))
    print(json.dumps(res,ensure_ascii=False,indent=1))

def V_hex(s): return bytes.fromhex(s)

if __name__=='__main__': main()
