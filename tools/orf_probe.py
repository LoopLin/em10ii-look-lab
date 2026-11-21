"""ORF 探针：读元数据、导出线性 TIFF 与 sRGB JPEG（no_auto_bright），供与相机直出 JPEG 对比。

用法: python tools/orf_probe.py <file.ORF> <out_prefix>
"""
import sys, os
import numpy as np
import rawpy
from PIL import Image


def main():
    src, out = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    with rawpy.imread(src) as raw:
        sizes = raw.sizes
        print('sizes:', sizes)
        print('color_desc:', raw.color_desc)
        print('black_level_per_channel:', raw.black_level_per_channel)
        print('white_level:', raw.white_level)
        print('camera_whitebalance:', raw.camera_whitebalance)
        print('daylight_whitebalance:', raw.daylight_whitebalance)
        cm = raw.color_matrix
        print('color_matrix rows:', cm.shape)
        print(np.array2string(cm, precision=4, suppress_small=True))
        print('rgb_xyz_matrix:')
        print(np.array2string(raw.rgb_xyz_matrix, precision=4, suppress_small=True))
        try:
            tc = raw.tone_curve
            print('tone_curve len', len(tc))
        except Exception as e:
            print('tone_curve: n/a', e)
        # 线性 16-bit（存 npy + 8-bit 预览）
        lin = raw.postprocess(use_camera_wb=True, no_auto_bright=True,
                              gamma=(1, 1), output_bps=16, half_size=False)
        np.save(out + '_linear16.npy', lin)
        Image.fromarray((lin.astype(np.float32) / 65535.0 * 255).clip(0, 255).astype(np.uint8)).save(out + '_linear8.png')
        # sRGB 8-bit（rawpy 默认 sRGB gamma）
        rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True,
                              gamma=(2.4, 12.92), output_bps=8, half_size=False)
        Image.fromarray(rgb).save(out + '_srgb.jpg', quality=95)
        print('written', out + '_linear16.png', out + '_srgb.jpg')


if __name__ == '__main__':
    main()
