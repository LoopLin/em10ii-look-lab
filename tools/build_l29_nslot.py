#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_l29_nslot.py -- L29 候选补丁：Picture Mode 第 2 项（N / Elite Color 200）发出 colorTone key = 2

状态（重要）
------------
本脚本落地的是**候选假设**，不是已定位的枚举修复。详见 `.tmp/l29-report.md`：
菜单项 -> colorTone key 的映射代码**未定位**；本包改的是全镜像里唯一一处与
真机实测四档 key 完全吻合的数据表：

    RAM 0x605E9CC8: 00 00 00 01 01 03 04 05 08 08 08 08
    以 0x605E9CCB 为基：   [1,1,3,4,5,8,8,8,8]
    语义（按 docs/mode-key-map.md 4.3 的键表顺序）：
        idx0 Vivid    = 1
        idx1 Natural  = 1   <-- 疑似 bug，应为 2
        idx2 Muted    = 3
        idx3 Portrait = 4
        idx4 i-Enhance= 5
        idx5.. ART    = 8   （无固定键）

补丁只改 1 个字节：0x605E9CCC 01 -> 02。不碰任何 tone / 矩阵 / chroma 数据。

一包一根：BIN 只从 stock 起；输出到 .tmp/，不写 build/、不写卡、不碰 I 盘。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

BASE = 0x60021020
SRC = 'stock/OLY_E_120_1400_0000_0000.BIN'
OUT_BIN = '.tmp/L29_nslot.BIN'
OUT_PATCH = '.tmp/l29_nslot.patch'
OUT_LAYOUT = '.tmp/l29_nslot_layout.json'

RAM_ADDR = 0x605E9CCC      # mode->key 表里 Natural 那一格
OLD_BYTE = 0x01            # 现状（与 Vivid 撞车）
NEW_BYTE = 0x02            # docs/mode-key-map.md 4.3: key 0x00000002 = Natural

# 任务书点名的既有写点（硬编码兜底；另外还会动态解析真实 patch 文件）
MANDATED_SPANS = [
    (0x605DAF44, 0x200, 'tone slot k0'),
    (0x605DB144, 0x200, 'tone slot k1'),
    (0x605DB344, 0x200, 'tone slot k2'),
    (0x605DB544, 0x200, 'tone slot k3'),
    (0x605DECD4, 0x48, 'SU 桩 B63 (0x605DECD4 起)'),
    (0x605DED1C, 38, 'chroma 选择器桩 (0x605DED1C 起)'),
    (0x605DED44, 0x3C, 'SD 桩 B59 (0x605DED44 起)'),
    (0x605DED80, 0x4A, 'SU 桩 B59 (0x605DED80 起)'),
    (0x605DEDD4, 16, 'tone 指针表'),
    (0x605DEDE4, 16, 'chroma 块指针表'),
    (0x605DEE3C, 16, '矩阵指针表'),
    (0x605DEF44, 0x2000, 'bank2 已用区（0x605DEF44 起）'),
    (0x601ADE04, 50, '矩阵桩'),
]

RAM_RE = re.compile(r'^\s*ram\s+0x([0-9a-fA-F]+)\s*=\s*([0-9a-fA-F]+)')


