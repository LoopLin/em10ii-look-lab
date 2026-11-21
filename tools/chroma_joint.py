# -*- coding: utf-8 -*-
"""chroma_joint.py -- joint offline refit of the 3x3 matrix (Q8 fixed point) + the 16/17-point
Cb/Cr tables for the two Kodak-family film looks (V = Ektachrome 100 VS, N = Elite Color 200).

Offline only: nothing here touches the camera, the firmware image, or I:.

Why joint: an earlier single-channel greedy fit (Cb alone, then Cr) made three looks worse --
the two chroma axes are coupled through the YCbCr reconstruction, so the objective must be the
full 24-patch dE00 with both axes moving at the same time.

Pipeline
  1. .tmp/chroma_gap.json -> 24 patches: scene-linear `lin`, measured JPEG Lab `lab_m`, reference
     target Lab `lab_t`, and the (s, F) fitted on the 6 grey patches.
  2. Calibrate the forward chain model (build/looks_final/_tools/lookfit.py Model) to the measured
     JPEG with one input gain `g` per look.  rawpy's camera-linear scale is not the scale the
     delivered parameters were fitted on: without `g` the model is 12..32 dE00 off
     (see .tmp/cj_probe.py); with `g` it reproduces the measured chart to ~4..7 dE00 mean.
  3. Freeze that residual as a per-patch Lab bias  bias_i = lab_m_i - model_base_i, and predict
        pred_i = model_new_i + bias_i
     so the "before" column *is* the real measurement and the "after" column is a model delta
     that shares the baseline calibration (transfer assumption: JPEG-side residual is invariant
     to the parameter change).
  4. Optimise by integer coordinate descent with monotone projection:
       - matrix: 6 free Q8 entries, m00/m11/m22 derived so every row sums to 256, and every
         candidate passes the encoder's fixed-point rule (enc_matrix_hw, mirrored from
         tools/build_l30.py);
       - chroma: the 15 free y values of each of the 4 tables, monotone non-decreasing, first
         step <= 33 so the chain's Q7 slope z = round(128*dy/17) never reaches the 255 clamp
         (255 = hardware gain ceiling 1.992);
     against mean dE00.  Two regularisers: pull-back toward the current table (`lam`) and
     curvature (`mus`) -- 24 patches against ~66 integers will otherwise fit per-band noise.
     A leave-one-patch-out (LOO) refit is reported as the honest generalisation estimate.

Usage
  python tools/chroma_joint.py                     # V,N with the default regulariser grid
  python tools/chroma_joint.py --looks V --mus 0.03 --lam 0
  python tools/chroma_joint.py --matrix-only       # control: matrix free, chroma frozen
"""
import argparse
import json
import os
import struct
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.path.insert(0, os.path.join(ROOT, 'build', 'looks_final', '_tools'))
import lookfit as L            # noqa: E402
import oly_container as oc     # noqa: E402

LOOKS = {'V': 'ektachrome_100_vs', 'N': 'elite_color_200',
         'M': 'classic_chrome', 'P': 'pro_neg_hi'}
SLOT = {'V': 0x605DAF44, 'N': 0x605DB144, 'M': 0x605DB344, 'P': 0x605DB544}
FW_BASE = 0x60021020
CHROMA_X = np.arange(0, 256, 17)   # 16 nodes; the 17th (256) is the implicit end point
Y0 = 0                             # y[0] is not propagated by the chain; keep it stock
DY_MAX = 33                        # z = round(128*dy/17): dy=34 would hit the 255 clamp
GRAYS = list(range(18, 24))        # patches 19..24 are the grey ramp


# ------------------------------------------------------------------ firmware / parameters
def load_l39_tones():
    """The four 256-entry tone tables actually running in the flashed L39 image."""
    pay = oc.parse(open(os.path.join(ROOT, 'build', 'L39_tone_trim.BIN'), 'rb').read())[0].payload
    return {k: np.array(struct.unpack_from('<256H', pay, SLOT[k] - FW_BASE), float) / 1023.0
            for k in SLOT}


def load_look(k):
    with open(os.path.join(ROOT, 'build', 'looks_final', LOOKS[k] + '.json'), encoding='utf-8') as f:
        return json.load(f)


