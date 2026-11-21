"""向量化找 3x3 色彩矩阵候选（分块，避免爆内存）。"""
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

IMG = np.fromfile(r"G:\em10fw\analysis\00_0100_40300020.decoded.bin", dtype=np.uint8)
BASE = 0x60021020
a = np.frombuffer(IMG.tobytes(), dtype="<i2").astype(np.int32)
n = a.size
print("s16 count", n, flush=True)

out = []
CH = 1_000_000
for start in range(0, n - 9, CH):
    end = min(start + CH, n - 9)
    w = sliding_window_view(a[start:end + 8], 9).astype(np.int32)   # (end-start, 9)
    m = w.reshape(-1, 3, 3)
    rs = m.sum(2)
    diag = np.stack([m[:, 0, 0], m[:, 1, 1], m[:, 2, 2]], 1)
    for scale in (1024, 4096, 256, 16384, 512):
        ok = (np.abs(rs - scale) < scale * 0.02).all(1)
        ok &= (np.abs(m).max((1, 2)) < scale * 1.3)
        ok &= (diag > scale * 0.2).all(1)
        off = np.abs(m - np.eye(3, dtype=np.int32) * diag[:, None, :]).max((1, 2))
        ok &= off > scale * 0.01
        idx = np.nonzero(ok)[0]
        for i in idx:
            out.append((int(scale), int(BASE + 2 * (start + i)), m[i].tolist()))

print("candidates:", len(out), flush=True)
for scale, addr, mat in out[:80]:
    print(f"{addr:#010x} scale={scale} {mat}", flush=True)
