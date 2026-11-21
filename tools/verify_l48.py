# -*- coding: utf-8 -*-
"""静态校验 L48：相对 L47 只应改变四张 tone 表（端点归一）。"""
import sys, struct, json, hashlib
sys.path.insert(0, 'tools')
import oly_container as oc

BASE = 0x60021020
TONE = [('V', 0x605DAF44), ('N', 0x605DB144), ('M', 0x605DB344), ('P', 0x605DB544)]
a47 = open('build/L47_nfunnel.BIN', 'rb').read()
a48 = open('build/L48_endpoint.BIN', 'rb').read()
ok = True
p47 = oc.parse(a47)[0].payload
p48 = oc.parse(a48)[0].payload
diff = [i for i in range(len(p47)) if p47[i] != p48[i]]
lo = min(TONE, key=lambda x: x[1])[1] - BASE
hi = max(TONE, key=lambda x: x[1])[1] - BASE + 512
outside = [i for i in diff if not (lo <= i < hi)]
print('diff bytes:', len(diff), 'outside tone blocks:', len(outside))
ok &= not outside
for nm, a in TONE:
    off = a - BASE
    t = struct.unpack_from('<256H', p48, off)
    mono = all(t[i] <= t[i+1] for i in range(255))
    print('%s T0=%d T255=%d mono=%s' % (nm, t[0], t[255], mono))
    ok &= (t[0] == 0 and t[255] == 1023 and mono)
res = {'scope': '静态：L48 相对 L47 仅四张 tone 表变化 + 端点/单调性检查；无 ISP 仿真',
       'closed': bool(ok), 'diff_bytes': len(diff), 'outside': len(outside),
       'sha256': hashlib.sha256(a48).hexdigest()}
json.dump(res, open('build/L48_endpoint.BIN.sim.json', 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
print('closed=', ok)