def parse_ram_file(path):
    """解析 'ram 0xADDR=HEX' 行 -> [(addr, nbytes, source)]"""
    out = []
    if not os.path.exists(path):
        return out
    for line in open(path, encoding='utf-8', errors='replace'):
        m = RAM_RE.match(line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        n = len(m.group(2)) // 2
        out.append((addr, n, os.path.basename(path)))
    return out


def parse_omemu_json(path):
    out = []
    if not os.path.exists(path):
        return out
    data = json.load(open(path, encoding='utf-8'))
    for p in data if isinstance(data, list) else data.get('patches', []):
        if 'offset' not in p or 'new' not in p:
            continue
        addr = BASE + int(p['offset'])
        n = len(__import__('base64').b64decode(p['new']))
        out.append((addr, n, os.path.basename(path)))
    return out


def spans_from_patches():
    spans = []
    for p in ('.tmp/l28.patch', '.tmp/b95.patch', '.tmp/b90.patch', '.tmp/b69.patch'):
        spans += parse_ram_file(p)
    spans += parse_omemu_json('build/B95_looks.omemu.json')
    return spans


def covered(addr, spans):
    hits = []
    for a, n, src in spans:
        if a <= addr < a + n:
            hits.append((a, n, src))
    return hits


def main():
    sys.path.insert(0, 'tools')
    import oly_container as oc

    container = open(SRC, 'rb').read()
    parsed = oc.parse(container)
    blocks = [b for b in parsed if b.index == 0]
    if len(blocks) != 1:
        sys.exit('容器里没有唯一的 block 0：%r' % ([b.index for b in parsed],))
    if blocks[0].offset != 0 or len(blocks[0].header) != 32:
        sys.exit('block 0 布局不符合 ram->container 约定：offset=%r header_len=%r'
                 % (blocks[0].offset, len(blocks[0].header)))
    payload = blocks[0].payload
    off = RAM_ADDR - BASE
    if off < 0 or off >= len(payload):
        sys.exit('地址超出镜像：0x%X' % RAM_ADDR)
    cur = payload[off]
    if cur != OLD_BYTE:
        sys.exit('stock 该字节不是 0x%02X，而是 0x%02X（口径可能变了，先查）' % (OLD_BYTE, cur))

    # 1) 与既有 payload 的冲突检查
    spans = spans_from_patches()
    hard = [(a, n, s) for a, n, s in MANDATED_SPANS]
    clash = covered(RAM_ADDR, spans) + covered(RAM_ADDR, hard)
    if clash:
        sys.exit('冲突：0x%X 落在既有写点 %r' % (RAM_ADDR, clash))

    # 2) patch spec（单行、天然升序）
    # 注意：mkpatch.py 用 locale 默认编码读 patch 文件（本机 GBK），所以这里只能写 ASCII。
    patch_text = (
        '# L29 candidate patch: picture mode #2 (N slot) colorTone key 1 -> 2.\n'
        '# basis: bytes at 0x605E9CCB are 01 01 03 04 05 08 08 08 08, matching\n'
        '#        the real-camera four-slot keys 1/1/3/4 (Natural slot is the bug).\n'
        'ram 0x%x=%02x  # Natural key 0x%02X -> 0x%02X; Vivid/Muted/Portrait untouched\n'
        % (RAM_ADDR, NEW_BYTE, OLD_BYTE, NEW_BYTE)
    )
    with open(OUT_PATCH, 'w', encoding='ascii', newline='\n') as fh:
        fh.write(patch_text)

    # 3) 生成 BIN（走 mkpatch，保证与全项目同一套 ram->container 换算）
    if os.path.exists(OUT_BIN):
        os.remove(OUT_BIN)
    r = subprocess.run(
        [sys.executable, 'tools/mkpatch.py', SRC, '--out', OUT_BIN,
         '--patch-file', OUT_PATCH],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        sys.exit('mkpatch 失败:\n%s\n%s' % (r.stdout, r.stderr))

    built_container = open(OUT_BIN, 'rb').read()
    if len(built_container) != len(container):
        sys.exit('容器长度变了：%d vs %d' % (len(built_container), len(container)))
    built_payload = [b for b in oc.parse(built_container) if b.index == 0][0].payload

    decoded_diffs = [i for i in range(len(payload)) if payload[i] != built_payload[i]]
    if decoded_diffs != [off] or built_payload[off] != NEW_BYTE:
        sys.exit('decoded payload 差异不符合预期：%r（期望 [0x%X]）' % (decoded_diffs[:8], off))

    # 容器里的 payload 是 16 路交织 + 取反存储，所以"文件偏移"不等于 RAM 偏移。
    # 用 oly_container.encode 反推存储字节的真实落点。
    enc_stock = oc.encode(payload)
    enc_new = oc.encode(bytes(built_payload))
    stored_diffs = [i for i in range(len(enc_stock)) if enc_stock[i] != enc_new[i]]
    if len(stored_diffs) != 1:
        sys.exit('存储层差异不是 1 字节：%r' % (stored_diffs[:8],))
    file_stored = 32 + stored_diffs[0]

    file_diffs = [i for i in range(len(container)) if container[i] != built_container[i]]
    trailer_lo = 32 + len(payload)
    bad = [i for i in file_diffs if i != file_stored and i < trailer_lo]
    if bad:
        sys.exit('文件差异越界：%r（期望只在 0x%X 与 trailer 0x%X+）'
                 % ([(hex(x), container[x], built_container[x]) for x in bad[:8]],
                    file_stored, trailer_lo))

    layout = {
        'src': SRC,
        'out_bin': OUT_BIN,
        'out_patch': OUT_PATCH,
        'sha256_src': hashlib.sha256(container).hexdigest(),
        'sha256_out': hashlib.sha256(built_container).hexdigest(),
        'ram_addr': '0x%X' % RAM_ADDR,
        'decoded_off_in_block0': '0x%X' % off,
        'container_stored_file_off': '0x%X' % file_stored,
        'stock_byte': cur,
        'new_byte': NEW_BYTE,
        'decoded_context_after': bytes(built_payload[off - 4:off + 8]).hex(' '),
        'decoded_diffs': ['0x%X' % d for d in decoded_diffs],
        'file_diffs': ['0x%X' % d for d in file_diffs],
        'trailer_lo': '0x%X' % trailer_lo,
        'checked_spans': len(spans) + len(hard),
        'clash': clash,
    }
    json.dump(layout, open(OUT_LAYOUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    print('OK  0x%X: %02x -> %02x   (decoded 0x%X, stored file 0x%X)'
          % (RAM_ADDR, OLD_BYTE, NEW_BYTE, off, file_stored))
    print('    BIN   %s  (%d B, sha256 %s)' % (OUT_BIN, len(built_container), layout['sha256_out'][:16]))
    print('    patch %s' % OUT_PATCH)
    print('    decoded diff %r' % layout['decoded_diffs'])
    print('    file diff    %r  (trailer starts 0x%X)' % (layout['file_diffs'], trailer_lo))
    print('    ctx   %s' % layout['decoded_context_after'])
    print('    既有写点核查 %d 段，无冲突' % (len(spans) + len(hard)))


if __name__ == '__main__':
    main()
