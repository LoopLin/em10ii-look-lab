#!/usr/bin/env python3
"""把「一条全局 tone 曲线 + 每档六轴」打成一个 patch 文件（配合 tools/mkpatch.py）。

用法:
  python tools/pack_look.py spec.json -o .tmp/look.patch
  python tools/mkpatch.py stock/OLY_E_120_1400_0000_0000.BIN --out build/xxx.BIN --patch-file .tmp/look.patch

spec.json:
{
  "tone": [256 个 0..1023 整数],          # 必填，写进 44 张表（全局影调）
  "sixaxis": {                            # 可选，按档写；缺省沿用原厂
     "natural":  {"sat": [6 ints], "hue": [6 ints]},
     "vivid":    {...},
     "muted":    {...},
     "portrait": {...},
     "ienhance": {...}
  },
  "tone_tables": 44                       # 可选，默认 44（全部覆盖）
}
"""
import argparse, json, struct

TONE_BASE, TONE_STEP, TONE_COUNT = 0x605D8344, 0x200, 44
SIX_BASE, SIX_STEP, SIX_COUNT = 0x605ECF5A, 24, 12
# rec = A/B 成对；当前映射依据原厂数值比对（Vivid=2/3、Muted=4/5、Portrait=6/7 已核对）
MODE_REC = {"natural": 0, "vivid": 2, "muted": 4, "portrait": 6, "ienhance": 8}

def clamp(v, lo, hi): return max(lo, min(hi, int(round(v))))

def emit(lines, addr, blob, tag):
    for off in range(0, len(blob), 32):
        lines.append(f"ram 0x{addr + off:08x}={blob[off:off+32].hex()}")
    print(f"  {tag}: 0x{addr:08X} {len(blob)} B")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    spec = json.load(open(a.spec, encoding="utf-8"))
    tone = spec["tone"]
    assert len(tone) == 256, len(tone)
    tone = [clamp(v, 0, 1023) for v in tone]
    mono = all(tone[i] <= tone[i+1] for i in range(255))
    if not mono:
        raise SystemExit("tone 非单调，拒绝打包")

    lines = []
    n = spec.get("tone_tables", TONE_COUNT)
    blob = b"".join(struct.pack("<H", v) for v in tone)
    print(f"tone: {len(blob)} B x {n} 张")
    for k in range(n):
        emit(lines, TONE_BASE + k * TONE_STEP, blob, f"tone[{k}]")

    for mode, rec in MODE_REC.items():
        cfg = spec.get("sixaxis", {}).get(mode)
        if not cfg: continue
        sat = [clamp(v, -2048, 2047) for v in cfg["sat"]]
        hue = [clamp(v, -2048, 2047) for v in cfg["hue"]]
        assert len(sat) == 6 and len(hue) == 6, mode
        rec_blob = struct.pack("<12h", *(sat + hue))
        for r in (rec, rec + 1):          # A/B 两套同值
            emit(lines, SIX_BASE + r * SIX_STEP, rec_blob, f"sixaxis {mode} rec{r}")

    open(a.out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"{len(lines)} 行 -> {a.out}")

if __name__ == "__main__":
    main()
