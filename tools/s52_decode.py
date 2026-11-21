"""S52 判读：在一组同场景照片里，按"相对影调"认签名。

S52 把五个画质模式各接了一条极端曲线：
    dark4 : x**4        明显变暗、暗部压死（中位 65/1023）
    bright: x**0.35     明显变亮、发灰（中位 804/1023）
    step  : 0.55 阶跃    大片纯黑 + 亮部正常
    dark2 : x**2        略暗（中位 258/1023）
    ident : 恒等        基本不变（中位 514/1023）

因为不同场景绝对亮度差很多，**只看相对排序**：
    最暗的 → dark4 ；最亮的 → bright ；
    纯黑面积最大的 → step ；剩下两个里较亮的 → ident，较暗的 → dark2 。

用法：
    python tools/s52_decode.py refs/shoots/24_S52_group_tone/*.JPG
"""
import glob
import sys
import numpy as np
from PIL import Image, ImageOps


def stats(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((800, 800))
    a = np.asarray(im, np.float32) / 255.0
    y = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]) * 1023
    return {
        "p50": float(np.percentile(y, 50)),
        "p90": float(np.percentile(y, 90)),
        "dark": float((y < 0.03 * 1023).mean()),
    }


def main(paths):
    paths = sorted(sum([glob.glob(p) for p in paths], []))
    if not paths:
        print("没有匹配的文件")
        return
    rows = [(p, stats(p)) for p in paths]
    print(f"{'file':26s} {'p50':>7s} {'p90':>7s} {'纯黑%':>7s}")
    for p, s in rows:
        print(f"{p.replace(chr(92),'/').split('/')[-1]:26s} {s['p50']:7.0f} {s['p90']:7.0f} {100*s['dark']:7.1f}")

    if len(rows) < 3:
        print("\n（少于 3 张，无法做相对排序；请给五个模式各一张）")
        return

    by_p50 = sorted(rows, key=lambda r: r[1]["p50"])
    assign = {}
    assign[by_p50[0][0]] = "dark4（最暗）"
    assign[by_p50[-1][0]] = "bright（最亮）"
    rest = [r for r in by_p50 if r[0] not in assign]
    if len(rest) >= 3:
        step = max(rest, key=lambda r: r[1]["dark"])
        assign[step[0]] = "step（纯黑最多）"
        rest = [r for r in rest if r[0] != step[0]]
    if len(rest) >= 2:
        assign[rest[-1][0]] = "ident（剩两里较亮）"
        assign[rest[0][0]] = "dark2（剩两里较暗）"
    print("\n按相对关系推断：")
    for p, _ in rows:
        print(f"  {p.replace(chr(92),'/').split('/')[-1]:26s} → {assign.get(p, '?')}")
    print("\n注意：这套排序假设五张是同场景同光；若现场有变，结论要打折。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