def enc_matrix_hw(f):
    """Fixed-point rule of the encoder (mirror of tools/build_l30.py:enc_matrix_hw)."""
    f = [int(v) for v in f]
    f[1] &= ~1
    f[7] &= ~1
    if (f[3] + f[5]) & 1:
        f[3] &= ~1
        f[5] &= ~1
    f[0] = 256 - f[1] - f[2]
    f[4] = 256 - f[3] - f[5]
    f[6] = 256 - f[7] - f[8]
    assert sum(f[0:3]) == 256 and sum(f[3:6]) == 256 and sum(f[6:9]) == 256
    return f


def matrix_from_free(v):
    m01, m02, m10, m12, m20, m21 = [int(x) for x in v]
    return enc_matrix_hw([0, m01, m02, m10, 0, m12, m20, m21, 0])


def free_from_matrix(m):
    m = [int(x) for x in m]
    return [m[1], m[2], m[3], m[5], m[6], m[7]]


def matrix_ok(m):
    return all(-2048 <= v <= 2048 for v in m)


def z_of(y):
    return [min(255, max(0, int(round(128.0 * (y[i + 1] - y[i]) / 17.0)))) for i in range(15)]


def project_y(y):
    """integer, non-decreasing, first step <= DY_MAX, inside 0..255, y[0] fixed."""
    y = np.clip(np.round(np.asarray(y, float)), 0, 255).astype(int)
    y[0] = Y0
    for i in range(1, len(y)):
        y[i] = min(max(y[i], y[i - 1]), y[i - 1] + DY_MAX)
    for i in range(len(y) - 2, -1, -1):
        y[i] = min(y[i], y[i + 1])
    y[0] = Y0
    for i in range(1, len(y)):
        y[i] = max(y[i], y[i - 1])
    return y


def curvature(y):
    y = np.asarray(y, float)
    return float(np.abs(np.diff(y, 2)).sum()) / max(1, len(y) - 2)



# ------------------------------------------------------------------ model-free bounds
def bounds(key, gap, f0=False):
    """What a chroma-table-only change can reach, WITHOUT the chain model.

    Uses only the measured patch codes (rgb8) and the reference target (lab_t):
      A  keep L*, take a*/b* from the target -> residual that only tone/matrix could fix
      A2 keep a*/b*, take L* from the target -> residual that only tone/matrix could fix
      B  best single global axis gain (k_cb, k_cr) on the measured chroma codes.  A 16-node
         table reproduces this band by band, so B is a fair estimate of the ceiling -- and a
         single (k_cb, k_cr) is the lowest-risk table you can write.
    """
    import chroma_gap as cg
    r = gap[LOOKS[key]]['raw']
    rgb = np.array(r['rgb8'], float) / 255.0
    lab_m = np.array(r['lab_m'])
    if f0:   # target without the veil-glare assumption: F -> 0
        lab_t = L.srgb_to_lab(L.oetf(cg.lut_lin(LOOKS[key], np.array(r['lin'], float), r['s'])))
    else:
        lab_t = np.array(r['lab_t'])
    y = rgb @ L.Y601
    cb, cr = 128 + 255 * (rgb @ L.CB_RGB), 128 + 255 * (rgb @ L.CR_RGB)
    a = L.ciede2000(np.c_[lab_m[:, 0], lab_t[:, 1], lab_t[:, 2]], lab_t)
    a2 = L.ciede2000(np.c_[lab_t[:, 0], lab_m[:, 1], lab_m[:, 2]], lab_t)
    best = (1e9, 1.0, 1.0)
    for kcb in np.arange(0.40, 1.61, 0.02):
        for kcr in np.arange(0.40, 1.61, 0.02):
            rgb2 = L.from_ycbcr(y, 128 + kcb * (cb - 128), 128 + kcr * (cr - 128))
            d = L.ciede2000(L.srgb_to_lab(np.clip(rgb2, 0, 1)), lab_t).mean()
            if d < best[0]:
                best = (d, kcb, kcr)
    return dict(base=round(float(L.ciede2000(lab_m, lab_t).mean()), 2),
                floor_dL=round(float(a.mean()), 2), floor_dC=round(float(a2.mean()), 2),
                gain=round(float(best[0]), 2), k_cb=round(float(best[1]), 2),
                k_cr=round(float(best[2]), 2))


