#!/usr/bin/env python3
"""Make flatpanda/om-emu run on Windows and teach modelcheck a patch overlay.

Two upstream problems on Windows:

1. Repo paths resolve with ``new URL('../../', import.meta.url).pathname``, which yields
   "/G:/..." and then ``path.resolve`` prefixes the drive again ("G:\\G:\\...").
2. modelcheck's child loads its modules with ``await import('G:/...')`` — Node refuses a
   bare Windows path in an ESM import (ERR_UNSUPPORTED_ESM_URL_SCHEME).

This script fixes both (idempotent) and adds an ``OM_EMU_PATCH`` environment hook to the
modelcheck child: a ``;``-separated list of om-emu patch files (the same JSON that
``bin/nav.mjs --patch`` takes), applied in memory to the stock blocks with
``patchedFirmware``. ``OM_EMU_PROOF=<file>`` writes the patch summary and the patched
block's sha256 there, so a run proves which image it booted.

Usage: python tools/omemu_winfix.py <om-emu checkout root>
"""

import re
import sys
from pathlib import Path

URL_PATH = re.compile(r"new URL\((['\"][^'\"]+['\"]), import\.meta\.url\)\.pathname")
IMPORT = re.compile(r"^import .*?;\s*$", re.M)

J_OLD = "const J = process.argv[1], DEVICE = process.argv[2],"
J_NEW = ("const J = (await import('node:url')).pathToFileURL(process.argv[1]).href, "
         "DEVICE = process.argv[2],")
LOAD_OLD = "const { loadImage, loadBlock, loadNand } = await import(J + '/bin/run.mjs');"
LOAD_NEW = LOAD_OLD + """
const { patchedFirmware, readPatchFiles } = await import(J + '/checks/lib/env.mjs');
const PF = process.env.OM_EMU_PATCH ? patchedFirmware(readPatchFiles(process.env.OM_EMU_PATCH.split(';').filter(Boolean))) : null;
if (PF) { console.error('patched: ' + JSON.stringify(PF.summary));
  if (process.env.OM_EMU_PROOF) { const fsx = await import('node:fs');
    fsx.writeFileSync(process.env.OM_EMU_PROOF, JSON.stringify({ summary: PF.summary, raw_sha: createHash('sha256').update(PF.raw).digest('hex') }, null, 2)); } }"""
ENV_OLD = "const env = { rom: loadImage(), resBlock: loadBlock(), nand: loadNand(),"
ENV_NEW = "const env = { rom: PF ? PF.rom : loadImage(), resBlock: PF ? PF.resBlock : loadBlock(), nand: loadNand(),"
GUARD_OLD = "  const line = r.stdout.trim().split('\\n').pop();"
GUARD_NEW = """  if (r.stdout == null) { console.log(`${m} (${MODELS[m].name}) ${device}: no stdout (${r.error && r.error.code}) ${(r.stderr||'').slice(-800)}`); bad++; continue; }
  const line = r.stdout.trim().split('\\n').pop();"""


def patch(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    new = URL_PATH.sub(lambda m: f"fileURLToPath(new URL({m.group(1)}, import.meta.url))", text)
    if new != text and "fileURLToPath" in new and "node:url" not in new:
        imports = list(IMPORT.finditer(new))
        at = imports[0].start() if imports else 0
        new = new[:at] + "import { fileURLToPath } from 'node:url';\n" + new[at:]
    if new != text:
        path.write_text(new, encoding="utf-8")
    return new != text


def patch_modelcheck(path: Path) -> list:
    text = path.read_text(encoding="utf-8")
    done = []
    for old, new, label in ((J_OLD, J_NEW, "child-import-url"),
                            (LOAD_OLD, LOAD_NEW, "OM_EMU_PATCH"),
                            (ENV_OLD, ENV_NEW, "patched-env"),
                            (GUARD_OLD, GUARD_NEW, "no-stdout-guard")):
        if new in text:
            continue
        if old not in text:
            raise SystemExit(f"modelcheck: cannot find the line for {label}; upstream changed")
        text = text.replace(old, new, 1)
        done.append(label)
    path.write_text(text, encoding="utf-8")
    return done


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    root = Path(sys.argv[1])
    changed = [p for p in root.rglob("*.mjs") if patch(p)]
    mc = root / "emu" / "bin" / "modelcheck.mjs"
    done = patch_modelcheck(mc) if mc.exists() else []
    print(f"path fixes: {len(changed)} file(s); modelcheck: {', '.join(done) or 'already patched'}")


if __name__ == "__main__":
    main()
