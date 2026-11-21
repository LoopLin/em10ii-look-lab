"""从 OM Digital Solutions 官方更新服务取原厂固件（纯 Python，避免 PowerShell 编码坑）。

用法: python tools/fetch_fw.py <MODEL_CODE> <out_dir> [start_version]
"""
import sys, os, time, urllib.request, hashlib, xml.etree.ElementTree as ET

MODEL = sys.argv[1] if len(sys.argv) > 1 else 'OLYE113000000000'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'refs/fw'
START = sys.argv[3] if len(sys.argv) > 3 else '00001000'

ts = time.strftime('%Y%m%d%H%M%S', time.gmtime())
xml = f"""<?xml version='1.0' encoding='utf-8'?><firmware_demand><revision>0001</revision><mac_address>00-00-00-00-00-00</mac_address><time>{ts}</time><language>ENU</language><application>FWUT</application><type>0000</type><version>00005000</version><os>WIN</os><country_code>0001</country_code><hardwares><firmup_mode>0</firmup_mode><hardware_info><hard_code>{MODEL}</hard_code><firmware_version>{START}</firmware_version><serial_no>000000000</serial_no></hardware_info></hardwares></firmware_demand>"""

req = urllib.request.Request('https://fw01.om-digitalsolutions.net/ww_inform/fcache.do',
                             data=xml.encode('utf-8'),
                             headers={'Content-Type': 'text/xml; Charset=UTF-8', 'User-Agent': 'Olympus'})
with urllib.request.urlopen(req, timeout=60) as r:
    body = r.read().decode('utf-8', 'replace')
root = ET.fromstring(body)
info = root.find('.//firmware_info')
if info is None:
    print('no firmware_info'); print(body[:500]); sys.exit(2)
print('model', info.findtext('model_name'), 'id', info.findtext('model_id'),
      'ver', info.findtext('version'), 'date', info.findtext('up_date'))
url = info.findtext('url'); name = info.findtext('file_name'); size = int(info.findtext('file_size'))
print('file', name, size, url)
os.makedirs(OUT, exist_ok=True)
dst = os.path.join(OUT, name)
if os.path.exists(dst) and os.path.getsize(dst) == size:
    print('exists, skip'); 
else:
    req2 = urllib.request.Request(url, headers={'User-Agent': 'Olympus'})
    with urllib.request.urlopen(req2, timeout=600) as r, open(dst + '.part', 'wb') as f:
        f.write(r.read())
    os.replace(dst + '.part', dst)
h = hashlib.sha256(open(dst, 'rb').read()).hexdigest()
print('sha256', h, 'size', os.path.getsize(dst))