def gain_tables(y0, k):
    """The whole change: one gain per axis, applied to the table around the neutral point."""
    return project_y(128.0 + k * (np.asarray(y0, float) - 128.0))


# ------------------------------------------------------------------ forward chain
def disp(lin, M, tone, cb_y, cr_y):
    M = np.asarray(M, float)
    M = M.reshape(3, 3) if M.shape != (3, 3) else M
    y = np.clip(lin @ (M / 256.0).T, 0, 1)
    idx = np.clip(y / L.Model.XWHITE, 0, 1) * 255.0
    t = np.interp(idx, np.arange(256), tone)
    _, cbi, cri = L.ycbcr(t)
    cb2 = np.interp(np.clip(cbi, 0, 255), CHROMA_X, cb_y)
    cr2 = np.interp(np.clip(cri, 0, 255), CHROMA_X, cr_y)
    return L.from_ycbcr(t @ L.Y601, cb2, cr2)


def lab_of(rgb):
    return L.srgb_to_lab(np.clip(rgb, 0, 1))


class LookFit:
    def __init__(self, key, gap, tones, g=None):
        self.key = key
        raw = gap[LOOKS[key]]['raw']
        self.lin = np.array(raw['lin'], float)
        self.lab_m = np.array(raw['lab_m'], float)
        self.lab_t = np.array(raw['lab_t'], float)
        self.s, self.F = raw['s'], raw['F']
        self.tone = tones[key]
        j = load_look(key)
        self.M0 = [int(v) for v in j['matrix_q8']]
        self.cb0 = np.array([p[1] for p in j['chroma_cb']], int)
        self.cr0 = np.array([p[1] for p in j['chroma_cr']], int)
        self.g = g if g is not None else self.calibrate()
        base = lab_of(self.predict_raw(self.M0, self.cb0, self.cr0))
        self.bias = self.lab_m - base
        self.base_pred = self.predict_lab(self.M0, self.cb0, self.cr0)

    def calibrate(self):
        best = (1e9, 1.0)
        for g in np.exp(np.linspace(np.log(0.5), np.log(20.0), 90)):
            d = L.ciede2000(lab_of(disp(self.lin * g, self.M0, self.tone, self.cb0, self.cr0)),
                            self.lab_m).mean()
            if d < best[0]:
                best = (d, g)
        return float(best[1])

    def predict_raw(self, M, cby, cry):
        return disp(self.lin * self.g, M, self.tone, np.asarray(cby, float), np.asarray(cry, float))

    def predict_lab(self, M, cby, cry):
        return lab_of(self.predict_raw(M, cby, cry)) + self.bias

    def de(self, M, cby, cry):
        return L.ciede2000(self.predict_lab(M, cby, cry), self.lab_t)

    def cost(self, M, cby, cry, lam, mus):
        c = float(self.de(M, cby, cry).mean())
        if lam:
            c += lam * (np.abs(np.asarray(cby, float) - self.cb0).sum()
                        + np.abs(np.asarray(cry, float) - self.cr0).sum()) / 30.0
        if mus:
            c += mus * (curvature(cby) + curvature(cry))
        return c

    def fit(self, lam=0.0, mus=0.0, sweeps=14, seed=0, matrix_only=False):
        rng = np.random.RandomState(seed)
        M = list(self.M0)
        cby, cry = self.cb0.copy(), self.cr0.copy()
        best = self.cost(M, cby, cry, lam, mus)
        steps = [8, 4, 2, 1]
        msteps = [32, 16, 8, 4, 2, 1]
        for _ in range(sweeps):
            improved = False
            for pos in range(6):
                for st in msteps:
                    for dv in (st, -st):
                        fr = free_from_matrix(M)
                        fr[pos] += dv
                        cand = matrix_from_free(fr)
                        if not matrix_ok(cand):
                            continue
                        c = self.cost(cand, cby, cry, lam, mus)
                        if c < best - 1e-9:
                            best, M, improved = c, cand, True
            if matrix_only:
                if not improved:
                    break
                continue
            idx = np.arange(1, 16)
            for tag in rng.permutation(np.r_[idx, idx + 100]):
                y = cby.copy() if tag < 100 else cry.copy()
                j = int(tag % 100)
                for st in steps:
                    for dv in (st, -st):
                        cand = y.copy()
                        cand[j] += dv
                        cand = project_y(cand)
                        if np.array_equal(cand, y):
                            continue
                        c = (self.cost(M, cand, cry, lam, mus) if tag < 100
                             else self.cost(M, cby, cand, lam, mus))
                        if c < best - 1e-9:
                            best = c
                            if tag < 100:
                                cby = cand
                            else:
                                cry = cand
                            y = cand
                            improved = True
            if not improved:
                break
        return M, cby, cry, best

    def _sub(self, keep):
        F = LookFit.__new__(LookFit)
        F.__dict__.update(self.__dict__)
        F.lin, F.lab_m, F.lab_t = self.lin[keep], self.lab_m[keep], self.lab_t[keep]
        F.bias = self.bias[keep]
        return F

    def loo(self, lam=0.0, mus=0.0, sweeps=5, matrix_only=False):
        out = np.zeros(24)
        for i in range(24):
            Fse = self._sub(np.arange(24) != i)
            M, cb, cr, _ = Fse.fit(lam=lam, mus=mus, sweeps=sweeps, seed=i, matrix_only=matrix_only)
            out[i] = float(L.ciede2000(self.predict_lab(M, cb, cr), self.lab_t)[i])
        return out



