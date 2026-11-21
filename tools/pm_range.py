"""Extract lines from .tmp/full_dis.txt whose leading address is within [lo,hi]."""
import sys

path = r'G:\em10fw\.tmp\full_dis.txt'
lo = int(sys.argv[1], 16)
hi = int(sys.argv[2], 16)

started = False
with open(path, 'r', encoding='utf-8', errors='replace') as f:
    for line in f:
        if len(line) < 9:
            continue
        a = line.split()[0]
        try:
            addr = int(a, 16)
        except ValueError:
            continue
        if addr < lo:
            continue
        if addr > hi:
            break
        started = True
        print(line.rstrip('\n'))
if not started:
    print('(no lines in range)')
