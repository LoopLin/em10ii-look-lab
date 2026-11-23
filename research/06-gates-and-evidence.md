# 06 · 五道门与证据规则

> Status: 现行开发纪律；发行版只带 11-22 快照
> Evidence: 流程规则 + 多次事故复盘
> 适用范围：E-M10 II / E120 1400 0000 0000

## 问题

什么算“可以上卡”？  
“模拟跑过了”“EXIF 里有记录”“照片能拍”分别能证明什么？

## 原来的判断

早期只看“包能构建、模拟能跑、相机能开机”，结果 M1/M1b 连续把相机刷成重启循环。

## 规则

### 1. 模型门先于构建

每个包先写 `build/<name>.model.json`，至少五项：

- `question`：这个包只回答什么问题；
- `model`：预期机制；
- `prediction`：可观测的预测；
- `falsifier`：什么结果会推翻它；
- `observables`：至少两个可读量。

`model_gate.py --pkg build/<name>.BIN` 必须 `MODEL_OK`。写不出 falsifier 就是猜，不许构建。

### 2. 五道门 + 能力回归

工作区现行顺序：

1. `audit_beta_pack.py`
2. `patch_check.py --reach`
3. libsim（`closed:true`）
4. `second_verify.py --base stock`
5. `check_stub_region.py`（每个跳转目标都过）
6. `verify_caps.py` → `CAPS_OK n/n`

其中第 5/6 是后加的门：M1/M1b 的桩位事故、能力回归都需要它们。

### 3. 证据四等级

- **真机**：相机上实际发生，有可复查的输出；
- **模拟**：libsim/om-emu 实际执行到，但不等于真机；
- **静态已证**：字节/分支/调用点直接读出；
- **推理**：结构上像，但运行期没证。

模拟通过 ≠ 真机闭环。静态“可执行”不等于“运行时被映射”。

### 4. 一包一问

一个包只回答一个问题；不要在同一包里同时换 tone、矩阵、色度、UI。  
失败时才能知道是哪一项造成。

### 5. 写卡纪律

- 一包一根：从官方固件起，不在上一版上改；
- 卡上保留官方包；
- 写卡只走 `flash.py`/`flash_gate.py`，只认 `CARD_OK sha=…`；
- 写后回读；
- 额度账本 `cards.py` 是一代 12 张，不按天重置；
- 模型门不过、能力回归不过，不上卡。

## 反例

### M1/M1b

- `check_stub_region` 没做；
- 只信“文件里全 FF、0 引用”；
- 桩在装载器不拷贝的空档 → 两包都重启。

### T0bA

- 五门全过、模拟全过；
- 但读卡在 JPEG 写卡事务里重入 → 真机重启；
- 说明门禁能证明“改动受控”，不能证明“运行期上下文安全”。

### P0S-2

- 技术主路径大体实现；
- 但 F1 方向位、F2 落卡信号、F3 首读未检查；
- 审核判“不可上卡”，不是“代码不会跑”。

## 发行版差异

发行版是 **2026-11-22 快照**：

- `tools/` 是当时完整的 369 个脚本快照，不是“最小集合”；其中包含 `flash_gate.py`、`audit_beta_pack.py`、`patch_check.py`、`second_verify.py` 等；
- `model_gate.py`、`cards.py`、`flash.py`、`check_stub_region.py`、`verify_caps.py` 是 11-23 之后新增的，没有随发行版发布；
- 发行版按四道门快照执行；五道门 + 能力回归是开发工作区纪律。

不要把“发行版缺脚本”理解成“纪律取消”。

## 相关

- `docs/WORK-LAW.md`
- `docs/RELEASE-NOTES-2026-11-24.md`
- `research/01-m1-m1b-boot-loop.md`
- `research/04-looks-loader-freeze.md`
