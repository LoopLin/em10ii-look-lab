#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hsl_upload_test.py — 用离线 MN10300 harness 验证「10 张 HSL 表能不能喂进 ISP 上传通道」。

被测函数：0x60479960（LUT 上传）。本工具不碰真机、不写 SD 卡，只在本机模拟器里跑。

构造：
  outer 结构体 @0x61E10000   —— 真实调用里是 a1 指向的那个外层对象
      +0x00  标志位字（保持 0，避免走 cache-tag 分支）
      +0x0C  指向 inner 的指针（函数里 `mov (12,a0),d0` 取的就是它）
  inner 结构体 @0x61E11000   —— 即反汇编里的 (4,SP)，下面的偏移都相对它
      +0x3C4 门控（非零才上传 HSL 段）
      +0x41C / +0x420 / +0x1230  三张 tone 类表指针
      +0x1434 + 4k               10 条 HSL 表指针
  ISP 窗口 @0x9E00A400（dump 0x2800 字节）

10 张表统一按 u32[128] 铺（每项 = 0x40000000 | (k<<16) | i），这样
「按 32 位读」和「按 16 位读」得到的两组期望值完全不同，可以**实测**每条槽位的
位宽语义，而不是只按反汇编的结论去对答案。

用法：
  python tools/hsl_upload_test.py                # 正例 + 反例（门控关闭）
  python tools/hsl_upload_test.py --no-negative  # 只跑正例
  python tools/hsl_upload_test.py --json .tmp/hsl.json
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

BASE = 0x60021020          # 镜像基址
FUNC = 0x60479960          # LUT 上传函数入口
RET_HSL = 0x60479B8F       # 正常通道走完后的 ret（HSL 段之后）
RET_GATED = 0x60479AFC     # 门控关闭时的提前 ret
ISP = 0x9E00A400           # ISP 目标窗口
ISP_LEN = 0x2800           # 0x1400..0x2800 正好 10 条槽

OUTER = 0x61E10000
INNER = 0x61E11000
SP = 0x61E0F000
SCRATCH_LO, SCRATCH_LEN = 0x61E00000, 0x18000

TONE = 0x61E13000          # inner+0x1230 -> u16[256]，落到 ISP+0x000（16 位->32 位补齐）
P41C = 0x61E13400          # inner+0x41C -> u32[256]，落到 ISP+0xC00
P420 = 0x61E13800          # inner+0x420 -> u32[256]，落到 ISP+0x400
HSL_BASE = 0x61E14000      # 10 张 HSL 表，每张 128 项，间隔 0x200
HSL_STRIDE = 0x200

# (槽位, ISP 目标偏移, 反汇编读出的位宽)
SLOTS = [
    (0, 0x1400, 32), (1, 0x1600, 32), (2, 0x1800, 32), (3, 0x1A00, 16),
    (4, 0x1C00, 32), (5, 0x1E00, 16), (6, 0x2000, 16), (7, 0x2200, 16),
    (8, 0x2400, 16), (9, 0x2600, 16),
]
N = 128


def u32(v):
    return struct.pack('<I', v & 0xFFFFFFFF)


def hsl_value(k, i):
    """第 k 张第 i 项：高 16 位与低 16 位都非零，位宽读错立刻露馅。"""
    return 0x40000000 | (k << 16) | i


def build_pokes(gate_on=True, outer64=0x10):
    """返回 [(addr, bytes)]，全部用 --poke 交给 em10_run.py。

    gate_on: inner+0x3C4 写 1（上传）还是 0（跳过 HSL 段）。
    outer64: outer+0x64 的字；默认 0x10（bit4 置位）。反例里可以清零，
             用来验证「btst 0x4 到底作用在谁身上」。
    """
    # 布局自检：inner 结构体一直长到 +0x145C，别让表压到那上面
    _layout = [(OUTER, 0x68), (INNER, 0x145C), (TONE, 0x200), (P41C, 0x400),
               (P420, 0x400)] + [(HSL_BASE + HSL_STRIDE * k, HSL_STRIDE) for k in range(10)]
    _layout.sort()
    for (a, la), (b, _lb) in zip(_layout, _layout[1:]):
        assert a + la <= b, '布局重叠: %#x+%#x 压到 %#x' % (a, la, b)
    assert _layout[-1][0] + _layout[-1][1] <= SCRATCH_LO + SCRATCH_LEN, '越出 scratch'
    p = []
    p.append((OUTER + 0x00, u32(0)))            # 标志字：不触碰 cache-tag 分支
    p.append((OUTER + 0x0C, u32(INNER)))        # inner 指针
    p.append((OUTER + 0x34, u32(0)))            # 0 -> 标志位保持 1（见报告）
    p.append((OUTER + 0x64, u32(outer64)))      # bit4：生产者（0x604B934A）写进来的开关位
    p.append((INNER + 0x3C4, u32(1 if gate_on else 0)))   # 主门控
    p.append((INNER + 0x41C, u32(P41C)))
    p.append((INNER + 0x420, u32(P420)))
    p.append((INNER + 0x1230, u32(TONE)))
    for k, _off, _w in SLOTS:
        p.append((INNER + 0x1434 + 4 * k, u32(HSL_BASE + HSL_STRIDE * k)))

    # tone: u16[256]
    tone = b''.join(struct.pack('<H', 0x8000 | i) for i in range(256))
    p.append((TONE, tone))
    # +0x41C / +0x420: u32[256]
    p.append((P41C, b''.join(u32(0x30000000 | i) for i in range(256))))
    p.append((P420, b''.join(u32(0x20000000 | i) for i in range(256))))
    # 10 张 HSL 表
    for k, _off, _w in SLOTS:
        p.append((HSL_BASE + HSL_STRIDE * k,
                  b''.join(u32(hsl_value(k, i)) for i in range(N))))
    return p


