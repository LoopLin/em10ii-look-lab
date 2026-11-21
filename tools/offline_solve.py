# -*- coding: utf-8 -*-
"""离线闭环：用已实测的 f(T)->Y 反解四张 tone 表，并在本地预测输出。
不刷机；只写 build/L56_offline.BIN 与预测报告。"""
import sys, json, struct, hashlib
import numpy as np
sys.path.insert(0,'tools')
import oly_container as oc, judge_chart as jc, rawpy
from PIL import Image
from read_probe import get_user_comment
BASE=0x60021020
SLOT={'V':0x605DAF44,'N':0x605DB144,'M':0x605DB344,'P':0x605DB544}
LOOK={'V':'ektachrome_100_vs','N':'elite_color_200','M':'classic_chrome','P':'pro_neg_hi'}
BUILDS=[('build/L52_evcal.BIN',['174','175','176','177']),
        ('build/L53_newton.BIN',['178','179','180','181']),
        ('build/L54_newton2.BIN',['182','183','184','185']),
        ('build/L55_newton3.BIN',['186','187','188','189'])]
EV=[('build/L48_endpoint.BIN',['photos/EM102/L48_ev/PB21%04d'%i for i in range(4,8)]),
    ('build/L48_endpoint.BIN',['photos/EM102/L48_ev/PB21%04d'%i for i in range(8,12)])]
D=r'I:\DCIM\100OLYMP\PB210%s'
bb=jc._frame(np.asarray(Image.open(D%'174'+'.JPG').convert('RGB').resize((960,720))).astype(float))
def geom(bb):
    fy0,fy1,fx0,fx1=bb; W,H=fx1-fx0,fy1-fy0
    return fx0+0.065*W,fx1-0.062*W,fy0+0.018*H,fy1-0.009*H
PX0,PX1,PY0,PY1=geom(bb)
def idxs(orf,look):
    d=json.load(open('build/looks_final/%s.json'%look,encoding='utf-8'))
    M=np.array(d['matrix_q8'],float).reshape(3,3)/256.0
    with rawpy.imread(orf) as r:
        rgb=r.postprocess(use_camera_wb=True,gamma=(1,1),no_auto_bright=True,output_bps=16,half_size=True).astype(float)/65535.0
    y=rgb.reshape(-1,3)@M.T
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
# 1) 收集 (T,Y) 样本
samples={m:[] for m in SLOT}
for pkg,stems in BUILDS+EV:
    pay=oc.parse(open(pkg,'rb').read())[0].payload
    T={m:np.array(struct.unpack_from('<256H',pay,a-BASE),float) for m,a in SLOT.items()}
    for st in stems:
        if st.startswith('photos'): jpg=st+'.JPG'; orf=st+'.ORF'
        else: jpg=D%st+'.JPG'; orf=D%st+'.ORF'
        k=get_user_comment(jpg)
        m={1:'V',2:'N',3:'M',4:'P'}.get(k) if k else None
        if m is None:
            m=stems.index(st); m=list('VNMP')[m if len(stems)==4 else 0]
        for i,y in zip(idxs(orf,LOOK[m]),ys(jpg)): samples[m].append((T[m][i],y))
# 2) 拟合单调 f：按 T 分箱取中位数，再平滑
def fit_f(pts):
    pts=sorted(pts); T=np.array([p[0] for p in pts]); Y=np.array([p[1] for p in pts])
    bins=np.arange(0,1025,32); idx=np.digitize(T,bins)-1
    bx=[];by=[]
    for b in range(len(bins)-1):
        s=idx==b
        if s.sum()>=2: bx.append(np.median(T[s])); by.append(np.median(Y[s]))
    bx=np.array(bx+[1023.0]); by=np.array(by+[max(by)+3])
    bo=np.maximum.accumulate(np.clip(by,0,255))
    return bx,bo,len(T)
F={m:fit_f(samples[m]) for m in SLOT}
for m in SLOT: print('f[%s] 样本 %d, 锚点 %d'%(m,F[m][2],len(F[m][0])))
# 3) 反解：T(i) = f^-1(D_ref(i))
pay=bytearray(oc.parse(open('build/L55_newton3.BIN','rb').read())[0].payload)
pred={m:[] for m in SLOT}; tgt={m:[] for m in SLOT}
for m in SLOT:
    d=json.load(open('build/looks_final/%s.json'%LOOK[m],encoding='utf-8'))
    Dref=np.array(d['tone256'],float)/1023.0*255.0
    bx,by,_=F[m]
    T=np.interp(Dref,by,bx)
    T[0]=0.0; T=np.maximum.accumulate(np.clip(T,0,1023))
    T[255]=max(T[254],min(1023.0,np.interp(255.0,by,bx)))
    struct.pack_into('<256H',pay,SLOT[m]-BASE,*np.round(T).astype(int))
out=oc.reconstruct([oc.replace_payload(oc.parse(open('build/L55_newton3.BIN','rb').read())[0],bytes(pay))]+oc.parse(open('build/L55_newton3.BIN','rb').read())[1:])
open('build/L56_offline.BIN','wb').write(out)
# 4) 本地预测：用校准 ORF 的索引 + 新表 + f 预测输出
print('\n预测 (目标 vs 预测, 基于 186-189 的 ORF)')
for st,m in zip(['186','187','188','189'],SLOT):
    d=json.load(open('build/looks_final/%s.json'%LOOK[m],encoding='utf-8'))
    Dref=np.array(d['tone256'],float)/1023.0*255.0
    T=np.array(struct.unpack_from('<256H',pay,SLOT[m]-BASE),float)
    bx,by,_=F[m]
    ix=idxs(D%st+'.ORF',LOOK[m]); p=[float(np.interp(T[i],bx,by)) for i in ix]; t=[float(Dref[i]) for i in ix]
    print('%s 目标 %s  预测 %s  最大 %.1f'%(m,' '.join('%3.0f'%x for x in t),' '.join('%3.0f'%x for x in p),max(abs(a-b) for a,b in zip(p,t))))
print('sha',hashlib.sha256(out).hexdigest()[:16])
