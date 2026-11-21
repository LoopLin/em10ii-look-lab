# 上传前整理与风险清单（v2，2026-11-22 审计版）

> v1（2026-11-15）已冻结到 `docs/archive/UPLOAD-CHECKLIST-2026-11-15.md`。
> 本版按 11-22 的工作区实况重写：目录体积、新增产物（成品包、台账、先例文档）、以及 v1 没覆盖的两类新风险（**照片里的第三方品牌**、**代码片段引用**）。
> 本文只做**分类与合规分析**；除"发行版目录"外不移动原工作区任何文件。

---

## 0. 一句话结论

**不要在原工作区上删删减减**：在 `G:\` 旁边新建一个干净目录，只拷"可以保留"的部分，把"修改后可以保留"的改完再拷，然后在新目录里 `git init`。原工作区留作档案馆。

风险只有六类：**官方固件 / 商业 LUT / 第三方代码与文档 / 个人照片（含第三方品牌）/ 商标词 / 措辞**。前四类靠"不进仓库"解决，后两类靠"改名与改写"解决。

---

## 1. 风险矩阵（v2）

| 编号 | 风险 | 来源 | 等级 | 处理 |
|---|---|---|---|---|
| L1 | 分发官方固件或其镜像 | OM Digital Solutions | **高** | 不进仓库；只发 `.patch` + 构建脚本 |
| L2 | 分发商业 LUT 或衍生 cube | FUJIFILM / Kodak | **高** | 不进仓库；只写"风格参考" |
| L3 | 用商标做预设名/项目名 | 商标权 | 中高 | 中性名 + 指代性声明 |
| L4 | 用 OLYMPUS / OM SYSTEM / E-M10 做项目名 | 商标权 | 中 | 项目名中性；描述性提及 + 声明 |
| L5 | 引用 GPL-3.0 代码未按 GPL 发布 | cmdada/em10-fw | 中 | 整体 GPL-3.0（沿用 v1 决定） |
| L6 | 引用 Apache-2.0 文档未保留 NOTICE | fishcu、caduceus | 低 | NOTICE 列清 + 注明"仅参考思路" |
| L7 | 个人照片 / EXIF / 路径泄露 | 隐私 | 中 | 照片一律不进包 |
| **L11（新）** | **照片里出现第三方品牌**（烟盒、饮料罐、乳品包装等） | 商标 + 肖像/产品 | **中高** | 照片不进包；文档里改用自制色卡或纯色块示意图 |
| **L12（新）** | **文档里大段引用第三方源码**（`file:line` + 代码块） | 版权 | 中 | 只留"结论 + 链接 + 不超过 10 行的必要片段" |
| L8 | 措辞被解读为"解析教程" | 规避技术措施类 | 中 | 中性措辞（§7.2） |
| L9 | 刷机损坏引发索赔 | 民事 | 中 | 免责声明 + 只列已验证补丁 |
| L10 | 平台条款 | GitHub AUP | 低 | 不涉恶意软件/DRM |

> 有利事实（沿用 v1）：本机固件**没有加密**，只有混淆（逐字节取反 + 16 字节列置换），我们做的是格式解析与参数定位，不是跳过密码。

---

## 2. 商标

规则同 v1，补充两条：

1. **我们的 look 名**（Ektachrome / Elite Color / Classic Chrome / Pro Neg Hi）在发行版里一律换成中性编号：`look-01 … look-04`，文档里写"风格参考对象"，文件与菜单名不带商标词。
2. **正文提到商标**必须有一次性声明（§7.1）；**文件名、目录名、预设名、EXIF 写死值**里一律不出现。

---

## 3. 版权：不进仓库的东西（按 11-22 实况更新）

| 路径 | 大小（11-22 实测） | 说明 |
|---|---:|---|
| `<official-firmware>/` | 67 MB | 官方固件本体 |
| `S1_backup/` | 266 MB | 官方包与早期成品 |
| `build/`（整目录） | **8.2 GB** | 全部成品 BIN、中间镜像、道具 |
| `photos/` | **7.7 GB** | 实拍 JPG/ORF（**含第三方品牌与个人桌面**） |
| `refs/`（整目录） | **4.1 GB** | 官方固件、商业 LUT、第三方仓库副本、实拍 |
| `.tmp/` | **3.4 GB** | 临时镜像、工具链、克隆的第三方仓库 |
| `analysis/*.decoded.bin` | 69 MB | 解码后的固件本体（等同于固件） |
| `gfx-eterna-55-3d-lut-v110/` | 225 MB | 商业 LUT 包 |
| `nohup.out`、`quit` | 小 | 运行残留，不入包 |

**成品包（RELEASE_v1 等）= 官方固件 + 几 KB 补丁**，分发它等于分发固件 ⇒ 只发 `.patch`。

### 3.1 补丁定义怎么写（沿用 v1，加一条）

- 只保留 `offset + new + old_sha256(前 8 字节)`；应用时用哈希校验原字节；
- **新（v2）**：`mkpatch.py` 的输出侧加"预检块"（旧字节哈希 + 长度 + 段名），让每个补丁自带完整性校验——这是 Caduceus 的做法，与机型无关。

---

## 4. 第三方项目（更新）

| 来源 | 许可 | 我们怎么用 | 要求 |
|---|---|---|---|
| `flatpanda/om-emu` | MIT | fork + Windows 路径修补 | 保留版权与许可；改动处注明 |
| `fishcu/oly_fw_analysis` | Apache-2.0 | 读文档、参考流程 | NOTICE；**不复制正文**，只链接 + 摘要 |
| `cakelesscoder/caduceus-ep7`（读的是 v0.34a） | Apache-2.0 | 读文档、参考纪律 | 同上；`docs/refs/caduceus-e-p7.md` 属摘要，发布前抽查引用比例 |
| `cmdada/em10-fw` | GPL-3.0 | 参考思路 | 整体 GPL-3.0；不整段拷贝 |
| LibRaw `colordata.cpp` | LGPL-2.1/CDDL | 只取矩阵数值 | 数值是事实，注明来源 |
| 奥林巴斯固件 / 商业 LUT | 版权 | 只分析不走私 | 用户自备；仓库不含 |

**v2 新增**：`docs/refs/*.md` 是我们写的摘要，但里面用了 `file:line` 与代码片段——发布前逐份抽查：**单份引用片段总行数 ≤ 30 行**，其余改成"见上游文件 X 第 N 行"的指针。

---

## 5. 分类（v2 白名单/黑名单）

### 5.1 不进包（黑名单，等同 v1 + 新增）

`<official-firmware>/`、`S1_backup/`、`build/`（除 `*.patch` / `*.json` 定义）、`photos/`、`refs/`、`.tmp/`、`gfx-eterna-55-3d-lut-v110/`、`analysis/*.decoded.bin`、`nohup.out`、`quit`、`**/__pycache__/`、`*.BIN`、`*.bin`、`*.ORF`、`*.JPG`、`*.cube`、`*.npy`

### 5.2 进包（白名单）

| 路径 | 说明 | 改不改 |
|---|---|---|
| `docs/STATUS.md`、`docs/INDEX.md`、`docs/METHOD-efficiency.md`、`docs/EVIDENCE-RULES.md` | 现行权威文档 | 改：去掉 `<official-firmware>/` 路径与盘符 |
| `docs/firmware/PACKAGES.md` | 包台账（结论 + 流水） | 改：删 sha 以外的 BIN 路径 |
| `docs/analysis/*.md` | 分析结论（含 `sd-file-api.md`、`prior-art-mapping.md`、`infra-plan.md`） | 改：hex 只留地址/长度；抽查第三方引用 |
| `docs/refs/*.md` | 两份先例摘要 | 改：片段 ≤30 行 + 只留链接 |
| `docs/reports/*.md` | 日报 | 改：去掉个人路径 |
| `tools/*.py` | 构建/校验/分析工具 | **改：盘符参数化、去 `<HOME>/ |
| `build/*.patch`、`build/*.sim.json`（仅定义类） | 补丁定义 | 改：路径相对化 |
| `assets/` 自制测试图（色卡、ramp） | 自制 | 确认无个人内容 |

### 5.3 必须改写的地方（v2 精确清单）

1. `<WORKSPACE>/`、`<ARCHIVE>/`、`L:\DCOLYMP`、`<CARD>/DCIM/`、`<CARD>/DCOLYMP/`、`<HOME>/ → 环境变量或相对路径；
2. `J:`/`I:` 盘符硬编码 → `--card` 参数（`tools/flash_gate.py`、`tools/compare_baseline.py`、`tools/read_probe.py`）；
3. 正文里的 `PB2xxxxx` 文件名、日期、个人设备信息 → 泛化（`<sample>.JPG`）；
4. 商标词（文件名/预设名）→ `look-01..04`；
5. `hex` 大段 → 地址 + 长度；
6. "解析/解除/跳过" → "格式解析/参数定位/校验和"。

---

## 6. 发行版目录结构（v2）

```
em10ii-look-lab/            # 中性名，不含厂商商标
├── README.md               # 简介 + 三条已知限制 + §7.1 声明
├── LICENSE                 # GPL-3.0 全文
├── NOTICE                  # 第三方出处
├── SAFETY.md               # 机型/版本限定、变砖免责、恢复步骤
├── QUICKSTART.md           # 自备官方包 → 校验 → 打补丁 → 拷卡
├── .gitignore
├── patches/                # *.patch + *.sim.json（不含 BIN）
├── tools/                  # 构建/校验/分析（盘符参数化）
└── docs/                   # STATUS / INDEX / analysis / refs / reports / firmware/PACKAGES.md
```

### 6.1 `.gitignore`（v2）

```
*.BIN
*.bin
*.decoded.bin
*.sim.bin
*.ORF
*.orf
*.JPG
*.jpg
*.cube
*.npy
<official-firmware>/
S1_backup/
build/
photos/
refs/
.tmp/
.tools/
.cache/
.dsh-progress/
gfx-eterna-55-3d-lut-v110/
nohup.out
quit
**/__pycache__/
```

---

## 7. 声明与措辞

### 7.1 README 顶部声明（v2）

```
本项目与 OM Digital Solutions、FUJIFILM、Eastman Kodak 无任何关联，
未获其授权或认可。文中提及的产品名仅作指代性描述。