def poke_args(pokes):
    out = []
    for addr, data in pokes:
        for i in range(0, len(data), 0x400):          # 切块，别撞命令行长度上限
            out += ['--poke', '%#x=%s' % (addr + i, data[i:i + 0x400].hex())]
    return out


def run(pokes, stop, rw, limit, out_path):
    cmd = [sys.executable, str(DRIVER), '--pc', hex(FUNC), '--stop', hex(stop),
           '--reg', 'A0=%#x' % ISP, '--reg', 'A1=%#x' % OUTER, '--reg', 'SP=%#x' % SP,
           '--code', '%#x:%#x' % (BASE, IMAGE.stat().st_size), '--limit', str(limit),
           '--out', str(out_path)]
    for lo, ln in rw:
        cmd += ['--rw', '%#x:%#x' % (lo, ln)]
    cmd += poke_args(pokes)
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if not Path(out_path).exists():
        sys.stderr.write(p.stdout[-2000:] + '\n' + p.stderr[-2000:] + '\n')
        raise SystemExit('harness 没有产出 %s' % out_path)
    return json.loads(Path(out_path).read_text())


def run_autostub(pokes, stop, limit, tag, pre_stubs=()):
    """按 probe_auto 的做法：跑到未映射地址就把那一页当普通 RAM 补上，重跑。

    pre_stubs 是上一次跑出来的页地址，直接带上可以省掉重复试探。
    """
    # 代码区只能用 --code 送（同址再给 --rw 引擎会报 Overlapping regions）
    rw = [(SCRATCH_LO, SCRATCH_LEN), (ISP, ISP_LEN)] + [(p, 0x1000) for p in pre_stubs]
    covered = [((BASE, IMAGE.stat().st_size))] + list(rw)
    stubs = []
    for step in range(20):
        j = run(pokes, stop, rw, limit, ROOT / '.tmp' / ('hsl_%s.json' % tag))
        if j.get('status') != 'memory_fault':
            return j, stubs
        addr = j['fault']['address']
        page = addr & ~0xFFF
        ft = j['fault']
        print('  [fault] pc=%#x addr=%#x size=%s kind=%s completed=%s' % (
            ft.get('pc', 0), addr, ft.get('size'), ft.get('kind'), j.get('completed')))
        if any(lo <= addr < lo + ln for lo, ln in covered):
            sys.stderr.write('已映射区内仍 fault: %#x\n' % addr)
            return j, stubs
        rw.append((page, 0x1000))
        covered.append((page, 0x1000))
        stubs.append(page)
        print('  [补边界] %#x -> 映射 %#x..%#x' % (addr, page, page + 0xFFF))
    return j, stubs


def capture(j, addr):
    for m in j.get('memory', []):
        if m.get('address') == addr:
            raw = m.get('hex') or ''
            return bytes.fromhex(raw) if raw else bytes(m.get('data') or [])
    return None


def words(buf, off, n=3):
    return [struct.unpack_from('<I', buf, off + 4 * i)[0] for i in range(n)]


