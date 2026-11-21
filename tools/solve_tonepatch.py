# -*- coding: utf-8 -*-
"""按索引做局部线性标定 + 样条锚点求解，输出 build/L56.BIN（不刷机）。"""
import sys,json,struct,hashlib; sys.path.insert(0,'tools')
import numpy as np
import oly_container as oc, judge_chart as jc, rawpy
from PIL import Image
BASE=0x60021020
SLOT={'V':0x605DAF44,'N':0x605DB144,'M':0x605DB344,'P':0x605DB544}
LOOK={'V':'ektachrome_100_vs','N':'elite_color_200','M':'classic_chrome','P':'pro_neg_hi'}
BUILDS=[('build/L52_evcal.BIN',['174','175','176','177']),('build/L53_newton.BIN',['178','179','180','181']),
        ('build/L54_newton2.BIN',['182','183','184','185']),('build/L55_newton3.BIN',['186','187','188','189'])]
D=r'I:\DCIM\100OLYMP\PB210%s'
bb=jc._frame(np.asarray(Image.open(D%'174'+'.JPG').convert('RGB').resize((960,720))).astype(float))
fy0,fy1,fx0,fx1=bb; W,H=fx1-fx0,fy1-fy0
PX0,PX1=fx0+0.065*W,fx1-0.062*W; PY0,PY1=fy0+0.018*H,fy1-0.009*H
AN=np.arange(0,256,10); AN=np.append(AN,255)
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
def pchip(x,y,xi):
    h=np.diff(x); d=np.diff(y)/h; mm=np.zeros(len(x))
    for k in range(1,len(x)-1):
        if d[k-1]*d[k]<=0: mm[k]=0
        else:
            w1=2*h[k]+h[k-1]; w2=h[k]+2*h[k-1]; mm[k]=(w1+w2)/(w1/d[k-1]+w2/d[k])
    mm[0]=d[0]; mm[-1]=d[-1]; out=np.empty(len(xi))
    for j,t in enumerate(xi):
        k=min(max(np.searchsorted(x,t)-1,0),len(x)-2); s=(t-x[k])/h[k]
        out[j]=(2*s**3-3*s**2+1)*y[k]+(s**3-2*s**2+s)*h[k]*mm[k]+(-2*s**3+3*s**2)*y[k+1]+(s**3-s**2)*h[k]*mm[k+1]
    return out
# 每档每格：跨 build 的 (T[i], Y) → 局部斜率
pay0=oc.parse(open('build/L55_newton3.BIN','rb').read())[0].payload
NEW={}
for m in SLOT:
    d=json.load(open('build/looks_final/%s.json'%LOOK[m],encoding='utf-8'))
    Dref=np.array(d['tone256'],float)/1023.0*255.0
    st=[s for s in BUILDS[-1] if True][0] if False else None
    T=np.array(struct.unpack_from('<256H',pay0,SLOT[m]-BASE),float)
    ix=idxs(D%[k for k,v in zip(['174','175','176','177'],'VNMP') if v==m][0]+'.ORF',LOOK[m])
    for c,i in enumerate(ix):
        Ts=[];Ys=[]
        for pkg,stems in BUILDS:
            p=oc.parse(open(pkg,'rb').read())[0].payload
            Tb=np.array(struct.unpack_from('<256H',p,SLOT[m]-BASE),float)
            st_=[s for s,v in zip(stems,'VNMP') if v==m][0]
            Ts.append(Tb[i]); Ys.append(ys(D%st_+'.JPG')[c])
        Ts=np.array(Ts,float); Ys=np.array(Ys,float)
        if float(Ts.max()-Ts.min())<5: slope=0.30; b=Ys.mean()-slope*Ts.mean()
        else:
            slope,b=np.polyfit(Ts,Ys,1); slope=max(slope,0.05)
        target=float(Dref[i]); cur=slope*T[i]+b
        dT=(target-cur)/slope
        a=int(min(AN,key=lambda x:abs(x-i)))
        T[a]=float(np.clip(T[a]+dT,-0,1023))
        print('%s c%d i=%3d anchor %3d slope %.2f cur %.0f target %.0f dT %+0.0f'%(m,c,i,a,slope,cur,target,dT))
    T[0]=0.0; T=np.maximum.accumulate(np.clip(T,0,1023)); T[255]=max(T[254],min(1023.0,T[255]))
    NEW[m]=T
pay=bytearray(pay0)
for m,T in NEW.items(): struct.pack_into('<256H',pay,SLOT[m]-BASE,*np.round(T).astype(int))
blocks=oc.parse(open('build/L55_newton3.BIN','rb').read())
out=oc.reconstruct([oc.replace_payload(blocks[0],bytes(pay))]+blocks[1:])
open('build/L56_patchcal.BIN','wb').write(out)
print('sha',hashlib.sha256(out).hexdigest()[:16])
