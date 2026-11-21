# -*- coding: utf-8 -*-
"""Disassemble a RAM range:  python tools/mx_dis.py 0x604B6900 0x604B6F00 [outfile]"""
import subprocess, sys, os

BASE = 0x60021020
BIN = r"analysis\00_0100_40300020.decoded.bin"
start = int(sys.argv[1], 0)
end = int(sys.argv[2], 0)
out = sys.argv[3] if len(sys.argv) > 3 else None
cmd = [sys.executable, r"tools\mn10300_dis.py", BIN,
       "--base", hex(BASE),
       "--off", hex(start - BASE), "--len", hex(end - start)]
r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
txt = r.stdout
if out:
    open(out, "w", encoding="utf-8").write(txt)
    print("wrote", out, len(txt.splitlines()), "lines", "rc", r.returncode)
else:
    print(txt)
