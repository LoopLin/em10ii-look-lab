#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hsl_sim.py — 在本地 MN10300 harness 里复现「E-M10 II COLOR 表被 install 读出」。

被测函数：0x604B922D(a0=表, d0=count, a1=model)。实测语义：
  每条 28B 记录取 u16[0,1,2,3,4,5,10,11,12,6,7,8]，按这个顺序写到
  model+676 起，每条占 24B；共 12 条（外循环 2 趟 × 内循环 6 条）。
  函数尾部另把 count(=2015) 写到 model+964。

本工具不碰真机、不写卡、不生成固件，只在本机模拟器里跑。

用法：
  python tools/hsl_sim.py                      # 基线 + 单字段扰动，两轮都跑
  python tools/hsl_sim.py --field 6 --delta 100
  python tools/hsl_sim.py --table-offset 0x5cf778 --md docs/hsl-dataset-sim.md
  python tools/hsl_sim.py --json .tmp/hsl_sim.json

注意：--stop 0x604B9340 不是「写完之后」——它落在外循环计数器的自增里，
每趟命中一次，第一次命中时只写完 6 条。默认用 0x604B9353（两趟都写完、
且已写入 model+964 之后），详情见 docs/hsl-dataset-sim.md。
"""
import argparse
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT / 'analysis' / '00_0100_40300020.decoded.bin'
DRIVER = ROOT / 'tools' / 'em10_run.py'

BASE = 0x60021020             # 镜像 RAM 基址：RAM = BASE + 文件偏移
TABLE_ADDR = 0x605F0798       # COLOR 表（静态默认）在 RAM 里的地址
TABLE_OFF = TABLE_ADDR - BASE  # = 0x5CF778
TABLE_LEN = 3028              # 12*28 + 4*96*4 + 6*96*2 + 4
REC_LEN = 28                  # 一条记录 28B
N_REC = 12                    # 12 条
DEST_BASE_IN_MODEL = 676      # model+0x2A4
DEST_LEN = 24                 # 每条写 12 个 u16

FUNC = 0x604B922D             # install 入口
STOP_OK = 0x604B9353          # 12 条全写完、count 也写完之后
STOP_HALF = 0x604B9340        # 陷阱：第一趟结束处，只写完 6 条
CODE_LO, CODE_LEN = 0x604B9200, 0x400

TABLE_POKE = 0x61E00000       # 表放这里
MODEL = 0x61E10000            # model 放这里
SP = 0x61E20000               # SP 放这里
DUMP_LEN = 0x400              # model 前 1KB 足够

# 文档/反汇编给出的写入顺序：目标第 k 个 u16 <- 源记录的第 ORDER[k] 个 u16
ORDER = (0, 1, 2, 3, 4, 5, 10, 11, 12, 6, 7, 8)
COUNT_SLOT = 964              # model+964 <- count
TRAILER_OFF = 0xBD0           # 表尾 u16 = 2015

UNUSED = sorted(set(range(REC_LEN // 2)) - set(ORDER))   # [9, 13]


class HarnessError(RuntimeError):
    pass


def load_table(image, offset, length=TABLE_LEN):
    if offset < 0 or offset + length > len(image):
        raise SystemExit('表区间越界: %#x+%d > 镜像 %d 字节' % (offset, length, len(image)))
    return image[offset:offset + length]


def src_records(table):
    return [struct.unpack_from('<14H', table, r * REC_LEN) for r in range(N_REC)]


def expected_out(table):
    """按 ORDER 把每条记录映射成 12 个 u16，返回 [12][12]。"""
    return [[rec[i] for i in ORDER] for rec in src_records(table)]


def poke_args(pokes):
    out = []
    for addr, data in pokes:
        for i in range(0, len(data), 0x400):
            out += ['--poke', '%#x=%s' % (addr + i, data[i:i + 0x400].hex())]
    return out


def run_install(table, stop, out_path, limit=200000):
    """把表 poke 进去，从 install 入口跑到 stop，返回 (json, dummy_bytes)。"""
    cmd = [sys.executable, str(DRIVER),
           '--pc', hex(FUNC), '--stop', hex(stop),
           '--reg', 'A0=%#x' % TABLE_POKE,
           '--reg', 'A1=%#x' % MODEL,
           '--reg', 'D0=%d' % struct.unpack_from('<H', table, TRAILER_OFF)[0],
           '--reg', 'SP=%#x' % SP,
           '--code', '%#x:%#x' % (CODE_LO, CODE_LEN),
           '--rw', '%#x:%#x' % (TABLE_POKE, 0x2000),
           '--rw', '%#x:%#x' % (MODEL, 0x1000),
           '--rw', '%#x:%#x' % (0x61E1F000, 0x2000),
           '--dump', '%#x:%#x' % (MODEL, DUMP_LEN),
           '--limit', str(limit), '--out', str(out_path)]
    cmd += poke_args([(TABLE_POKE, table)])
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if not Path(out_path).exists():
        sys.stderr.write(proc.stdout[-2000:] + '\n' + proc.stderr[-2000:] + '\n')
        raise HarnessError('harness 没有产出 %s' % out_path)
    j = json.loads(Path(out_path).read_text())
    if j.get('fault'):
        raise HarnessError('fault @ %s' % j['fault'])
    dump = None
    for m in j.get('memory', []):
        if m.get('address') == MODEL:
            raw = m.get('hex')
            dump = bytes.fromhex(raw) if raw else bytes(m.get('data') or [])
    if dump is None:
        raise HarnessError('dump 里没有 %#x：%s' % (MODEL, [hex(m.get('address', 0)) for m in j.get('memory', [])]))
    return j, dump


def dest_words(dump, rec):
    return list(struct.unpack_from('<%dH' % (DEST_LEN // 2), dump, DEST_BASE_IN_MODEL + DEST_LEN * rec))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--image', default=str(IMAGE))
    ap.add_argument('--table-offset', default=hex(TABLE_OFF),
                    help='COLOR 表在镜像里的文件偏移（默认 %#x）' % TABLE_OFF)
    ap.add_argument('--record', type=int, default=0, help='扰动哪一条记录（默认 0）')
    ap.add_argument('--field', type=int, default=6, help='扰动该记录的第几个 u16（0..13，默认 6）')
    ap.add_argument('--delta', type=int, default=100, help='加多少（默认 +100）')
    ap.add_argument('--stop', default=hex(STOP_OK))
    ap.add_argument('--limit', type=int, default=200000)
    ap.add_argument('--json', default=str(ROOT / '.tmp' / 'hsl_sim.json'))
    ap.add_argument('--md', help='把结果表写成 markdown')
    args = ap.parse_args()

    image = Path(args.image).read_bytes()
    table = load_table(image, int(args.table_offset, 0))

    trailer = struct.unpack_from('<H', table, TRAILER_OFF)[0]
    if trailer != 2015:
        print('[warn] 表尾 u16 = %d，不是 2015' % trailer)

    stop = int(args.stop, 0)
    if stop == STOP_HALF:
        print('[warn] stop=%#x 落在外循环计数器自增上，只会捕获 6 条' % STOP_HALF)

    out_dir = Path(args.json).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 实验 1：基线 ----
    j1, dump1 = run_install(table, stop, str(out_dir / 'hsl_base.json'), args.limit)
    exp = expected_out(table)
    base_rows = []
    mismatch = 0
    for r in range(N_REC):
        got = dest_words(dump1, r)
        ok = got == exp[r]
        mismatch += (not ok)
        base_rows.append({'rec': r, 'expect': exp[r], 'dump': got, 'match': ok})
    count_seen = struct.unpack_from('<H', dump1, COUNT_SLOT)[0] if dump1 and len(dump1) >= COUNT_SLOT + 2 else None

    # ---- 实验 2：单字段扰动 ----
    pert = bytearray(table)
    rec = args.record
    field = args.field
    old = struct.unpack_from('<H', pert, rec * REC_LEN + 2 * field)[0]
    new = (old + args.delta) & 0xFFFF
    struct.pack_into('<H', pert, rec * REC_LEN + 2 * field, new)
    j2, dump2 = run_install(pert, stop, str(out_dir / 'hsl_perturb.json'), args.limit)

    pert_rows = []
    changed = []
    for r in range(N_REC):
        a, b = dest_words(dump1, r), dest_words(dump2, r)
        diffs = [k for k in range(len(a)) if a[k] != b[k]]
        if diffs:
            changed.append((r, diffs))
        pert_rows.append({'rec': r, 'base': a, 'perturbed': b, 'changed': diffs})

    # 期望：只有 rec 里 ORDER 中 field 所在的那一格变
    exp_slot = ORDER.index(field) if field in ORDER else None
    expect_changed = [(rec, [exp_slot])] if exp_slot is not None else []
    cross_talk_ok = changed == expect_changed

    # ---- 打印 ----
    print('镜像      %s (%d B)' % (args.image, len(image)))
    print('表偏移    %#x  长度 %d  表尾 u16=%d' % (int(args.table_offset, 0), len(table), trailer))
    print('stop      %#x  status=%s attempted=%d' % (stop, j1['status'], j1['attempted']))
    print()
    print('实验 1：基线 12 条（目标按 0,1,2,3,4,5,10,11,12,6,7,8 取源）')
    for row in base_rows[:2]:
        print('  rec%d src=%s' % (row['rec'], [row['expect'][k] for k in range(12)]))
        print('       dump=%s  %s' % (row['dump'], 'OK' if row['match'] else 'MISMATCH'))
    print('  全部 12 条一致: %s (不符 %d 条)' % (mismatch == 0, mismatch))
    print('  model+%d (count) = %s' % (COUNT_SLOT, count_seen))
    print()
    print('实验 2：rec%d 的第 %d 个 u16: %d -> %d' % (rec, field, old, new))
    for r, diffs in changed[:3]:
        print('  rec%d 变化的槽位 %s' % (r, diffs))
    print('  只有预期那一格变化: %s' % cross_talk_ok)

    result = {
        'image': args.image, 'table_offset': int(args.table_offset, 0),
        'table_len': len(table), 'trailer': trailer, 'stop': stop,
        'status': j1['status'], 'attempted': j1['attempted'],
        'order': list(ORDER), 'unused_fields': UNUSED,
        'count_slot': count_seen,
        'baseline': base_rows, 'baseline_all_match': mismatch == 0,
        'perturb': {'record': rec, 'field': field, 'old': old, 'new': new,
                    'expected_changed': expect_changed, 'actual_changed': changed,
                    'no_crosstalk': cross_talk_ok, 'rows': pert_rows},
    }
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1), encoding='utf-8')
        print('\nJSON -> %s' % args.json)

    if args.md:
        write_md(Path(args.md), result)
        print('MD   -> %s' % args.md)

    return 0 if (mismatch == 0 and cross_talk_ok) else 1


def write_md(path, res):
    hdr = 'COLOR 表 install 复现结果（%s）' % Path(res['image']).name
    L = []
    L.append('## %s\n' % hdr)
    L.append('- 表偏移 `%#x`，长度 %d B，表尾 u16 = %d'
             % (res['table_offset'], res['table_len'], res['trailer']))
    L.append('- 入口 `0x604B922D`，stop `%#x`，status = `%s`，执行 %d 条指令'
             % (res['stop'], res['status'], res['attempted']))
    L.append('- 写入顺序 `0,1,2,3,4,5,10,11,12,6,7,8`；源记录里 %s 号 u16 不参与'
             % '、'.join(str(u) for u in res['unused_fields']))
    L.append('- 12 条全部逐项一致：**%s**\n' % ('是' if res['baseline_all_match'] else '否'))

    L.append('### 实验 1 · 基线（源值 → dump 值）\n')
    L.append('| rec | 源（按写入顺序） | dump（model+676 起） | 一致 |')
    L.append('|---:|---|---|---|')
    for row in res['baseline']:
        L.append('| %d | `%s` | `%s` | %s |'
                 % (row['rec'], ', '.join(str(v) for v in row['expect']),
                    ', '.join(str(v) for v in row['dump']), '✅' if row['match'] else '❌'))
    L.append('')
    L.append('### 实验 2 · 单字段扰动\n')
    p = res['perturb']
    L.append('只改第 %d 条记录第 %d 个 u16：%d → %d（其余字节不动）。\n'
             % (p['record'], p['field'], p['old'], p['new']))
    L.append('| rec | 扰动前 | 扰动后 | 变化槽位 |')
    L.append('|---:|---|---|---|')
    for row in p['rows']:
        L.append('| %d | `%s` | `%s` | %s |'
                 % (row['rec'], ', '.join(str(v) for v in row['base']),
                    ', '.join(str(v) for v in row['perturbed']),
                    ', '.join(str(k) for k in row['changed']) or '—'))
    L.append('')
    L.append('预期变化 `%s`，实测 `%s` → **%s**\n'
             % (p['expected_changed'], p['actual_changed'],
                '无串扰，一条记录 = 一个扇区' if p['no_crosstalk'] else '有串扰，需复查'))
    Path(path).write_text('\n'.join(L) + '\n', encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())
