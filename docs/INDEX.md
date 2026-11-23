# 文档索引（docs/）

> **唯一入口：`docs/STATUS.md`（唯一真相源，2026-11-21 起）。**
> 任何文档与 `STATUS.md` 冲突，以 `STATUS.md` 为准；旧文档顶部若带 `🗄 已冻结`，说明它只作历史，不要引用其中的结论。
> 读完 `STATUS.md` 再读它 §9 列的几份只读参考，其它都可以不看。


> **S5 收尾补记（2026-11-22 晚）**：新增/更新了 `docs/map/function-coverage.md`、`docs/map/zero-run-candidates.md`、`docs/map/ctx-schema.md`（+`.json`）、`docs/firmware/CAPABILITIES.md`、`docs/analysis/preview-flatten.md`；门从四道变五道 + 能力回归（见 `docs/WORK-LAW.md` §1/§5.3）；A1 桩位定为 `0x605F2600`。

## 现行（准确、可依赖）

- `WORK-LAW.md` —— **工作律（最高优先）**：反汇编 → 代码地图 → 模拟器（带 `--patch`）→ 五道门 → 一包一问
- `ROADMAP-longterm.md` —— **路线图 A–F**（价值×成本与推荐顺序）
- `RELEASE-NOTES-2026-11-24.md` —— **本次发行说明**：发行物仍为 RELEASE_v1；P4.0/P4.1 未并入；loader 冻结；工具集为 11-22 快照
- `../research/INDEX.md` —— **研究笔记**：10 篇失败/决策记录（M1/M1b、预览、HSL、loader、半按、门禁、N k 值、LOOKS、门 A、HSL 表）；先看这层，不要当教程

- `STATUS.md` —— **唯一真相源**：目标 / 硬规定 / 真机已验证 / 已判死 / 当前数值 / 卡上现状 / 墓碑 / 开放问题
- `METHOD-efficiency.md` —— 四道门、直接写卡规则、效率纪律
- `EVIDENCE-RULES.md` —— 证据分级判据（含"预览≠成片"）
- `map/dead-zones.md` —— 四个"看着是空、其实会炸"的区域
- `HANDOFF-S4.md` —— **技术底稿**：地址表 / 容器格式 / 通道清单（"现状"部分已失效）
- `firmware/successes/README.md`、`firmware/failures/README.md` —— 包的历史账本
- `map/INDEX.md`、`color/INDEX.md` —— 结构与色彩文档的分组索引

## 已冻结（只作历史，别引用结论）

`STATUS-2026-11-16/18/19.md`、`STATE-2026-11-16.md`、`HANDOFF-S1..S5`、`PLAN-2026-11-19/21.md`、`READING-NOTES-S5.md`、`SUMMARY.md`、`AUDIT-SELF-2026-11-16.md`。

## 以下为历史清单（保留用于查具体数字，状态列多为旧标）

## 归档与索引

- `map/INDEX.md` —— 22 份结构图的分组索引（主线链路 / 通道细节 / 区域与安全）
- `color/INDEX.md` —— 28 份色彩文档的分组索引（现行规格 / 分析推导）
- `chat/README.md` —— 对话产物（交接信、复盘、给读者的解释），不是技术结论
- `chat/WORKING-STYLE-S4.md` —— 第四代的工作方式（17 条习惯 + 3 条别学我）
- `plans/README.md` —— 所有计划（在用 + `done/` 已完成）
- `archive/README.md` —— 已取代的文档（旧交接、旧计划），附还原命令
- `experiments/INDEX.md` —— 34 份历史实验的速查表（含"是否已被推翻"）
- `DOCS-OPT-2026-11-17.md` —— 本次整理做了什么、没做什么、下次该做什么

## 包的历史账本

- `firmware/successes/README.md` —— 成功固件：目标 / 数字 / 证据等级 / 还能复用吗
- `firmware/failures/README.md` —— 失败固件：预期 / 失败表现 / 根因 / 还要不要上机

## 开工前必读（纪律）

- `EVIDENCE-RULES.md` —— 写结论前的四条判据
- `AGENT-BRIEF-PITFALLS.md` —— 已踩过的坑，派子智能体时第一段就贴它