本仓库不包含任何官方固件、官方 LUT 或受版权保护的素材。
使用者需自行从官方渠道获取固件，并自行承担全部风险。

刷写第三方固件可能导致设备损坏、失去保修、数据丢失。
本项目不提供任何形式的担保或技术支持。
仅适用于：Olympus E-M10 Mark II，固件 E120 1400 0000 0000。
```

### 7.2 措辞（沿用 v1，补两条）

| 不要写 | 改写为 |
|---|---|
| 解析 / 解除 / 跳过限制 | 格式解析 / 参数定位 / 完整性校验 |
| 一键刷机 | 补丁应用（需自备官方包） |
| **（v2）"我们已经完美复刻"** | "四档中两档达标（V/N），两档有已知偏差（M/P 高光 +20 码）" |
| **（v2）"支持所有 E-M10 系列"** | "仅 E-M10 II / E120 1400" |

---

## 8. 上传前自检（v2，命令可直接跑）

```bash
# 1 固件镜像与成品
find . -type f \( -name '*.BIN' -o -name '*.bin' \) | head
# 2 照片
find . -type f \( -name '*.ORF' -o -name '*.JPG' -o -name '*.jpg' \) | head
# 3 商业 LUT
find . -type f \( -name '*.cube' -o -name '*.xmp' \) | head
# 4 个人路径（v2 增加 I:/J: 盘与卡名）
grep -rnE '<WORKSPACE>/|<ARCHIVE>/|L:\\\\DCOLYMP|<CARD>/DCIM/|J:\\\\DC|c:\\\\Users\\\\<user-id>' \
  --include='*.py' --include='*.md' --include='*.json' . | head -30
