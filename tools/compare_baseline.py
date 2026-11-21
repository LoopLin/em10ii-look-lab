# -*- coding: utf-8 -*-
"""把新拍的 V/N/M/P 与①原厂四档基准图、②参考 look 目标 同时对比，输出一张表。
用法: python tools/compare_baseline.py 186 187 188 189 [--stock 061 062 063 064]"""
import sys, json, glob, os
import numpy as np
sys.path.insert(0,'tools')
import judge_chart as jc
from PIL import Image
def _root():
    for c in 'DEFGHIJKLMNOPQRSTUVWXYZ':
        d='%s:\\DCIM\\100OLYMP'%c
        if os.path.isdir(d): return d
    return r'I:\\DCIM\\100OLYMP'
D=os.path.join(_root(),'PB%02d%04d')
def path_of(n):
    for yy in (22,21,20):
        x=D%(yy,int(n))
        if os.path.exists(x+'.JPG'): return x+'.JPG'
    return D%(22,int(n))+'.JPG'
STOCK=os.path.join('photos','stock_reference')
LOOK={'V':'ektachrome_100_vs','N':'elite_color_200','M':'classic_chrome','P':'pro_neg_hi'}
def six(path,bb=None):
    a=np.asarray(Image.open(path).convert('RGB').resize((960,720))).astype(float)
    bb = bb if bb is not None else jc._frame(a)
    fy0,fy1,fx0,fx1=bb; W,H=fx1-fx0,fy1-fy0
    px0,px1=fx0+0.065*W,fx1-0.062*W; py0,py1=fy0+0.018*H,fy1-0.009*H
    out=[]
    for c in range(6):
        cx=px0+(c+0.5)*(px1-px0)/6; cy=py0+3.5*(py1-py0)/4; s=0.05*(px1-px0)
        v=np.median(a[int(cy-s):int(cy+s)+1,int(cx-s):int(cx+s)+1].reshape(-1,3),axis=0)
        out.append(round(float(np.dot(v,[0.2126,0.7152,0.0722])),1))
    return out
def target(mode, ref_jpg):
    """参考 look 目标：用同场景 ORF + look 的矩阵/tone 渲染（无 chroma）。"""
    import rawpy
    d=json.load(open('build/looks_final/%s.json'%LOOK[mode],encoding='utf-8'))
    M=np.array(d['matrix_q8'],float).reshape(3,3)/256.0
    T=np.array(d['tone256'],float)/1023.0*255.0
    orf=ref_jpg[:-4]+'.ORF'
    try: rr=rawpy.imread(orf)
    except Exception: return None
    with rr as r:
        lin=r.postprocess(use_camera_wb=True,gamma=(1,1),no_auto_bright=True,output_bps=16,half_size=True).astype(float)/65535.0
    y=lin.reshape(-1,3)@M.T
    s=np.where(y>0.0031308,1.055*np.maximum(y,0)**(1/2.4)-0.055,12.92*y)
    idx=np.clip(np.round(s*255),0,255).astype(int)
    img=(T[idx]*255.0/255.0).reshape(lin.shape)
    Hh,Ww,_=img.shape
    a=np.asarray(Image.open(ref_jpg).convert('RGB').resize((960,720))).astype(float)
    bb=jc._frame(a)
    fy0,fy1,fx0,fx1=bb; W,H=fx1-fx0,fy1-fy0
    px0,px1=fx0+0.065*W,fx1-0.062*W; py0,py1=fy0+0.018*H,fy1-0.009*H
    out=[]
    for c in range(6):
        cx=px0+(c+0.5)*(px1-px0)/6; cy=py0+3.5*(py1-py0)/4
        px,py=int(cx/960*Ww),int(cy/720*Hh)
        v=np.median(img[max(0,py-6):py+6,max(0,px-6):px+6].reshape(-1,3),axis=0)
        out.append(round(float(np.dot(v,[0.2126,0.7152,0.0722])),1))
    return out
def main():
    nums=sys.argv[1:5]
    stock={'V':os.path.join(STOCK,'PB160061.JPG'),'N':os.path.join(STOCK,'PB160062.JPG'),
           'M':os.path.join(STOCK,'PB160063.JPG'),'P':os.path.join(STOCK,'PB160064.JPG')}
    print('%-3s %-32s %-32s %-32s'%('档','新拍(白→黑)','原厂基准(白→黑)','参考look目标(白→黑)'))
    for n,m in zip(nums,'VNMP'):
        jpg=path_of(n)
        cur=six(jpg); bb=jc._frame(np.asarray(Image.open(jpg).convert('RGB').resize((960,720))).astype(float))
        sv=six(stock[m],bb); tv=target(m,jpg)
        print('%-3s %-32s %-32s %-32s'%(m,' '.join('%5.0f'%x for x in cur),
              ' '.join('%5.0f'%x for x in sv),' '.join('%5.0f'%x for x in tv) if tv else '—'))
main()
