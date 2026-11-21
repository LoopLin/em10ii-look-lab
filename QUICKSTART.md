# QUICKSTART — 五步把补丁打到自己卡上

前提：**Olympus E-M10 Mark II**，固件 **`E120 1400 0000 0000`**。其它机型/版本请直接停手，先看 [`SAFETY.md`](SAFETY.md)。

下面命令都在本包根目录执行；Windows 下把 `python` 换成你实际的解释器（3.9+）。

## 第 1 步：取官方包

从官方渠道下载 E-M10 II 的固件更新包（版本 `E120 1400 0000 0000`），解出里面的：

```
OLY_E_120_1400_0000_0000.BIN
```

放到本包根目录。**不要**用任何第三方转包、网盘镜像或「已改好」的文件。

## 第 2 步：校验 sha256

```bash
python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" OLY_E_120_1400_0000_0000.BIN
```

必须输出：

```
ff9d302318ecf6e06c0b31c181fc6a185e93623e819749db31cec7977fa92052
```

同时确认大小正好 **69,599,232 字节**：

```bash
python -c "import os,sys;print(os.path.getsize(sys.argv[1]))" OLY_E_120_1400_0000_0000.BIN
```

对不上就到此为止——补丁只对这一个字节版本有效。

## 第 3 步：应用补丁

```bash
python tools/mkpatch.py OLY_E_120_1400_0000_0000.BIN \
  --out E1209999.BIN \
  --patch-file patches/RELEASE_v1.patch \
  --expect-sha256 ff9d302318ecf6e06c0b31c181fc6a185e93623e819749db31cec7977fa92052
```

成功后会打印容器校验结果（signature / model / size%16 / header-trailer / additive checksum 全通过），并在当前目录生成 `E1209999.BIN`。

参考值：产物 sha256 应为 `0b08a232cee544c5724e8ed661fcad97d75d1e9f9886229c30ea01d2280cafad`（与 `patches/RELEASE_v1.BIN.sim.json` 一致）。想先看会改哪些字节，加 `--dry-run`。

> 这一步只在本地生成文件，不接触相机。

## 第 4 步：拷到卡上

1. 用读卡器打开存储卡（建议先备份 `DCIM/`）。
2. 在**卡根目录**建一个名为 `DCOLYMP` 的文件夹（大写，拼写不要错）。
3. 把 `E1209999.BIN` 拷进去，最终路径是：

```
<卡根>\DCOLYMP\E1209999.BIN
```

4. 安全弹出卡，插回相机。相机里**不要**再重命名、不要放到 `DCIM/` 下。

## 第 5 步：开机验证

1. 电量 ≥ 90%，接入电源，中途不断电。
2. 开机，进菜单看色彩/风格相关选项：四档风格应显示为 `look-01 … look-04`（风格参考），而不是厂商预设名。
3. 拍一张，看 JPEG 的影调与色彩是否落在预期范围；EXIF `UserComment` 里会有 `k=N` 标记（N = 档位）。
4. 想回退：关机 → 取卡 → 删掉 `DCOLYMP\E1209999.BIN` → 走官方恢复流程（`SAFETY.md` 的「出问题怎么恢复」）。

预期效果与三条已知限制（M/P 高光偏亮约 20 码、预览待真机确认、色度 ΔE00 ≤6）写在 [`README.md`](README.md) 里，验证时按那个基准看，不要按「完美复刻」的预期看。

## 可选：核对产物

```bash
python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" E1209999.BIN
```

应为 `0b08a232cee544c5724e8ed661fcad97d75d1e9f9886229c30ea01d2280cafad`（`patches/RELEASE_v1.BIN.sim.json` 里记的就是这个值）。这一步只是自检，与刷不刷机无关——不同补丁产物不同，换补丁时以补丁自带的记录为准。