# 5 大文件
find . -type f -size +5M | head
# 6 商标词（文件名）
find . -iname '*provia*' -o -iname '*velvia*' -o -iname '*astia*' -o -iname '*eterna*' \
  -o -iname '*acros*' -o -iname '*portra*' -o -iname '*ektar*'
# 7 商标词（正文，人工判断指代性）
grep -rniE 'provia|velvia|astia|classic chrome|eterna|acros|portra|ektar' --include='*.md' . | head -40
# 8（v2）引用片段长度抽查
grep -rn '```' docs/refs/*.md | wc -l
```

### 8.2 人工核对

- [ ] `du -sh .` **< 80 MB**
- [ ] 无 `*.BIN/*.ORF/*.JPG/*.cube`
- [ ] README 顶部有 §7.1 声明，且写明三条已知限制
- [ ] `LICENSE` GPL-3.0 全文；`NOTICE` 列全第三方
- [ ] 无个人姓名/邮箱/学校/公司；无 `<user-id>`
- [ ] 无"解析/解除/跳过"
- [ ] 预设与文件名无商标词
- [ ] 只列真机验证过的补丁；M/P 偏差写明
- [ ] `docs/refs/*` 单份引用 ≤30 行
- [ ] `tools/` 全部盘符参数化，默认值不再是 `J:`

---

## 9. 发布后应对（沿用 v1）

- 收到 DMCA / 律师函：**先下架争议文件，不争论**；保留补丁与文档，删除争议内容，回复"已移除"。
- 不在 issue 里对抗；不公开对方邮件。
- 留一份"发布时文件清单 + 来源"记录，用于证明"哪些是自己写的"。

---

## 10. v1 → v2 变更记录

| 项 | v1（11-15） | v2（11-22） |
|---|---|---|
| 工作区体积 | 18 GB | **24 GB**（build 8.2 / photos 7.7 / refs 4.1 / .tmp 3.4） |
| 新风险 | — | **L11 照片内第三方品牌**、**L12 文档大段引用源码** |
| 补丁定义 | offset+new+old_sha | 同 + **预检块**（旧字节哈希/长度/段名） |
| 第三方 | 3 家 | + caduceus **v0.34a**、PEN-F 仓库；refs 改成摘要 |
| 目录 | `em10ii-looks` | `em10ii-look-lab`（中性）+ `SAFETY.md` / `QUICKSTART.md` |
| 自检 | 7 条 | 8 条（+ 引用片段长度） |
| 已知限制 | 未写 | **必须写**：V/N 达标、M/P 高光 +20、预览待修、色度 ≤6 |
