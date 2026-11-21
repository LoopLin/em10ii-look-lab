#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""index_solve.py — 用 L58 锯齿探针求解 tone 表输入索引的仿射模型。

模型（按档各自解）:
    T    = SAW[clip(round(a * i_model + b), 0, 255)]      # 10 bit, 0..1023
    out8 ≈ S(T)                                          # ISP 侧单调响应
    i_model = 离线模型索引：look 矩阵(matrix_q8) 后的线性 RGB 取 luma，
              再 sRGB 编码 ×255。正是 tools/offline_from_l58.py 的 idx_gray 域。

数据: build/L58_sawtooth.BIN 拍的四张
    PB210196 = V(ektachrome_100_vs) / 197 = N(elite_color_200)
    PB210198 = M(classic_chrome)    / 199 = P(pro_neg_hi)

方法:
  * 几何不用 JPG 自己找框（solarize 图上不稳），改用 ORF：rawpy half_size
    出线性 RGB -> luma -> 99.5 百分位归一 -> judge_chart._frame 找黑框，
    得**归一化** (y0,y1,x0,x1)，按同一比例映射到 JPG。
  * 每块取中心 50% 的中位数（24 块 = 4 行 x 6 列，row-major）。
  * L58 表是**三角波**（锚点 0,10,...,250,255 交替 1023/0），并且固件只把
    27 个锚点做单调三次重采样后送硬件（docs/analysis/tone-second-stage.md），
    所以“亮/暗”只是 0/1023 的粗糙二值化：
        out8 >= 180 记 B，<= 60 记 D，其余 "?"（不参与亮暗约束）。
  * 主检验：Pearson(out8, T[a*i+b]) 在 (a,b) 网格上取最大，并用置换给出
    “网格搜索后的零分布”——p 值不显著则判该档**未定**，不给硬凑的 a,b。
  * 次检验：置信块亮暗命中数（24 块里的 B/D），同样给置换 p 值与错配数。

用法:
    python tools/index_solve.py             # 用缓存(没有则重建)求解
    python tools/index_solve.py --rebuild   # 重新从 ORF/JPG 采样
    python tools/index_solve.py --json build/index_solve_result.json
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "build", "index_solve_cache.json")
DCIM = r"I:\DCIM\100OLYMP\PB21%04d"

SLOTS = [
    ("V", 196, "ektachrome_100_vs"),
    ("N", 197, "elite_color_200"),
    ("M", 198, "classic_chrome"),
    ("P", 199, "pro_neg_hi"),
]

LUM = np.array([0.2126, 0.7152, 0.0722])

# L58 表锚点（tools/build_l58.py）
ANCHORS = list(range(0, 256, 10)) + [255]
AVALS = [1023 if k % 2 == 0 else 0 for k in range(len(ANCHORS))]


def saw_table():
    """tools/build_l58.py 生成的 256 项 10bit 三角波。"""
    x = np.arange(256, dtype=float)
    return np.interp(x, ANCHORS, AVALS)


SAW = saw_table()
OUT_LEVELS = sorted(set((SAW / 1023.0 * 255.0).round(1).tolist()))


def srgb_encode(v):
    v = np.maximum(v, 0.0)
    return np.where(v > 0.0031308, 1.055 * v ** (1 / 2.4) - 0.055, 12.92 * v)


def _look(lookname):
    p = os.path.join(ROOT, "build", "looks_final", "%s.json" % lookname)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _matrix(look):
    return np.array(look["matrix_q8"], float).reshape(3, 3) / 256.0


def frame_norm(rgb):
    """raw 线性 RGB -> 归一化灰度 -> judge_chart._frame，返回像素坐标框。"""
    import judge_chart as jc

    g = rgb @ LUM
    hi = np.percentile(g, 99.5)
    if hi <= 0:
        raise RuntimeError("blank ORF")
    g = np.clip(g / hi * 255.0, 0, 255)
    img = np.stack([g, g, g], axis=-1).astype(np.uint8)
    return jc._frame(img)


def patch_boxes(frame, shape):
    """judge_chart.sample 的几何，返回 24 个 (y0,y1,x0,x1) 像素框（中心 50%）。"""
    fy0, fy1, fx0, fx1 = frame
    H, W = shape[:2]
    fx0, fx1 = max(fx0, 0), min(fx1, W - 1)
    fy0, fy1 = max(fy0, 0), min(fy1, H - 1)
    Wf, Hf = fx1 - fx0, fy1 - fy0
    px0 = fx0 + 0.065 * Wf
    px1 = fx1 - 0.062 * Wf
    py0 = fy0 + 0.018 * Hf
    py1 = fy1 - 0.009 * Hf
    cw = (px1 - px0) / 6.0
    ch = (py1 - py0) / 4.0
    out = []
    for r in range(4):
        for c in range(6):
            x0 = px0 + c * cw
            y0 = py0 + r * ch
            mx, my = cw * 0.25, ch * 0.25
            out.append((int(round(y0 + my)), int(round(y0 + ch - my)),
                        int(round(x0 + mx)), int(round(x0 + cw - mx))))
    return out


