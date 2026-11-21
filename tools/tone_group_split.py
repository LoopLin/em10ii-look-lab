#!/usr/bin/env python3
"""Darken selected tone-table groups (by group index) to probe which group each picture mode reads.

usage: tone_group_split.py OUT.BIN "0-10" [scale]
"""
import sys, struct, struct as st
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
PTRTBL = 0x60600920
TBL0 = 0x605D8344
TBL_STRIDE = 0x200
TBL_COUNT = 44

def main():
    out = sys.argv[1]
    spec = sys.argv[2]
    scale = float(sys.argv[3]) if len(sys.argv) > 3 else 0.55
    groups = set()
    for part in spec.split(','):
        if '-' in part:
            a, b = part.split('-'); groups.update(range(int(a), int(b) + 1))
        else:
            groups.add(int(part))
    data = open('stock/OLY_E_120_1400_0000_0000.BIN', 'rb').read()
    blocks = oc.parse(data)
    blk = blocks[0]
    p = bytearray(blk.payload)
    off = PTRTBL - BASE
    ptrs = list(st.unpack_from('<168I', p, off))
    uniq = sorted(set(ptrs))
    kof = {ptr: i for i, ptr in enumerate(uniq)}
    print('unique tables:', len(uniq), 'groups touched:', sorted(groups))
    changed = 0
    for g in range(21):
        ks = {kof[ptrs[g * 8 + j]] for j in range(8) if ptrs[g * 8 + j] in kof}
        if g not in groups:
            continue
        for k in ks:
            base = TBL0 + k * TBL_STRIDE - BASE
            for i in range(256):
                v = st.unpack_from('<H', p, base + 2 * i)[0]
                nv = int(round(v * scale))
                nv = max(0, min(1023, nv))
                if nv != v:
                    st.pack_into('<H', p, base + 2 * i, nv); changed += 1
    blocks[0] = oc.replace_payload(blk, bytes(p))
    open(out, 'wb').write(oc.reconstruct(blocks))
    print('bytes changed:', changed, '->', out)

main()