def analyse(isp):
    """返回 (逐槽结果, 其余三段结果)。"""
    rows = []
    for k, off, wid in SLOTS:
        wrap = [struct.unpack_from('<I', isp, off + 4 * i)[0] for i in range(N)]
        v = [hsl_value(k, i) for i in range(N)]
        exp32 = v[:]                                        # 按 32 位读
        exp16 = [((v[i // 2] >> (16 * (i % 2))) & 0xFFFF) for i in range(N)]  # 按 16 位读
        m32 = wrap == exp32
        m16 = wrap == exp16
        exp = exp32 if wid == 32 else exp16
        rows.append({
            'slot': k, 'dest': off, 'width': wid,
            'match': wrap == exp,
            'measured': 32 if m32 else (16 if m16 else None),
            'expected': exp, 'actual': wrap,
        })
    # 其余三段
    tone_src = [0x8000 | i for i in range(256)]
    s41c = [0x30000000 | i for i in range(256)]
    s420 = [0x20000000 | i for i in range(256)]
    others = []
    for name, off, src, wid in (('tone(out+0x1230)', 0x000, tone_src, 16),
                                ('out+0x420', 0x400, s420, 32),
                                ('out+0x41C', 0xC00, s41c, 32)):
        got = [struct.unpack_from('<I', isp, off + 4 * i)[0] for i in range(256)]
        others.append({'name': name, 'dest': off, 'match': got == src, 'got': got})
    return rows, others


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=400000)
    ap.add_argument('--no-negative', action='store_true', help='不跑门控关闭的反例')
    ap.add_argument('--json', default='.tmp/hsl_upload_result.json')
    a = ap.parse_args()

    Path(ROOT / '.tmp').mkdir(exist_ok=True)
    print('=== 正例：gate(inner+0x3C4)=1，10 条 HSL 表全部挂好 ===')
    j, stubs = run_autostub(build_pokes(True), RET_HSL, a.limit, 'pos')
    regs = j.get('registers') or []
    print('  status=%s completed=%s PC=%#x' % (
        j.get('status'), j.get('completed'), regs[9] if len(regs) > 9 else 0))
    if stubs:
        print('  自动补边界: ' + ', '.join('%#x' % s for s in stubs))
    isp = capture(j, ISP)
    ok = j.get('status') == 'returned' and isp is not None

    rows, others = analyse(isp) if isp else ([], [])
    if isp:
        print()
        print('  槽位  ISP偏移   位宽  期望(前3项)                 实际(前3项)                 匹配  实测位宽')
        for r in rows:
            print('   %-4d %#06x   %-3d  %s  %s  %-4s  %s' % (
                r['slot'], r['dest'], r['width'],
                ' '.join('%08x' % x for x in r['expected'][:3]),
                ' '.join('%08x' % x for x in r['actual'][:3]),
                'OK' if r['match'] else 'FAIL',
                r['measured'] or '?'))
        print()
        for o in others:
            print('  %-18s +%#05x  %s' % (o['name'], o['dest'],
                                          'OK' if o['match'] else 'FAIL'))

    neg = None
    case_c = None
    if not a.no_negative:
        print()
        print('=== 反例 A：gate(inner+0x3C4)=0，其余完全相同 ===')
        j2, _ = run_autostub(build_pokes(False), RET_GATED, a.limit, 'neg', stubs)
        isp2 = capture(j2, ISP)
        # 门控只挡 HSL 段；tone / +0x420 / +0x41C 三段在门控之前就写过了
        hsl_nz = sum(1 for b in isp2[0x1400:0x2800] if b) if isp2 else -1
        low_nz = sum(1 for b in isp2[0x0000:0x1400] if b) if isp2 else -1
        neg = {'status': j2.get('status'),
               'pc': (j2.get('registers') or [0] * 32)[9],
               'hsl_nonzero': hsl_nz, 'low_nonzero': low_nz}
        print('  status=%s PC=%#x  ISP 0x1400..0x2800 非零字节=%s  0x0000..0x1400 非零字节=%s'
              % (neg['status'], neg['pc'], hsl_nz, low_nz))

        # 反例 B：gate=1（bit4 为 0），但 outer+0x64 的 bit4 清掉。
        # 若门控真是「out+0x3C4 的 bit4」，这里就该跳过；实测是照样上传。
        print()
        print('=== 反例 B：gate(inner+0x3C4)=1（bit4=0），outer+0x64=0 ===')
        j3, _ = run_autostub(build_pokes(True, outer64=0x0), RET_HSL, a.limit,
                             'nobit', stubs)
        isp3 = capture(j3, ISP)
        r3, _o3 = analyse(isp3) if isp3 else ([], [])
        case_c = {'status': j3.get('status'),
                  'hsl_nonzero': sum(1 for b in isp3[0x1400:0x2800] if b) if isp3 else -1,
                  'slots_matched': sum(1 for r in r3 if r['match'])}
        print('  status=%s  ISP 0x1400..0x2800 非零字节=%s  槽位匹配 %d/10'
              % (case_c['status'], case_c['hsl_nonzero'], case_c['slots_matched']))

    summary = {'positive_ok': ok and sum(1 for r in rows if r['match']) == 10,
               'status': j.get('status'), 'stubs': stubs,
               'slots': [{k: r[k] for k in ('slot', 'dest', 'width', 'match', 'measured')}
                         for r in rows],
               'others_ok': {o['name']: o['match'] for o in others},
               'negative': neg, 'no_bit4_case': case_c}
    Path(ROOT / a.json).write_text(json.dumps(summary, indent=1))
    nslots = sum(1 for r in rows if r['match'])
    print()
    print('汇总: 上传通道%s；槽位匹配 %d/10；反例%s' % (
        '可用' if ok else '不可用', nslots,
        '按预期被拦住（HSL 段 0 字节）' if (neg and neg['hsl_nonzero'] == 0) else
        ('未跑' if neg is None else '异常：HSL 段仍有写入')))
    print('明细 JSON: %s' % a.json)
    return 0 if (ok and nslots == 10) else 1


if __name__ == '__main__':
    sys.exit(main())