## 阅读顺序（新会话）

1. `HANDOFF-S5.md` —— 五代交接：现状 / 硬性规定 / 八个坑 / 下一步（**先读这个**）
1b. `STATUS-2026-11-16.md` —— 四类能力现状（打通 / 进行中 / 搁置 / 不可用）
1c. `map/dead-zones.md` —— 四个"看着是空、其实会炸"的区域
1d. `map/chroma-mode-diff.md` —— 色度为什么只对 i-e 生效（结构已排除，真因见 S5 §5.3）
1e. `EVIDENCE-RULES.md` —— 结论的证据分级判据（含"预览≠成片"）
2. `HANDOFF-S4.md` —— 技术底稿（地址、容器、通道清单）
2. `SUMMARY.md` —— 总报告：系统认识、方法论、踩坑
3. `capability-inventory.md` —— 一条条通道的“能不能用 + 证据等级”
4. `docs/archive/looks/looks-spec.md` —— 配方参数（S77 要用的就这一份）
5. `docs/archive/audits/AUDIT-2026-11-14.md` —— 段表/X 推翻清单（判落点、判可用性）
6. `docs/archive/audits/DOCS-AUDIT-2026-11-15.md` —— 本次文档整理改了什么
7. `PEN-F-REFERENCE.md` —— 要对照别家怎么做色彩时读（只读参考，别照抄地址）

## 顶层：当前有效

| 文件 | 作用 | 状态 |
| :-- | :-- | :-- |
| `HANDOFF-S4.md` | 四代交接（权威）：目标、硬约束、三条通道、重分级清单、工具、下一步 | ✅ |
| `docs/archive/handoff/HANDOFF-S3.md` | 三代交接（**已被 S4 取代**，只当历史） | 🗄 |
| `evidence-regrade-2026-11-15.md` | 证据重分级：铁 / 未证实 / 没接到 三档，附单目录扰动方案（未出包） | ✅ |
| `map/tone-dirs.md` | **21 目录模型**：选择器 `0x604B8049` 真语义、handler→目录、每档独立影调的结构证据 | ✅ |
| `map/chroma-align.md` | 色度奇基址 `0x605D4829` 不 fault（100×`movbu`）；引擎是 NONSTRICT_ALIGNMENT | ✅ |
| `map/open-channels.md` | 矩阵/六轴/HSL 的卡点＝运行期门；三条候选补丁（未出包） | ✅ |
| `map/semantics-holes.md` | `(b0,b1)`=ucPictureGroup/Type、只派发一次、`ctx+0x30` 写者、`0x605DEF44`=bank2 | ✅ |
| `color/tone-calibration.md` | **tone 表口径定案**：10 位显示值曲线，索引=传感器码/白点；18% 灰在索引 22 | ✅ |
| `SUMMARY.md` | 总报告（2026-11-15 校准版） | ✅ |
| `capability-inventory.md` | 能力清单（2026-11-15 校准版） | ✅ |
| `docs/archive/looks/looks-spec.md` | 四套胶片观感的 tone 参数（**2026-11-15 起以 `docs/color/look-spec-*.md` 为准**；§三 六轴整节作废） | ⚠️ 顶部已指引 |
| `docs/archive/audits/AUDIT-2026-11-14.md` | 段表、X1–X4 推翻清单、落点核对 | ✅ |
| `docs/archive/audits/DOCS-AUDIT-2026-11-15.md` | 本次 md 整理记录 | ✅ |
| `docs/chat/lessons.md` | 方法论与纪律 | ✅ |
| `route-table-vs-patch.md` | 改表 vs 补丁的对比与决策 | ✅ |
| `ctx-copymap.md` | ctx 字段复制表（哪些存在、哪些不存在） | ✅ |
| `ctx-map.md` | ctx 偏移总表（含“旧判断作废”） | ✅ |
| `mode-key-map.md` | 档位键 ↔ 模式映射 | ⚠️ 六轴部分作废 |
| `incident-2026-11-14.md` | 黑屏事故与恢复记录 | ⚠️ “0x60DB0000 不加载”已推翻 |
| `hook-abi.md` | hook 的寄存器/栈 ABI 与调用约定 | ✅ |
| `hook-scaffold.md` | 补丁基础设施：工具链、空 hook、libsim | ✅ |
| `tone-selector.md` | tone 选择器访问器（`0x604B8049`） | ✅ |
| `PEN-F-REFERENCE.md` | PEN-F 色彩通路完整拆解（对照参考；地址只对 build 3101 有效） | ✅ |
| `research-external.md` | 外部项目调研（PEN-F/E-P7/鱼翅等） | 🗄 参考 |
| `handoff-notes-2026-11-13.md` | 11-13 外部仓库动态 | 🗄 |
| `docs/archive/handoff/HANDOFF.md` | 最初任务书 | 🗄 |
| `fetch_firmware.ps1` | 固件获取脚本 | — |

