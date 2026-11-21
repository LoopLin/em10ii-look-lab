# -*- coding: utf-8 -*-
"""四档 look 与参考 LUT 的色度差距（真机照片 + 离线 LUT 计算）。

模型
  RAW 线性 x  --s-->  参考 LUT 输入 oetf(s*x)  -->  LUT 输出  --oetf-->  目标显示码 t
  实拍 JPG                                                            -->  实测显示码 m
  拍摄端干扰：t 经"曝光 s + 面纱眩光 F"退化后再与 m 比
      obs_lin = F + (1-F)*oetf^-1(t)   ->  t' = oetf(obs_lin)
  (s, F) 只用 6 个灰块拟合，之后 24 块的 ΔE00 全部来自颜色本身。

输出 .tmp/chroma_gap.json + 终端明细。
"""
import json
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.path.insert(0, os.path.join(ROOT, 'build', 'looks_final', '_tools'))
os.chdir(ROOT)

import judge_chart as jc            # noqa: E402
import lookfit as L                 # noqa: E402

IMG = r'I:\DCIM\100OLYMP'

PATCH_NAMES = [
    'dark skin', 'light skin', 'blue sky', 'foliage', 'blue flower', 'bluish green',
    'orange', 'purplish blue', 'moderate red', 'purple', 'yellow green', 'orange yellow',
    'blue', 'green', 'red', 'yellow', 'magenta', 'cyan',
    'white 9.5', 'neutral 8', 'neutral 6.5', 'neutral 5', 'neutral 3.5', 'black 2',
]
FAMILY = {
    '肤色': [0, 1], '蓝': [2, 7, 12], '紫': [4, 9, 16], '青': [5, 17],
    '绿': [3, 13], '黄': [10, 15], '橙': [6, 11], '红': [8, 14],
    '灰': [18, 19, 20, 21, 22, 23],
}
GRAYS = list(range(18, 24))

SHOTS = [
    ('PB210134', 1, 'ektachrome_100_vs', 'Ektachrome 100 VS'),
    ('PB210136', 3, 'classic_chrome', 'Classic Chrome'),
    ('PB210137', 4, 'pro_neg_hi', 'Pro Neg Hi'),
    ('PB210139', 2, 'elite_color_200', 'Elite Color 200（光照不同）'),
]


def patch_centers(jpg):
    im = np.asarray(Image.open(jpg).convert('RGB').resize((960, 720))).astype(float)
    fy0, fy1, fx0, fx1 = jc._frame(im)
    W, H = fx1 - fx0, fy1 - fy0
    px0, px1 = fx0 + 0.065 * W, fx1 - 0.062 * W
    py0, py1 = fy0 + 0.018 * H, fy1 - 0.009 * H
    out = []
    for r in range(4):
        for c in range(6):
            w0 = px0 + (px1 - px0) * c / 6
            w1 = px0 + (px1 - px0) * (c + 1) / 6
            h0 = py0 + (py1 - py0) * r / 4
            h1 = py0 + (py1 - py0) * (r + 1) / 4
            out.append(((w0 + w1) / 2 / 960.0, (h0 + h1) / 2 / 720.0,
                        (w1 - w0) / 2 / 960.0, (h1 - h0) / 2 / 720.0))
    return np.array(out)


def raw_patches(orf, centers, inset=0.5):
    import rawpy
    with rawpy.imread(orf) as r:
        rgb = r.postprocess(use_camera_wb=True, gamma=(1, 1), no_auto_bright=True,
                            output_bps=16, half_size=True).astype(np.float32) / 65535.0
    Hh, Ww, _ = rgb.shape
    out = []
    for cx, cy, hw, hh in centers:
        x0 = int((cx - hw * inset) * Ww); x1 = int((cx + hw * inset) * Ww) + 1
        y0 = int((cy - hh * inset) * Hh); y1 = int((cy + hh * inset) * Hh) + 1
        out.append(np.median(rgb[y0:y1, x0:x1].reshape(-1, 3), axis=0))
    return np.array(out)


def lut_lin(look, lin, s):
    """参考 LUT 在显示码域的线性输出（oetf^-1(LUT 输出)）。"""
    tgt = L.target_cached(look)(np.clip(lin * s, 0, 1))
    return np.clip(tgt, 0, 1)


def degrade(t_lin, F):
    return np.clip(F + (1.0 - F) * t_lin, 0, 1)


def srgb_lab(d):
    return L.srgb_to_lab(np.clip(d, 0, 1))


