#!/usr/bin/env python3
"""Minimal driver for the fishku MN10300 harness, retargeted at E-M10 II block 0.

Protocol (PFMN002) is identical to the upstream penf_emulator.py; only the
firmware base and the region setup differ.

Usage:
  python tools/em10_run.py --pc 0x6048325c --stop 0x61E01000 \
      --reg A0=0x61E00000 --code 0x60483000:0x400 \
      --rw 0x61E00000:0x1000 --dump 0x61E00000:0x40 --limit 5000
"""
import argparse
import json
import struct
import subprocess
import sys
from pathlib import Path

BASE = 0x60021020
IMAGE = Path('analysis/00_0100_40300020.decoded.bin')
ENGINE = Path('.tmp/olya/toolchains/mn103-gnu-build/.libs/penf-mn103.exe')
REGISTERS = ['D0', 'D1', 'D2', 'D3', 'A0', 'A1', 'A2', 'A3', 'SP', 'PC', 'PSW',
             'MDR', 'LIR', 'LAR', 'FAULT', 'Z', 'XY', 'R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7',
             'RAMB', 'RAR', 'U', 'VC', 'EXB', 'CRT', 'UNK']


def parse_span(text):
    addr, _, ln = text.partition(':')
    if ln.lower().startswith('0x'):
        end = int(ln, 16)
    else:
        end = int(ln, 0)
    start = int(addr, 0)
    if end > start and end - start > 0x400000 and text.count(':'):
        pass
    return start, end


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pc', required=True)
    ap.add_argument('--stop', default='0x61E01000')
    ap.add_argument('--reg', action='append', default=[])
    ap.add_argument('--code', action='append', default=[])
    ap.add_argument('--rw', action='append', default=[])
    ap.add_argument('--rwx', action='append', default=[],
                    help='region inside the image mapped rwx (stub pages that both '
                         'execute and write their own data, e.g. 0x605DED44)')
    ap.add_argument('--rw-any', action='append', default=[],
                    help='rw region outside the image range (zero-seeded); '
                         'the engine does not enforce x/w permissions, so this is '
                         'for scratch buses (stack, work objects, ISP windows).')
    ap.add_argument('--dump', action='append', default=[])
    ap.add_argument('--limit', type=int, default=20000)
    ap.add_argument('--trace', type=int, default=0)
    ap.add_argument('--breakpoint', action='append', default=[])
    ap.add_argument('--image', default=str(IMAGE))
    ap.add_argument('--poke', action='append', default=[],
                    help='addr=hexbytes, written into an rw region before run')
    ap.add_argument('--out')
    args = ap.parse_args()

    image = Path(args.image).read_bytes()
    regions = []

    def slice_image(start, end):
        if not BASE <= start < end <= BASE + len(image):
            sys.exit(f'code span outside image: {start:#x}-{end:#x}')
        return image[start - BASE:end - BASE]

    for spec in args.code:
        start, ln = parse_span(spec)
        regions.append((f'code_{start:x}', start, slice_image(start, start + ln), 'rx', None))

    for spec in args.rw:
        start, ln = parse_span(spec)
        # rw regions must be seeded from the image, otherwise reads see zeros
        regions.append((f'rw_{start:x}', start, slice_image(start, start + ln), 'rw', None))

    for spec in args.rwx:
        start, ln = parse_span(spec)
        regions.append((f'rwx_{start:x}', start, slice_image(start, start + ln), 'rwx', None))

    for spec in args.rw_any:
        start, ln = parse_span(spec)
        regions.append((f'rwany_{start:x}', start, bytes(ln), 'rw', None))

    pokes = []
    for spec in args.poke:
        addr, _, hx = spec.partition('=')
        pokes.append((int(addr, 0), bytes.fromhex(hx)))

    regs = {name: 0 for name in REGISTERS}
    regs['PC'] = int(args.pc, 0)
    regs['SP'] = 0x61E00800
    for item in args.reg:
        name, _, value = item.partition('=')
        name = name.upper()
        if name not in REGISTERS:
            sys.exit(f'unknown register {name}')
        regs[name] = int(value, 0)

    stop = int(args.stop, 0)
    bps = [int(b, 0) for b in args.breakpoint]
    payload = bytearray(b'PFMN002\0' + struct.pack('<6I', args.limit, stop, args.trace,
                                                   len(regions), len(bps), 0))
    payload.extend(struct.pack(f'<{len(bps)}I32I', *bps, *[regs[n] for n in REGISTERS]))
    for name, start, data, perm, _alias in regions:
        buf = bytearray(data)
        for pa, pb in pokes:
            if start <= pa and pa + len(pb) <= start + len(buf):
                buf[pa - start:pa - start + len(pb)] = pb
        mask = sum(bit for ch, bit in [('r', 1), ('w', 2), ('x', 4)] if ch in perm)
        payload.extend(struct.pack('<5I', start, len(buf), mask, 1, 0xffffffff))
        payload.extend(bytes(buf))

    proc = subprocess.run([str(ENGINE)], input=payload, capture_output=True, timeout=120)
    if proc.returncode:
        sys.exit(f'engine failed {proc.returncode}: {proc.stderr.decode(errors="replace")}')
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        sys.exit(f'bad output: {proc.stdout[:400]!r} {proc.stderr[:400]!r}')

    reg = dict(zip(REGISTERS, result['registers']))
    print(json.dumps({k: result[k] for k in ('status', 'attempted', 'completed', 'signal', 'fault')}, indent=1))
    print('PC=%08x D0=%08x D1=%08x A0=%08x A1=%08x' % (reg['PC'], reg['D0'], reg['D1'], reg['A0'], reg['A1']))
    for cap in result.get('memory', []):
        raw = cap.get('hex')
        if raw is None:
            raw = cap.get('data')
        data = bytes.fromhex(raw) if isinstance(raw, str) else bytes(raw or [])
        print(f"capture {cap.get('name','?')} {len(data)} B")
        for i in range(0, min(len(data), 512), 16):
            print('  %08x  %s' % (cap.get('address',0) + i, ' '.join('%02x' % b for b in data[i:i + 16])))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1))


if __name__ == '__main__':
    main()
