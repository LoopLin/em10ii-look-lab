# 仓库结构与发布规则（v3，2026-11-24）

> 这份文件定义**发行仓库长什么样、每次怎么发**。
> 工作区的操作规则见 `docs/HANDOFF-GITHUB.md`（工作区路径）；两边是同一套结构，不能各写各的。
>
> v2（2026-11-22）已冻结在 git 历史；本版按 2026-11-24 重构后的实际结构重写。

---

## 0. 一句话

**发行版 = 补丁定义 + 最小工具 + 真实文档 + 研究笔记。**
不含固件、成品 BIN、照片、商业 LUT、历史脚本；所有文档链接必须指向真实文件。

---

## 1. 标准仓库结构

```text
em10ii-look-lab/
├── README.md                  项目入口、上机前四条、已知限制
├── LICENSE                    GPL-3.0 全文
├── NOTICE                     第三方出处与许可
├── AI-DISCLOSURE.md           AI 参与方式、证据等级
├── SAFETY.md                  机型/版本限定、变砖风险、恢复步骤
├── QUICKSTART.md              五步上手
├── LOOKS.md                   四款外观与适用场景
├── .gitignore                 固件/照片/LUT/构建目录
├── patches/
│   ├── RELEASE_v1.patch       唯一发行补丁
│   └── *.sim.json             补丁的静态校验记录（可选）
├── tools/
│   ├── README.md              工具入口说明（先看这个）
│   └── 12 个正式脚本          见 §3
├── docs/
│   ├── README.md              唯一文档入口
│   ├── INDEX.md               旧入口，只做跳转
│   ├── STATUS.md              现行状态（唯一真相源）
│   ├── WORK-LAW.md            方法红线
│   ├── ROADMAP-longterm.md    长期 backlog（不是已发布能力）
│   ├── METHOD-efficiency.md   效率方法
│   ├── release/
│   │   ├── UPLOAD-CHECKLIST.md        本文件
│   │   ├── RELEASE-CHECKLIST.md       正式包验收判据
│   │   └── PACKAGING-REPORT-YYYY-MM-DD.md  历史打包记录
│   ├── releases/
│   │   ├── README.md          发行历史索引
│   │   └── YYYY-MM-DD.md      每次发行一个文件
│   ├── analysis/              精选分析（只留真实文件）
│   ├── color/                 色彩校准记录
│   ├── firmware/              PACKAGES.md / FLASH-LOG.md
│   ├── map/                   精选链路图
│   ├── refs/                  先例摘要（不是原文）
│   └── dev/                   历史开发资料（不随发行流程更新）
├── research/
│   ├── INDEX.md               研究笔记入口
│   └── 01..10.md              失败与决策记录
└── assets/                    自制测试图（色卡/ramp）
```

### 1.1 根目录只放用户文档

允许：README、LICENSE、NOTICE、AI-DISCLOSURE、SAFETY、QUICKSTART、LOOKS、`.gitignore`。
不允许：打包报告、上传清单、历史脚本清单、构建日志、临时文件。

### 1.2 `docs/` 的职责

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| 根 | 状态、纪律、路线、方法 | 一次性分析、日报 |
| `release/` | 发布规则、验收判据、历史打包报告 | 用户教程 |
| `releases/` | 按日期归档的发行说明 | 现行状态 |
| `analysis/` | 选留的分析结论 | 全量实验记录、照片 |
| `color/` | 色彩校准记录 | 商业 LUT |
| `firmware/` | 包台账、刷机日志 | 成品 BIN |
| `map/` | 链路图 | 全量反汇编 |
| `refs/` | 先例摘要（≤30 行引用） | 第三方全文 |
| `dev/` | 历史脚本清单等 | 现行入口 |

### 1.3 `research/` 的职责

- 十篇固定主题：M1/M1b、预览、HSL、loader、半按、门禁、N k、LOOKS、门 A、HSL 表；
- 每篇固定结构：问题 / 原判断 / 方法 / 结果 / 被推翻 / 影响 / 证据与未证 / 相关文件；
- 不写固件、成品 BIN、照片、个人路径、商业 LUT、第三方全文；
- 未证推断必须标“未证”，不能写成结论。

---

## 2. 什么能进、什么不能进

### 2.1 禁止（硬）

- `*.BIN` / `*.bin` / `*.decoded.bin` / `*.sim.bin`；
- `*.ORF` / `*.JPG` / `*.jpeg` / `*.cube` / `*.npy`；
- 官方固件、商业 LUT、实拍照片、第三方仓库副本；
- `build/`、`stock/`、`photos/`、`refs/`、`.tmp/`、`gfx-eterna-*`；
- 个人路径（Windows 用户目录、工作区绝对路径）、卡符、设备序列号；
- 未验证的推断冒充结论。

### 2.2 允许

- `*.patch`、`*.sim.json`（纯文本定义）；
- `tools/*.py`（只限 §3 的 12 个正式入口）；
- `docs/**/*.md`、`research/**/*.md`、根目录用户文档；
- `assets/` 自制色卡/ramp。

### 2.3 历史脚本

2026-11-24 前的 369 个脚本中，357 个历史构建/探针脚本**不随发行版**。
需要时从 git 历史取：

```bash
git show 53f6e70:tools/build_b90.py
```

工作区 `工作区 `tools/`` 保留完整历史；不要整目录复制。

---

## 3. `tools/` 正式入口（12 个）

