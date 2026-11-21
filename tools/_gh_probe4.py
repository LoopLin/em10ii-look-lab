import json, subprocess, urllib.request

H = {"User-Agent": "dsh-agent", "Accept": "application/vnd.github.raw"}

def raw(repo, path):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/{path}", headers=H)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")

out = {}
for name, args in (("git", ["git", "--version"]), ("node", ["node", "--version"])):
    try:
        out[name] = subprocess.run(args, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as exc:
        out[name] = {"err": repr(exc)}

try:
    blobs = raw("flatpanda/om-emu", "BLOBS.sha256")
    out["blobs_E120"] = [ln for ln in blobs.splitlines() if "E_120" in ln or "E120" in ln]
except Exception as exc:
    out["blobs_E120"] = {"err": repr(exc)}

try:
    models = raw("flatpanda/om-emu", "emu/src/fw/models.mjs")
    lines = models.splitlines()
    idx = [i for i, ln in enumerate(lines) if "E120" in ln]
    out["models_E120"] = [lines[i] for i in idx][:20]
    for i in idx[:2]:
        out.setdefault("models_ctx", []).extend(lines[max(0, i - 3):i + 25])
except Exception as exc:
    out["models_E120"] = {"err": repr(exc)}

# tarball reachability
try:
    req = urllib.request.Request("https://codeload.github.com/flatpanda/om-emu/tar.gz/refs/heads/main", headers={"User-Agent": "dsh-agent"})
    with urllib.request.urlopen(req, timeout=60) as r:
        chunk = r.read(1024)
        out["tarball"] = {"status": r.status, "first_bytes": chunk[:2].hex(), "len": r.headers.get("Content-Length")}
except Exception as exc:
    out["tarball"] = {"err": repr(exc)}

print("HEADLESS " + json.dumps(out, ensure_ascii=False))
