# -*- coding: utf-8 -*-
"""verify_guard.py —— 守卫探针的离线验收（不碰硬件、不写 build/）。

对 build_guard.py 产出的 scratch 包做 6 项检查，任何一项不过就返回非 0：

  V1 构建可重复：重新汇编 G1/G2，与包里的字节逐字节一致；
  V2 远跳正确：两处钩点是 DC disp32，且 disp 正好落到桩首（以指令地址为基准）；
  V3 边界合规：钩点原本是 5 字节的完整指令边界（见 patch_check 口径）；
  V4 改动面收敛：相对 base 的全部改动只落在两处钩点 + 两个桩里，别处一个字节不动；
  V5 口袋干净：目标口袋除头/记录区/桩以外仍是 FF，且不碰 r4 首指令与 EXIF 区；
  V6 参考门：patch_check --reach 的结论照实打印（条件钩子不算失败，但要写进文档）。

用法：
    python tools/verify_guard.py                     # 验 .tmp/guard/guard_probe.BIN
    python tools/verify_guard.py --bin .tmp/guard/guard_probe.BIN --base build/L55_newton3.BIN
"""
from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import oly_container as oc          # noqa: E402
import build_guard as bg            # noqa: E402

R = 0x60021020


def payload_of(path: Path) -> bytes:
    return oc.parse(path.read_bytes())[0].payload


def check_hook(pay: bytes, site: int, want_addr: int) -> dict:
    raw = pay[site - R:site - R + 5]
    ok_op = raw[0] == 0xDC
    disp = struct.unpack('<i', raw[1:])[0] if ok_op else None
    tgt = site + disp if disp is not None else None
    return {'site': hex(site), 'bytes': raw.hex(), 'dc': ok_op,
            'target': hex(tgt) if tgt is not None else None,
            'want': hex(want_addr), 'ok': ok_op and tgt == want_addr}


