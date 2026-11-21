import hashlib, json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(r"G:\em10fw")
sys.path.insert(0, str(ROOT / "tools"))
import oly_container as oc

stock = ROOT / "stock" / "OLY_E_120_1400_0000_0000.BIN"
data = stock.read_bytes()
blk0 = oc.parse(data)[0]
old = blk0.payload[0x50D50B:0x50D50B + 12].hex()
out = Path(tempfile.gettempdir()) / "mkpatch_test" / "noop2.bin"
if out.exists():
    out.unlink()
cmd = [sys.executable, str(ROOT / "tools" / "mkpatch.py"), str(stock),
       "--out", str(out), "--patch", f"0x6052E52B={old}"]
p = subprocess.run(cmd, capture_output=True, text=True)
res = {"rc": p.returncode, "old": old, "err": p.stderr[-300:]}
if p.returncode == 0:
    b = out.read_bytes()
    res["identical_to_source"] = b == data
    res["sha_out"] = hashlib.sha256(b).hexdigest()
    res["sha_src"] = hashlib.sha256(data).hexdigest()
print("HEADLESS " + json.dumps(res))
