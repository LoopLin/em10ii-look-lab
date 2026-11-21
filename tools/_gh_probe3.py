import json, urllib.request

H = {"User-Agent": "dsh-agent", "Accept": "application/vnd.github.raw"}

def raw(repo, path):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/contents/{path}", headers=H)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")

ms = raw("flatpanda/om-emu", "docs/model-survey.md")
hits = [ln for ln in ms.splitlines() if any(k in ln for k in ("E120", "E121", "E122", "E125", "E-M10 II", "old"))]
print("HEADLESS " + json.dumps({"len": len(ms), "hits": hits[:60]}, ensure_ascii=False))
