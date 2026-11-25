# docs/ 目录说明

> 这是发行版的**文档入口**。只列真实存在的文件；旧的长索引已停用。

## 按目的找

### 用户先读

- [`../README.md`](../README.md) —— 项目入口、四条上机提醒、已知限制
- [`../SAFETY.md`](../SAFETY.md) —— 适用机型、变砖风险、恢复步骤
- [`../QUICKSTART.md`](../QUICKSTART.md) —— 五步上手
- [`../LOOKS.md`](../LOOKS.md) —— 四款外观与适用场景
- [`../research/INDEX.md`](../research/INDEX.md) —— 研究笔记：失败与决策记录，不是教程

### 现行状态与纪律

- [`STATUS.md`](STATUS.md) —— 当前发行快照的状态、真机已验证、已判死、限制
- [`WORK-LAW.md`](WORK-LAW.md) —— 方法红线：反汇编 → 代码地图 → 模拟器 → 五道门 → 一包一问
- [`ROADMAP-longterm.md`](ROADMAP-longterm.md) —— 长期 backlog（不是已发布能力）
- [`METHOD-efficiency.md`](METHOD-efficiency.md) —— 效率方法

### 发行与维护

- [`release/UPLOAD-CHECKLIST.md`](release/UPLOAD-CHECKLIST.md) —— **仓库结构与发布规则（现行）**
- [`release/RELEASE-CHECKLIST.md`](release/RELEASE-CHECKLIST.md) —— 正式包验收判据
- [`release/PACKAGING-REPORT-2026-11-22.md`](release/PACKAGING-REPORT-2026-11-22.md) —— 2026-11-22 打包记录（历史；路径以本文为准）
- [`releases/2026-11-26.md`](releases/2026-11-26.md) —— 最新研究快照（发行物未变）
- [`releases/2026-11-24.md`](releases/2026-11-24.md) —— 上一版发行说明
- [`releases/README.md`](releases/README.md) —— 发行历史索引
- [`dev/DEV-SCRIPTS-2026-11-22.md`](dev/DEV-SCRIPTS-2026-11-22.md) —— 2026-11-22 的历史脚本清单（历史；脚本已移出发行树）

### 技术分析

- [`analysis/`](analysis/) —— 选留的分析：合法来源、先例、SD API、M1 探针、验收
- [`map/`](map/) —— 选留的链路图（当前只有 `preview-chain.md`）
- [`color/`](color/) —— 选留的色彩校准记录
- [`firmware/`](firmware/) —— `PACKAGES.md`（包台账）与 `FLASH-LOG.md`（刷机日志）
- [`refs/`](refs/) —— Caduceus / PEN-F 的先例摘要（不是原文）

## 目录约定

```text
docs/
  README.md             本文件，唯一文档入口
  INDEX.md              旧入口，只做跳转
  STATUS.md             现行状态
  WORK-LAW.md           方法红线
  ROADMAP-longterm.md   长期 backlog
  METHOD-efficiency.md  效率方法
  release/              发布规则、验收清单、历史打包报告
  releases/             按日期归档的发行说明
  dev/                  历史开发资料（不随发行流程更新）
  analysis/             精选分析
  color/                精选色彩记录
  firmware/             包台账 / 刷机日志
  map/                  精选链路图
  refs/                 先例摘要
```

## 不放进来的东西

- 固件、成品 BIN、照片、商业 LUT、第三方全文；
- 个人路径、卡符、设备序列号；
- 未验证的推断冒充结论；
- 369 个历史脚本的清单式镜像（历史脚本在工作区/git 历史，不随发行版）。

## 新增文档的规则

1. 先判断它是“用户文档 / 发行规则 / 技术分析 / 研究笔记”；
2. 放进对应目录，不要堆在 `docs/` 根；
3. 更新本文件与 `docs/releases/README.md`（如果是发行相关）；
4. 检查没有个人路径、照片、固件片段、第三方长引用；
5. 结论必须与 `STATUS.md` 或 `research/` 的证据等级一致。