def code_lab(c):
    return L.srgb_to_lab(np.clip(c / 255.0, 0, 1))


def lch(lab):
    C = np.hypot(lab[:, 1], lab[:, 2])
    h = np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360.0
    return lab[:, 0], C, h


def wrap(d):
    return (d + 180.0) % 360.0 - 180.0


def fit_sf_joint(items):
    """同一次拍摄的若干 look 共用 (s, F)：s=曝光尺度，F=面纱眩光系数（线性域）。
    items = [(look, lin, meas_disp), ...]，只用 6 个灰块。"""
    labm = [srgb_lab(m)[GRAYS, 0] for _, _, m in items]

    def cost(s, F):
        c = 0.0
        for (look, lin, _), lm in zip(items, labm):
            d = L.oetf(degrade(lut_lin(look, lin, s), F))
            c += float(np.mean((srgb_lab(d)[GRAYS, 0] - lm) ** 2))
        return c / len(items)
    best = (1e9, None, None)
    for s in np.linspace(1.5, 4.6, 63):
        for F in np.linspace(0.0, 0.25, 26):
            c = cost(s, F)
            if c < best[0]:
                best = (c, s, F)
    _, s0, F0 = best
    for s in np.linspace(s0 * 0.96, s0 * 1.04, 25):
        for F in np.linspace(max(0, F0 - 0.015), F0 + 0.015, 13):
            c = cost(s, F)
            if c < best[0]:
                best = (c, s, F)
    return float(best[1]), float(best[2])


def analyse(stem, k, look, note, s_fit, F):
    jpg = os.path.join(IMG, stem + '.JPG')
    orf = os.path.join(IMG, stem + '.ORF')
    rgb8 = jc.sample(jpg)
    meas = rgb8 / 255.0
    lin = raw_patches(orf, patch_centers(jpg))

    t_lin = lut_lin(look, lin, s_fit)
    tgt = L.oetf(degrade(t_lin, F))          # 目标（含拍摄退化）
    # 对照 1：只校曝光、不加眩光
    tgt_nf = L.oetf(t_lin)
    # 对照 2：不校任何东西
    tgt_raw = L.oetf(lut_lin(look, lin, 1.0))

    labm, labt = srgb_lab(meas), srgb_lab(tgt)
    de = L.ciede2000(labm, labt)
    de_nf = L.ciede2000(labm, srgb_lab(tgt_nf))
    de_raw = L.ciede2000(labm, srgb_lab(tgt_raw))
    Lm, Cm, hm = lch(labm)
    Lt, Ct, ht = lch(labt)

    out = dict(stem=stem, k=k, look=look, note=note, s=s_fit, F=F,
               rgb8=rgb8.round(1).tolist(), lin=lin.tolist(),
               de=de.tolist(), de_nf=de_nf.tolist(), de_raw=de_raw.tolist(),
               dL=(Lm - Lt).tolist(), dC=(Cm - Ct).tolist(),
               dh=wrap(hm - ht).tolist(),
               da=(labm[:, 1] - labt[:, 1]).tolist(),
               db=(labm[:, 2] - labt[:, 2]).tolist(),
               lab_m=labm.tolist(), lab_t=labt.tolist(),
               tgt_disp=(tgt * 255).round(1).tolist())

    # ---- 全局色偏（灰块均值）与 3x3 线性矩阵可行性
    da_g = float(np.mean([out['da'][i] for i in GRAYS]))
    db_g = float(np.mean([out['db'][i] for i in GRAYS]))
    lab_fix = labm.copy()
    lab_fix[:, 1] -= da_g
    lab_fix[:, 2] -= db_g
    de_fix = L.ciede2000(lab_fix, labt)

    ml = np.clip(L.eotf(meas), 0, 1)
    tl = np.clip(L.eotf(tgt), 0, 1)
    W, *_ = np.linalg.lstsq(ml, tl, rcond=None)
    de_mat = L.ciede2000(srgb_lab(L.oetf(np.clip(ml @ W, 0, 1))), labt)

    out.update(da_g=da_g, db_g=db_g,
               de_fix=de_fix.tolist(), de_mat=de_mat.tolist(),
               mat=W.tolist())

    # ---- 去掉全局色偏后，各族剩下的 (da, db)
    fam = {}
    da_r = np.array(out['da']) - da_g
    db_r = np.array(out['db']) - db_g
    dL_r = np.array(out['dL'])
    for f, ids in FAMILY.items():
        ids = np.array(ids)
        fam[f] = dict(n=len(ids), de=float(de[ids].mean()),
                      de_fix=float(de_fix[ids].mean()),
                      dL=float(dL_r[ids].mean()),
                      da=float(da_r[ids].mean()), db=float(db_r[ids].mean()),
                      dC=float(np.mean(np.array(out['dC'])[ids])),
                      dh=float(np.mean(np.array(out['dh'])[ids])))
    out['fam'] = fam
    return out


