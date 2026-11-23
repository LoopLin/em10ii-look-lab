# PACKAGING-REPORT — 发行版生成记录（2026-11-22）

> **2026-11-24 路径注记**：这是历史打包记录，路径已按结构重构调整；现行仓库结构与发布规则见 [`UPLOAD-CHECKLIST.md`](UPLOAD-CHECKLIST.md) v3。本文里的 `tools/` 数量/目录树是 2026-11-22 的旧快照，不是当前结构。

生成者：打包流程（自动），范围为 `docs/release/UPLOAD-CHECKLIST.md` v2 §5 白名单 / §5.3 改写清单 / §6 目录结构 / §7 声明 / §8 自检。

```
源工作区   <WORKSPACE>            # 本机为 em10fw 项目根
目标目录   <WORKSPACE 同级目录>     # 名称见交付说明（em10fw-release-2026-11-22）
生成时间   2026-11-22
```

目标目录此前不存在，因此没有覆盖任何历史包。

---

## 1. 拷贝清单

| 目录 / 文件 | 文件数 | 大小 | 来源 |
|---|---:|---:|---|
| `README.md` `SAFETY.md` `QUICKSTART.md` `NOTICE` `.gitignore` `docs/release/PACKAGING-REPORT-2026-11-22.md` | 6 | 19.9 KB | 本次新写 |
| `LICENSE` | 1 | 34.3 KB | `<WORKSPACE>\.tmp\ofw\LICENSES\GPL-3.0.txt`（674 行，原样） |
| `patches/` | 2 | 7.6 KB | `build/RELEASE_v1.patch`、`build/RELEASE_v1.BIN.sim.json` |
| `assets/` | 4 | 304.4 KB | `assets/colorcard_A4.png`、`colorcard_tablet_2560x1440.png`、`colorcard_tablet_2560x1600.png`、`ramp_tablet_2560x1440.png`（自制） |
| `tools/` | 366 | 2.57 MB | `tools/*.py`（无 `__pycache__`） |
| `docs/`（顶层 7 篇） | 7 | 69.0 KB | STATUS / INDEX / METHOD-efficiency / EVIDENCE-RULES / UPLOAD-CHECKLIST / RELEASE-CHECKLIST / ROADMAP-longterm |
| `docs/firmware/` | 1 | 11.3 KB | `PACKAGES.md` |
| `docs/analysis/` | 25 | 332.4 KB | 全部 `*.md` |
| `docs/refs/` | 2 | 85.0 KB | `caduceus-e-p7.md`、`penf-oly-fw-analysis.md` |
| `docs/reports/` | 1 | 5.8 KB | `2026-11-22.md` |
| **合计** | **415** | **3.42 MiB** | — |

与 80 MB 上限相比余量充足（体积主要来自 `tools/` 与 `assets/`）。

**明确未拷**：`stock/`（67 MB）、`S1_backup/`（266 MB）、`build/`（8.2 GB，仅取 `RELEASE_v1.patch` 与其 sim 记录）、`photos/`（7.7 GB）、`refs/`（4.1 GB）、`.tmp/`（3.4 GB）、`analysis/*.decoded.bin`（69 MB）、`gfx-<lut-pack>-v110/`、`nohup.out`、`quit`、任何 `__pycache__`、任何 `*.BIN/*.bin/*.ORF/*.JPG/*.cube/*.npy`。

**只拷一个补丁**：`build/*.patch` 中 S 系列 6 个历史补丁（S5–S10）按要求未拷；`build/*.sim.json` 里 101 份历史包的记录同样未拷，避免读者误以为还有多个可选成品。

---

## 2. 改写清单（按类型）

### 2.1 路径与个人目录（§5.3 第 1、2 条）

| 原写法 | 改为 | 影响文件 |
|---|---|---|
| 源工作区绝对路径（盘符 + `em10fw`） | `<WORKSPACE>` / `os.environ.get("EM10FW_ROOT", ".")` | docs ×25+、tools ×47 |
| 卡上 `DCIM\100OLYMP` 的绝对路径（三种盘符写法） | `<CARD>/DCIM/100OLYMP` / `os.path.join(CARD, "DCIM", "100OLYMP", …)`，`CARD = os.environ.get("EM10FW_CARD", ".")` | docs ×5、tools ×20 |
| 卡上 `DCOLYMP` 的绝对路径（三种盘符写法） | `<CARD>/DCOLYMP` | STATUS.md、UPLOAD-CHECKLIST.md、tools |
| 个人主目录绝对路径 | `<HOME>` / `os.environ.get("EM10FW_TARGET", …)` | UPLOAD-CHECKLIST.md、read_zonal.py |
| 旧素材盘路径（盘符 + 日文目录名） | `<CARD>` | UPLOAD-CHECKLIST.md |
| `ROOT = r"<盘符>:\em10fw"` 等 3 种赋值形态（str / `Path()` / 带尾分隔符） | `os.environ.get("EM10FW_ROOT", ".")`（`Path(...)` 形态保留 `Path(...)`） | tools ×47 |
| `verify_b90.py` 的斜杠形态（带尾 `/`） | 保留尾部 `/` 语义（拼接 `ROOT+'build/…'`） | 1 处 |

