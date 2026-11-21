import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(r"G:\em10fw")
sys.path.insert(0, str(ROOT / "tools"))
import oly_container as oc

stock = ROOT / "stock" / "OLY_E_120_1400_0000_0000.BIN"
data = stock.read_bytes()
src_sha = hashlib.sha256(data).hexdigest()
orig = data[0x50D52B:0x50D52B + 12]

outdir = Path(tempfile.gettempdir()) / "mkpatch_test"
outdir.mkdir(exist_ok=True)
results = {}

def run(name, patch_arg, out):
    cmd = [sys.executable, str(ROOT / "tools" / "mkpatch.py"), str(stock),
           "--out", str(out), "--patch-off", patch_arg]
    p = subprocess.run(cmd, capture_output=True, text=True)
    results[name] = {"rc": p.returncode, "err": p.stderr[-500:], "out": p.stdout[-800:]}
    return p

# 1) no-op patch: same bytes
noop = outdir / "noop.bin"
p = run("noop", f"0x50D52B={orig.hex()}", noop)
if p.returncode == 0:
    b = noop.read_bytes()
    results["noop"]["identical"] = (b == data)
    results["noop"]["sha"] = hashlib.sha256(b).hexdigest()

# 2) real 12-byte change
chg = outdir / "chg.bin"
new = bytes(range(12))
p = run("change", f"0x50D52B={new.hex()}", chg)
if p.returncode == 0:
    b = chg.read_bytes()
    blocks = oc.parse(b)
    results["change"]["parse_ok"] = True
    results["change"]["changed_positions"] = [i for i in range(len(data)) if data[i] != b[i]]
    results["change"]["len_equal"] = len(b) == len(data)
    results["change"]["blk0_sha"] = hashlib.sha256(blocks[0].payload).hexdigest()
    results["change"]["other_blocks_same"] = all(
        blocks[i].payload == oc.parse(data)[i].payload for i in (1, 2))
    results["change"]["trailer_hex"] = blocks[0].trailer.hex()
print("HEADLESS " + json.dumps(results))
