# -*- coding: utf-8 -*-
"""B22 四套 look 包：独立回读校验（不复用出包器代码）。

用法：
    PYTHONIOENCODING=utf-8 python tools/verify_b22_looks.py            # 四套全查
    PYTHONIOENCODING=utf-8 python tools/verify_b22_looks.py --json .tmp/B22_verify.json

设计原则
--------
* 只用 `tools/oly_container.parse` 打开容器（这是容器格式库，不是出包器）；
  **不 import `tools/build_beta_look.py`**，tone 重采样 / 色度块 z 段 / 矩阵编码
  全部在本文件里按文档口径重新实现一遍，因此能抓到出包器的实现偏差。
* 四件事逐项核：tone 256×u16、池指针 168 项、色度块 100 B、矩阵 m0 的 9×i16 + 门字节 2 B。
* 「区外 0 字节」按两级证明：
    - 解码后 payload 的差异区间必须整体落进 5 个预期区间；
    - 容器文件级差异 = 上述 payload 差异 + 块尾 4 B 校验和，别无他处。
* 矩阵编码器 enc/dec 往返按 docs/color/look-spec-matrix.md §0.2 的规则独立复算。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oly_container as oc  # noqa: E402  （容器格式库，允许；出包器不允许）

BASE = 0x60021020
STOCK = 'stock/OLY_E_120_1400_0000_0000.BIN'
SPEC_DIR = '.tmp/looks_matrix'

TONE_RAM = 0x605D8344
TONE_LEN = 0x200
POOL_RAM = 0x60600920
POOL_N = 168
POOL_LEN = 4 * POOL_N
CHROMA_RAM = 0x605D4829
CHROMA_LEN = 100
MATRIX_RAM = 0x605ECECA
MATRIX_LEN = 18
GATE_RAM = 0x604B6AD4
GATE_LEN = 2
GATE_EXPECT = b'\x80\x01'

# look 规格索引口径 → 相机口径（与 docs/firmware/beta 的口径一致：18% 灰 22 → 46）
IDX_OLD_18 = 22.0
IDX_CAM_18 = 46.0

REGIONS = [
    ('tone', TONE_RAM, TONE_LEN),
    ('pool', POOL_RAM, POOL_LEN),
    ('chroma', CHROMA_RAM, CHROMA_LEN),
    ('matrix', MATRIX_RAM, MATRIX_LEN),
    ('gate', GATE_RAM, GATE_LEN),
]

LOOKS = ('reala_ace', 'astia', 'eterna', 'bleach_bypass')


# ------------------------------------------------------------------ 独立重算

def load_spec(look):
    with io.open(os.path.join(SPEC_DIR, look + '.json'), encoding='utf-8') as f:
        return json.load(f)


def retarget_tone(tone):
    """new[j] = old[j * 22/46]，线性内插（不 import 出包器）。"""
    old = [int(v) for v in tone]
    if len(old) != 256:
        raise ValueError('spec tone 长度 %d != 256' % len(old))
    k = IDX_OLD_18 / IDX_CAM_18
    out = []
    for j in range(256):
        x = j * k
        i0 = int(x)
        if i0 >= 255:
            out.append(old[255])
        else:
            t = x - i0
            out.append(int(round(old[i0] * (1.0 - t) + old[i0 + 1] * t)))
    return out


def tone_bytes(tone):
    return b''.join(int(v).to_bytes(2, 'little') for v in tone)


def derive_z(xs, ys):
    """z[i] = round((y[i]-y[i-1])*128/(x[i]-x[i-1]))，z[0]=0，z[16]=128（独立实现）。

    2026-11-15 修正：旧实现把「第 i-1→i 节点」的增益写进 z[i+1]（整块右移一格，
    B07 四套与 B22 初版都带这个 bug）。这里按 B01 探针包里相机实际吃下的写法：
    段增益落在**后一个节点**上。自检见 selftest_z()。
    """
    z = bytearray(17)
    for i in range(1, 16):
        dx = int(xs[i]) - int(xs[i - 1])
        dy = int(ys[i]) - int(ys[i - 1])
        z[i] = (int(round(dy * 128.0 / dx)) & 0xFF) if dx else 128
    z[16] = 128
    return bytes(z)


def selftest_z():
    """B01 探针块的 y 序列：z[4..7] 必须是 78,76,76,77。"""
    x = [0, 0, 0, 0, 64, 128, 192, 255, 255, 255, 255, 255, 255, 255, 255, 255]
    y = [51, 51, 51, 51, 90, 128, 166, 204, 204, 204, 204, 204, 204, 204, 204, 204]
    z = derive_z(x, y)
    got = list(z[4:8])
    return got == [78, 76, 76, 77], got


def chroma_bytes(ch):
    x = [int(v) for v in ch['cb_x']]
    y = [int(v) for v in ch['cb_y']]
    X = [int(v) for v in ch['cr_x']]
    Y = [int(v) for v in ch['cr_y']]
    for name, a, n in (('cb_x', x, 16), ('cb_y', y, 16), ('cr_x', X, 16), ('cr_y', Y, 16)):
        if len(a) != n:
            raise ValueError('chroma.%s 长度 %d != %d' % (name, len(a), n))
    blk = bytearray(100)
    blk[0x00:0x10] = bytes(x)
    blk[0x10:0x20] = bytes(y)
    blk[0x20:0x31] = derive_z(x, y)
    blk[0x32:0x42] = bytes(X)
    blk[0x42:0x52] = bytes(Y)
    blk[0x52:0x63] = derive_z(X, Y)
    return bytes(blk)


def matrix_table(spec):
    """规格 table_values → **硬件实际会用的** 9 个 i16（等价于编码器 0x604B6A22 的偶化）。

    硬件对 m01/m21 无条件清 LSB（`and 0xfffe`），m10+m12 为奇时把两者一起清；
    对角再由行和 256 反推。独立实现，不看编码器代码。
    """
    q = (spec.get('matrix') or {}).get('quantized') or {}
    flat = [int(v) for v in q['table_values']]
    if len(flat) != 9:
        raise ValueError('matrix 不是 3x3')
    flat[1] &= ~1          # m01
    flat[7] &= ~1          # m21
    if (flat[3] + flat[5]) & 1:    # m10 + m12 为奇 → 一起偶化
        flat[3] &= ~1
        flat[5] &= ~1
    for r in range(3):
        flat[r * 3] = 256 - flat[r * 3 + 1] - flat[r * 3 + 2]
    return flat


# ---------------------------------------------------- 矩阵编码器 enc/dec（独立）

def _even_floor(v):
    """按位与 0xFE：负数向 −∞ 取偶（−27 → −28），与硬件 AND 语义一致。"""
    return v & ~1


def enc(m):
    """9 个表值 → 编码器写进 register_image 的 9 个 u16（§0.2 规则）。"""
    m0, m01, m02, m10, m11, m12, m20, m21, m22 = m
    a = _even_floor(m01)
    b = m02
    d = m10
    e = _even_floor(m12) if ((m10 + m12) & 1) else m12
    g = m20
    h = _even_floor(m21)
    return {
        'reg': [0x80 - (a + b), a, b,
                d, 0x80 - (d + e), e,
                g, h, 0x80 - (g + h)],
        'touched_even': (a != m01, e != m12, h != m21),
    }


def dec(reg):
    """编码器那 9 个 u16 → 硬件实际使用的 3×3（对角 = 256 − 行内非对角和）。"""
    r0, r1, r2, r3, r4, r5, r6, r7, r8 = reg
    return [r0 + 128, r1, r2,
            r3, r4 + 128, r5,
            r6, r7, r8 + 128]


def roundtrip(m):
    e = enc(m)
    back = dec(e['reg'])
    return back, e


# ------------------------------------------------------------------ 差异分析

def runs(a, b):
    """返回 [(start, len)]，a/b 等长的差异连续区间。"""
    n = min(len(a), len(b))
    out = []
    i = 0
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def coverage(run_list, region_list):
    """逐 run 判定落在哪个预期区间（可以有多个重叠命中）。"""
    verdict = []
    for start, ln in run_list:
        end = start + ln
        hit = [name for name, addr, rlen in region_list
               if start >= addr - BASE and end <= addr - BASE + rlen]
        verdict.append({'offset': start, 'len': ln, 'regions': hit})
    return verdict


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def verify_one(look, stock_blocks, stock_raw):
    out = {'look': look, 'ok': True, 'checks': {}, 'errors': []}

    path = 'build/B22_%s.BIN' % look
    raw = open(path, 'rb').read()
    blocks = oc.parse(raw)          # 内含签名/尺寸/头尾一致/加和校验
    out['path'] = path
    out['size'] = len(raw)
    out['sha256'] = sha256(raw)
    out['sha256_16'] = sha256(raw)[:16]
    out['sha256_32'] = sha256(raw)[:32]
    out['source_sha256'] = sha256(stock_raw)
    out['source_size'] = len(stock_raw)
    out['blocks'] = len(blocks)

    b0 = blocks[0].payload
    s0 = stock_blocks[0].payload
    out['payload_size'] = len(b0)

    # ---- 期望值（独立重算）
    spec = load_spec(look)
    tone = retarget_tone(spec['tone'])
    want_tone = tone_bytes(tone)
    want_ch = chroma_bytes(spec['chroma'])
    flat = matrix_table(spec)
    want_mx = b''.join(int(v).to_bytes(2, 'little', signed=True) for v in flat)

    got_tone = b0[TONE_RAM - BASE:TONE_RAM - BASE + TONE_LEN]
    got_pool = b0[POOL_RAM - BASE:POOL_RAM - BASE + POOL_LEN]
    got_ch = b0[CHROMA_RAM - BASE:CHROMA_RAM - BASE + CHROMA_LEN]
    got_mx = b0[MATRIX_RAM - BASE:MATRIX_RAM - BASE + MATRIX_LEN]
    got_gate = b0[GATE_RAM - BASE:GATE_RAM - BASE + GATE_LEN]

    # 1) tone 256×u16
    tone_vals = list(struct.unpack('<256H', got_tone))
    tone_ok = got_tone == want_tone
    tone_mono = all(a <= b for a, b in zip(tone_vals, tone_vals[1:]))
    tone_range = all(0 <= v <= 1023 for v in tone_vals)
    out['checks']['tone'] = {
        'equal_expected': tone_ok,
        'len': len(got_tone),
        'monotonic': tone_mono,
        'range_0_1023': tone_range,
        'tone[22]': tone_vals[22], 'tone[46]': tone_vals[46],
        'tone[64]': tone_vals[64], 'tone[128]': tone_vals[128],
        'tone[255]': tone_vals[255],
        'sha256': sha256(got_tone),
    }
    if not (tone_ok and tone_mono and tone_range):
        out['ok'] = False
        out['errors'].append('tone 校验失败')

    # 2) 池指针 168 项，全指 0x605D8344
    ptrs = list(struct.unpack('<%dI' % POOL_N, got_pool))
    pool_ok = all(p == TONE_RAM for p in ptrs)
    out['checks']['pool'] = {
        'n': len(ptrs), 'all_point_to_tone': pool_ok,
        'distinct_values': sorted(set(ptrs))[:8],
        'len': len(got_pool),
    }
    if not pool_ok:
        out['ok'] = False
        out['errors'].append('池指针未全部指向 tone 表')

    # 3) 色度块 100 B
    ch_ok = got_ch == want_ch
    out['checks']['chroma'] = {
        'equal_expected': ch_ok,
        'len': len(got_ch),
        'Cb_y': list(got_ch[0x10:0x20]),
        'Cr_y': list(got_ch[0x42:0x52]),
        'sha256': sha256(got_ch),
    }
    if not ch_ok:
        out['ok'] = False
        out['errors'].append('色度块校验失败')

    # 4) 矩阵 m0 9×i16 + 门字节
    mx_vals = list(struct.unpack('<9h', got_mx))
    mx_ok = got_mx == want_mx
    rows = [sum(mx_vals[i * 3:i * 3 + 3]) for i in range(3)]
    back, encinfo = roundtrip(flat)
    rt_ok = back == flat
    out['checks']['matrix'] = {
        'equal_expected': mx_ok,
        'table_values': mx_vals,
        'row_sums': rows,
        'row_sum_256': rows == [256, 256, 256],
        'encoder_regs': encinfo['reg'],
        'even_touched': {
            'm01': encinfo['touched_even'][0],
            'm12': encinfo['touched_even'][1],
            'm21': encinfo['touched_even'][2],
        },
        'roundtrip_lossless': rt_ok,
        'decoded_back': back,
        'parity_ok': (mx_vals[1] % 2 == 0 and mx_vals[7] % 2 == 0
                      and (mx_vals[3] + mx_vals[5]) % 2 == 0),
        'sha256': sha256(got_mx),
    }
    if not (mx_ok and out['checks']['matrix']['row_sum_256']):
        out['ok'] = False
        out['errors'].append('矩阵 m0 校验失败')
    if not rt_ok:
        out['ok'] = False
        out['errors'].append('矩阵 enc/dec 往返非无损')
    if not out['checks']['matrix']['parity_ok']:
        out['ok'] = False
        out['errors'].append('矩阵奇偶未对齐（m01/m21 或 m10+m12 为奇）')

    gate_ok = got_gate == GATE_EXPECT
    out['checks']['gate'] = {
        'bytes': got_gate.hex(),
        'expected': GATE_EXPECT.hex(),
        'ok': gate_ok,
        'stock_bytes': s0[GATE_RAM - BASE:GATE_RAM - BASE + GATE_LEN].hex(),
    }
    if not gate_ok:
        out['ok'] = False
        out['errors'].append('门字节不是 8001')

    # ---- 区外 0 字节：解码 payload 级
    pruns = runs(s0, b0)
    cov = coverage(pruns, REGIONS)
    outside = [c for c in cov if not c['regions']]
    dec_changed = sum(ln for _, ln in pruns)
    out['diff'] = {
        'decoded_changed_bytes': dec_changed,
        'decoded_runs': [{'ram': hex(c['offset'] + BASE), 'len': c['len'],
                          'regions': c['regions']} for c in cov],
        'outside_runs': outside,
        'outside_bytes': sum(c['len'] for c in outside),
        'per_region': {name: sum(c['len'] for c in cov if name in c['regions'])
                       for name, _, _ in REGIONS},
    }
    if outside:
        out['ok'] = False
        out['errors'].append('payload 存在预期区间外的改动：%r' % outside)

    # ---- 区外 0 字节：容器文件级（payload + 块尾 4 B 校验和）
    furn = runs(stock_raw, raw)
    trailer_ok = []
    for start, ln in furn:
        # 块 0 的 payload 在文件里是编码过的；把每个 run 映射到所属块
        owner = None
        for blk in blocks:
            pstart = blk.offset + 32
            pend = pstart + len(blk.payload)
            tstart = pend
            tend = pend + 32
            if pstart <= start and start + ln <= pend:
                owner = ('payload', blk.index)
                break
            if tstart <= start and start + ln <= tend:
                owner = ('trailer', blk.index)
                break
        trailer_ok.append({'file_offset': start, 'len': ln,
                           'owner': None if owner is None else owner[0],
                           'block': None if owner is None else owner[1]})
    bad_container = [r for r in trailer_ok
                     if r['owner'] not in ('payload', 'trailer')
                     or (r['owner'] == 'trailer' and r['len'] > 4)]
    out['diff']['file_changed_bytes'] = sum(ln for _, ln in furn)
    out['diff']['file_runs'] = trailer_ok
    if bad_container:
        out['ok'] = False
        out['errors'].append('容器级改动超出 payload/校验和：%r' % bad_container)

    # 18% 灰显示值（表值 → 8 位显示；相机口径索引 46 = 0.18*255）
    g18 = tone_vals[46] / 1023.0 * 255.0
    out['gray18'] = {
        'index': 46,
        'tone_value': tone_vals[46],
        'display8': round(g18, 3),
        'display8_round': int(round(g18)),
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default='.tmp/B22_verify.json')
    a = ap.parse_args()

    stock_raw = open(STOCK, 'rb').read()
    stock_blocks = oc.parse(stock_raw)

    z_ok, z_got = selftest_z()
    print('自检 derive_z（B01 探针 y）：z[4..7]=%s  期望 [78, 76, 76, 77]  ok=%s'
          % (z_got, z_ok))
    if not z_ok:
        return 2

    report = {'stock': STOCK, 'stock_sha256': sha256(stock_raw),
              'stock_size': len(stock_raw), 'looks': {}}
    for look in LOOKS:
        r = verify_one(look, stock_blocks, stock_raw)
        report['looks'][look] = r
        print('== %-14s %s  sha=%s  size=%d  changed(payload)=%d  outside=%d'
              % (look, 'OK ' if r['ok'] else 'FAIL', r['sha256_32'],
                 r['size'], r['diff']['decoded_changed_bytes'],
                 r['diff']['outside_bytes']))
        print('   tone eq=%s mono=%s range=%s  gray18 idx46=%d -> %.2f' % (
            r['checks']['tone']['equal_expected'], r['checks']['tone']['monotonic'],
            r['checks']['tone']['range_0_1023'],
            r['gray18']['tone_value'], r['gray18']['display8']))
        print('   pool %d 项全指 tone=%s ; chroma eq=%s ; matrix=%s rows=%s'
              % (r['checks']['pool']['n'], r['checks']['pool']['all_point_to_tone'],
                 r['checks']['chroma']['equal_expected'],
                 r['checks']['matrix']['table_values'],
                 r['checks']['matrix']['row_sums']))
        print('   enc regs=%s  roundtrip_lossless=%s  gate=%s(stock %s)'
              % (r['checks']['matrix']['encoder_regs'],
                 r['checks']['matrix']['roundtrip_lossless'],
                 r['checks']['gate']['bytes'], r['checks']['gate']['stock_bytes']))
        print('   per-region churn: %s ; file-level %d B'
              % (r['diff']['per_region'], r['diff']['file_changed_bytes']))
        if r['errors']:
            print('   ERRORS:', r['errors'])

    os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
    with io.open(a.json, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print('report ->', a.json)
    return 0 if all(r['ok'] for r in report['looks'].values()) else 1


if __name__ == '__main__':
    sys.exit(main())