按上面规则改完后，仍会命中的盘符串**全部**是固件内部格式串，不是个人路径，已逐条核对：

- `%c:\DCIM`、`%c:\DCIM\%s\%s`、`%c:\DCIM\101OLYMP` 等（`docs/analysis/sd-file-api.md`，SD 文件 API 的字符串常量表）
- `C:\DCIM\`、`C:\DCIM\100OLYMP\…`（同文件，USB 主机侧路径仿真；样例名已泛化）
- `A:\DCOLYMP\EM10MOD\PING.BIN`（`docs/analysis/m1-probe.md`，探针包写入的目标名）
- `c:\DCIM\...`（`docs/release/UPLOAD-CHECKLIST.md` §8 注释，说明这类字符串属正常）

### 2.2 样例文件名（§5.3 第 3 条）

`PB21xxxx` / `PB12xxxx` / `PB15xxxx` 一律泛化为 `<sample>` 或 `<sample-NNN>`（保留后三位以便对照同一组图）。涉及 `STATUS.md`、`INDEX.md`、`docs/analysis/` 多篇（shot-ledger 的整张表、index-def 的探针命名、chroma-gap 的入参列表）与 tools 的用法串。

### 2.3 风格名与第三方风格名（§5.3 第 4 条）

- 四档风格原名 → `look-01` … `look-04`，**每个文件首次出现**补「（风格参考）」，例如 `look-01（风格参考；反转片，高饱和）`。
- LUT/参考图文件名 → `look-01.png`、`look-03.cube` 等中性名。
- 第三方胶片风格名（8 个词，逐词列举见 §7.2 的检查用词表；本报告不重复）→ `<style-a>` … `<style-h>`，或整行改写成「第三方风格名，此处不列举」。
- 相机自带模式名（Vivid / Natural / Muted / Portrait / i-Enhance 等）**保留**：它们指机内模式，不是我们的预设名。

### 2.4 措辞（§5.3 第 6 条 / §7.2）

规避类措辞三词（见 §7.2 左列）→ 格式解析 / 参数定位 / 跳过。全文替换后复检为 0 命中（含 `SAFETY.md` 的「不要跳过校验」与 tools 注释里的「被跳过」）。

### 2.5 大段 hex（§5.3 第 5 条）

对「≥9 行、且 ≥60% 行是 `地址 + 字节`」的代码块，压成两行：

```
; 0xSTART–0xEND（原 N 行，≈M B），逐字节转储已省略
; 例：<保留一行样例，截断到 78 字符>
```

共压 7 处：`chroma-limits.md`（2）、`future-needs-2026-11-18.md`（1）、`guard-probe-design.md`（2）、`preview-real-path.md`（1）、`tone-control-bend.md`（1）。带助记符的反汇编块不动（见 §5 待确认 3）。

### 2.6 引用片段（§4 要求 ≤30 行）

| 文件 | 引用块 | 引用正文行数 | 处理 |
|---|---:|---:|---|
| `docs/refs/caduceus-e-p7.md` | 8 | 43 → **25** | 超长块截断为 2 行 + `... # 其余片段见上游原文` |
| `docs/refs/penf-oly-fw-analysis.md` | 3 | 15 | 原样（未超限） |

### 2.7 逐文件要点

