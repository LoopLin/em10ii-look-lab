# 鸣谢与方法参考

本项目站在一批公开逆向、模拟器和相机工具项目的肩膀上。这里写清楚**参考了什么**、**没有拿什么**，以及**哪些东西不能跨机型照搬**。

> 原则：只借方法、数据结构、测试纪律和产品形态；**不复制外部代码、固件、LUT、照片或大段字节**。跨机型地址一律不搬。
> 本文件不是法律意见；涉及具体代码再使用时，以各项目自己的许可证为准。
> 本仓库随附的许可证全文：`LICENSES/Apache-2.0.txt`、`LICENSES/MIT.txt`；GPL-3.0 见根目录 `LICENSE`。

## 同机型（E-M10 Mark II / E120 / fw 1.4）

### atx/fenykl

- 仓库：<https://github.com/atx/fenykl>
- 参考了什么：
  - 同机型 loader + 卡上插件（`FENYKL.BIN` / `FENYKL.TST`）的一次性加载与“删文件回 stock”的恢复边界；
  - R/B swap 的**无 hook 色彩矩阵重指**思路：CAS、关中断、uncached alias、restore；
  - `plugin.toml` 的 capability/body 组织方式。
- 没有拿什么：没有复制它的 loader、插件代码或任何地址；没有把它的实验性基础设施接进本仓库。
- 许可：仓库未写统一许可证，使用前需向作者核实；本仓库目前只参考思路。

### Meghnus/oly-truepic-harness

- 仓库：<https://github.com/Meghnus/oly-truepic-harness>
- 参考了什么：负控（检查必须能在旧包上失败）、lock-step 双跑对拍、内存 delta、并行 lane、真实路径模拟优先于手挑函数。
- 没有拿什么：没有移植它的 profile、解释器或测试代码；没有把“公开模拟器跑过 Mark II”当成我们的真机证据。
- 许可：未在本仓库核实；只参考方法。

### meukmachine/em10_mk1

- 仓库：<https://github.com/meukmachine/em10_mk1>
- 参考了什么：固件容器、内存图、RTOS 任务、USB/PTP、Wi-Fi API、myset 的资料索引。
- 没有拿什么：没有复制文档、表或代码；没有把 Mark I 的地址/布局搬到 E120。
- 许可：未在本仓库核实；只作资料索引。

## 同代 / 其他机型（只借方法）

### Meghnus/om10mi-mods

- 仓库：<https://github.com/Meghnus/om10mi-mods>
- 参考了什么：
  - “HSL 放在 Color Creator 这类非标准模式”的产品形态旁证；
  - Color Wheel 的 12 区 HSL 交互（H/S/L −7..+7、实时预览、配方保存）；
  - 把 LUT 拟合成“矩阵 + tone 曲线”的近似路线（相机没有 3D LUT 阶段）。
- 没有拿什么：
  - 没有复制它的代码、UI、菜单索引表、ART/Color Creator 槽位布局；
  - 没有把它的 14 个 film look 当成“Color Creator 内切换”，它们是 **Picture Mode 槽**；
  - 没有拿它证明 E-M10 II 标准档有 HSL——它恰好证明 HSL 在 Color Creator。
- 许可：未在本仓库核实；只参考产品形态与方法。

### cmdada/em10-fw

- 仓库：<https://github.com/cmdada/em10-fw>
- 参考了什么：
  - **load-padding**：镜像里 `0xFF` 填充的区段不会被 `c_start` 拷贝，写进去的代码运行时不在 RAM；
  - MN103 非对齐 32-bit load 会 fault，模拟器不检查会“离线能跑、真机不启动”；
  - 插件 loader、模拟器负控、恢复思路。
- 没有拿什么：没有复制代码或任何 Mark I 地址；没有把 Mark I 的布局当 E120。
- 许可：GPL-3.0；本仓库未复制其代码（相似度审计见 `docs/analysis/license-provenance.md`）。

### fishcu/oly_fw_analysis（PEN-F 先例）

- 仓库：<https://github.com/fishcu/oly_fw_analysis>
- 参考了什么：
  - PEN-F 的 HSL 落点与 image job 结构；
  - “HSL 可以是标准 job 的普通字段”的架构参考；
  - 容器/校验、patch 预检、证据分级的方法。
- 代码使用：本仓库的 `tools/oly_container.py` **衍生自该项目**的 `tools/oly_container.py`，并做本地修改；按 Apache-2.0 随本仓库分发（见 `NOTICE`）。
- 没有拿什么：没有搬 PEN-F 的 `604f7506` / `604f747f` 地址、job 布局或 HSL 实现；E-M10 II 没有同样的 COLOR job 货位。

### cakelesscoder/caduceus-ep7（E-P7 先例）

- 仓库：<https://github.com/cakelesscoder/caduceus-ep7>
- 参考了什么：
  - HSLFX / look 编辑器的产品形态；
  - `edits.json` 式“每条编辑带 expect 原厂字节”的预检；
  - 死区登记、store 审计、四条路径（capture/preview/recording/compact）验收；
  - 崩溃记录到 RAM、下次写卡。
- 没有拿什么：没有复制 HSLFX 整平台、EP7MOD 协议、grain 配方或任何地址。
- 许可：Apache-2.0；本仓库只以摘要形式参考，未复制代码。

### flatpanda/om-emu

- 仓库：<https://github.com/flatpanda/om-emu>
- 参考了什么：多机型 JS 模拟器；本仓库用它的流程/UI/模式盘能力做“是否执行到某 PC”的检查。
- 没有拿什么：没有把 HLE 的 lab JPEG 当成 EXIF/ISP 证据；没有把它的 0 命中当成“路径不存在”的反证。
- 许可：MIT；模拟器 fork 与 Windows 路径修补随分发保留原版权与许可。

## 数据与工具

### LibRaw

- 仓库：<https://github.com/LibRaw/LibRaw>
- 参考了什么：相机色彩矩阵的公开数值（事实数据），用于拟合的起点。
- 没有拿什么：没有分发 LibRaw 代码；没有把公开矩阵当成真机唯一真相。
- 许可：LGPL-2.1 / CDDL-1.0；仅引用数值，未分发代码。

### Olympus / OM Digital Solutions 固件

- 固件版权归其权利人所有。
- 本仓库**不包含**任何官方固件、固件镜像、官方 LUT 或受版权保护的素材；只包含描述改动的补丁文本与自制的测试图。
- 使用者需自行从官方渠道获取固件，并自行承担刷写风险。

## 没有拿的东西（清单）

- 任何跨机型绝对地址（PEN-F、E-P7、E-M10 Mark I 的都不行）；
- E-P7 的 HSLFX 整平台、EP7MOD 协议、grain 配方；
- PEN-F 的 COLOR job 布局/HSL 实现；
- E-M10 Mark I 的 UI 菜单索引表、ART/Color Creator 槽位布局；
- fenykl 的 loader/插件代码（只借模式）；
- 任何外部仓库的固件、解码结果或大段字节；
- 任何实拍照片、个人路径、设备序列号。

## 商标声明

本项目和 OM Digital Solutions、FUJIFILM、Eastman Kodak 无任何关联，未获其授权或认可。文中提及的产品名仅作指代性描述。
