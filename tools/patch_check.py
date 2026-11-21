# -*- coding: utf-8 -*-
"""patch_check.py —— 出包前的三道硬门（离线，不需要刷机）。

门 1（可达性）：从函数入口到插入点，不允许存在"跳过插入点"的分支。
    B24f 就是栽在这条：0x604B8129 只是 `bcc` 的目标，key 大的情况走 `jmp 0x604b8320` 绕开了它。
门 2（跳转复核）：插入点写下的 DC disp32 必须正好落到桩首，桩尾的跳转必须正好回到约定位置。
门 3（插入点是指令边界）：插入的 5 字节必须从一条完整指令的开头开始。

用法：
    python tools/patch_check.py --dis check 0x604B8098 0x604B811D
    python tools/patch_check.py --jump check 0x604B811D 0x605DED44
    python tools/patch_check.py --run build/B24h_stash3.BIN --spec .tmp/b24h_spec.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT / 'analysis' / '00_0100_40300020.decoded.bin'
BASE = 0x60021020
DIS = ROOT / 'tools' / 'mn10300_dis.py'

LINE = re.compile(r'^\s*([0-9A-F]{8})\s+([0-9A-F ]+?)\s{2,}(\S.*)$')
BRANCH = re.compile(r'^(beq|bne|bgt|bge|blt|ble|bhi|bls|bcc|bcs|bvc|bvs|bmi|bpl|bra)\s+\(0x([0-9a-fA-F]+)')
JMP = re.compile(r'^jmp\s+\(?0x([0-9a-fA-F]+)')
CALL = re.compile(r'^call\s+0x([0-9a-fA-F]+)')


def disasm(ram: int, length: int) -> list[tuple[int, str, str]]:
    """返回 [(ram, bytes_hex, text)]"""
    off = ram - BASE
    out = subprocess.run([sys.executable, str(DIS), str(IMAGE), '--base', hex(BASE),
                          '--off', hex(off), '--len', hex(length)],
                         capture_output=True, text=True, encoding='utf-8', errors='replace')
    rows = []
    for ln in out.stdout.splitlines():
        m = LINE.match(ln)
        if m:
            rows.append((int(m.group(1), 16), m.group(2).strip(), m.group(3).strip()))
    return rows


def check_reach(entry: int, insert: int, verbose: bool = True) -> bool:
    rows = disasm(entry, insert - entry + 16)
    starts = [r[0] for r in rows]
    ok = True
    if insert not in starts:
        print(f'[FAIL] 0x{insert:08X} 不是指令边界（附近指令起点: '
              f'{[hex(s) for s in starts if abs(s - insert) < 8]}）')
        return False
    for ram, _b, text in rows:
        if ram >= insert:
            break
        m = BRANCH.match(text)
        if m:
            target = int(m.group(2), 16)
        else:
            m = JMP.match(text)
            if not m:
                continue
            target = int(m.group(1), 16)
        bypass = (target != insert) and (target > insert or target <= ram)
        if bypass:
            print(f'[FAIL] 0x{ram:08X} `{text}` 目标 0x{target:08X}：'
                  f'从这条分支可以跳过插入点 0x{insert:08X}')
            ok = False
        elif verbose:
            print(f'[ok  ] 0x{ram:08X} `{text}` → 不绕过')
    if ok:
        print(f'[PASS] 0x{entry:08X} → 0x{insert:08X}：线性路径上没有跳过的分支')
    return ok


def check_jump(site: int, dest: int, blob: bytes) -> bool:
    o = site - BASE
    if blob[o] != 0xDC:
        print(f'[FAIL] 0x{site:08X} 不是 DC 远跳（{blob[o]:02X}）')
        return False
    disp = int.from_bytes(blob[o + 1:o + 5], 'little')
    got = (site + disp) & 0xFFFFFFFF
    if got != dest:
        print(f'[FAIL] 0x{site:08X} 跳转落到 0x{got:08X}，期望 0x{dest:08X}')
        return False
    print(f'[PASS] 0x{site:08X} → 0x{dest:08X}')
    return True


def run_spec(out: Path, spec: Path) -> bool:
    blob = out.read_bytes()
    sys.path.insert(0, str(ROOT / 'tools'))
    import oly_container as oc
    decoded = oc.parse(blob)[0].payload
    s = json.loads(spec.read_text(encoding='utf-8'))
    ok = True
    for item in s.get('inserts', []):
        ok &= check_jump(int(item['site'], 0), int(item['dest'], 0), decoded)
    for item in s.get('reaches', []):
        ok &= check_reach(int(item['entry'], 0), int(item['insert'], 0), verbose=False)
    print('[ALL PASS]' if ok else '[SOME FAILED]')
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--reach', nargs=2, metavar=('ENTRY', 'INSERT'))
    ap.add_argument('--blob')
    ap.add_argument('--jump', nargs=2, metavar=('SITE', 'DEST'))
    ap.add_argument('--run', nargs=2, metavar=('BIN', 'SPEC'))
    a = ap.parse_args()
    ok = True
    if a.reach:
        ok &= check_reach(int(a.reach[0], 0), int(a.reach[1], 0))
    if a.jump:
        blob = Path(a.blob).read_bytes() if a.blob else IMAGE.read_bytes()
        ok &= check_jump(int(a.jump[0], 0), int(a.jump[1], 0), blob)
    if a.run:
        ok &= run_spec(Path(a.run[0]), Path(a.run[1]))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