## 顶层：HSL 专题（整条链判死，结构留着备查）

| 文件 | 内容 | 状态 |
| :-- | :-- | :-- |
| `hsl-gates-2026-11-14.md` | **最终判词**：为什么四次全阴、标准档不调用 | ✅（判死依据） |
| `hsl-dataset-spec.md` | 数据集格式、12 描述符、10×96 项表 | ⚠️ 结构 ✅，用法 ❌ |
| `hsl-dataset-sim.md` | libsim 里的数据集灌入实验 | ⚠️ |
| `hsl-real-gate.md` | 真机门控分析 | ⚠️ |
| `hsl-flag-enable.md` | 开关字段与门 | ⚠️ |
| `hsl-hook-plan.md` | hook 换数据集方案（未采用） | 🗄 |
| `hsl-color-table-lab.md` | COLOR 表实验 | 🗄 |

## 顶层：六轴 / 矩阵 / 事故 / hook 实验

| 文件 | 内容 | 状态 |
| :-- | :-- | :-- |
| `sixaxis-layout.md` | 六轴记录布局（12×24B，A/B，cell） | ⚠️ 结构 ✅，生效 ❌ |
| `sixaxis-cellmap.md` | 六轴 cell 映射（四档归属） | ⚠️ 同上 |
| `n3-hook-tone.md` | N3 tone 换指针方案（五档分组） | 🗄 未采用 |
| `s72-fourlooks.md` | S72 四 look 设计 | 🗄 |
| `s72a-hook-test.md` | S72a hook 测试 | 🗄 |
| `s73-hsl-only.md` | S73 纯 HSL | ⚠️ 阴性 |
| `s75-docs/color/hsl-gate.md` | S75 HSL 门 | ⚠️ 阴性 |

## color/（色彩链路细节）

| 文件 | 主题 | 状态 |
| :-- | :-- | :-- |
| `docs/color/chroma-curve.md` | 色度块结构（**池 65 块，选择器引用 61**）与调法 | ⚠️ 块数旧写 61，已校正 |
| `docs/color/chroma-selector.md` | 色度选择器（8 组 × 12 槽）+ **S15 五模式探针：标准档只读 k=0** | ✅ 只读 k=0；8 组是 ART 路径 |
| `docs/color/tone-family-44.md` | tone 44 张的组织 | ✅ |
| `docs/color/tone-pointer.md` | `0x60600920` 指针表（21 组 × 8） | ✅ |
| `docs/color/tone-table-8slots.md` | 8 槽/基准-替代推断 | ⚠️ 部分作废 |
| `docs/color/sixaxis.md` | 六轴 12 条记录 | ⚠️ 结构 ✅，每档生效 ❌ |
| `docs/color/matrix-reader.md` | 矩阵读取者与合成 | ⚠️ 结构 ✅，标准档不吃 |
| `docs/color/matrix-ctx.md` | M_ctx 写者排查 | ⚠️ 同上 |
| `docs/color/per-mode-look.md` | 每档 look 的设想（六轴独立） | ⚠️ 六轴部分作废 |
| `docs/color/permode-scalars.md` | 每档标量（对比度/彩度）链路 | ✅ 标量链路，彩度死字段 |
| `docs/color/hs-curves.md` | H/S 实测曲线 | ⚠️ HSL 判死后的残留数据 |
| `docs/color/hsl-gate.md` / `docs/color/hsl-runtime.md` / `docs/color/hsl-writer.md` / `docs/color/hsl-upload-test.md` / `docs/color/hsl-phase1.md` | HSL 运行时/门/写者 | ⚠️ 结构 ✅，用法 ❌ |
| `docs/color/fuji-looks.md` | 11 个富士观感的量化表 | ✅ |
| `docs/color/fuji-lut-plan.md` | 富士 LUT 复刻计划 | ✅ |
| `docs/color/kodak-looks.md` | 柯达 9 份 HaldCLUT 反推（tone/矩阵/16 点折线误差、曝光偏移、建议四套） | ✅ |

