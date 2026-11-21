"""Run mn10300_dis on a RAM range and save UTF-8 listing."""
import subprocess, sys

BIN = r"analysis\00_0100_40300020.decoded.bin"
BASE = 0x60021020


def dis(ram, length, out=None):
    off = ram - BASE
    r = subprocess.run(
        [sys.executable, r"tools\mn10300_dis.py", BIN, "--base", hex(BASE),
         "--off", hex(off), "--len", hex(length)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    txt = r.stdout + r.stderr
    if out:
        open(out, "w", encoding="utf-8").write(txt)
    return txt


if __name__ == "__main__":
    ram = int(sys.argv[1], 0)
    ln = int(sys.argv[2], 0)
    out = sys.argv[3] if len(sys.argv) > 3 else None
    t = dis(ram, ln, out)
    print(t)
