#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B50：真实四套 look（几何与 B48 完全相同，只换 tone 表）

B48 已用真机证明：派发侧 stash + 上传侧按 k 选表这套几何，在**成片**上生效
（PB160057-060：极亮 / 极暗 / 海报化 / 反相，一一对应）。

B50 只做一件事——把槽 22–25 的四张极端表，换成四套真 look 的曲线：
  k=1 vivid    → reala ace
  k=2 natural  → astia
  k=3 muted    → eterna
  k=4 portrait → bleach bypass

代码、桩、stash、指针目录、钩子位置逐字节沿用 B48。
"""
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_b48 as base  # noqa: E402

ROOT = base.ROOT
SPEC_DIR = os.path.join(ROOT, '.tmp', 'looks_beta')

LOOKS = {
    'reala_ace': 'reala ace',
    'astia': 'astia',
    'eterna': 'eterna',
    'bleach_bypass': 'bleach bypass',
}

# k = 1..4 对应的 look（顺序即 vivid/natural/muted/portrait）
KIND_TO_LOOK = {
    'bright': 'reala_ace',
    'dark': 'astia',
    'poster': 'eterna',
    'invert': 'bleach_bypass',
}

IDX_OLD_18 = 22.0
IDX_CAM_18 = 46.0


def retarget(tone):
    """look 规格的索引口径 → 相机口径（18% 灰从 22 挪到 46）。"""
    import numpy as np
    old = np.asarray([int(v) for v in tone], dtype=float)
    k = IDX_OLD_18 / IDX_CAM_18
    j = np.arange(256, dtype=float)
    new = np.interp(j * k, np.arange(256, dtype=float), old)
    new = np.maximum.accumulate(np.clip(new, 0, 1023))
    return [int(round(v)) for v in new]


def look_tone(name):
    with open(os.path.join(SPEC_DIR, name + '.json'), encoding='utf-8') as f:
        spec = json.load(f)
    tone = retarget(spec['tone'])
    if len(tone) != 256:
        raise SystemExit('%s tone 长度 %d' % (name, len(tone)))
    return tone


_CACHE = {}


def table_bytes(kind):
    look = KIND_TO_LOOK[kind]
    if look not in _CACHE:
        _CACHE[look] = look_tone(look)
    tone = _CACHE[look]
    return struct.pack('<256H', *tone)


def main():
    base.OUT = 'build/B50_looks4.BIN'
    if os.path.exists(os.path.join(ROOT, base.OUT)):
        raise SystemExit('输出已存在：' + base.OUT)
    # 把表换成真 look
    base.table_bytes = table_bytes
    info = base.build()

    base.b46.run([sys.executable, 'tools/mkpatch.py', base.SRC,
                  '--out', base.OUT, '--patch-file', info['patch']])

    # 独立回读校验
    stock = base.payload_of(base.SRC)
    built = base.payload_of(base.OUT)
    diff = [i for i in range(len(built)) if built[i] != stock[i]]
    outside = [i for i in diff
               if not any(a - base.BASE <= i < a - base.BASE + l for a, l in info['ranges'])]
    print('\n改动 %d 字节；区外 %d 字节 -> %s' % (len(diff), len(outside), 'OK' if not outside else 'FAIL'))
    ok = not outside
    for s in base.SLOTS:
        got = built[base.TONES + s * 0x200 - base.BASE: base.TONES + s * 0x200 - base.BASE + 0x200]
        same = got == info['tabs'][s]
        ok = ok and same
        print('  slot %-2d 表一致=%s' % (s, same))
    m0 = (0x605ECECA - base.BASE)
    same_m0 = built[m0:m0 + 18] == stock[m0:m0 + 18]
    ok = ok and same_m0
    print('  原厂 m0 未动=%s' % same_m0)
    print('\n  -> %s' % ('PASS' if ok else 'FAIL'))

    out = os.path.join(ROOT, 'build', 'B50_looks4.BIN')
    whole = open(out, 'rb').read()
    rep = {
        'bin': os.path.basename(out),
        'sha256': hashlib.sha256(whole).hexdigest(),
        'size': len(whole),
        'geometry': 'same as B48',
        'tables': {},
    }
    for kind, look in KIND_TO_LOOK.items():
        t = _CACHE[look]
        rep['tables'][kind] = {'look': look, 'T46': t[46], 'T103': t[103], 'T255': t[255]}
    with open(out + '.looks.json', 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
