#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_y1.py -- Y1: 原厂画质 + SD 抓键 + SU 写 EXIF（Y 系列 = 原厂基线）"""
import io, os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT,'tools')); os.chdir(ROOT)
import build_b46 as b46
SRC='stock/OLY_E_120_1400_0000_0000.BIN'; OUT='build/Y1.BIN'; PATCH='.tmp/y1.patch'
STASH=0x605DED70; EXIF_TEXT=0x61894C90
SD_HOOK,SD_LEN,SD_STUB,SD_RET=0x604B811D,7,0x605DED44,0x604B8124
SU_HOOK,SU_LEN,SU_STUB,SU_RET=0x60479A2B,5,0x605DECD4,0x60479A32
SD="""\t.am33
\t.text
_start:
\tmov\td1,d2
\tmov\t0x%X,a0
\tmov\td2,(a0)
\tcmp\t0x1010066,d2
""" % STASH
SU="""\t.am33
\t.text
_start:
\tmov\t0x605DED70,a2
\tmov\t(a2),d3
\tmov\t0x61894C90,a2
\tmov\td3,(4,a2)
\tmov\ta0,(8,a2)
\tmov\t(a0),d0
\tmov\td0,(12,a2)
\tmov\t0x31534142,d0
\tmov\td0,(16,a2)
\tmov\td3,d1
\tand\t7,d1
\tmov\td3,d0
\tasr\t21,d0
\tand\t0x18,d0
\tor\td0,d1
\tadd\t-1,d1
\tcmp\t3,d1
\tbhi\tLend
\tmov\td1,d0
\tasl\t16,d0
\tadd\t0x313D6B,d0
\tmov\td0,(a2)
Lend:
\tmov\t(a0),d0
\tmov\td2,a1
\tadd\t0x400,d2
"""
def build(txt,sym,stub,ret):
    body=b46.assemble(txt,sym,stub)
    return body+b46.jmp_bytes(stub+len(body),ret)
def main():
    sd=build(SD,'y1_sd',SD_STUB,SD_RET); su=build(SU,'y1_su',SU_STUB,SU_RET)
    if len(sd)>0x28: raise SystemExit('sd too big %d'%len(sd))
    if len(su)>0x74: raise SystemExit('su too big %d'%len(su))
    lines=['ram 0x%x=%s'%(SD_HOOK,(b46.jmp_bytes(SD_HOOK,SD_STUB)+b'\x00'*(SD_LEN-5)).hex()),
           'ram 0x%x=%s'%(SD_STUB,sd.hex()),
           'ram 0x%x=%s'%(SU_HOOK,(b46.jmp_bytes(SU_HOOK,SU_STUB)+b'\x00'*(SU_LEN-5)).hex()),
           'ram 0x%x=%s'%(SU_STUB,su.hex())]
    io.open(PATCH,'w',encoding='utf-8',newline='\n').write('\n'.join(lines)+'\n')
    print('sd %d B'%len(sd),'su %d B'%len(su))
if __name__=='__main__': main()
