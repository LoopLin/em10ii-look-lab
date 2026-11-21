# -*- coding: utf-8 -*-
"""L55 静态校验：与 L54 只差四张 tone 表；表单调、端点、N 槽、弯曲门、钩点与 L54 相同。"""
import sys, struct, json, hashlib
sys.path.insert(0,'tools')
import oly_container as oc
BASE=0x60021020
A=[0x605DAF44,0x605DB144,0x605DB344,0x605DB544]
a=open('build/L54_newton2.BIN','rb').read(); b=open('build/L55_newton3.BIN','rb').read()
pa=bytearray(oc.parse(a)[0].payload); pb=bytearray(oc.parse(b)[0].payload)
diff=[i for i in range(len(pa)) if pa[i]!=pb[i]]
lo,hi=min(diff)+BASE,max(diff)+BASE
ok=True
within=all(A[0]<=lo and hi<A[-1]+512 for _ in [0])
print('差异 %d 字节  %#x..%#x  全在四张表内=%s'%(len(diff),lo,hi,within)); ok&=within
for addr in A:
    T=struct.unpack_from('<256H',pb,addr-BASE)
    mono=all(T[i]<=T[i+1] for i in range(255)); rng=(T[0]==0 and 600<=T[255]<=1023)
    print('  %#x  T0=%d T64=%d T128=%d T192=%d T255=%d 单调=%s 端点=%s'%(addr,T[0],T[64],T[128],T[192],T[255],mono,rng))
    ok&=mono and rng
for off,name in ((0x5E9CCB,'nslot'),):
    print('  %s L54=%s L55=%s'%(name,pa[off],pb[off])); ok&=pa[off]==pb[off]
print('closed=%s sha=%s'%(ok,hashlib.sha256(b).hexdigest()[:16]))
json.dump({'closed':bool(ok),'scope':'静态：tone 表二次修正，钩点/门/槽不变；无 ISP 仿真'},open('build/L55_newton3.BIN.sim.json','w'),ensure_ascii=False,indent=1)
