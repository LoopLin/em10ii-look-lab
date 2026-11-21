#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chart_sample.py — 色卡 24 块取样（抗噪）

规则（2026-11-18 用户定）：照片有噪点，取样必须用区域统计，不能取单像素。
本工具：自动找黑色边框 -> 6x4 网格 -> 每格取中心 40% 区域的中位数 RGB，
并给出区域内像素的标准差（噪点水平）；可选输出 Lab 与两图对应块的 ΔE。
"""
import sys, json, math
import numpy as np
from PIL import Image

def lab(rgb):
    r,g,b=[v/255.0 for v in rgb]
    def f(t): return t/12.92 if t<=0.04045 else ((t+0.055)/1.055)**2.4
    r,g,b=f(r),f(g),f(b)
    X=(r*0.4124+g*0.3576+b*0.1805)/0.95047
    Y=(r*0.2126+g*0.7152+b*0.0722)
    Z=(r*0.0193+g*0.1192+b*0.9505)/1.08883
    def g2(t): return t**(1/3) if t>0.008856 else 7.787*t+16/116
    fx,fy,fz=g2(X),g2(Y),g2(Z)
    return (116*fy-16, 500*(fx-fy), 200*(fy-fz))

def frame_bbox(gray):
    h,w=gray.shape
    # 1) 找白色相纸：大块亮区
    bright=gray>=195
    rows=bright.sum(axis=1); cols=bright.sum(axis=0)
    rs=[i for i,v in enumerate(rows) if v>0.30*w]; cs=[i for i,v in enumerate(cols) if v>0.30*h]
    if not rs or not cs: raise SystemExit('no sheet')
    sy0,sy1,sx0,sx1=min(rs),max(rs),min(cs),max(cs)
    # 2) 在相纸内部找黑色边框
    sub=gray[sy0:sy1+1, sx0:sx1+1]
    dark=sub<75
    sh,sw=sub.shape
    rr=dark.sum(axis=1); cc=dark.sum(axis=0)
    rt=max(0.55*sw,60); ct=max(0.55*sh,60)
    rs=[i for i,v in enumerate(rr) if v>rt]; cs=[i for i,v in enumerate(cc) if v>ct]
    if not rs or not cs: raise SystemExit('no frame')
    return sy0+min(rs), sy0+max(rs), sx0+min(cs), sx0+max(cs)

def bands(profile, frac=0.55):
    lo,hi=float(profile.min()), float(profile.max())
    th=lo+(hi-lo)*frac
    out=[]; st=None
    for i,v in enumerate(profile):
        if v>th and st is None: st=i
        elif v<=th and st is not None: out.append((st,i-1)); st=None
    if st is not None: out.append((st,len(profile)-1))
    return [b for b in out if b[1]-b[0]>=8]

def sample(path, grid=(6,4), inset=0.30):
    im=Image.open(path).convert('RGB')
    W,H=im.size
    sw=768; sh=576
    small=im.resize((sw,sh))
    gray=np.asarray(small.convert('L')).astype(np.float64)
    y0,y1,x0,x1=frame_bbox(gray)
    b=max(2,int(0.008*sw))
    bx0,bx1,by0,by1=x0+b,x1-b,y0+b,y1-b
    lum=gray[by0:by1+1, bx0:bx1+1]
    cols=bands(lum.mean(axis=0)); rows=bands(lum.mean(axis=1))
    if len(cols)!=grid[0] or len(rows)!=grid[1]:
        raise SystemExit('grid %dx%d != %s x %s' % (grid[0],grid[1],len(cols),len(rows)))
    kx,ky=W/sw, H/sh
    patches=[]
    for r in range(grid[1]):
        for c in range(grid[0]):
            cx0=(bx0+cols[c][0])*kx; cx1=(bx0+cols[c][1])*kx
            cy0=(by0+rows[r][0])*ky; cy1=(by0+rows[r][1])*ky
            mx=(cx1-cx0)*0.25; my=(cy1-cy0)*0.25
            box=(int(cx0+mx),int(cy0+my),int(cx1-mx),int(cy1-my))
            crop=np.asarray(im.crop(box)).astype(np.float64)
            med=np.median(crop.reshape(-1,3),axis=0)
            sd=crop.reshape(-1,3).std(axis=0).mean()
            patches.append({'r':r,'c':c,'rgb':[round(float(v),1) for v in med],'sigma':round(float(sd),2)})
    return patches

def main():
    a=sys.argv[1:]
    if len(a)<1: raise SystemExit('usage: chart_sample.py IMG [IMG2]')
    p1=sample(a[0])
    # find bright patch index for exposure reference (white patch = row3 col0)
    if len(a)==1:
        for x in p1: print(x['r'],x['c'],x['rgb'],'sd',x['sigma'])
        return
    p2=sample(a[1])
    des=[]
    for b1,b2 in zip(p1,p2):
        L1=lab(b1['rgb']); L2=lab(b2['rgb'])
        de=math.sqrt(sum((x-y)**2 for x,y in zip(L1,L2)))
        des.append(round(de,2))
    print('per-patch dE:',des)
    print('mean dE=%.2f max=%.2f'%(sum(des)/len(des),max(des)))

if __name__=='__main__': main()
