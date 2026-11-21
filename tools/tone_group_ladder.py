#!/usr/bin/env python3
"""Give every tone-table group its own brightness, so ONE flash reveals which
group each picture mode reads.

usage: tone_group_ladder.py OUT.BIN [lo] [step]

Group g's tables are scaled by  lo + step*g   (default 0.55 + 0.02*g).
Group 20 == 0.55+0.40 = 0.95, i.e. near stock; 21 distinct levels, ~2% apart.
"""
import sys
import struct as st

sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
PTRTBL = 0x60600920
TBL0 = 0x605D8344
TBL_STRIDE = 0x200


def main():
    out = sys.argv[1]
    lo = float(sys.argv[2]) if len(sys.argv) > 2 else 0.55
    step = float(sys.argv[3]) if len(sys.argv) > 3 else 0.02

    data = open('stock/OLY_E_120_1400_0000_0000.BIN', 'rb').read()
    blocks = oc.parse(data)
    blk = blocks[0]
    p = bytearray(blk.payload)
    off = PTRTBL - BASE
    ptrs = list(st.unpack_from('<168I', p, off))
    uniq = sorted(set(ptrs))
    kof = {ptr: i for i, ptr in enumerate(uniq)}
    print('unique tables:', len(uniq))

    # group -> set of table indices
    g2k = {}
    for g in range(21):
        g2k[g] = {kof[ptrs[g * 8 + j]] for j in range(8) if ptrs[g * 8 + j] in kof}
    # a table may be shared by several groups; last writer wins, so verify no overlap
    seen = {}
    for g, ks in g2k.items():
        for k in ks:
            seen.setdefault(k, []).append(g)
    shared = {k: v for k, v in seen.items() if len(v) > 1}
    if shared:
        print('NOTE shared tables (will use the smallest group):', shared)

    changed = 0
    for g in range(21):
        s = lo + step * g
        for k in g2k[g]:
            if len(seen[k]) > 1 and g != min(seen[k]):
                continue
            base = TBL0 + k * TBL_STRIDE - BASE
            for i in range(256):
                v = st.unpack_from('<H', p, base + 2 * i)[0]
                nv = max(0, min(1023, int(round(v * s))))
                if nv != v:
                    st.pack_into('<H', p, base + 2 * i, nv)
                    changed += 1
    blocks[0] = oc.replace_payload(blk, bytes(p))
    open(out, 'wb').write(oc.reconstruct(blocks))
    print('bytes changed:', changed, '->', out)
    for g in range(21):
        print(f'  g{g:02d} scale {lo + step*g:.3f}  tables {sorted(g2k[g])}')


main()