def output_chroma_delta(f, M, cby, cry):
    """Per-patch change of the *output* chroma codes produced by the new tables.
    Independent cross-check against docs/analysis/chroma-gap.md 4.3, which derived the
    required output-domain dCb/dCr directly from measured-vs-target (no chain model)."""
    lin = f.lin * f.g
    Mm = np.asarray(M, float).reshape(3, 3)
    y = np.clip(lin @ (Mm / 256.0).T, 0, 1)
    idx = np.clip(y / L.Model.XWHITE, 0, 1) * 255.0
    t = np.interp(idx, np.arange(256), f.tone)
    _, cbi, cri = L.ycbcr(t)
    cbi = np.clip(cbi, 0, 255); cri = np.clip(cri, 0, 255)
    dcb = np.interp(cbi, CHROMA_X, cry * 0 + 0)  # placeholder, replaced below
    dcb = np.interp(cbi, CHROMA_X, np.asarray(cby, float)) - np.interp(cbi, CHROMA_X, f.cb0.astype(float))
    dcr = np.interp(cri, CHROMA_X, np.asarray(cry, float)) - np.interp(cri, CHROMA_X, f.cr0.astype(float))
    return cbi, cri, dcb, dcr


def row_bounds_txt(row):
    b = row['bounds']
    return ('ceiling(ideal chroma) %.2f | global gain %.2f @ k_cb=%.2f k_cr=%.2f | dL floor %.2f'
            % (b['floor_dL'], b['gain'], b['k_cb'], b['k_cr'], b['floor_dC']))