def build_cache(verbose=True):
    import rawpy
    from PIL import Image

    data = {}
    for slot, num, lookname in SLOTS:
        orf = DCIM % num + ".ORF"
        jpg = DCIM % num + ".JPG"
        if verbose:
            print("decode %s ..." % os.path.basename(orf), flush=True)
        with rawpy.imread(orf) as r:
            rgbw = r.postprocess(use_camera_wb=True, gamma=(1, 1),
                                 no_auto_bright=True, output_bps=16,
                                 half_size=True).astype(np.float32) / 65535.0
        H, W, _ = rgbw.shape
        look = _look(lookname)
        lin = rgbw.reshape(-1, 3) @ _matrix(look).T
        disp = (srgb_encode(lin) * 255.0).reshape(H, W, 3)
        dispL = disp @ LUM
        frame = frame_norm(rgbw)
        boxes = patch_boxes(frame, (H, W))
        jp = np.asarray(Image.open(jpg).convert("RGB").resize((W, H),
                                                              Image.BILINEAR)).astype(np.float32)
        jpL = jp @ LUM
        idx, out8, rgb = [], [], []
        for b in boxes:
            y0, y1, x0, x1 = b
            idx.append(float(np.median(dispL[y0:y1, x0:x1])))
            out8.append(float(np.median(jpL[y0:y1, x0:x1])))
            rgb.append([float(np.median(jp[y0:y1, x0:x1, k])) for k in range(3)])
        data[slot] = {
            "shot": num, "look": lookname,
            "frame_norm": [float(v) for v in frame],
            "raw_shape": [H, W],
            "idx": idx, "outY": out8, "rgb": rgb,
        }
        if verbose:
            print("  frame(y0,y1,x0,x1)=%s" % np.round(frame, 1).tolist())
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


def load_cache(rebuild=False):
    if rebuild or not os.path.exists(CACHE):
        return build_cache()
    with open(CACHE, encoding="utf-8") as f:
        return json.load(f)


def _pred_matrix(idx, A, B):
    """返回 (na*nb, 24) 的预测 8bit 值（三角波表直查）。"""
    na, nb = len(A), len(B)
    ii = np.round(A[:, None, None] * idx[None, None, :] + B[None, :, None])
    np.clip(ii, 0, 255, out=ii)
    return (SAW[ii.astype(np.int16)] / 1023.0 * 255.0).reshape(na * nb, -1)


def _pearson_rows(P, y):
    yc = y - y.mean()
    Pc = P - P.mean(axis=1, keepdims=True)
    den = np.sqrt((Pc * Pc).sum(axis=1) * (yc * yc).sum()) + 1e-12
    return (Pc @ yc) / den