| 文件 | 改了什么 |
|---|---|
| `docs/release/UPLOAD-CHECKLIST.md` | 自身也是被发布对象：个人盘符/主目录 → 占位；§6.1 的 LUT 包名 → `gfx-<lut-pack>-v110/`（与 §3 / §5.1 一致）；§8 自检块改为可直接跑的命令（商标词表放包外 `tm.txt`）；措辞改写 |
| `docs/STATUS.md` | 风格名 → look-01..04，卡路径 → `<CARD>`，样例名泛化 |
| `docs/analysis/chroma-gap.md` | 入参卡路径与样例名泛化、LUT 文件名中性化、Kodak/Fuji 保留为「参考组」家族名 |
| `docs/analysis/matrix-chroma.md` | LOOKS 数组 → `["style-a"…"style-d"]`；矩阵表里的风格名 → `<style-x>` |
| `docs/analysis/shot-ledger.md` | 整张表的样例文件名 → `<sample-NNN>` |
| `docs/analysis/sd-file-api.md` | 仅样例名泛化；`%c:\DCIM` 等固件常量保留 |
| `docs/analysis/*.md` 其余 | hex 压缩、路径占位、措辞改写 |
| `tools/*.py`（81 个被改） | `ROOT`/卡路径参数化、`compare_baseline.py` 的 `LOOK` 改 `look-01..04`、`acceptance_sheet.py` 的标签改 `look-0N`、注释与用法串占位化；改完 366/366 通过 `py_compile`，0 个回退 |

---

## 3. 补丁可用性验证

```
$ python tools/mkpatch.py <官方包> --out X.BIN --patch-file patches/RELEASE_v1.patch
container_checks: passed (signature, model, size%16, header/trailer, additive checksum)
$ sha256sum X.BIN
0b08a232cee544c5724e8ed6…
```

与 `build/RELEASE_v1.BIN` 记录的 `0b08a232cee544c5724e8ed661fcad97d75d1e9f9886229c30ea01d2280cafad` 一致（前 20 位相同即同一文件）。验证用的是包内 `tools/mkpatch.py` + `tools/oly_container.py`，未改动原工作区任何文件。

---

## 4. §8 自检实际输出

| # | 命令 | 结果 |
|---|---|---|
| 0 | `du -sh .` | `4.3M	.` |
| 1 | `find . -type f \( -name '*.BIN' -o -name '*.bin' \)` | 空（0 个） |
| 2 | `find . -type f \( -name '*.ORF' -o -name '*.JPG' -o -name '*.jpg' \)` | 空（0 个） |
| 3 | `find . -type f \( -name '*.cube' -o -name '*.xmp' \)` | 空（0 个） |
| 4 | `grep -rnE '[A-Za-z]:[\\/]Users\|DCIM\|DCOLYMP\|…em10'` | 19 条，全部是固件内部格式串（`%c:\DCIM` / `C:\DCIM\…` / `A:\DCOLYMP\…`）：其中 14 条在 `docs/`，5 条是本报告为举证直接引用的同一批字符串。无个人路径、无个人主目录名 |
| 5 | `find . -type f -size +5M` | 空（最大单文件 `LICENSE` 34.3 KB，其次 `colorcard_A4.png` 160.9 KB） |
| 6 | `find . -iname "*<商标词>*"` | 0（文件名/目录名无商标词） |
| 7 | `grep -rni` 商标词表（12 词）于 `*.md` | 0 命中（`portra` 在 `\bPortra\b` 词边界下为 0；裸串命中项全是 `Portrait` 模式名） |
| 8 | `grep -rn '```' docs/refs/*.md \| wc -l` | `22`（caduceus 16 = 8 块，penf 6 = 3 块，fence 成对） |

补充核对：

- `README.md` 顶部含 §7.1 声明原文（逐字一致；仅加了 Markdown 引用标记 `> ` 便于渲染）→ ✅
- `README.md` 写明三条已知限制（M/P 高光 +20 码、预览待真机确认、色度 ΔE00 ≤6）→ ✅
- `LICENSE` 674 行 GPL-3.0 全文（`GNU GENERAL PUBLIC LICENSE` / `Version 3`）→ ✅
- `NOTICE` 列全 om-emu / fishcu / caduceus / cmdada / LibRaw → ✅
- 无个人姓名、邮箱、学校、公司、个人主目录名（全文 0 命中）→ ✅
- 无规避类措辞（0 命中）→ ✅
- 366 个 `*.py` 全部 `py_compile` 通过 → ✅
- 无 `__pycache__`、无空目录 → ✅

---

## 5. 待人工确认（8 条，第 1 条为发布阻断级）

