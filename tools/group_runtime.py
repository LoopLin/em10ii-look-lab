import json, subprocess, sys, re
BASE=0x605D8344; STEP=0x200
rows=[]
for g in range(21):
    ga=0x60600920+0x20*g
    for d1 in (0,2,3,4):
        cmd=[sys.executable,'tools/em10_run.py','--pc','0x604B8049','--stop','0x604B8095',
             '--reg',f'A0=0x{ga:08X}','--reg',f'D1={d1}',
             '--code','0x604B8000:0x300','--rw','0x60600000:0x1000','--limit','2000']
        try:
            out=subprocess.run(cmd,capture_output=True,text=True,timeout=60).stdout
        except subprocess.TimeoutExpired:
            rows.append({'g':g,'d1':d1,'err':'timeout'}); continue
        m=re.search(r'A0=([0-9a-fA-F]{8})',out)
        st=re.search(r'"status":\s*"(\w+)"',out)
        if not m:
            rows.append({'g':g,'d1':d1,'err':st.group(1) if st else 'no-output'}); continue
        a0=int(m.group(1),16)
        k=(a0-BASE)//STEP if BASE<=a0<BASE+STEP*64 else None
        rows.append({'g':g,'d1':d1,'a0':f'0x{a0:08X}','table':k,'status':st.group(1) if st else '?'})
json.dump(rows,open('docs/map/group-runtime.json','w',encoding='utf-8'),indent=1)
print('rows',len(rows),'errors',sum(1 for r in rows if 'err' in r))
