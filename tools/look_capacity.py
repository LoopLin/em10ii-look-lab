# -*- coding: utf-8 -*-
"""对全部富士观感做"可交付能力"分析：只用 tone+色度（六轴已作废）时的残差。"""
import json, os, subprocess, sys, glob
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUBE = os.path.join(ROOT, ".tmp", "fujilut", "cube lut")
CFG  = os.path.join(ROOT, ".tmp", "fuji_fit_cfg.json")
rows = []
for c in sorted(glob.glob(os.path.join(CUBE, "*_sRGB.cube"))):
    name = os.path.basename(c).replace("_sRGB.cube", "")
    out = os.path.join(ROOT, ".tmp", "cap", name.replace(" ", "_"))
    if os.path.exists(CFG): os.remove(CFG)          # cfg 会吃掉命令行参数
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "fuji_fit.py"), c, "--out", out],
                       capture_output=True, text=True, cwd=ROOT)
    spec = out + "_spec.json"
    if not os.path.exists(spec):
        print("FAIL", name, r.returncode, (r.stdout or "")[-300:], (r.stderr or "")[-300:]); continue
    d = json.load(open(spec, encoding="utf-8"))
    rows.append((name, d.get("err_tone_only"), d.get("err_with_matrix"), d.get("err_full")))
    print("ok", name, rows[-1][1:], flush=True)
if os.path.exists(CFG): os.remove(CFG)
print("\n%-16s %8s %8s %8s  %s" % ("look", "tone", "+matrix", "+six", "可交付?"))
for n, e1, e2, e3 in sorted(rows, key=lambda t: (t[1] if t[1] is not None else 9e9)):
    ok = "✅" if (e2 is not None and e2 < 8) else ("~" if e2 is not None and e2 < 12 else "❌")
    print("%-16s %8.1f %8.1f %8.1f  %s" % (n, e1 or -1, e2 or -1, e3 or -1, ok))
json.dump([dict(zip(("look","tone_only","with_matrix","full"), r)) for r in rows],
          open(os.path.join(ROOT, ".tmp", "cap", "summary.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