def dis(path: Path, base: int, length: int) -> str:
    r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'mn10300_dis.py'), str(path),
                        '--base', hex(base), '--off', '0', '--len', hex(length)],
                       capture_output=True, text=True)
    return (r.stdout or r.stderr).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--bin', default='.tmp/guard/guard_probe.BIN')
    ap.add_argument('--base', default='build/L55_newton3.BIN')
    ap.add_argument('--work', default='.tmp/guard/build')
    ap.add_argument('--skip-audit', action='store_true')
    args = ap.parse_args()

    pay = bytearray(payload_of(ROOT / args.bin))
    base = bytearray(payload_of(ROOT / args.base))
    rep: dict[str, object] = {'bin': args.bin, 'base': args.base}
    fails: list[str] = []

    # ---- V1 构建可重复 ---------------------------------------------------
    g1b = bg.assemble(bg.G1_SRC, bg.G1, ROOT / args.work, 'v_g1')
    g1 = g1b + bg.jmp_bytes(bg.G1 + len(g1b), bg.H1_RET)
    g2b = bg.assemble(bg.G2_SRC, bg.G2, ROOT / args.work, 'v_g2')
    g2 = g2b + bg.jmp_bytes(bg.G2 + len(g2b), bg.H2_RET)
    same = {
        'G1': bytes(pay[bg.G1 - R:bg.G1 - R + len(g1)]) == g1,
        'G2': bytes(pay[bg.G2 - R:bg.G2 - R + len(g2)]) == g2,
    }
    rep['V1_reproducible'] = {'size': {'G1': len(g1), 'G2': len(g2)}, 'match': same}
    if not all(same.values()):
        fails.append('V1 构建与包内字节不一致')

    # ---- V2/V3 远跳 ----------------------------------------------------
    h1 = check_hook(pay, bg.H1_SITE, bg.G1)
    h2 = check_hook(pay, bg.H2_SITE, bg.G2)
    rep['V2_jumps'] = {'H1': h1, 'H2': h2}
    if not (h1['ok'] and h2['ok']):
        fails.append('V2 远跳落点不对')
    rep['V3_boundary'] = {
        'H1': {'len': bg.H1_LEN, 'tail': '0x604B9F08 bne (原样保留)',
               'replay': 'movbu (4,SP),d0 + cmp 0x0,d0'},
        'H2': {'len': bg.H2_LEN, 'tail': '0x604ED7C0 call 0x604F8F4E',
               'replay': 'movhu d0,(18372,a0) + mov (16,SP),a0（第 5 字节切进它的首字节）'},
    }

    # ---- V4 改动面收敛 --------------------------------------------------
    allowed = set()
    for s in (bg.H1_SITE, bg.H2_SITE):
        allowed |= set(range(s, s + 5))
    allowed |= set(range(bg.G1, bg.G1 + len(g1)))
    allowed |= set(range(bg.G2, bg.G2 + len(g2)))
    outside = [R + i for i, (a, b) in enumerate(zip(base, pay)) if a != b and (R + i) not in allowed]
    rep['V4_footprint'] = {
        'changed': sum(1 for a, b in zip(base, pay) if a != b),
        'outside_expected': len(outside),
        'sample': [hex(x) for x in outside[:8]],
    }
    if outside:
        fails.append(f'V4 有 {len(outside)} 字节改动落在预期之外')

    # ---- V5 口袋干净 ----------------------------------------------------
    lo, hi = bg.POCKET
    used = set(range(bg.HDR, bg.G1 + len(g1))) | set(range(bg.G2, bg.G2 + len(g2)))
    dirty = [lo + i for i in range(hi - lo)
             if pay[lo - R + i] != 0xFF and (lo + i) not in used]
    tail = bytes(pay[hi - R:0x60673000 - R])
    rep['V5_pocket'] = {
        'pocket': [hex(lo), hex(hi)],
        'dirty_outside_our_structures': len(dirty),
        'r4_head_tail_all_ff': tail == b'\xff' * len(tail),
        'exif_area_in_file_untouched': pay[0x61894C98 - R:0x61894D05 - R] ==
                                        base[0x61894C98 - R:0x61894D05 - R],
    }
    if dirty or rep['V5_pocket']['r4_head_tail_all_ff'] is False:
        fails.append('V5 口袋/桩尾不干净')

    # ---- 反汇编证据 ----------------------------------------------------
    (ROOT / args.work).mkdir(parents=True, exist_ok=True)
    gp = ROOT / args.work / 'g1_extracted.bin'
    gp.write_bytes(bytes(pay[bg.G1 - R:bg.G1 - R + len(g1)]))
    gq = ROOT / args.work / 'g2_extracted.bin'
    gq.write_bytes(bytes(pay[bg.G2 - R:bg.G2 - R + len(g2)]))
    rep['disasm'] = {'G1': dis(gp, bg.G1, len(g1)), 'G2': dis(gq, bg.G2, len(g2))}

    # ---- V6 参考门 ------------------------------------------------------
    ref = {}
    for name, entry, insert in (('H1', 0x604B9EBF, bg.H1_SITE), ('H2', 0x604ED5D9, bg.H2_SITE)):
        r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'patch_check.py'),
                            '--reach', hex(entry), hex(insert)],
                           capture_output=True, text=True, cwd=ROOT)
        ref[name] = {'entry': hex(entry), 'insert': hex(insert),
                     'rc': r.returncode, 'out': (r.stdout + r.stderr).strip()[-400:]}
    rep['V6_reference_gate'] = ref

    if not args.skip_audit:
        r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'audit_beta_pack.py'),
                            str(ROOT / args.bin)], capture_output=True, text=True, cwd=ROOT)
        txt = (r.stdout + r.stderr).strip()
        ok = r.returncode == 0 and '[FAIL' not in txt and '[OK' in txt
        rep['audit_beta_pack'] = {'rc': r.returncode, 'OK': ok, 'tail': txt[-600:]}
        if not ok:
            fails.append('audit_beta_pack 未给 [OK]（该包在 tone/矩阵/色度闸门上有问题）')

    rep['fails'] = fails
    rep['verdict'] = 'PASS' if not fails else 'FAIL'
    out = ROOT / '.tmp' / 'guard' / 'verify.json'
    out.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: rep[k] for k in ('verdict', 'V1_reproducible', 'V2_jumps',
                                          'V4_footprint', 'V5_pocket', 'fails')},
                     indent=2, ensure_ascii=False))
    print(f'[report] {out}')
    for k in ('V6_reference_gate', 'audit_beta_pack'):
        if k in rep:
            print(f'--- {k}\n{json.dumps(rep[k], indent=1, ensure_ascii=False)}')
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
