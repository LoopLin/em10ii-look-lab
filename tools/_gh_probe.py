import json, urllib.request

H = {"User-Agent": "dsh-agent", "Accept": "application/vnd.github.v3+json"}

def get(url, accept=None):
    h = dict(H)
    if accept:
        h["Accept"] = accept
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

out = {}
for repo in ("flatpanda/om-emu", "txt231/GhidraMn10300"):
    try:
        meta = json.loads(get(f"https://api.github.com/repos/{repo}"))
        out[repo] = {
            "desc": meta.get("description"),
            "stars": meta.get("stargazers_count"),
            "size_kb": meta.get("size"),
            "default_branch": meta.get("default_branch"),
            "pushed_at": meta.get("pushed_at"),
        }
        tree = json.loads(get(f"https://api.github.com/repos/{repo}/git/trees/{meta['default_branch']}?recursive=1"))
        out[repo]["files"] = [t["path"] for t in tree.get("tree", [])][:200]
        try:
            rd = get(f"https://api.github.com/repos/{repo}/readme", "application/vnd.github.raw").decode("utf-8", "replace")
            out[repo]["readme_head"] = rd[:3000]
        except Exception as exc:
            out[repo]["readme_err"] = repr(exc)
    except Exception as exc:
        out[repo] = {"err": repr(exc)}
print("HEADLESS " + json.dumps(out, ensure_ascii=False))
