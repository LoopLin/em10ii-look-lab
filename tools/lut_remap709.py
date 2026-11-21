"""把「F-Log2 -> Rec.709」的 cube 重映射成「Rec.709 -> 风格」的 cube。

原理：
  原 LUT: x(log 编码) -> out(Rec.709 风格)
  基线 ref: x(log 编码) -> y(纯 Rec.709 转换)
  新 LUT: 对每个 Rec.709 输入 y，先反解 x = ref^-1(y)，再取 out = LUT(x)
          => y(Rec.709) -> out(风格)

这样在 PS 里对普通 sRGB/Rec.709 照片套用，就近似得到该风格。
近似点：反解按中性轴（灰阶）单通道进行，忽略通道间耦合；对风格化用途足够。

用法:
    python tools/lut_remap709.py <src.cube> <out.cube>
    python tools/lut_remap709.py --all <F-Log2目录> <输出目录>
"""
import sys, os, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lut_analyze import parse_cube, make_getter


def build_inverse(ref, n=1024):
    xs = [i / n for i in range(n + 1)]
    ys = [ref(x, x, x)[0] for x in xs]

    def inv(y):
        if y <= ys[0]:
            return 0.0
        if y >= ys[-1]:
            return 1.0
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ys[mid] < y:
                lo = mid
            else:
                hi = mid
        d = ys[hi] - ys[lo]
        t = (y - ys[lo]) / d if d > 1e-9 else 0.0
        return xs[lo] + t * (xs[hi] - xs[lo])
    return inv


def remap(src_path, out_path, ref_get, inv, size=33):
    meta, s, vals = parse_cube(src_path)
    get = make_getter(s, vals)
    title = meta.get('title', os.path.basename(src_path))
    lines = []
    lines.append(f'#title:{title}_Rec709')
    lines.append('#remapped: input Rec.709 (approx.), derived from F-Log2 LUT')
    lines.append('#model:' + meta.get('model', ''))
    lines.append('#Gamma: Rec.709 to ' + title)
    lines.append('#Gamut: ITU-R BT.709 to ITU-R BT.709')
    lines.append('LUT_3D_SIZE %d' % size)
    for bi in range(size):
        b = bi / (size - 1)
        zb = inv(b)
        for gi in range(size):
            g = gi / (size - 1)
            zg = inv(g)
            for ri in range(size):
                r = ri / (size - 1)
                zr = inv(r)
                o = get(zr, zg, zb)
                lines.append('%.6f %.6f %.6f' % o)
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    return out_path


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == '--all':
        src_dir, out_dir = sys.argv[2], sys.argv[3]
        ref_path = os.path.join(src_dir, 'FLog2_to_FLog2-709_33grid_V.1.00.cube')
        _, _, rv = parse_cube(ref_path)
        from lut_analyze import make_getter as mg
        ref_get = mg(33, rv)
        inv = build_inverse(ref_get)
        for p in sorted(glob.glob(os.path.join(src_dir, '*.cube'))):
            base = os.path.basename(p)
            if base == 'FLog2_to_FLog2-709_33grid_V.1.00.cube':
                continue
            out = os.path.join(out_dir, base.replace('.cube', '_Rec709.cube'))
            remap(p, out, ref_get, inv)
            print('ok', out)
    else:
        src, out = sys.argv[1], sys.argv[2]
        ref_path = os.path.join(os.path.dirname(src), 'FLog2_to_FLog2-709_33grid_V.1.00.cube')
        _, _, rv = parse_cube(ref_path)
        from lut_analyze import make_getter as mg
        ref_get = mg(33, rv)
        inv = build_inverse(ref_get)
        remap(src, out, ref_get, inv)
        print('written', out)


if __name__ == '__main__':
    main()
