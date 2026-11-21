import json, os, tarfile, urllib.request
from pathlib import Path

ROOT = Path(r"G:\em10fw")
cache = ROOT / ".cache"
cache.mkdir(exist_ok=True)
tgz = cache / "om-emu-main.tar.gz"
if not tgz.exists():
    req = urllib.request.Request("https://codeload.github.com/flatpanda/om-emu/tar.gz/refs/heads/main",
                                 headers={"User-Agent": "dsh-agent"})
    with urllib.request.urlopen(req, timeout=300) as r, open(tgz, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)

dest = ROOT / "refs" / "om-emu-main"
dest.mkdir(parents=True, exist_ok=True)
with tarfile.open(tgz, "r:gz") as tf:
    members = tf.getmembers()
    root_name = members[0].name.split("/")[0]
    for m in members:
        rel = m.name[len(root_name) + 1:]
        if not rel:
            continue
        target = dest / rel
        if m.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif m.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(m) as src, open(target, "wb") as out:
                out.write(src.read())
fw = dest / "fw" / "old"
fw.mkdir(parents=True, exist_ok=True)
stock = ROOT / "stock" / "OLY_E_120_1400_0000_0000.BIN"
target_bin = fw / stock.name
if not target_bin.exists():
    target_bin.write_bytes(stock.read_bytes())
print("HEADLESS " + json.dumps({
    "tgz": str(tgz), "tgz_size": tgz.stat().st_size,
    "dest": str(dest), "files": sum(1 for _ in dest.rglob("*") if _.is_file()),
    "fw": [p.name for p in (dest / "fw").rglob("*.BIN")],
    "top": sorted(p.name for p in dest.iterdir())[:20],
}, ensure_ascii=False))