def fit_slot(idx, out8, nperm=300, seed=7, verbose=False):
    idx = np.asarray(idx, float)
    out8 = np.asarray(out8, float)

    # ---- 主检验：Pearson(out, T[a*idx+b])，细网格 ----
    A = np.arange(0.25, 3.001, 0.005)
    B = np.arange(-40.0, 40.001, 0.25)
    P = _pred_matrix(idx, A, B)
    r = _pearson_rows(P, out8)
    k = int(np.argmax(r))
    best = {"a": float(A[k // len(B)]), "b": float(B[k % len(B)]),
            "pearson": float(r[k])}

    # 零分布：置换 out8，同网格取最大相关（粗网格即可，省时）
    Ac = np.arange(0.25, 3.001, 0.02)
    Bc = np.arange(-40.0, 40.001, 0.5)
    Pc = _pred_matrix(idx, Ac, Bc)
    rng = np.random.default_rng(seed)
    null = np.array([float(_pearson_rows(Pc, rng.permutation(out8)).max())
                     for _ in range(nperm)])
    best["pearson_null_mean"] = float(null.mean())
    best["pearson_null_p95"] = float(np.percentile(null, 95))
    best["pearson_p"] = float((null >= best["pearson"]).mean())
    best["nperm"] = nperm

    # ---- 次检验：置信块亮暗命中 ----
    conf = np.where(out8 >= 180.0, True, np.where(out8 <= 60.0, False, None))
    cmask = np.array([v is not None for v in conf])
    clab = np.array([bool(v) for v in conf if v is not None], bool)
    sign_pred = P >= 128.0
    conf_pred = sign_pred[:, cmask]
    K = int(cmask.sum())
    hits = (conf_pred == clab[None, :]).sum(axis=1)
    hk = int(np.argmax(hits))
    best["conf_k"] = K
    best["conf_hits"] = int(hits[hk])
    best["conf_a"] = float(A[hk // len(B)])
    best["conf_b"] = float(B[hk % len(B)])
    best["conf_mismatch"] = K - int(hits[hk])
    nullh = np.array([int((conf_pred == rng.permutation(clab)[None, :]).sum(axis=1).max())
                      for _ in range(nperm)])
    best["conf_p"] = float((nullh >= hits[hk]).mean())

    # ---- 每块预测/实测（用置信检验给出的 a,b）----
    rows = []
    for p in range(len(idx)):
        lab = conf[p]
        pk = bool(sign_pred[hk, p])
        rows.append({
            "patch": p, "row": p // 6, "col": p % 6,
            "idx": float(idx[p]), "out8": float(out8[p]),
            "pred8": float(P[hk, p]),
            "meas": "-" if lab is None else ("B" if lab else "D"),
            "pred": "B" if pk else "D",
            "match": None if lab is None else (bool(lab) == pk),
        })
    best["rows"] = rows
    best["unconf"] = [r_["patch"] for r_ in rows if r_["match"] is None]

    # 判定：p<0.05 且 r>=0.75 才算定出
    best["determined"] = bool(best["pearson_p"] < 0.05 and best["pearson"] >= 0.75)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="重新从 ORF/JPG 采样")
    ap.add_argument("--json", default=None, help="把结果写到 JSON")
    args = ap.parse_args()

    data = load_cache(args.rebuild)
    print("L58 table: T[0:12]=%s ... T[250:256]=%s" %
          (SAW[:12].astype(int).tolist(), SAW[250:].astype(int).tolist()))
    print("output levels (0..255) = %s" % OUT_LEVELS)
    print("note: firmware resamples the 256-entry table on 27 anchors "
          "(0,10,...,250,255) before HW (docs/analysis/tone-second-stage.md)")
    print()

    results = {}
    for slot, num, lookname in SLOTS:
        d = data[slot]
        b = fit_slot(d["idx"], d["outY"])
        b.update(shot=num, look=lookname)
        results[slot] = b

        print("=" * 78)
        print("slot %s (PB21%04d, %s)   i_model = disp luma 0..255" %
              (slot, num, lookname))
        print("  primary : best Pearson r=%.3f at a=%.3f b=%.2f | "
              "null mean %.2f p95 %.2f | p=%.3f"
              % (b["pearson"], b["a"], b["b"], b["pearson_null_mean"],
                 b["pearson_null_p95"], b["pearson_p"]))
        print("  secondary: confident patches %d (B>=180 / D<=60): hits %d "
              "(%.0f%%), mismatch %d, chance p=%.3f"
              % (b["conf_k"], b["conf_hits"],
                 100.0 * b["conf_hits"] / max(b["conf_k"], 1),
                 b["conf_mismatch"], b["conf_p"]))
        print("  verdict : %s" % ("DETERMINED a=%.3f b=%.2f" % (b["a"], b["b"])
                                  if b["determined"] else
                                  "UNDETERMINED (fit not significant)"))
        print("  patch row col   idx   out   pred  meas pred")
        for rr in b["rows"]:
            print("   %2d    %d  %d  %6.1f %6.1f %6.1f   %s    %s   %s"
                  % (rr["patch"], rr["row"], rr["col"], rr["idx"], rr["out8"],
                     rr["pred8"], rr["meas"], rr["pred"],
                     "OK" if rr["match"] else ("--" if rr["match"] is None else "MISS")))
        print()

    # 跨档一致性：四张表的表内容相同，若 idx 相同则 out 应当相同
    idx0 = [data[s]["idx"][18] for s, _, _ in SLOTS]
    out0 = [data[s]["outY"][18] for s, _, _ in SLOTS]
    print("cross-slot check (gray patch 18): idx=%s  outY=%s  spread=%.0f"
          % (np.round(idx0, 1).tolist(), np.round(out0, 1).tolist(),
             max(out0) - min(out0)))
    print("  -> same model index, same table: outputs must match; they do not.")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print("wrote", args.json)


if __name__ == "__main__":
    main()
