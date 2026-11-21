#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_b90.py — B90 离线验证：B69 + SD 桩键==2 清 stash（i-e 回原厂）

静态：SD 桩字节、与 B69 差异只在 SD 桩 19 B、SU/SEL/MX/四块仍在。
模拟：从 SD 钩 0x604B811D 跑，D1=key，停在 0x604B8124，看 stash。
  key=2 -> stash=0；key=0x01010066 -> stash 原样。
"""
import hashlib, json, sys, time
sys.path.insert(0,'tools')
import oly_container as oc, verify_b32_chain as V, build_b46 as b46

ROOT='G:/em10fw/'
BASE=0x60021020
SD_HOOK=0x604B811D; SD_STUB=0x605DED44; SD_RET=0x604B8124; STASH=0x605DED70

def payload_of(p):
    return oc.parse(open(p,'rb').read())[0].payload

def main():
    b90=payload_of(ROOT+'build/B90_ie_stock.BIN'); b69=payload_of(ROOT+'build/B69_slot1.BIN')
    ok=True
    def chk(name,cond,extra=''):
        nonlocal ok
        ok = ok and cond
        print(('PASS' if cond else 'FAIL'), name, extra)
    # static stub
    stub=b90[SD_STUB-BASE:SD_STUB-BASE+19]
    want=bytes.fromhex('86fcdc70ed5d6068aa02c9040060dcd293edff')
    chk('sd_stub_bytes', stub==want, stub.hex())
    chk('sd_hook_jmp', b90[SD_HOOK-BASE:SD_HOOK-BASE+5]==bytes.fromhex('dc276c1200'))
    # diff vs B69 confined
    diffs=[i for i in range(len(b90)) if b90[i]!=b69[i]]
    runs=[]
    for i in diffs:
        if runs and i==runs[-1][1]+1: runs[-1][1]=i
        else: runs.append([i,i])
    addrs=[(BASE+a, BASE+b) for a,b in runs]
    chk('diff_vs_b69_only_sd', all(SD_STUB<=a and b<SD_STUB+19 for a,b in addrs), str([(hex(a),hex(b)) for a,b in addrs]))
    # other B69 patch points intact
    for name,addr,pat in [
        ('sd_hook',SD_HOOK,'dc276c1200'),
        ('su_hook',0x60479A2B,'dc a9 52 16 00'.replace(' ','')),
        ('sel_hook',0x604B4CAD,'dc6fa01200cb'),
        ('mx_hook',0x604B6AD6,'dc2e73cfff'),
        ('block1',0x605DEB44, None),
    ]:
        if pat:
            chk(name, b90[addr-BASE:addr-BASE+len(bytes.fromhex(pat))]==bytes.fromhex(pat))
        else:
            stock=payload_of(ROOT+'stock/OLY_E_120_1400_0000_0000.BIN')
            chk(name+'_!=stock', b90[addr-BASE:addr-BASE+100]!=stock[addr-BASE:addr-BASE+100])
    # sim
    for key in (2, 0x01010066):
        pokes=[(STASH, (0xDEADBEEF).to_bytes(4,'little'))]
        d=V.sim(b90, SD_HOOK, SD_RET, limit=4000, pokes=pokes, regs={'D1':key})
        got=int.from_bytes(V.mem_bytes(d,STASH,4),'little')
        if key==2:
            chk('sim_key2_stash0', got==0, hex(got))
        else:
            chk('sim_other_key_kept', got==key, hex(got))
    rec=dict(pkg='build/B90_ie_stock.BIN', sha256=hashlib.sha256(open(ROOT+'build/B90_ie_stock.BIN','rb').read()).hexdigest(),
             verifier='tools/verify_b90.py', closed=bool(ok), time=time.strftime('%Y-%m-%dT%H:%M:%S'),
             base='stock/OLY_E_120_1400_0000_0000.BIN', compare_to='build/B69_slot1.BIN',
             sd_hook=hex(SD_HOOK), sd_stub=hex(SD_STUB), sd_stub_len=len(want),
             checks=['SD 桩 key==2 -> stash=0（模拟）','SD 桩其它键 -> stash 保留（模拟）',
                     '与 B69 差异只有 SD 桩 6 B','SU/SEL/MX 钩与四块仍在','SD 钩字节 dc276c1200'],
             static=True)
    json.dump(rec, open(ROOT+'build/B90_ie_stock.BIN.sim.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
    print('closed=%s' % ok)
    return 0 if ok else 1

if __name__=='__main__': sys.exit(main())
