import sys, hashlib
sys.path.insert(0,'tools')
import oly_container as oc
BASE=0x60021020; END=0x60DA5000
stock=oc.parse(open('stock/OLY_E_120_1400_0000_0000.BIN','rb').read())[0].payload
SITES={
 'SU钩':(0x60479A2B,5),'SD钩':(0x604B811D,5),'SEL-B':(0x604B4B5D,6),'SEL-C':(0x604B4CAD,6),
 'HSL-76':(0x604B9756,5),'HSL-51':(0x604B9751,21),'HSL-4D':(0x604B974D,5),'HSL-F9':(0x604B97F9,7),
 'SU桩':(0x605DECD4,16),'SU桩2':(0x605DED80,16),'SEL桩':(0x605DED1C,16),'SD桩':(0x605DED44,16),
 'EXIF':(0x61894C90,12)}
def zone(a,n):
    b=a+n-1
    if a>=0x60DE0000 or a>=0x60E00000: return('禁用:未加载区',100)
    if b>=END: return('禁用:越段尾',100)
    if b>=0x60DA4E00: return('禁用:段尾栈区',100)
    if a>=0x605DEF44 and b<0x605DF044: return('禁用:bank2',100)
    if a>=0x603D7B04 and b<0x603D7F00: return('禁用:0x603D7B04',100)
    if 0x605DAF44<=a and b<0x605DB744: return('数据:tone LUT',0)
    if 0x605F0000<=a and b<0x605F7000: return('数据:n4/n11/mask',0)
    if 0x605E0000<=a and b<0x605F0000 and a<0x605F0000: return('数据:其他已知区',0)
    if 0x61894C90<=a and b<0x61894D20: return('数据:EXIF文本',0)
    if 0x605DEB44<=a and b<0x605DED44: return('槽52',0)
    if 0x605DED44<=a and b<0x605DEE0C: return('槽53前200B',0)
    if 0x605DEE0C<=a and b<0x605DEF44: return('槽53 ART尾(预算外)',2)
    if 0x601ADE04<=a and b<0x601ADE40: return('口袋0x601ADE04',2)
    if 0x604B974D<=a and b<0x604B9800: return('代码:HSL门区',1)
    if 0x604B4B00<=a and b<0x604B4D00: return('代码:选择器区',1)
    if 0x60479A00<=a and b<0x60479B00: return('代码:上传器区',1)
    if 0x604B8100<=a and b<0x604B8200: return('代码:键派发区',1)
    if 0x6066159E<=a and b<0x60661600: return('口袋0x6066159E(仅模拟)',2)
    return('未分类',3)
RESULTS={
 'B71_hsl_hook':'无效果','B72_hsl_gate':'无效果','B73_hsl_probe':'探针正常','B74_hsl_bit':'无效果',
 'B75_hsl_trace':'开机循环重启','B76_hsl_trace2':'正常','B77_hsl_content':'无效果','B78_hsl_force':'无效果(无效包)',
 'B79_hsl_force2':'无效果','B80_hsl_cc00':'无效果+i-Auto卡死','B81_hsl_probe':'探针正常',
 'B82_hsl_gateprobe':'四档花屏+死机','B83_hsl_gateprobe':'四档花屏+死机','B84_hsl_hookonly':'四档花屏+死机','B85_hsl_tramp':'未上机'}
rows=[]
for name in sys.argv[1:]:
    img=oc.parse(open('build/%s.BIN'%name,'rb').read())[0].payload
    diffs=[i for i in range(len(img)) if img[i]!=stock[i]]
    spans=[]
    if diffs:
        s=prev=diffs[0]
        for i in diffs[1:]:
            if i!=prev+1: spans.append((BASE+s,prev-s+1)); s=i
            prev=i
        spans.append((BASE+s,prev-s+1))
    sites=[k for k,(a,n) in SITES.items() if img[a-BASE:a-BASE+n]!=stock[a-BASE:a-BASE+n]]
    rows.append((name,spans,sites))
out=['# 近期固件审计（B71–B85，2026-11-18）','',
 '审计对象：色度收口（B69）之后的所有包。只读，不改包，不写卡。',
 '判据：`docs/map/dead-zones.md` §5–§7 + `docs/METHOD-efficiency.md` 落点启用清单。','',
 '注：`口袋0x601ADE04` 判 WARN 是“只在矩阵路径真机验证过”；B82–B85 把它用在 HSL 门路径，属未验证。','']
out.append('| 包 | 差异段 | 总字节 | 关键补丁点 | 真机结果 | 结论 |'); out.append('|:--|--:|--:|:--|:--|:--|')
for name,spans,sites in rows:
    worst=max([zone(a,n)[1] for a,n in spans], default=0)
    if 'SU钩' not in sites and not name.startswith(('B81','B82','B83','B84','B85')):
        worst=max(worst,100); reason='缺基线补丁(SU钩)'
    res=RESULTS.get(name,''); v='FAIL' if (worst>=100 or '花屏' in res or '循环重启' in res or '卡死' in res) else ('WARN' if worst>=2 else 'OK')
    out.append('| %s | %d | %d | %s | %s | %s |'%(name,len(spans),sum(n for _,n in spans),', '.join(sites),RESULTS.get(name,'—'),v))
out.append('')
for name,spans,sites in rows:
    worst=max([zone(a,n)[1] for a,n in spans], default=0)
    missing = 'SU钩' not in sites and not name.startswith(('B81','B82','B83','B84','B85'))
    res=RESULTS.get(name,''); v='FAIL' if (worst>=100 or missing or '花屏' in res or '循环重启' in res) else ('WARN' if worst>=2 else 'OK')
    out.append('## %s — %s'%(name,v)); out.append('')
    if missing: out.append('- **缺基线补丁**：SU 钩未改，等于在 stock 上只加 HSL（B78 事故同型）。'); out.append('')
    out.append('| 地址 | 长度 | 区域 | 判定 |'); out.append('|:--|--:|:--|:--|')
    for a,n in spans[:25]:
        z,lv=zone(a,n); mark='FAIL' if lv>=100 else ('WARN' if lv>=2 else 'OK')
        out.append('| 0x%08X | %d | %s | %s |'%(a,n,z,mark))
    if len(spans)>25: out.append('| … | | 其余 %d 段（多为数据岛连续段） | |'%(len(spans)-25))
    out.append('')
open('docs/audit/AUDIT-2026-11-18.md','w',encoding='utf-8').write('\n'.join(out)+'\n')
print('\n'.join(out[:36]))
