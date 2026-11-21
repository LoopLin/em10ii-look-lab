# -*- coding: utf-8 -*-
"""Code xref helpers for the E-M10 II image (MN10300/AM33-2).

Encodings used here (verified against tools/mn10300_dis.py output):
  call rel16 : CD <s16 LE> 00 00      target = cia + s16        (5 bytes)
  call rel32 : DD <s32 LE> ...        target = cia + s32        (7 bytes)
  mov imm32,An : FC <regbyte> <u32 LE>
  mov imm16,An : 3C/3D... (2 bytes after opcode)
"""
import struct, sys

BIN = r"G:\em10fw\analysis\00_0100_40300020.decoded.bin"
BASE = 0x60021020
data = open(BIN, "rb").read()
N = len(data)

def calls_to(target, lo=None, hi=None):
    """All sites whose call displacement lands on `target`."""
    res = []
    lo = 0 if lo is None else lo - BASE
    hi = N if hi is None else hi - BASE
    for o in range(max(0, lo), min(N - 7, hi)):
        b = data[o]
        if b == 0xCD:
            d = struct.unpack_from("<h", data, o + 1)[0]
            if BASE + o + d == target:
                res.append((BASE + o, 5, "call16"))
        elif b == 0xDD:
            d = struct.unpack_from("<i", data, o + 1)[0]
            if BASE + o + d == target:
                res.append((BASE + o, 7, "call32"))
    return res

def imm32_refs(val, lo=None, hi=None):
    res = []
    lo = 0 if lo is None else lo - BASE
    hi = N if hi is None else hi - BASE
    for o in range(max(0, lo), min(N - 5, hi)):
        if data[o] == 0xFC and struct.unpack_from("<I", data, o + 2)[0] == val:
            res.append((BASE + o, data[o + 1]))
    return res

def ptr_refs(val):
    res = []
    p = struct.pack("<I", val)
    i = data.find(p)
    while i >= 0:
        res.append(BASE + i)
        i = data.find(p, i + 1)
    return res

if __name__ == "__main__":
    mode = sys.argv[1]
    args = [int(x, 0) for x in sys.argv[2:]]
    if mode == "call":
        for a in args:
            r = calls_to(a)
            print("calls to %08X : %d" % (a, len(r)))
            for x in r:
                print("   %08X %s" % (x[0], x[2]))
    elif mode == "imm":
        for a in args:
            r = imm32_refs(a)
            print("imm32 %08X : %d" % (a, len(r)))
            for x in r:
                print("   %08X reg=%02X" % x)
    elif mode == "ptr":
        for a in args:
            r = ptr_refs(a)
            print("ptr %08X : %d" % (a, len(r)))
            for x in r:
                print("   %08X" % x)
