#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""read_dial_probe.py —— 读 L37_dialprobe 写在 EXIF UserComment 里的拨盘快照

复用 tools/read_probe.py:get_user_comment（TIFF/IFD0/ExifIFD/0x9286 解析），
取回 UserComment 的整段字节（前 8 B 是字符集头），第 8 B 起 117 B 就是文本区。

用法：
  python tools/read_dial_probe.py SHOT.JPG [MORE.JPG ...]
  python tools/read_dial_probe.py --selftest          # 构造一份载荷自检解析

字段（文本区偏移 → 含义，详见 docs/analysis/dial-probe.md）：
  +0x00 u32  b'k=7\\x00'           帧标记（第 3 字节是 k 的 ASCII）
  +0x04 u32  b'dL37'               包 key
  +0x08 u16  magic 0x5044 ('DP')
  +0x0A u8   version 1
  +0x0B u8   valid = *(model+0x1616)  拨盘有效字节（0 ⇒ 弯门条件不成立）
  +0x0C 5×u16 dial_p = *(p+2i)      门实际吃的那 5 个值（p = *(model+0x3B4)）
  +0x16 5×u16 dial_m = *(model+0x159C+2i)  直读候选区
  +0x20 u32  ptr   = *(S+0x1230)
  +0x24 u32  S     = *(job+12) = model+0x20C
  +0x28 u32  model = *(job+4)
  +0x2C u32  p     = *(model+0x3B4)
  +0x30 u32  sum32 = t[+0x04..+0x2F] 的 11 个 u32 之和（mod 2^32）
"""
from __future__ import annotations

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from read_probe import get_user_comment  # noqa: E402

LEN = 0x34
MAGIC = 0x5044
VERSION = 1
NOD = 0x8001


def decode(text: bytes):
    """text = 117 B 文本区（0x61894C90 起）。返回 dict 或 None。"""
    if len(text) < LEN:
        return None
    kword, key = struct.unpack_from('<II', text, 0)
    magic, = struct.unpack_from('<H', text, 8)
    version, valid = text[10], text[11]
    dp = struct.unpack_from('<5H', text, 12)
    dm = struct.unpack_from('<5H', text, 22)
    ptr, S, model, p = struct.unpack_from('<IIII', text, 32)
    chk, = struct.unpack_from('<I', text, 48)
    calc = sum(struct.unpack_from('<11I', text, 4)) & 0xFFFFFFFF
    return {
        'k': kword, 'k_ascii': kword.to_bytes(4, 'little'),
        'key': key, 'magic': magic, 'version': version, 'valid': valid,
        'dial_p': tuple(v - 0x10000 if v >= 0x8000 else v for v in dp),
        'dial_m': tuple(v - 0x10000 if v >= 0x8000 else v for v in dm),
        'dial_p_raw': dp, 'dial_m_raw': dm,
        'ptr': ptr, 'S': S, 'model': model, 'p': p,
        'sum32': chk, 'sum32_calc': calc, 'sum_ok': chk == calc,
    }


def show(path: str) -> dict | None:
    uc = get_user_comment(path)
    print(path)
    if uc is None:
        print('  no UserComment')
        return None
    text = uc[8:]
    d = decode(text)
    if d is None:
        print('  UserComment 太短（%d B）' % len(text))
        return None
    ok = (d['magic'] == MAGIC and d['version'] == VERSION and d['sum_ok']
          and d['k_ascii'][:3] == b'k=')
    print('  [%s] k=%r key=%r magic=%#06x v%d valid=%d sum=%s'
          % ('OK ' if ok else 'BAD', d['k_ascii'], d['key'].to_bytes(4, 'little'),
             d['magic'], d['version'], d['valid'],
             'ok' if d['sum_ok'] else 'BAD %#x!=%#x' % (d['sum32'], d['sum32_calc'])))

    def fmt(vals, raw):
        out = []
        for v, r in zip(vals, raw):
            out.append('n/a' if r == NOD else '%d' % v)
        return '[%s]' % ', '.join(out)

    print('  dial_p  %s  raw %s' % (fmt(d['dial_p'], d['dial_p_raw']),
                                    ' '.join('%04x' % r for r in d['dial_p_raw'])))
    print('  dial_m  %s  raw %s' % (fmt(d['dial_m'], d['dial_m_raw']),
                                    ' '.join('%04x' % r for r in d['dial_m_raw'])))
    if d['dial_p_raw'] == d['dial_m_raw']:
        print('  -> dial_p == dial_m（p 就指向 model+0x159C）')
    else:
        diff = [b - a for a, b in zip(d['dial_m'], d['dial_p'])]
        print('  -> dial_p != dial_m，差 %r（p 另有落点，见文档）' % diff)
    print('  ptr=%#010x S=%#010x model=%#010x p=%#010x'
          % (d['ptr'], d['S'], d['model'], d['p']))
    if d['ptr'] == (d['S'] + 0x1234) & 0xFFFFFFFF:
        print('  -> 弯过了：*(S+0x1230) == S+0x1234（表在就地重建）')
    else:
        print('  -> 没弯：*(S+0x1230) != S+0x1234（原厂表指针）')
    if d['model'] and d['S']:
        print('  -> S - model = %#x（应为 0x20C）' % ((d['S'] - d['model']) & 0xFFFFFFFF))
    return d


def _selftest() -> int:
    text = bytearray(b' ' * 117)
    struct.pack_into('<II', text, 0, 0x00373D6B, 0x37334C64)
    struct.pack_into('<H', text, 8, MAGIC)
    text[10] = VERSION
    text[11] = 1
    struct.pack_into('<5H', text, 12, 3, 0xFFFF, 0x8001, 0, 7)
    struct.pack_into('<5H', text, 22, 3, 0xFFFE, 0x8001, 1, 7)
    struct.pack_into('<IIII', text, 32, 0x605D8C80, 0x60600000, 0x605FFDF4, 0x60600000 + 0x159C)
    struct.pack_into('<I', text, 48, sum(struct.unpack_from('<11I', text, 4)) & 0xFFFFFFFF)
    d = decode(bytes(text))
    assert d and d['magic'] == MAGIC and d['sum_ok'], d
    assert d['dial_p'] == (3, -1, NOD - 0x10000, 0, 7), d['dial_p']
    print('selftest OK: k=%r key=%r dial_p=%r sum_ok=%s'
          % (d['k_ascii'], d['key'].to_bytes(4, 'little'), d['dial_p'], d['sum_ok']))
    return 0


def main(argv):
    if not argv:
        argv = ['--selftest']
    if argv[0] == '--selftest':
        return _selftest()
    bad = 0
    for p in argv:
        d = show(p)
        bad += 0 if d and d['sum_ok'] else 1
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
