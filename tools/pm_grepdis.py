"""Search .tmp/full_dis.txt (huge linear disassembly) for a regex, with line context."""
import re
import sys
from collections import deque

path = r'G:\em10fw\.tmp\full_dis.txt'
pat = re.compile(sys.argv[1])
ctx = int(sys.argv[2]) if len(sys.argv) > 2 else 6
maxhits = int(sys.argv[3]) if len(sys.argv) > 3 else 60

win = deque(maxlen=ctx + 1)
hits = 0
with open(path, 'r', encoding='utf-8', errors='replace') as f:
    for line in f:
        win.append(line.rstrip('\n'))
        if pat.search(line):
            hits += 1
            print('--- hit %d' % hits)
            for L in win:
                print(L)
            if hits >= maxhits:
                break
print('hits=%d' % hits)