def summ(r):
    de = np.array(r['de'])
    order = np.argsort(-de)
    return dict(mean=float(de.mean()), p90=float(np.percentile(de, 90)), mx=float(de.max()),
                mean_nf=float(np.mean(r['de_nf'])), mean_raw=float(np.mean(r['de_raw'])),
                max_nf=float(np.max(r['de_nf'])), p90_nf=float(np.percentile(r['de_nf'], 90)),
                mean_fix=float(np.mean(r['de_fix'])), mean_mat=float(np.mean(r['de_mat'])),
                p90_fix=float(np.percentile(r['de_fix'], 90)),
                max_fix=float(np.max(r['de_fix'])),
                worst=[dict(i=int(j), name=PATCH_NAMES[j], de=float(de[j]),
                            dL=float(r['dL'][j]), dC=float(r['dC'][j]), dh=float(r['dh'][j]),
                            da=float(r['da'][j]), db=float(r['db'][j])) for j in order[:5]],
                worst_fix=[dict(i=int(j), name=PATCH_NAMES[j], de=float(r['de_fix'][j]),
                                dL=float(r['dL'][j]), dC=float(r['dC'][j]),
                                da=float(r['da'][j]) - r['da_g'],
                                db=float(r['db'][j]) - r['db_g'])
                           for j in np.argsort(-np.array(r['de_fix']))[:5]])


def main():
    # 同一批拍摄共用 (s, F)：PB210134/136/137 同一场景同一曝光；PB210139 光照不同
    groupA = ['ektachrome_100_vs', 'classic_chrome', 'pro_neg_hi']
    items = []
    for stem, k, look, note in SHOTS:
        jpg = os.path.join(IMG, stem + '.JPG')
        lin = raw_patches(os.path.join(IMG, stem + '.ORF'), patch_centers(jpg))
        items.append((look, lin, jc.sample(jpg) / 255.0))
    sA, FA = fit_sf_joint([it for it in items if it[0] in groupA])
    sB, FB = fit_sf_joint([it for it in items if it[0] not in groupA])
    sf = {look: ((sA, FA) if look in groupA else (sB, FB)) for look, _, _ in items}
    print('共拟合: V/M/P s=%.2f F=%.3f ; N s=%.2f F=%.3f' % (sA, FA, sB, FB))

    res = {}
    for stem, k, look, note in SHOTS:
        r = analyse(stem, k, look, note, *sf[look])
        r['group_sf'] = sf[look]
        sm = summ(r)
        res[look] = dict(raw=r, summary=sm)
        print('\n== %s  %s (k=%d)  s=%.2f  F=%.3f' % (stem, note, k, r['s'], r['F']))
        print('   dE00 raw(不校) %.1f | 只校曝光 %.2f | 曝光+眩光 %.2f (p90 %.2f max %.2f)'
              % (sm['mean_raw'], sm['mean_nf'], sm['mean'], sm['p90'], sm['mx']))
        print('   去全局色偏 %.2f (p90 %.2f max %.2f) | 最优3x3 %.2f'
              % (sm['mean_fix'], sm['p90_fix'], sm['max_fix'], sm['mean_mat']))
        print('   gray: dL ' + ' '.join('%+.1f' % r['dL'][i] for i in GRAYS))
        print('   worst: ' + ', '.join('%d %s %.1f(dL%+.0f dC%+.0f dh%+.0f)'
                                       % (w['i'], w['name'], w['de'], w['dL'], w['dC'], w['dh'])
                                       for w in sm['worst']))
        print('   worst(chroma, 去偏): ' + ', '.join('%d %s %.1f(da%+.1f db%+.1f)'
                                                     % (w['i'], w['name'], w['de'], w['da'], w['db'])
                                                     for w in sm['worst_fix']))
        print('   family(去全局偏): ' + ' | '.join(
            '%s %.2f da%+.1f db%+.1f dC%+.1f dh%+.0f'
            % (f, v['de_fix'], v['da'], v['db'], v['dC'], v['dh']) for f, v in r['fam'].items()))
    with open(os.path.join(ROOT, '.tmp', 'chroma_gap.json'), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, ensure_ascii=False)


if __name__ == '__main__':
    main()
