#!/usr/bin/env python3
"""Build a static reference index: table base -> every 4-byte pointer to it.

Usage: python tools/ref_index.py [--out docs/map/ref-index.json]
"""
import json, struct, sys, os

IMG = 'analysis/00_0100_40300020.decoded.bin'
BASE = 0x60021020

# --- known table families ------------------------------------------------
def tone_tables():
    # 44 unique u16[256] tables, 0x200 stride, starting 0x605D8344
    return [0x605D8344 + i*0x200 for i in range(64)]

def tone_group_tables():
    return [0x60600920 + i*0x20 for i in range(5)]

def chroma_blocks():
    return [0x605D4829 + i*0x64 for i in range(65)]

OTHER = {
    'sixaxis_records': 0x605ECF5A,
    'hsl_flat_dataset': 0x605F0798,
    'hsl_real_buffer': 0x605F5A64,
    'chroma_ptr_table_8x12': 0x605FFDC8,
    'matrix_area': 0x60DB0000,
    'tone_lut_identity': 0x605D8144,
}

def main():
    d = open(IMG,'rb').read()
    fams = {}
    fams['tone_table']   = {a:'tone_%02d' % i for i,a in enumerate(tone_tables())}
    fams['tone_group']   = {a:'group_%d' % i for i,a in enumerate(tone_group_tables())}
    fams['chroma_block'] = {a:'chroma_%02d' % i for i,a in enumerate(chroma_blocks())}
    fams['other']        = {a:k for k,a in OTHER.items()}

    # index every 4-byte dword position once
    n = len(d)//4
    dwords = struct.unpack('<%dI' % n, d[:n*4])
    pos = {}
    for i,v in enumerate(dwords):
        pos.setdefault(v, []).append(BASE + i*4)

    out = {}
    for fam, table in fams.items():
        for addr, name in table.items():
            hits = pos.get(addr, [])
            if hits:
                out.setdefault(fam, {})[name] = {
                    'addr': hex(addr),
                    'refs': [hex(h) for h in hits],
                }
    total = sum(len(v['refs']) for f in out.values() for v in f.values())
    print('families:', ', '.join('%s=%d tables, %d refs' % (f, len(v), sum(len(x['refs']) for x in v.values())) for f,v in out.items()))
    print('total refs:', total)
    os.makedirs('docs/map', exist_ok=True)
    json.dump({'image': IMG, 'base': hex(BASE), 'families': out},
              open('docs/map/ref-index.json','w'), indent=1)
    print('wrote docs/map/ref-index.json')

if __name__ == '__main__':
    main()
