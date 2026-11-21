# -*- coding: utf-8 -*-
"""L52：用 EV+2/EV0/EV-2 三组实测 (T,Y) 建每档 f，再反解参考 look 的目标显示值。

数据：L48 上的 PB210004-011（EV+2 / EV-2），灰阶 6 块 × 8 张。
"""
import sys, json, struct, hashlib
import numpy as np
sys.path.insert(0,'tools')
import oly_container as oc, judge_chart as jc, rawpy
from PIL import Image

BASE = 0x60021020
TONE = [('V', 0x605DAF44, 'ektachrome_100_vs'),
        ('N', 0x605DB144, 'elite_color_200'),
        ('M', 0x605DB344, 'classic_chrome'),
        ('P', 0x605DB544, 'pro_neg_hi')]
SHOTS = ['004','005','006','007','008','009','010','011']

raw = open('build/L48_endpoint.BIN','rb').read()
blocks = oc.parse(raw)
pay0 = blocks[0].payload
pay = bytearray(pay0)

def frame(path):
    a=np.asarray(Image.open(path).convert('RGB').resize((960,720))).astype(float)
    return jc._frame(a)
bb = frame(r'I:\DCIM\100OLYMP\PB210008.JPG')

def grays(jpg):
    a=np.asarray(Image.open(jpg).convert('RGB').resize((960,720))).astype(float)
    fy0,fy1,fx0,fx1=bb; W,H=fx1-fx0,fy1-fy0
    px0,px1=fx0+0.065*W,fx1-0.062*W; py0,py1=fy0+0.018*H,fy1-0.009*H
    out=[]
    for c in range(6):
        cx=px0+(c+0.5)*(px1-px0)/6; cy=py0+3.5*(py1-py0)/4; s=0.05*(px1-px0)
        out.append(np.median(a[int(cy-s):int(cy+s)+1,int(cx-s):int(cx+s)+1].reshape(-1,3),axis=0))
    return np.array(out)

def idxs(orf, look):
    d=json.load(open('build/looks_final/%s.json'%look,encoding='utf-8'))
    M=np.array(d['matrix_q8'],float).reshape(3,3)/256.0
    with rawpy.imread(orf) as r:
        rgb=r.postprocess(use_camera_wb=True,gamma=(1,1),no_auto_bright=True,output_bps=16,half_size=True).astype(float)/65535.0
    Hh,Ww,_=rgb.shape
    fy0,fy1,fx0,fx1=bb; W,H=fx1-fx0,fy1-fy0
    px0,px1=fx0+0.065*W,fx1-0.062*W; py0,py1=fy0+0.018*H,fy1-0.009*H
    ids=[]
    for c in range(6):
        cx=px0+(c+0.5)*(px1-px0)/6; cy=py0+3.5*(py1-py0)/4
        px,py=int(cx/960*Ww),int(cy/720*Hh)
        v=np.median(rgb[max(0,py-6):py+6,max(0,px-6):px+6].reshape(-1,3),axis=0)
        lin=float(np.dot(M@v,[0.2126,0.7152,0.0722]))
        i=255*((1.055*lin**(1/2.4)-0.055) if lin>0.0031308 else 12.92*lin)
        ids.append(int(max(0,min(255,round(i)))))
    return ids

for nm, addr, look in TONE:
    T = np.array(struct.unpack_from('<256H', pay0, addr-BASE), float)
    d = json.load(open('build/looks_final/%s.json'%look, encoding='utf-8'))
    tone = np.array(d['tone256'], float)
    Ts, Ys = [], []
    for stem in SHOTS:
        jpg = r'I:\DCIM\100OLYMP\PB210%s.JPG'%stem
        orf = r'I:\DCIM\100OLYMP\PB210%s.ORF'%stem
        gi = idxs(orf, look); gj = grays(jpg)
        for i, g in zip(gi, gj):
            Ts.append(float(T[i])); Ys.append(float(np.dot(g,[0.2126,0.7152,0.0722])))
    o = np.argsort(Ts); Tp = np.array(Ts)[o]; Yp = np.array(Ys)[o]
    uT, inv = np.unique(np.round(Tp), return_inverse=True)
    uY = np.array([Yp[inv==k].mean() for k in range(len(uT))])
    # 单调化 Y（取累计最大）
    for k in range(1,len(uY)):
        if uY[k] < uY[k-1]: uY[k] = uY[k-1]
    D = tone/1023.0*255.0
    Tnew = np.interp(D, uY, uT, left=0.0, right=1023.0)
    Tnew = np.clip(np.round(Tnew),0,1023).astype(int)
    for i in range(1,256):
        if Tnew[i] < Tnew[i-1]: Tnew[i] = Tnew[i-1]
    struct.pack_into('<256H', pay, addr-BASE, *Tnew)
    print('%s pts=%d T=[%d..%d] Y=[%.0f..%.0f] -> T[0]=%d T[64]=%d T[128]=%d T[192]=%d T[255]=%d'
          % (nm,len(uT),uT[0],uT[-1],uY[0],uY[-1],Tnew[0],Tnew[64],Tnew[128],Tnew[192],Tnew[255]))

blocks[0]=oc.replace_payload(blocks[0],bytes(pay))
out=oc.reconstruct(blocks)
open('build/L52_evcal.BIN','wb').write(out)
print('size',len(out),'sha',hashlib.sha256(out).hexdigest()[:16])
