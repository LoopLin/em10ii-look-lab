#!/usr/bin/env python3
"""Code-provenance audit: how much of our tools/ resembles third-party repos.

Offline, read-only. Emits a short table: our file -> best match -> Jaccard of
normalised 12-token shingles. High values (>0.15) deserve a manual look.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OURS = sorted((ROOT / "tools").glob("*.py"))
REF_DIRS = [ROOT / ".tmp" / "cx", ROOT / ".tmp" / "ofw", ROOT / "refs"]
SUFFIX = {".py", ".js", ".mjs", ".c", ".h", ".igen", ".txt"}
MIN_SHINGLES = 40


def tokens(text: str) -> list[str]:
    text = re.sub(r"(?s)/\*.*?\*/|//[^\n]*|#[^\n]*", " ", text)
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]", text.lower())


def shingles(text: str, k: int = 12) -> set[tuple[str, ...]]:
    t = tokens(text)
    return {tuple(t[i:i + k]) for i in range(len(t) - k + 1)}


def best_match(ours: set[tuple[str, ...]], refs: list[tuple[Path, set]]) -> tuple[Path | None, float]:
    best, score = None, 0.0
    for path, sh in refs:
        if not sh:
            continue
        inter = len(ours & sh)
        if not inter:
            continue
        j = inter / len(ours | sh)
        if j > score:
            best, score = path, j
    return best, score


def main() -> int:
    refs: list[tuple[Path, set]] = []
    for d in REF_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() in SUFFIX and p.is_file() and p.stat().st_size < 2_000_000:
                try:
                    refs.append((p, shingles(p.read_text(encoding="utf-8", errors="ignore"))))
                except OSError:
                    pass
    print(f"reference files: {len(refs)}", file=sys.stderr)

    rows = []
    for p in OURS:
        try:
            sh = shingles(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if len(sh) < MIN_SHINGLES:
            continue
        ref, score = best_match(sh, refs)
        rows.append((score, p, ref))
    rows.sort(reverse=True)
    for score, p, ref in rows[:15]:
        name = ref.relative_to(ROOT) if ref else "-"
        print(f"{score:6.3f}  {p.relative_to(ROOT)!s:42s} {name}")
    print(f"\nfiles compared: {len(rows)}; >0.15: {sum(1 for r in rows if r[0] > 0.15)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