def summarise(d):
    return dict(mean=round(float(d.mean()), 2), p90=round(float(np.percentile(d, 90)), 2),
                max=round(float(d.max()), 2), grey=round(float(d[GRAYS].mean()), 2),
                worst=int(np.argmax(d)) + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--looks', default='V,N')
    ap.add_argument('--lam', nargs='+', type=float, default=[0.0, 0.02])
    ap.add_argument('--mus', nargs='+', type=float, default=[0.0, 0.02, 0.05, 0.12])
    ap.add_argument('--out', default='.tmp/chroma_joint.json')
    ap.add_argument('--matrix-only', action='store_true')
    ap.add_argument('--mode', choices=['joint', 'gain', 'both'], default='joint')
    a = ap.parse_args()

    with open(os.path.join(ROOT, '.tmp', 'chroma_gap.json'), encoding='utf-8') as f:
        gap = json.load(f)
    tones = load_l39_tones()
    res = {}

    for key in a.looks.split(','):
        f = LookFit(key, gap, tones)
        base = L.ciede2000(f.base_pred, f.lab_t)
        raw0 = L.ciede2000(lab_of(f.predict_raw(f.M0, f.cb0, f.cr0)), f.lab_t)
        print('\n=== %s (%s)  s=%.2f F=%.3f  input gain g=%.2f' % (key, LOOKS[key], f.s, f.F, f.g))
        print('    measured (= bias-corrected baseline): mean %.2f max %.2f'
              % (base.mean(), base.max()))
        print('    chain model at current params (g-cal, no bias): mean %.2f max %.2f'
              % (raw0.mean(), raw0.max()))
        row = dict(key=key, look=LOOKS[key], s=f.s, F=f.F, g=f.g, base=summarise(base),
                   bounds=bounds(key, gap),
                   bounds_f0=(bounds(key, gap, f0=True) if key == 'V' else None))
        row.update(matrix_q8_now=f.M0, chroma_cb_now=list(map(int, f.cb0)),
                   chroma_cr_now=list(map(int, f.cr0)), matrix_only=a.matrix_only, runs=[])
        print('    model-free bounds: %s' % row_bounds_txt(row))
        if row['bounds_f0']:
            print('    model-free bounds, F=0 target: %s'
                  % row_bounds_txt({'bounds': row['bounds_f0']}))

        if a.mode in ('gain', 'both'):
            gb = row['bounds']
            gbest = (1e9, gb['k_cb'], gb['k_cr'], None, None)
            for kcb in np.arange(0.30, 1.41, 0.02):
                for kcr in np.arange(0.30, 1.41, 0.02):
                    cby, cry = gain_tables(f.cb0, kcb), gain_tables(f.cr0, kcr)
                    d = f.de(f.M0, cby, cry)
                    if d.mean() < gbest[0]:
                        gbest = (float(d.mean()), float(kcb), float(kcr), cby, cry)
            _, kcb, kcr, cby, cry = gbest
            yend = f.lin * f.g
            Mm = np.array(f.M0, float).reshape(3, 3)
            t = np.interp(np.clip(np.clip(yend @ (Mm / 256.0).T, 0, 1) / L.Model.XWHITE, 0, 1) * 255.0,
                          np.arange(256), f.tone)
            _, cbi, cri = L.ycbcr(t)
            dcbi = (np.interp(np.clip(cbi, 0, 255), CHROMA_X, cby.astype(float))
                    - np.interp(np.clip(cbi, 0, 255), CHROMA_X, f.cb0.astype(float)))
            dcri = (np.interp(np.clip(cri, 0, 255), CHROMA_X, cry.astype(float))
                    - np.interp(np.clip(cri, 0, 255), CHROMA_X, f.cr0.astype(float)))
            d = f.de(f.M0, cby, cry)
            print('  [gain] k_cb=%.2f k_cr=%.2f -> pred %.2f max %.2f  (model-free B: %.2f @ %.2f/%.2f)'
                  % (kcb, kcr, d.mean(), d.max(), gb['gain'], gb['k_cb'], gb['k_cr']))
            print('         cb %s' % list(map(int, cby)))
            print('         cr %s' % list(map(int, cry)))
            cbyf, cryf = gain_tables(f.cb0, gb['k_cb']), gain_tables(f.cr0, gb['k_cr'])
            df = f.de(f.M0, cbyf, cryf)
            print('  [gain/model-free k] k_cb=%.2f k_cr=%.2f -> pred %.2f max %.2f'
                  % (gb['k_cb'], gb['k_cr'], df.mean(), df.max()))
            print('         cb %s' % list(map(int, cbyf)))
            print('         cr %s' % list(map(int, cryf)))
            row['gain_free'] = dict(k_cb=gb['k_cb'], k_cr=gb['k_cr'], summary=summarise(df),
                                    chroma_cb=[[int(x), int(y)] for x, y in zip(CHROMA_X, cbyf)],
                                    chroma_cr=[[int(x), int(y)] for x, y in zip(CHROMA_X, cryf)],
                                    z_cb=z_of(cbyf), z_cr=z_of(cryf),
                               de_patch_before=[round(float(v), 2) for v in base],
                               de_patch_after=[round(float(v), 2) for v in df])
            row['gain'] = dict(k_cb=kcb, k_cr=kcr, summary=summarise(d),
                               chroma_cb=[[int(x), int(y)] for x, y in zip(CHROMA_X, cby)],
                               chroma_cr=[[int(x), int(y)] for x, y in zip(CHROMA_X, cry)],
                               z_cb=z_of(cby), z_cr=z_of(cry),
                               out_dcb=[round(float(v), 1) for v in dcbi],
                               out_dcr=[round(float(v), 1) for v in dcri])

        for mus in (a.mus if a.mode in ('joint', 'both') else []):
            for lam in a.lam:
                if a.matrix_only and (mus or lam):
                    continue
                M, cb, cr, _ = f.fit(lam=lam, mus=mus, matrix_only=a.matrix_only)
                d = f.de(M, cb, cr)
                d_raw = L.ciede2000(lab_of(f.predict_raw(M, cb, cr)), f.lab_t)
                loo = f.loo(lam=lam, mus=mus, matrix_only=a.matrix_only)
                print('  lam=%-5g mus=%-5g  pred %.2f/%.2f  rawmodel %.2f (was %.2f)  '
                      'cbD%2d crD%2d  curv %.1f/%.1f  LOO %.2f  mat_changed %d'
                      % (lam, mus, d.mean(), d.max(), d_raw.mean(), raw0.mean(),
                         np.abs(cb - f.cb0).max(), np.abs(cr - f.cr0).max(),
                         curvature(cb), curvature(cr), loo.mean(),
                         sum(1 for i in range(9) if M[i] != f.M0[i])))
                cbi, cri, dcb, dcr = output_chroma_delta(f, M, cb, cr)
                print('      out-code shift at patches: ' + ' '.join(
                    '%d:(%+.0f,%+.0f)' % (i + 1, dcb[i], dcr[i]) for i in range(0, 8)))
                row['runs'].append(dict(
                    lam=lam, mus=mus, summary=summarise(d), loo=summarise(loo),
                    raw_model=summarise(d_raw), raw_model_base=summarise(raw0),
                    matrix_q8=M,
                    matrix_delta=[int(M[i]) - int(f.M0[i]) for i in range(9)],
                    matrix_bytes_changed=sum(1 for i in range(9) if M[i] != f.M0[i]),
                    chroma_cb=[[int(x), int(y)] for x, y in zip(CHROMA_X, cb)],
                    chroma_cr=[[int(x), int(y)] for x, y in zip(CHROMA_X, cr)],
                    cb_maxdelta=int(np.abs(cb - f.cb0).max()),
                    cr_maxdelta=int(np.abs(cr - f.cr0).max()),
                    z_cb=z_of(cb), z_cr=z_of(cr),
                    curvature=[round(curvature(cb), 2), round(curvature(cr), 2)],
                    out_dcb=[round(float(v), 1) for v in dcb],
                    out_dcr=[round(float(v), 1) for v in dcr],
                    de_patch_before=[round(float(v), 2) for v in base],
                    de_patch_after=[round(float(v), 2) for v in d]))
        res[key] = row

        if key == 'V':
            import chroma_gap as cg
            tgt_nf = L.srgb_to_lab(L.oetf(cg.lut_lin(LOOKS[key], f.lin, f.s)))  # lab_t with F=0
            fnf = LookFit.__new__(LookFit)
            fnf.__dict__.update(f.__dict__)
            fnf.lab_t = tgt_nf
            base_nf = L.ciede2000(f.base_pred, tgt_nf)
            print('  [F=0 target] measured-vs-target %.2f (doc: 8.35)'
                  % base_nf.mean())
            row['f0'] = dict(base=summarise(base_nf), runs=[])
            for mus in (0.0, 0.05):
                M, cb, cr, _ = fnf.fit(lam=0.0, mus=mus)
                d = fnf.de(M, cb, cr)
                loo = fnf.loo(lam=0.0, mus=mus)
                print('  [F=0] mus=%-5g mean %.2f max %.2f  LOO %.2f'
                      % (mus, d.mean(), d.max(), loo.mean()))
                row['f0']['runs'].append(dict(mus=mus, summary=summarise(d), loo=summarise(loo),
                                              matrix_q8=M,
                                              chroma_cb=[[int(x), int(y)] for x, y in zip(CHROMA_X, cb)],
                                              chroma_cr=[[int(x), int(y)] for x, y in zip(CHROMA_X, cr)]))

    with open(os.path.join(ROOT, a.out), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print('\nwrote', a.out)


if __name__ == '__main__':
    main()