| 文件 | 职责 |
|---|---|
| `mkpatch.py` | 应用 `.patch` 生成成品 |
| `build_release.py` | 从 stock 一次组装 `RELEASE_v1`（工作区用） |
| `verify_release.py` | 从 stock 起的逐字节白名单审计 |
| `make_release_patch.py` | 从 stock+成品生成 `.patch` |
| `flash_gate.py` | 写卡判定；只认 `CARD_OK sha=…` + 回读 |
| `audit_beta_pack.py` | 容器/块/sha/钩子结构审计 |
| `patch_check.py` | 钩点可达性/单条补丁检查 |
| `second_verify.py` | 相对 base 的二次差异审计 |
| `scrub_release.py` | 文档脱敏/路径改写 |
| `gen_packages.py` | 包台账辅助 |
| `oly_container.py` | 容器解析/编码库 |
| `mn10300_dis.py` | MN10300 反汇编器（`patch_check` 需要） |

新增工具的条件：

1. 是发行流程必需，不是一次性探针；
2. 不依赖工作区绝对路径；
3. 依赖齐全，能 `python -m py_compile`；
4. 在 `tools/README.md` 登记；
5. 通过一次完整重放。

---

## 4. 每次发行的顺序

### 4.1 在工作区

1. 从 stock 起构建/重放，确认成品 sha；
2. 跑工作区五道门 + 能力回归；
3. 生成/更新 `patches/*.patch`、`*.sim.json`；
4. 更新工作区 `STATUS.md`、`PACKAGES.md`、`research/`（如需要）；
5. 只有用户明确说“发”/“推”才进入发行树。

### 4.2 在发行树

1. `git status` 必须干净或只含预期改动；
2. 只从工作区**按白名单**同步文件，不 `cp -r`；
3. 新日期发行说明：`docs/releases/YYYY-MM-DD.md`；
4. 更新 `docs/releases/README.md`、根 `README.md` 的发行说明链接；
5. 更新 `docs/STATUS.md` 的“本发行快照”段；
6. 历史文件加 banner（“路径已重构，以 docs/README 为准”）；
7. 跑 §5 自检；
8. 给 diff（文件列表 + 关键 diff），等确认；
9. commit，push。

### 4.3 发行说明必须写

- 发行物 sha（补丁/成品）；
- 相对上一版改了什么、没改什么；
- 已知限制；
- 验证命令与结果；
- 回滚方式。

---

## 5. 上传前自检（v3）

在发行树根执行：

```bash
# 1 禁止的二进制/照片/LUT
find . -type f \( -name '*.BIN' -o -name '*.bin' -o -name '*.ORF' \
  -o -name '*.JPG' -o -name '*.jpg' -o -name '*.cube' -o -name '*.npy' \) | head

# 2 目录体积与文件数
du -sh .
git ls-files | wc -l

# 3 个人路径/卡符/工作区路径
git grep -nE 'C:\\\\Users|G:\\\\em10fw|L:\\\\DCOLYMP|[Jj]:\\\\DC' -- '*.md' '*.py' '*.json' | head -30

# 4 工具语法
python -m compileall -q tools

# 5 工具入口存在
for f in mkpatch build_release verify_release flash_gate oly_container \
  audit_beta_pack patch_check second_verify make_release_patch \
  scrub_release gen_packages mn10300_dis; do
  test -f "tools/$f.py" || echo "MISSING tools/$f.py"
done

# 6 文档相对链接（只查本地 .md）
python - <<'PY'
import pathlib, re, sys
root = pathlib.Path('.')
bad = []
for md in root.rglob('*.md'):
    text = md.read_text(encoding='utf-8', errors='ignore')
    for m in re.finditer(r'\[[^\]]*\]\(([^)]+)\)', text):
        t = m.group(1).split('#')[0].strip()
        if not t or t.startswith(('http://','https://','mailto:')):
            continue
        p = (md.parent / t).resolve()
        if not p.exists():
            bad.append(f'{md}: {t}')
print('\n'.join(bad[:50]))
sys.exit(1 if bad else 0)
PY

# 7 工作树
git status --short
```

### 5.1 人工核对

- [ ] 根目录只有 §1.1 允许的文件；
- [ ] `tools/` 只有 12 个正式入口 + README；
- [ ] `docs/` 没有死链，`docs/README.md` 是唯一入口；
- [ ] `docs/releases/` 有本次日期文件与 README 索引；
- [ ] `research/` 仍是 10 篇 + INDEX，没有变成教程；
- [ ] 无 `*.BIN/*.ORF/*.JPG/*.cube`；
- [ ] 无个人路径/卡符/设备号；
- [ ] `RELEASE_v1.patch` sha 与 `docs/releases/` 记录一致；
- [ ] 工作区/发行树两边 `STATUS` 的“发行快照”段一致。

---

## 6. 免责与措辞

沿用 v2：不写“解析/解除/跳过限制”，改写“格式解析/参数定位/完整性校验”；不写“完美复刻”，写“两档达标、两档有已知偏差”；只写已验证补丁；机型只写 E-M10 II / E120 1400。

---

## 7. 回滚

- 发行仓库回滚：`git revert <commit>`；不要 force push。
- 相机回滚：见 `SAFETY.md`；删卡上 `E1209999.BIN`、保留官方 `E1200001.BIN`，按住 OK 开机。
- 任何“补丁导致变砖”的记录，先写 `docs/releases/` 与 `research/`，再谈下一次发行。
