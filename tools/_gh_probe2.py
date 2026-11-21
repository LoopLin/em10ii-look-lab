import json, subprocess, urllib.request

H = {"User-Agent": "dsh-agent", "Accept": "application/vnd.github.raw"}

def raw(repo, path):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/{path}", headers=H)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")

out = {}
for path in ("docs/model-survey.md", "BLOBS.md", "emu/README.md"):
    try:
        out[path] = raw("flatpanda/om-emu", path)[:5000]
    except Exception as exc:
        out[path] = {"err": repr(exc)}
try:
    out["node"] = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
except Exception as exc:
    out["node"] = {"err": repr(exc)}
print("HEADLESS " + json.dumps(out, ensure_ascii=False))