## firmware/（固件与工具）

`docs/firmware/firmware-analysis.md`（容器/校验/布局）✅、`docs/firmware/sd-card.md`（卡目录）✅、`docs/firmware/patch-prep.md` / `docs/firmware/experiment-conditions.md` ✅、`docs/firmware/recovery-risk.md` ✅、`docs/firmware/boundary-probe-uploader.md` ✅、`docs/firmware/asm-improvement.md` ✅、`docs/firmware/rescan-2026-11-12.md` 🗄、`docs/firmware/emu-trace-2026-11-12.md` 🗄、`docs/firmware/resource-manager.md` 🗄、`docs/firmware/om-emu-setup.md` / `docs/firmware/simulator-harness.md` / `docs/firmware/probe-harness.md` ✅、`docs/firmware/reference-survey.md` / `docs/firmware/research.md` 🗄。

## plans/

| 文件 | 内容 | 状态 |
| :-- | :-- | :-- |
| `docs/plans/done/plan-2026-11-13.md` | 当日计划 | 🗄 |
| `docs/plans/done/plan-2026-11-14.md` | N1–N4 计划（N3/N4 已被 HANDOFF-S3 取代） | 🗄 |

## experiments/（按版本，S1–S67 及专题）

每个文件记录：目的、补丁地址与字节、sha256、真机结果、结论。
**注意**：S52/S54/S63/S64/S65 的“阴性”结论是 0x20 偏移期的假阴性，别当证据引用。
值得先读：`docs/experiments/s22-tone-table.md`（指针重定向 +2EV）、`docs/experiments/s30-film.md` / `docs/experiments/s36-cc3.md`（影调里程碑）、`s88` 相关（44 表四档全变，记录见 HANDOFF-S3）、`docs/experiments/review-mode-attribution.md`（翻案复盘）。

## archive/

`docs/archive/overview.md`、`docs/archive/state-2026-11-11.md`、`docs/archive/tech-story.md` —— 早期总览与流水账，仅备查 🗄。
| `docs/plans/done/PLAN-next-2026-11-15.md` | 三个堵点的裁决 + 出包前真机验证清单 | 🎯 当前 |
| `docs/firmware/PACKAGES.md` | **包台账**：A 段 27 条有结论的包（改了什么/验什么/成败/原因），B 段 FLASH-LOG 全量流水 |
| `docs/ROADMAP-longterm.md` | **路线图 A–F**（SD 文件 API / 构建后 hook / 写卡诊断 / 12 区 HSL / 多套预设切换 / 证据与回归），带价值×成本与推荐顺序 |
| `docs/analysis/infra-plan.md` | **基础设施方案（地基）**：CONFIG.BIN/DIAG.BIN 格式、启动读取、构建后挂载、显示侧单独修、M1–M4 里程碑与风险表 |
| `docs/reports/2026-11-22.md` | **日报**：今日完成（RELEASE_v1 / N 修复 / 预览链 / 台账 / 地基）、浪费统计、明日 P0–P5 计划 |
| `docs/refs/caduceus-e-p7.md` | 先例：Caduceus E-P7 的通读笔记（hook 纪律、SD 文件 API、display-only、grain 挂成片后） |
| `docs/refs/penf-oly-fw-analysis.md` | 先例：PEN-F / OM 分析仓库笔记（HSL 落点、patch.json 预检、对齐坑） |
| `docs/analysis/prior-art-mapping.md` | **先例对照**：可搬 10 条 / 需重定位 8 条 / 不可搬 6 条 + 每个钩子必查的四条路径 |
