#!/usr/bin/env python3
"""Deep provenance + risk audit (2026-11-22).

Outputs machine-readable numbers used by docs/analysis/legal-risk-audit.md:

1. longest identical token run (>=40) between each tools/*.py and every
   third-party source file  -> catches partial copying that Jaccard misses;
2. verbatim overlap of docs/refs/*.md against the third-party docs;
3. whitelist inventory: brand words / risky wording / absolute paths / hex;
4. patch + sim.json inspection: do we leak original firmware bytes?
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF_DIRS = [ROOT / ".tmp" / "cx", ROOT / ".tmp" / "ofw", ROOT / "refs"]
SRC_SUFFIX = {".py", ".js", ".mjs", ".c", ".h"}
DOC_SUFFIX = {".md"}
WIN = 40  # tokens

BRAND = re.compile(r"(?i)\b(provia|velvia|astia|eterna|acros|classic chrome|portra|ektar|ektachrome|kodak|fujifilm|olympus|om system|olympus corporation)\b")
RISKY = re.compile(r"(破解|绕过|解锁|解除限制|circumvent|defeat drm|keygen)")
ABS = re.compile(r"(?:[A-Z]:\\\\?(?:Users|em10fw|DCIM|DCOLYMP|奥林巴斯)|<user-id>)")
HEX40 = re.compile(r"[0-9a-fA-F]{40,}")


def tok(text: str) -> list[str]:
    text = re.sub(r"(?s)/\*.*?\*/|//[^\n]*|#[^\n]*", " ", text)
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]", text.lower())


def grams(t: list[str], k: int = WIN) -> dict[tuple, int]:
    return {tuple(t[i:i + k]): i for i in range(len(t) - k + 1)}


def longest_common(a: list[str], b: list[str]) -> int:
    """Length (tokens) of the longest common contiguous run."""
    if not a or not b:
        return 0
    gb = grams(b)
    best = 0
    for i in range(len(a) - WIN + 1):
        key = tuple(a[i:i + WIN])
        j = gb.get(key)
        if j is None:
            continue
        n = WIN
        while i + n < len(a) and j + n < len(b) and a[i + n] == b[j + n]:
            n += 1
        best = max(best, n)
    return best


def ref_files(suffixes: set[str]):
    for d in REF_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() in suffixes and p.is_file() and p.stat().st_size < 2_000_000:
                yield p


def main() -> int:
    print("== 1. longest identical token run (>=40) between tools/*.py and third party")
    refs = [(p, tok(p.read_text(encoding="utf-8", errors="ignore"))) for p in ref_files(SRC_SUFFIX)]
    hits = 0
    for p in sorted((ROOT / "tools").glob("*.py")):
        a = tok(p.read_text(encoding="utf-8", errors="ignore"))
        if len(a) < WIN:
            continue
        best, where = 0, None
        for rp, rb in refs:
            n = longest_common(a, rb)
            if n > best:
                best, where = n, rp
        if best >= 60:
            hits += 1
            print(f"  {best:5d} tokens  {p.relative_to(ROOT)}  <- {where.relative_to(ROOT) if where else '-'}")
    print(f"  files with >=60 identical tokens: {hits} / {len(list((ROOT/'tools').glob('*.py')))}")

    print("\n== 2. verbatim overlap of docs/refs/*.md against third-party docs")
    docrefs = [(p, re.findall(r"\w+", p.read_text(encoding="utf-8", errors="ignore").lower()))
               for p in ref_files(DOC_SUFFIX) if ".tmp" in str(p)]
    for p in sorted((ROOT / "docs" / "refs").glob("*.md")):
        words = re.findall(r"\w+", p.read_text(encoding="utf-8", errors="ignore").lower())
        if len(words) < 200:
            continue
        best, where = 0, None
        for rp, rw in docrefs:
            n = longest_common(words, rw)
            if n > best:
                best, where = n, rp
        print(f"  {p.name}: longest verbatim run {best} words <- {where.name if where else '-'}")

    print("\n== 3. whitelist inventory")
    wl = ["docs/STATUS.md", "docs/INDEX.md", "docs/METHOD-efficiency.md", "docs/EVIDENCE-RULES.md",
          "docs/RELEASE-CHECKLIST.md", "docs/ROADMAP-longterm.md", "docs/firmware/PACKAGES.md",
          "docs/UPLOAD-CHECKLIST.md", "README.md", "NOTICE"]
    wl += [str(p.relative_to(ROOT).as_posix()) for p in (ROOT / "docs/analysis").glob("*.md")]
    wl += [str(p.relative_to(ROOT).as_posix()) for p in (ROOT / "docs/refs").glob("*.md")]
    wl += [str(p.relative_to(ROOT).as_posix()) for p in (ROOT / "docs/reports").glob("*.md")]
    seen = []
    for f in wl:
        p = ROOT / f
        if not p.exists():
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        b, r, a, h = len(BRAND.findall(t)), len(RISKY.findall(t)), len(ABS.findall(t)), len(HEX40.findall(t))
        if b or r or a or h:
            seen.append((f, b, r, a, h))
            print(f"  {f:52s} brand={b:3d} risky={r:2d} path={a:2d} hex={h:3d}")
    print(f"  files: {len([f for f in wl if (ROOT/f).exists()])}; flagged: {len(seen)}")

    print("\n== 4. patch / sim.json leakage check")
    for p in sorted((ROOT / "build").glob("RELEASE_v1.*")):
        t = p.read_text(encoding="utf-8", errors="ignore")
        oldish = len(re.findall(r"(?i)\bold|expect|\bwas\b", t))
        longs = len(HEX40.findall(t))
        print(f"  {p.name:24s} bytes={p.stat().st_size:7d} lines={t.count(chr(10)):4d} "
              f"old/expect-mentions={oldish} long-hex={longs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
