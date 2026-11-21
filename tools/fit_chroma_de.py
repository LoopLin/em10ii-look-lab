"""fit_chroma_de.py -- refit the 16-point Cb/Cr curves by direct dE00 descent.

Why: lookfit's chroma stage is fitted against the *fitted* display, so the 16
nodes never see dE00 directly.  This script keeps everything else (matrix, tone,
exposure) frozen and only moves the 32 y-values, so the result drops straight
into the same JSON/builder path.

Hardware constraints honoured (docs/analysis/chroma-limits.md):
  * 16 nodes, x = 0,17,...,255 (unchanged)
  * y non-decreasing, 0..255, integer
  * slope <= 1.992 (Q7 z == 255 saturates instead of wrapping)
  * y[0] is not forwarded by the hardware; keep it equal to y[1]'s value domain
"""
import json
import sys
import numpy as np

sys.path.insert(0, 'build/looks_final/_tools')
import lookfit as lf

LOOKS = ['ektachrome_100_vs', 'elite_color_200', 'classic_chrome', 'pro_neg_hi']
OUT = 'build/looks_chroma'
CH_X = np.arange(16) * 17.0
SLOPE_MAX = np.floor(1.992 * 17)


def load_look(look):
    d = json.load(open('build/looks_final/%s.json' % look, encoding='utf-8'))
    M = np.array(d['matrix_q8'], float).reshape(3, 3) / 256.0
    e = d['meta']['exposure_e_absorbed']
    tone = np.array(d['tone256'], float) / 1023.0
    cb = np.array(d['chroma_cb'], float)
    cr = np.array(d['chroma_cr'], float)
    return d, M, e, tone, cb, cr


def samples(stems, look, per_scene=6000, seed=1):
    """Return per-scene (lin, target_lin) pixel sets in camera-linear space."""
    rng = np.random.default_rng(seed)
    tgt = lf.target_cached(look)
    sets = []
    for stem in stems:
        lin = np.asarray(lf.load_scene(stem), np.float32).reshape(-1, 3)
        idx = rng.choice(len(lin), min(per_scene, len(lin)), replace=False)
        x = lin[idx]
        sets.append((x, np.clip(tgt(x), 0, 1)))
    return sets


def evaluate(M, e, tone, cb, cr, sets):
    m = lf.Model(e=e, M=M, tone=tone, cb=cb, cr=cr)
    des_n, tgt_n = 0.0, 0.0
    for x, t in sets:
        fit = np.clip(m.display(x), 0, 1)
        tgt = np.clip(lf.oetf(t), 0, 1)
        de = lf.ciede2000(lf.srgb_to_lab(fit), lf.srgb_to_lab(tgt))
        des_n += de.sum()
        tgt_n += len(de)
    return des_n / tgt_n


def fit_curve(base, other_curve, M, e, tone, sets, rounds=3, step=8.0):
    """Coordinate descent on one channel's 16 y values (other channel frozen)."""
    y = base.copy()
    cur = evaluate(M, e, tone, np.stack([CH_X, y], 1), other_curve, sets)
    for _ in range(rounds):
        improved = False
        for i in range(1, 16):
            for d in (-step, step):
                cand = y.copy()
                cand[i] = np.clip(cand[i] + d, 0, 255)
                # keep monotone + slope cap within the segment
                lo = max(cand[i - 1], cand[i] - SLOPE_MAX)
                hi = min(cand[min(i + 1, 15)], cand[i] + SLOPE_MAX)
                cand[i] = np.clip(cand[i], lo, hi)
                if cand[i] == y[i]:
                    continue
                # y[0] mirrors y[1] (hardware does not forward y[0])
                cand[0] = cand[1]
                v = evaluate(M, e, tone, np.stack([CH_X, cand], 1), other_curve, sets)
                if v < cur - 1e-4:
                    cur, y, improved = v, cand, True
        step *= 0.5
        if not improved and step < 1.0:
            break
    return y, cur


def main():
    import os
    os.makedirs(OUT, exist_ok=True)
    meta = lf.scene_list()
    for look in LOOKS:
        d, M, e, tone, cb, cr = load_look(look)
        sets = samples(meta['train'][:10] + meta['val'], look)
        y_cb = cb[:, 1].copy()
        y_cr = cr[:, 1].copy()
        base = evaluate(M, e, tone, cb, cr, sets)
        y_cb, _ = fit_curve(y_cb, cr, M, e, tone, sets)
        cb2 = np.stack([CH_X, y_cb], 1)
        y_cr, _ = fit_curve(y_cr, cb2, M, e, tone, sets)
        cr2 = np.stack([CH_X, y_cr], 1)
        after = evaluate(M, e, tone, cb2, cr2, sets)
        out = dict(d)
        out['chroma_cb'] = [[int(x), int(round(v))] for x, v in cb2]
        out['chroma_cr'] = [[int(x), int(round(v))] for x, v in cr2]
        out['meta'] = dict(d['meta'])
        out['meta']['chroma_refit'] = 'dE00 descent, matrix/tone frozen'
        json.dump(out, open('%s/%s.json' % (OUT, look), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        print('%-20s dE00 %.3f -> %.3f' % (look, base, after))


if __name__ == '__main__':
    main()
