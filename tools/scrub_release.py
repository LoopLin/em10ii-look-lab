#!/usr/bin/env python3
"""Scrub release docs: absolute paths, firmware blobs, risky wording, long hex.

Usage: python tools/scrub_release.py <src.md> <dst.md>
Read-only on the source; writes the scrubbed copy.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SUBS = [
    (r"G:\\+em10fw\\*", "<WORKSPACE>/"),
    (r"[A-Z]:\\+Users\\+[^\\\s]+\\*", "<HOME>/"),
    (r"[IJ]:\\+DCOLYMP\b\\*", "<CARD>/DCOLYMP/"),
    (r"[IJ]:\\+DCIM\b\\*", "<CARD>/DCIM/"),
    (r"H:\\+奥林巴斯\\*", "<ARCHIVE>/"),
    (r"stock/OLY_E_120_1400_0000_0000\.BIN", "<official-firmware>.BIN"),
    (r"stock/", "<official-firmware>/"),
    (r"\b[\w./\\-]*\.decoded\.bin\b", "<decoded-image>.bin"),
    (r"\b[A-Z]+\d{2,}[_\w]*\.BIN\b", "<firmware>.BIN"),
    (r"\b619?\d{0,3},?\d{3}(?:,?\d{3})?\s*B\b", "<size>"),
    (r"\b\d{1,3}(?:,\d{3}){2,}\b", "<size>"),
    (r"破解", "解析"),
    (r"绕过", "跳过"),
    (r"解锁", "解除"),
    (r"解除限制", "调整参数"),
    (r"(?i)\bcrack(?:ing|ed)?\b", "analysis"),
    (r"(?i)\bbypass(?:ing|ed)?\b", "skip"),
]

HEX_RUN = re.compile(r"\b(?:[0-9a-f]{2}[ ,:]){32,}[0-9a-f]{2}\b", re.I)


def scrub(text: str) -> tuple[str, dict[str, int]]:
    stats: dict[str, int] = {}
    for pat, rep in SUBS:
        text, n = re.subn(pat, rep, text)
        if n:
            stats[pat[:28]] = stats.get(pat[:28], 0) + n
    text, n = HEX_RUN.subn("<hex omitted>", text)
    if n:
        stats["long-hex"] = n
    return text, stats


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    out, stats = scrub(src.read_text(encoding="utf-8", errors="ignore"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8")
    print(f"{src} -> {dst}: " + ", ".join(f"{k}={v}" for k, v in stats.items() if v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
