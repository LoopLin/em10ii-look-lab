"""Scan full_dis.txt for a regex, print hits whose address is inside given regions."""
import re
import sys
from collections import deque

path = r'G:\em10fw\.tmp\full_dis.txt'
pat = re.compile(sys.argv[1])
ctx = int(sys.argv[2]) if len(sys.argv) > 2 else 2
regions = []
for a in sys.argv[3:]:
    lo, hi = a.split('-')
    regions.append((int(lo, 16), int(hi, 16)))

win = deque(maxlen=ctx + 1)
hits = 0
with open(path, 'r', encoding='utf-8', errors='replace') as f:
    for line in f:
        win.append(line.rstrip('\n'))
        parts = line.split()
        if not parts:
            continue
        try:
            addr = int(parts[0], 16)
        except ValueError:
            continue
        if not pat.search(line):
            continue
        if regions and not any(lo <= addr <= hi for lo, hi in regions):
            continue
        hits += 1
        print('--- hit %d' % hits)
        for L in win:
            print(L)
print('hits=%d' % hits)
