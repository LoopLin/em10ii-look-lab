#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_y1.py -- Y1: SD 抓键 + SU 写 EXIF（stock 画质）"""
import hashlib, io, json, struct, sys
sys.path.insert(0,'tools')
import verify_b32_chain as V
import verify_b59 as W
import oly_container as OC
STOCK='stock/OLY_E_120_1400_0000_0000.BIN'; OUT='build/Y1.BIN'
SD_HOOK,SD_STUB,SD_RET=0x604B811D,0x605DED44,0x604B8124
SU_HOOK,SU_STUB,SU_RET=0x60479A2B,0x605DECD4,0x60479A32
STASH=0x605DED70; EXIF_TEXT=0x61894C90; EXIF_REGION=W.EXIF_REGION
S=0x61E18000; DEST=0x61E1C000; BASE=0x60021020
def payload(p): return bytearray(OC.parse(open(p,'rb').read())[0].payload)
def main():
    checks=[]; problems=[]
    img=payload(OUT); st=payload(STOCK)
    spans=[]
    for i in range(len(st)):
        if st[i]!=img[i]:
            if spans and i==spans[-1][1]: spans[-1][1]=i+1
            else: spans.append([i,i+1])
    allowed=[(SD_HOOK-BASE,SD_HOOK-BASE+7),(SD_STUB-BASE,SD_STUB-BASE+19),
             (SU_HOOK-BASE,SU_HOOK-BASE+5),(SU_STUB-BASE,SU_STUB-BASE+74)]
    inside=all(any(a<=x and y<=b for a,b in allowed) for x,y in spans)
    covered=all(any(a<=x and y<=b for x,y in spans) for a,b in allowed) if spans else False
    checks.append(('diff_spans',inside and covered,spans))
    # SD: d1=key -> stash
    d=V.sim(bytes(img),SD_STUB,SD_RET,limit=2000,pokes=[(STASH,b'\x00'*4)],
            regs={'D1':3,'D2':0},extra_rwz=())
    got=struct.unpack('<I',V.mem_bytes(d,STASH,4))[0]
    checks.append(('sd_stash',got==3,hex(got)))
    # SU: k=1..4
    ok=True
    for k in (1,2,3,4):
        pokes=[(STASH,struct.pack('<I',k)),(S+0x1230,struct.pack('<I',0x605DAF44)),
               (EXIF_TEXT,bytes([0x20])*0x18)]
        d=V.sim(bytes(img),SU_STUB,SU_RET,limit=4000,pokes=pokes,
                regs={'A0':S+0x1230,'D2':DEST+0x400},extra_rwz=(EXIF_REGION,))
        m=V.mem_bytes(d,EXIF_TEXT,0x14)
        good=(m[0:3]==bytes([0x6B,0x3D,0x30+k]) and m[3]==0 and
              struct.unpack('<I',m[4:8])[0]==k and
              struct.unpack('<I',m[8:12])[0]==S+0x1230 and
              struct.unpack('<I',m[12:16])[0]==0x605DAF44 and m[16:20]==b'BAS1')
        ok=ok and good
        if not good: problems.append('k=%d %s'%(k,m.hex()))
    checks.append(('su_k1_4',ok,None))
    res={'pkg':OUT,'sha256':hashlib.sha256(open(OUT,'rb').read()).hexdigest(),
         'verifier':'tools/verify_y1.py','closed':not problems,'problems':problems,
         'checks':[{'name':n,'ok':bool(o),'value':v} for n,o,v in checks],
         'note':'Y1 = stock color; SD captures key, SU writes EXIF'}
    io.open(OUT+'.sim.json','w',encoding='utf-8').write(json.dumps(res,ensure_ascii=False,indent=1))
    print(json.dumps(res,ensure_ascii=False,indent=1))
if __name__=='__main__': main()
