#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_base1.py -- BASE1: 原厂 + 只写 EXIF 的 SU 桩（不改任何画质）
写入 UserComment 文本区 0x61894C90：
  +0x00 "k=N\\0"      +0x04 key32      +0x08 ctx+0x1230 (=W+0x1230)
  +0x0C *(W+0x1230)   +0x10 "BAS1" 标记
"""
import io, os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT,'tools')); os.chdir(ROOT)
import build_b46 as b46
SRC='stock/OLY_E_120_1400_0000_0000.BIN'
OUT='build/BASE1.BIN'; PATCH='.tmp/base1.patch'
HOOK=0x60479A2B; HOOK_LEN=5; RET=0x60479A32; STUB=0x605DECD4
TEXT="""
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
def main():
    body=b46.assemble(TEXT,'base1_stub',STUB)
    site=STUB+len(body); stub=body+b46.jmp_bytes(site,RET)
    if len(stub)>0x150: raise SystemExit('stub too big')
    hook=b46.jmp_bytes(HOOK,STUB)+b'\x00'*(HOOK_LEN-5)
    lines=['ram 0x%x=%s'%(HOOK,hook.hex()),'ram 0x%x=%s'%(STUB,stub.hex())]
    io.open(PATCH,'w',encoding='utf-8',newline='\n').write('\n'.join(lines)+'\n')
    print('hook',hook.hex(),'stub %d B'%len(stub))
if __name__=='__main__': main()
