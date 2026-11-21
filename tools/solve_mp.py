# -*- coding: utf-8 -*-
"""用 M/P 各自的 EV 三点扫描，离线拟合 f_T->Y 并反解表；V/N 保持 L55。"""
import sys,json,struct,hashlib; sys.path.insert(0,'tools')
import numpy as np
import oly_container as oc, judge_chart as jc, rawpy
from PIL import Image
BASE=0x60021020
SLOT={'V':0x605DAF44,'N':0x605DB144,'M':0x605DB344,'P':0x605DB544}
LOOK={'V':'ektachrome_100_vs','N':'elite_color_200','M':'classic_chrome','P':'pro_neg_hi'}
D=r'I:\DCIM\100OLYMP\PB21%04d'
SETS={'M':['190','192','191'],'P':['193','195','194']}  # -2, 0, +2
bb=jc._frame(np.asarray(Image.open(D%174+'.JPG').convert('RGB').resize((960,720))).astype(float))
fy0,fy1,fx0,fx1=bb; W,H=fx1-fx0,fy1-fy0
PX0,PX1=fx0+0.065*W,fx1-0.062*W; PY0,PY1=fy0+0.018*H,fy1-0.009*H
def idxs(orf,look):
    d=json.load(open('build/looks_final/%s.json'%look,encoding='utf-8'))
    Mx=np.array(d['matrix_q8'],float).reshape(3,3)/256.0
    with rawpy.imread(orf) as r:
        rgb=r.postprocess(use_camera_wb=True,gamma=(1,1),no_auto_bright=True,output_bps=16,half_size=True).astype(float)/65535.0
    y=rgb.reshape(-1,3)@Mx.T
    s=np.where(y>0.0031308,1.055*np.maximum(y,0)**(1/2.4)-0.055,12.92*y)
    ix=np.clip(np.round(s*255),0,255).astype(int).reshape(rgb.shape)
    Hh,Ww,_=rgb.shape; out=[]
    for c in range(6):
        cx=PX0+(c+0.5)*(PX1-PX0)/6; cy=PY0+3.5*(PY1-PY0)/4
        px,py=int(cx/960*Ww),int(cy/720*Hh)
        out.append(int(np.median(ix[py-6:py+6,px-6:px+6,0])))
    return out
def ys(jpg):
    a=np.asarray(Image.open(jpg).convert('RGB').resize((960,720))).astype(float); out=[]
    for c in range(6):
        cx=PX0+(c+0.5)*(PX1-PX0)/6; cy=PY0+3.5*(PY1-PY0)/4; s=0.05*(PX1-PX0)
        v=np.median(a[int(cy-s):int(cy+s)+1,int(cx-s):int(cx+s)+1].reshape(-1,3),axis=0)
        out.append(float(np.dot(v,[0.2126,0.7152,0.0722])))
    return out
pay=bytearray(oc.parse(open('build/L55_newton3.BIN','rb').read())[0].payload)
for m in ('M','P'):
    T=np.array(struct.unpack_from('<256H',pay,SLOT[m]-BASE),float)
    pts=[]
    for st in SETS[m]:
        orf=D%int(st)+'.ORF'; jpg=D%int(st)+'.JPG'
        for i,y in zip(idxs(orf,LOOK[m]),ys(jpg)): pts.append((T[i],y,i))
    pts.sort(); 
    print('%s 样本:'%m,' '.join('%.0f→%.0f@%d'%p for p in pts))
    # 按 T 分箱取中位，单调化
    Tt=np.array([p[0] for p in pts]); Yt=np.array([p[1] for p in pts])
    bins=np.arange(0,1100,40); bx=[];by=[]
    for b in range(len(bins)-1):
        s=(Tt>=bins[b])&(Tt<bins[b+1])
        if s.sum()>=1: bx.append(Tt[s].mean()); by.append(np.median(Yt[s]))
    bx=np.array(bx); by=np.maximum.accumulate(np.array(by))
    print('   f 锚点 %d 个, T %.0f..%.0f, Y %.0f..%.0f'%(len(bx),bx.min(),bx.max(),by.min(),by.max()))
    d=json.load(open('build/looks_final/%s.json'%LOOK[m],encoding='utf-8'))
    Dref=np.array(d['tone256'],float)/1023.0*255.0
    Tn=np.interp(Dref,by,bx); Tn[0]=0.0; Tn=np.maximum.accumulate(np.clip(Tn,0,1023)); Tn[255]=min(1023.0,max(Tn[254],bx[-1]))
    struct.pack_into('<256H',pay,SLOT[m]-BASE,*np.round(Tn).astype(int))
    # 预测 EV0 那张
    st=SETS[m][1]; pr=[float(np.interp(Tn[i],bx,by)) for i in idxs(D%int(st)+'.ORF',LOOK[m])]
    tg=[float(Dref[i]) for i in idxs(D%int(st)+'.ORF',LOOK[m])]
    print('   预测EV0',' '.join('%.0f'%x for x in pr),' 目标',' '.join('%.0f'%x for x in tg),' 最大差 %.1f'%max(abs(a-b) for a,b in zip(pr,tg)))
blocks=oc.parse(open('build/L55_newton3.BIN','rb').read())
out=oc.reconstruct([oc.replace_payload(blocks[0],bytes(pay))]+blocks[1:])
open('build/L57_mp.BIN','wb').write(out)
print('sha',hashlib.sha256(out).hexdigest()[:16])
