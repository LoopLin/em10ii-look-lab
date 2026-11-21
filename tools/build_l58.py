# -*- coding: utf-8 -*-
"""L58 锯齿探针：把四张 tone 表改成 0/1023 交替（锚点周期 20 索引）。

目的（一次拍摄同时拿到两样东西）：
  1. 每个色块的真实索引落在哪一级（输出亮/暗即知相位）；
  2. 表值 0 与 1023 两端的实际输出，用来定 f 的两端。

本包**只用于测量**，拍完把 V/N 表写回 L55（见 build/L58_restore.json）。
"""
import sys, json, struct, hashlib
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
SLOT = {'V': 0x605DAF44, 'N': 0x605DB144, 'M': 0x605DB344, 'P': 0x605DB544}
SRC = 'build/L55_newton3.BIN'
OUT = 'build/L58_sawtooth.BIN'


def main():
    blocks = oc.parse(open(SRC, 'rb').read())
    pay = bytearray(blocks[0].payload)
    restore = {}
    for m, addr in SLOT.items():
        old = struct.unpack_from('<256H', pay, addr - BASE)
        restore[m] = list(old)
        # 锚点周期 20：每 10 个索引一个锚点，锚点值在 0/1023 交替
        anchors = list(range(0, 256, 10)) + [255]
        vals = [1023 if (k % 2 == 0) else 0 for k in range(len(anchors))]
        # 建表：线性插值到每个索引，保证单调段内是干净斜坡
        T = [0] * 256
        for k in range(len(anchors) - 1):
            a, b = anchors[k], anchors[k + 1]
            va, vb = vals[k], vals[k + 1]
            for i in range(a, b + 1):
                t = (i - a) / float(b - a)
                T[i] = int(round(va + (vb - va) * t))
        struct.pack_into('<256H', pay, addr - BASE, *T)
    out = oc.reconstruct([oc.replace_payload(blocks[0], bytes(pay))] + blocks[1:])
    open(OUT, 'wb').write(out)
    json.dump(restore, open('build/L58_restore.json', 'w'), indent=0)
    print('sha', hashlib.sha256(out).hexdigest()[:16])
    print('差异字节', sum(1 for a, b in zip(open(SRC, 'rb').read(), out) if a != b))


if __name__ == '__main__':
    main()
