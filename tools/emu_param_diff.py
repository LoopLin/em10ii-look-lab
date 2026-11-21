"""Run emu_param_probe twice (stock, S13) and diff the dumped regions.

Called from Blender's Python (ops_bash cannot spawn node here).
"""
import json
import os
import subprocess
import sys

REPO = r"G:\em10fw\refs\om-emu-main"
PROBE = r"G:\em10fw\tools\emu_param_probe.mjs"
ROOT = r"G:\em10fw\.tmp\emuprobe"


def run(outdir, patch):
    env = dict(os.environ)
    env["OM_EMU_MODEL"] = "E120"
    env.pop("OM_EMU_PATCH", None)
    if patch:
        env["OM_EMU_PATCH"] = patch
    print("=== run", outdir, "patch=", patch, flush=True)
    r = subprocess.run(["node", PROBE, outdir, "s1"], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=900)
    print(r.stdout[-4000:], flush=True)
    print(r.stderr[-4000:], flush=True)
    print("exit", r.returncode, flush=True)
    return r.returncode


def main():
    stock = os.path.join(ROOT, "stock")
    s13 = os.path.join(ROOT, "s13")
    rc1 = run(stock, None)
    rc2 = run(s13, r"G:\em10fw\build\S13_curve_fix.omemu.json")
    if rc1 or rc2:
        print("PROBE FAILED", rc1, rc2)
        return
    a = json.load(open(os.path.join(stock, "index.json")))
    b = json.load(open(os.path.join(s13, "index.json")))
    for ra, rb in zip(a["index"], b["index"]):
        da = open(ra["file"], "rb").read()
        db = open(rb["file"], "rb").read()
        diffs = [i for i in range(min(len(da), len(db))) if da[i] != db[i]]
        print(f"\n{ra['name']}: {ra['lo']:#x}..{ra['hi']:#x}  diff_bytes={len(diffs)}")
        if not diffs:
            continue
        # group into runs
        runs = []
        start = prev = diffs[0]
        for i in diffs[1:]:
            if i == prev + 1:
                prev = i
                continue
            runs.append((start, prev))
            start = prev = i
        runs.append((start, prev))
        for s, e in runs[:40]:
            print(f"  {ra['lo']+s:#010x}..{ra['lo']+e:#010x} ({e-s+1} B)  "
                  f"stock={da[s:min(e+1,s+16)].hex()} s13={db[s:min(e+1,s+16)].hex()}")
        if len(runs) > 40:
            print(f"  ... {len(runs)-40} more runs")


main()