1. **补丁字节里仍写着四档风格原名（商标词）。** 解开 `patches/RELEASE_v1.patch` 的 UTF-16LE 段可见：`blk 1:0x749b98` 与 `blk 1:0x80678a` 两处 64 字节串里含原风格名与名次前缀（如 `…@A095@…` 这类 sid 前缀 + 名字）。这与 §2「预设名里不出现商标词」冲突。**包内文档、文件名、菜单以外的一切已中性化，但这几个字符串是固件字节，改名必须重建 UI 名钩子并真机复验**——属固件改动，超出本次打包范围，故原样发布并在本报告标注。
2. **`tools/build_*.py` 里的 UI 名表仍含同名（约 44–52 行/词）。** 未改，因为一改「工具能复现当前已发布补丁」这条性质就断了；它与第 1 条同源，建议与第 1 条一起处理。
3. **`docs/analysis/m1-probe.md` §4 保留 216 行桩反汇编**（地址 + 字节 + 助记符，覆盖 0x60672C58–0x60672F63）。它不是逐字节转储而是自写桩的代码清单，按「大段 hex → 地址+长度」压缩会毁掉该文档的核对价值，故保留；若要求严格地址+长度，可再压到 20 行 + 指针。
4. **`docs/INDEX.md` 保留全量索引**，其中指向 `docs/color/*`、`docs/map/*` 等未随包发布的条目（索引是工作区全貌，不带这些目录只影响「点进去看」）。是否需要删掉未发布条目待定。
5. **`.gitignore` 与 `UPLOAD-CHECKLIST.md` §6.1 的第 20 行**：原 v2 文本写的是 `gfx-eterna-55-3d-lut-v110/`，包内改为 `gfx-<lut-pack>-v110/`（去商标词 + 与 §3/§5.1 一致）。两处保持同一写法。若要严格「按 §6.1 原样」，需改回并接受该商标词出现在 `.gitignore`。
6. **`tools/verify_release.py` 依赖完整工作区**：顶部硬编码 `stock/…`、`build/RELEASE_v1.BIN`、`build/L55_newton3.BIN`，无 CLI，包内无法直接跑。已保留（它是 sim.json 记录里的校验器），但 `QUICKSTART.md` 改用 sha256 自检，避免给出不能跑的命令。是否给它加 `--base/--image/--ref` 参数待定。
7. **依赖 `.decoded.bin` / `.tmp/*` 的分析脚本**（约 20 个，如 `pm_*.py`、`tone_lut_gen.py`、`mx_*.py`）参数化了 `EM10FW_ROOT`，但它们要读的中间镜像不随包发布；读者需自己按 `docs/METHOD-efficiency.md` 复现解码步骤。是否在包内加一个「哪些脚本需要自备中间件」的清单表待定。
8. **`docs/analysis/chroma-gap.md`、`matrix-chroma.md` 里保留 `Kodak` / `Fuji` 家族名**（指参考目标的来源家族，不是我们的预设名），依据 §2 第 2 条「正文提到商标须有一次性声明」——README 顶部已有声明。若要求连家族名也中性化（改成「参考组 A/B」），需再改一轮。

---

## 6. 复核建议（给下一手）

```bash
cd <发行版目录>
du -sh .
find . -type f \( -name '*.BIN' -o -name '*.ORF' -o -name '*.JPG' -o -name '*.cube' \) | wc -l
grep -rnE '[A-Za-z]:[\\/]Users|DCOLYMP' --include='*.md' --include='*.py' . | wc -l
python -m py_compile $(find tools -name '*.py')   # 逐个数，Windows 下建议用循环
```

第 1 条未解决前，本包可以内部流转/自用，**不建议公开发布**（补丁字节里的风格名会跟着进 GitHub）。

## 最终状态（2026-11-22 03:2x 复核）

- 文件数 **402**，体积 **4.1 MB**；无 `*.BIN / *.ORF / *.JPG / *.cube`；无 >5MB 文件；无个人 id / 个人路径。
- `patches/RELEASE_v1.patch` = **保留业界通用风格名**的版本（用户决定，见 `LOOKS.md`），
  从 stock 重放 sha `0b08a232cee544c5724e8ed661fcad97d75d1e9f9886229c30ea01d2280cafad`，
  与 `build/RELEASE_v1.BIN` **逐字节一致**（本轮已实测）。
- 文档 24 份（精选白名单，不是全量 358 份）；商标类词 42 处（全部指代性描述）、
  风险措辞 0 处、个人路径 0 处。
- 新增/更新：`AI-DISCLOSURE.md`（AI 辅助声明）、`LOOKS.md`（外观表现+命名说明+已知限制）、
  `NOTICE`（逐文件归属表 + MIT 全文）、`docs/analysis/legal-risk-audit.md`（含最长公共 token 串实测）。
