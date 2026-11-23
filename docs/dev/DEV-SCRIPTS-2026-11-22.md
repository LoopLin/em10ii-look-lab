# 开发脚本台账（DEV-SCRIPTS）

> ⚠️ **这一页讲的全是开发版工具**，不是发行版的一部分。
> 发行版只带 `patches/`、精选文档和少量公开工具；这里的 `tools/*.py` 大多是一次性探针或某一代的构建器，
> 留着是为了**可复现与防重复踩坑**，不是为了让你照着跑。
> 共 **369 个脚本**，按「族」分组说明；最后一节是全量清单（自动生成）。

## 0. 先看这几条硬规矩（血换来的）

| 规矩 | 为什么 |
|---|---|
| 所有固件都从 `<official-firmware>.BIN` 起，**不在上一版上改** | 否则 sha 链断、差异不可解释，回滚也没法回 |
| 改镜像一律走 `tools/oly_container.py` 的 `parse/replace_payload/reconstruct` | 载荷是 **16 字节组置换 + 每字节 XOR 0xFF**；手算 `32 + (RAM-0x60021020)` 会写错位置（我犯过） |
| `mkpatch.py` 的 `ram` 行**必须按地址升序** | 乱序会报 `patch at 0x... did not land at its decoded offset` |
| `mkpatch.py` 不允许输出到输入**同目录**，也不覆盖已存在文件 | 报 `refusing to write into the original firmware directory` / `output already exists`；中间产物放 `build/_rel/`，先删旧文件 |
| `*.sim.json` 必须 **UTF-8 编码**，且含 `sha256`、`closed` | 默认 GBK 写会让 `flash_gate` 抛 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xbe` |
| 写卡只走 `tools/flash_gate.py`；写卡前确认**盘符是相机卡** | `find_card()` 早期从 D: 起找第一个 `\DCOLYMP`，结果写到了另一张卡上，相机还在跑旧固件（白拍一轮） |
| 桩只能放进已验证的 FF 口袋（如 `0x60661590..0x606615E0`、`0x60672C20..0x60672FF6`） | 放死区会被拦；口袋若无真机执行记录，逐帧路径做文件 I/O 有卡死风险（M1 尚未上机） |
| 新钩子必须回答四问：capture / preview / recording / compact | 少一路就会出现「预览有、存下来的没有」（PEN-F 的教训，见 `docs/refs/penf-oly-fw-analysis.md`） |

## 1. 核心（长期维护，改它们要跑全套门）

| 脚本 | 作用 |
|---|---|
| `oly_container.py` | 容器解析/重建（唯一的地址换算入口；源自 fishcu/oly_fw_analysis，Apache-2.0） |
| `mkpatch.py` | 从 stock + patch 生成新 BIN（补丁格式的唯一实现） |
| `patch_check.py` | `--reach` 可达性检查（分支旁路会让补丁"写了不生效"） |
| `audit_beta_pack.py` / `second_verify.py` | 包级体检与逐字节复核（`--base stock`） |
| `flash_gate.py` | 唯一写卡入口；四条件（audit/reach/sim closed/second_verify）全过才写 |
| `build_release.py` / `verify_release.py` | 从 stock 组装成品包；verify 做"白名单外 0 字节"检查 |
| `make_release_patch.py` | stock↔成品 diff 出 `RELEASE_v1.patch`，可逐字节重放 |
| `legal_audit_deep.py` / `license_audit.py` / `scrub_release.py` | 代码来源比对、许可审计、发行前清洗 |

## 2. 按代分组（构建器 build_*）

- **B 系列（B01–B95）**：早期通路/钩子实验。`build_b45.py`、`build_b46.py` 里有 `assemble_stub()` 的跳转自检范式，**后来的桩都照它写**。
  `build_b37_b38_chain.py`、`build_b40_chain.py`、`build_b41.py` 属于已判死的链路，仅存档。
- **L 系列（L2–L62）**：tone/色度迭代。`build_l20.py`→`build_l52.py`→`build_l58.py`→`build_l62.py` 是一条主线；
  `build_l23.py` 引入了「逐档仿射拟合」，`build_l58.py` 是锯齿探针，`build_l62.py` 是 V 色度降饱和（**真机判死，整片绿**）。
- **M 系列（M1）**：SD 文件 API 探针，`build_m1.py`（880 B 桩，未上机）。
- **S 系列（s14–s52）**：更早的场景生成器，历史材料，不再维护。

## 3. 探针/读取（一次性，但经常要回看）

`read_probe.py`（读 EXIF 的 `k=` / `B75A` 记录）、`read_keyprobe.py`、`read_modeprobe.py`、`read_slotprobe.py`、`read_dial_probe.py`、`read_b01_probe.py`、`read_zonal.py`——**统一约定**：探针只写 EXIF 文本区（`0x61894C90` 起），不碰 `k=` 字段，出片后必须回读核对（这是硬规定）。

## 4. 分析与拟合

`fit_four.py`（四档矩阵拟合）、`fit_chroma_de.py`（按 ΔE00 直接拟合色度，**结果三档变差，已弃**）、`chroma_gap.py`、`chroma_joint.py`、`judge_chart.py`（色卡取块与 ΔE00，**取代了 chart_sample.py**）、`compare_baseline.py`（新拍/原厂/参考目标三列）、`acceptance_sheet.py`（验收拼图）、`offline_*.py`（离线反解，多次失败，见文档）。

## 5. 仿真与反汇编辅助

`mn10300_dis.py` / `mn10300_asm.py`（薄封装 objdump/汇编器）、`field_access.py` / `watch_field.py`（第四代交付的"查表工具"）、`em10_run.py` / `omemu_winfix.py`（om-emu 运行与 Windows 修补，MIT）、`callers.py` / `six_*.py` / `pm_*.py`（各类交叉引用扫描，命名来自当时的寄存器惯例，**别当文档读**）。

## 6. 全量清单（自动生成，含大小与最后修改日期）

| 脚本 | 字节 | 首行说明 | 改于 |
|---|---|---|---|
| `tools/_get_omemu.py` | 1705 |  | 11-09 |
| `tools/_gh_probe.py` | 1332 |  | 11-09 |
| `tools/_gh_probe2.py` | 791 |  | 11-09 |
| `tools/_gh_probe3.py` | 587 |  | 11-09 |
| `tools/_gh_probe4.py` | 1706 |  | 11-09 |
| `tools/_mul_verify.py` | 3521 | Verify 0x604B67C3 semantics: dest = (A x B) >> 8  (3x3 s16, row-major). | 11-13 |
| `tools/_scan_mat.py` | 978 | Scan the decoded image for 9xu16 blocks whose three row sums are all 256 | 11-13 |
| `tools/_test_mkpatch.py` | 1707 |  | 11-09 |
| `tools/_test_mkpatch2.py` | 903 |  | 11-09 |
| `tools/acceptance_sheet.py` | 2549 | acceptance_sheet.py -- 22-scene acceptance contact sheet + numbers. | 11-21 |
| `tools/addscan.py` | 853 | addscan.py <imm16 hex ...>  -- find 'add imm16,aN' (FA D0..D3 imm16) sites, plus | 11-12 |
| `tools/audit_beta_pack.py` | 5962 | audit_beta_pack.py — 对已出的 B 测包做独立审计（不写任何文件，只读）。 | 11-15 |
| `tools/audit_recent.py` | 4967 |  | 11-18 |
| `tools/b22_compare.py` | 12975 | B22 四套 look 的离线对比：渲染 + 五联图 + 数值摘要（只读，不改出包器）。 | 11-15 |
| `tools/backup_photos.py` | 4608 | 把卡上的照片按固件版本归档到 G:/em10fw/photos/<版本>/ ，校验后删除卡上副本。 | 11-17 |
| `tools/build_atlas.py` | 4199 | Assemble docs/map/ATLAS.md from the scan results. | 11-15 |
| `tools/build_b37_b38_chain.py` | 22114 | build_b37_b38_chain.py —— B37 / B38：按 k 选 tone 表（B38 再加每档 3x3 矩阵）的离线闭环包。 | 11-16 |
| `tools/build_b40_chain.py` | 5029 | build_b40_chain.py —— B40：tone + 每档矩阵 + 每档色度，全部按 stash 的 k 分派。 | 11-16 |
| `tools/build_b41.py` | 10738 | build_b41.py -- B41：修 B38 的真机缺陷（矩阵改成"只换指针"，一处共享状态都不写） | 11-16 |
| `tools/build_b43.py` | 11001 | build_b41.py -- B41：修 B38 的真机缺陷（矩阵改成"只换指针"，一处共享状态都不写） | 11-16 |
| `tools/build_b45.py` | 10739 | build_b45.py -- B45 "pointer pick"：在 tone 上传器取指针处按指纹换表，零内存写入。 | 11-16 |
| `tools/build_b46.py` | 10799 | build_b46.py -- B46 "pointer pick, pool-facing stub"：在 tone 上传器取指针处按指纹换表，零内存写入。 | 11-16 |
| `tools/build_b47.py` | 11418 | build_b47.py -- B47 "pointer pick + 极端诊断表"：代码与 B46 逐字节相同，只把三张表换成一眼能认的极端曲线：在 tone | 11-16 |
| `tools/build_b48.py` | 7673 | build_b48.py —— B48：stash 分派 + 极端诊断表（四档一眼能分） | 11-16 |
| `tools/build_b49.py` | 7720 | build_b49.py —— B49：B48 + 把选中的表写回 ctx+0x1230（成片路径读的就是这个指针） | 11-16 |
| `tools/build_b50.py` | 3992 | B50：真实四套 look（几何与 B48 完全相同，只换 tone 表） | 11-16 |
| `tools/build_b51.py` | 15309 | build_b51.py —— B51：三条通道按档分派（本版只验"通道能不能通"，不调观感） | 11-16 |
| `tools/build_b52.py` | 8704 | build_b52.py —— B52：纯仪表包（只离线） | 11-16 |
| `tools/build_b53.py` | 16060 | build_b53.py —— B53：tone 表搬进 bank2，池区一个字节都不动（只离线） | 11-16 |
| `tools/build_b54.py` | 16043 | build_b54.py —— B54：B48 几何（tone 槽 22–25）＋ 矩阵极端四档（只离线） | 11-16 |
| `tools/build_b56.py` | 15024 | build_b56.py —— B56：用「改指针」做第一级替换（只离线） | 11-16 |
| `tools/build_b57.py` | 18336 | build_b57.py —— B57 = B56 + 两点修补（只离线） | 11-16 |
| `tools/build_b60.py` | 18797 | build_b60.py —— B60：四个标准档各用自己的色度块（B59 + 第三个钩子） | 11-16 |
| `tools/build_b61.py` | 20006 | build_b61.py —— B61：四张极端色度块 + 双写（新鲜记录与 slot1 一起写） | 11-16 |
| `tools/build_b62.py` | 5896 | build_b62.py —— B62：B61 + 1 字节闸门翻转（0x604803AF 的 bne -> bra） | 11-16 |
| `tools/build_b63.py` | 13496 | build_b63.py —— B63：B59 基线 + 四张极端色度块 + 扩展 SU 桩写 slot1+0x1D | 11-16 |
| `tools/build_b64.py` | 15671 | build_b64.py — B64: identical patch set to B63, but the four chroma blocks | 11-17 |
| `tools/build_b65.py` | 16643 | build_b65.py — B65: B63 patch set rebuilt from stock, SU stub guarded. | 11-17 |
| `tools/build_b66.py` | 16613 | build_b66.py — B66: B63 patch set rebuilt from stock, SU stub guarded. | 11-17 |
| `tools/build_b67.py` | 21360 | build_b67.py — B67: B66 patch set + 成片色度选择器钩子（每档独立成片色度） | 11-17 |
| `tools/build_b68.py` | 21360 | build_b67.py — B68: B66 patch set + 成片色度选择器钩子（每档独立成片色度） | 11-17 |
| `tools/build_b69.py` | 22601 | build_b67.py — B69: B66 patch set + 成片色度选择器钩子（每档独立成片色度） | 11-17 |
| `tools/build_b70.py` | 4653 | build_b70.py -- B70: read-only probe of the ten HSL slot pointers. | 11-19 |
| `tools/build_b94.py` | 22777 | build_b67.py — B94: B66 patch set + 成片色度选择器钩子（每档独立成片色度） | 11-18 |
| `tools/build_b95.py` | 16577 | build_b95.py -- B95: four film looks (tone LUT + 3x3 matrix + chroma block per m | 11-18 |
| `tools/build_b95_ui_names.py` | 12984 | Build the B95 UI-name patch (4 picture-mode names x 2 languages) as a byte-exact | 11-18 |
| `tools/build_base1.py` | 1480 | build_base1.py -- BASE1: 原厂 + 只写 EXIF 的 SU 桩（不改任何画质） | 11-20 |
| `tools/build_beta_alt.py` | 2234 | B24i: plan-A hook (no stash) -- at 0x604AA242 write the tone pointer for the | 11-15 |
| `tools/build_beta_hook4.py` | 2182 | B25: per-mode look via the plan-A hook (0x604AA242). | 11-15 |
| `tools/build_beta_hook4mx.py` | 3499 | B26: per-mode tone + per-mode 3x3 matrix via two hooks. | 11-15 |
| `tools/build_beta_keyprobe.py` | 4971 | B20：filter-id 探针（hook 版，全部落在 tone 池内）。 | 11-15 |
| `tools/build_beta_look.py` | 9982 | B 测出包器：把一个 look 的 tone 与色度块写进固件（21 个目录全指同一张表）。 | 11-15 |
| `tools/build_beta_looks_mx.py` | 22737 | B34：四套 look 的 tone **和** 3x3 矩阵，在同一条 SU 钩子里按同一个 k 分档。 | 11-15 |
| `tools/build_beta_modeprobe.py` | 10093 | build_beta_modeprobe.py - build the B24 "picture-mode probe" firmware package. | 11-15 |
| `tools/build_beta_probe.py` | 6973 | B01（beta 第 1 包）：21 目录签名曲线探针。 | 11-15 |
| `tools/build_beta_slotprobe.py` | 4029 | B18：槽位探针——一次刷机拿到「四个画面模式各自读哪个 (dir, slot)」。 | 11-15 |
| `tools/build_beta_stash.py` | 5730 | build_beta_stash.py —— B24f「stash 探针」：不再猜结构体偏移。 | 11-15 |
| `tools/build_beta_vprobe.py` | 3065 | B21：把 ctx+0x30 的 u16 编进亮度（v & 7），一次刷机读出四个模式的值。 | 11-15 |
| `tools/build_beta_zonalfeed.py` | 8989 | B28 zonal-feed: B26 + (mask 0xFFF & ten pointers to neutral tables) on every | 11-15 |
| `tools/build_beta_zonalmap.py` | 11926 | B35 zonal-map: B28 的通道 + 一张「12 段排列码」的 12 区表。 | 11-15 |
| `tools/build_guard.py` | 9838 | build_guard.py —— "写后自检守卫"探针的离线构建器（不产出可上卡的包）。 | 11-21 |
| `tools/build_hook_look4.py` | 15757 | B13_look4：按 picture mode 选 tone 曲线的代码 hook（E-M10 II / MN10300）。 | 11-15 |
| `tools/build_hook_look4b.py` | 25308 | B14_look4：按 picture mode 选 tone 曲线的代码 hook（E-M10 II / MN10300）。 | 11-15 |
| `tools/build_l12.py` | 2484 | build_l12.py -- L12: 六轴探针（stock + 一个钩 + 39 B 桩） | 11-19 |
| `tools/build_l13.py` | 2414 | build_l13.py -- L13: 六轴探针（换入口：派发器 0x604789CF） | 11-19 |
| `tools/build_l14.py` | 2661 | build_l14.py -- L14: 六轴只读探针（计数门 + 指针门，零写入） | 11-19 |
| `tools/build_l14c.py` | 2079 | build_l14c.py -- L14c: 最小重放探针（隔离开关：入口 vs 落点 vs 代码） | 11-19 |
| `tools/build_l15.py` | 2765 | build_l15.py -- L15: 六轴写入探针（同一派发入口，置 bit3 + 写 24 B 记录） | 11-19 |
| `tools/build_l16.py` | 2533 | build_l16.py -- L16: 标准四档六轴探针（每档一个可见色相偏移） | 11-20 |
| `tools/build_l16b.py` | 2261 | build_l16.py -- L16: 标准四档六轴探针（每档一个可见色相偏移） | 11-20 |
| `tools/build_l16c.py` | 2148 | build_l16.py -- L16: 标准四档六轴探针（每档一个可见色相偏移） | 11-20 |
| `tools/build_l16d.py` | 2128 | build_l16.py -- L16: 标准四档六轴探针（每档一个可见色相偏移） | 11-20 |
| `tools/build_l16e.py` | 2074 | build_l16.py -- L16: 标准四档六轴探针（每档一个可见色相偏移） | 11-20 |
| `tools/build_l17.py` | 2085 | build_l16.py -- L16: 标准四档六轴探针（每档一个可见色相偏移） | 11-20 |
| `tools/build_l18.py` | 2049 | build_l16.py -- L16: 标准四档六轴探针（每档一个可见色相偏移） | 11-20 |
| `tools/build_l2.py` | 17720 | build_l2.py -- L2 (Lemon 1.0.1): L1 looks + ART key probe (tone LUT + 3x3 matrix | 11-19 |
| `tools/build_l20.py` | 16837 | build_l20.py -- L20: four film looks (tone LUT + 3x3 matrix + chroma block per m | 11-21 |
| `tools/build_l23.py` | 17345 | build_l20.py -- L20: four film looks (tone LUT + 3x3 matrix + chroma block per m | 11-21 |
| `tools/build_l24.py` | 17428 | build_l20.py -- L20: four film looks (tone LUT + 3x3 matrix + chroma block per m | 11-21 |
| `tools/build_l25.py` | 17458 | build_l20.py -- L20: four film looks (tone LUT + 3x3 matrix + chroma block per m | 11-21 |
| `tools/build_l26.py` | 17763 | build_l20.py -- L20: four film looks (tone LUT + 3x3 matrix + chroma block per m | 11-21 |
| `tools/build_l27.py` | 17730 | build_l20.py -- L20: four film looks (tone LUT + 3x3 matrix + chroma block per m | 11-21 |
| `tools/build_l28.py` | 17473 | build_l20.py -- L20: four film looks (tone LUT + 3x3 matrix + chroma block per m | 11-21 |
| `tools/build_l29.py` | 17475 | build_l20.py -- L20: four film looks (tone LUT + 3x3 matrix + chroma block per m | 11-21 |
| `tools/build_l29_nslot.py` | 8854 | build_l29_nslot.py -- L29 候选补丁：Picture Mode 第 2 项（N / Elite Color 200）发出 colorTo | 11-21 |
| `tools/build_l3.py` | 18623 | build_l3.py -- L3: L2 + HSL dataset swap probe (Vivid's 12-zone dataset <- Parti | 11-19 |
| `tools/build_l30.py` | 17476 | build_l20.py -- L20: four film looks (tone LUT + 3x3 matrix + chroma block per m | 11-21 |
| `tools/build_l37.py` | 8738 | build_l37.py —— L37 dialprobe：把四个档位下的 tone 拨盘值取进 EXIF | 11-21 |
| `tools/build_l39.py` | 12284 | build_l39.py —— L39 keyprobe：把 IMGPRO 的 mode 字段与 key 一起抓进 EXIF | 11-21 |
| `tools/build_l4.py` | 18333 | build_l4.py -- L4: L2 + runtime HSL dataset probe (cell, dataset ptr, mask) | 11-19 |
| `tools/build_l46.py` | 12647 | build_l39.py —— L39 keyprobe：把 IMGPRO 的 mode 字段与 key 一起抓进 EXIF | 11-21 |
| `tools/build_l49.py` | 1926 | L49 = L47 + 四张 tone 表按「参考 look 目标 + 实测硬件曲线 f」逐点反解。 | 11-21 |
| `tools/build_l5.py` | 18494 | build_l4.py -- L5: L4 + picture-mode menu order (Cross Process after Portrait) | 11-19 |
| `tools/build_l50.py` | 4097 | L50：按「每档自己的实测 (T,out) 点」反解目标显示值。 | 11-21 |
| `tools/build_l51.py` | 3545 | L51：相对归一化 —— 让灰阶形状贴合参考 look，白点锚到本机实测白。 | 11-21 |
| `tools/build_l52.py` | 3700 | L52：用 EV+2/EV0/EV-2 三组实测 (T,Y) 建每档 f，再反解参考 look 的目标显示值。 | 11-21 |
| `tools/build_l58.py` | 1908 | L58 锯齿探针：把四张 tone 表改成 0/1023 交替（锚点周期 20 索引）。 | 11-21 |
| `tools/build_l6.py` | 18294 | build_l4.py -- L6: L2 + key-record probe at selector entry (ctx/key) | 11-19 |
| `tools/build_l62.py` | 1636 | build_l62.py -- L62 = L55 + Vivid 色度 y 轴降饱和（Cb x0.60 / Cr x0.70） | 11-22 |
| `tools/build_l7.py` | 18216 | build_l3.py -- L7: L2 + HSL dataset swap probe (n0, the dataset every standard m | 11-19 |
| `tools/build_look_pkg.py` | 7514 | 把一份 look 规格（tone 256 + 色度块 k=0）打成 SD 卡固件包。 | 11-15 |
| `tools/build_m1.py` | 15511 | build_m1.py -- M1 SD/fileif probe pack. | 11-22 |
| `tools/build_preview_fix.py` | 17205 | build_preview_fix.py -- 预览分派函数表指针补丁生成器（离线，只新建文件） | 11-22 |
| `tools/build_preview_permode.py` | 25137 | build_preview_permode.py — 生成 build/PREVIEW_PERMODE.BIN：预览「分档取表」桩。 | 11-22 |
| `tools/build_release.py` | 4982 | build_release.py -- 从原厂 stock 一路构建成品固件（不基于任何旧包） | 11-22 |
| `tools/build_ui_names2.py` | 6729 | Build the SCP (quick-select bar) half of the picture-mode rename: .tmp/ui_names2 | 11-19 |
| `tools/build_y1.py` | 1860 | build_y1.py -- Y1: 原厂画质 + SD 抓键 + SU 写 EXIF（Y 系列 = 原厂基线） | 11-20 |
| `tools/callers.py` | 847 | callers.py <target...>  --  find relative 'call' (CD disp16) sites whose target  | 11-12 |
| `tools/card_gray.py` | 3476 | 从"色彩标板"照片里抽出 21 级灰阶条的实测亮度。 | 11-11 |
| `tools/card_patches.py` | 4189 | 从平板标板照片里按透视变换取样每个色块，输出相对白块归一化后的色度。 | 11-11 |
| `tools/cc_strengths.py` | 2627 | 从 RAW 生成多档强度的候选曲线并渲染，用于离线挑版。 | 11-12 |
| `tools/cc_table.py` | 2882 | 按标定后的模型生成 Classic Chrome 的 256 项 tone 表。 | 11-12 |
| `tools/chart_sample.py` | 3905 | chart_sample.py — 色卡 24 块取样（抗噪） | 11-18 |
| `tools/chroma_gap.py` | 10726 | 四档 look 与参考 LUT 的色度差距（真机照片 + 离线 LUT 计算）。 | 11-21 |
| `tools/chroma_joint.py` | 22682 | chroma_joint.py -- joint offline refit of the 3x3 matrix (Q8 fixed point) + the  | 11-22 |
| `tools/chroma_transfer.py` | 3283 | 用同一场景、同一模式、不同固件的 ORF/JPG 对，反推相机色度传递函数。 | 11-11 |
| `tools/class_measure.py` | 2519 | 按「物体类别」对比不同固件对同一场景的颜色处理。 | 11-11 |
| `tools/code_sites.py` | 1369 | For each code site referencing a table, find enclosing function start + nearby c | 11-15 |
| `tools/compare_baseline.py` | 3477 | 把新拍的 V/N/M/P 与①原厂四档基准图、②参考 look 目标 同时对比，输出一张表。 | 11-22 |
| `tools/curve_dump.py` | 1904 | 把固件里的 100 字节曲线单元（88 条）dump 成表格，便于与相机 JPEG 对照。 | 11-10 |
| `tools/curve_scan.py` | 2519 | 在解码镜像里找「曲线表」候选：16 点单调递增的 x 数组 + 同样单调的 y 数组， | 11-11 |
| `tools/curve_scan16.py` | 1620 | 找 u16 曲线表：16/17/32 点、单调递增、跨度大（0..4095 或 0..1023）。 | 11-11 |
| `tools/diag_haze.py` | 3809 |  | 11-13 |
| `tools/dis_annot.py` | 2507 | 把反汇编结果写成 HxD「数据段」注解文件（_dis.txt），配合同目录的 _dis.lst。 | 11-10 |
| `tools/dispatch_map.py` | 5281 | dispatch_map.py — 离线复核「预览 tone 分派」0x604B986B / 臂 0x604B9CC3。 | 11-22 |
| `tools/dispatch_table.py` | 22138 | mode-dispatch table recovery for E-M10 II firmware (mn10300). | 11-15 |
| `tools/disr.py` | 721 | Run mn10300_dis on a RAM range and save UTF-8 listing. | 11-12 |
| `tools/dump_win.py` | 1378 | Dump printable ASCII / UTF-16LE runs (and raw hex) from windows of the | 11-09 |
| `tools/em10_run.py` | 5887 | Minimal driver for the fishku MN10300 harness, retargeted at E-M10 II block 0. | 11-15 |
| `tools/emu_param_diff.py` | 2173 | Run emu_param_probe twice (stock, S13) and diff the dumped regions. | 11-11 |
| `tools/fetch_fw.py` | 2253 | 从 OM Digital Solutions 官方更新服务取原厂固件（纯 Python，避免 PowerShell 编码坑）。 | 11-10 |
| `tools/field_access.py` | 30749 | field_access.py -- static field-access map for aN-relative structure offsets. | 11-22 |
| `tools/final_preview.py` | 5226 | 对指定 RAW 出「相机直出 / 1.35 优化 / 1.7 优化」三联，并在图上标注参数。 | 11-13 |
| `tools/find_file_api.py` | 39068 | find_file_api.py — 离线定位 E-M10 II (E120 v1.4) 固件里的 SD 卡文件读写 API。 | 11-22 |
| `tools/find_matrix.py` | 1233 | 向量化找 3x3 色彩矩阵候选（分块，避免爆内存）。 | 11-13 |
| `tools/finish_l58.py` | 5471 | L58 锯齿探针的离线收尾： | 11-21 |
| `tools/fit_chroma_de.py` | 4359 | fit_chroma_de.py -- refit the 16-point Cb/Cr curves by direct dE00 descent. | 11-21 |
| `tools/fit_four.py` | 4838 | 把选定的观感拆成「tone 表 + 六轴(6 段 sat/hue)」，并评估拟合误差。 | 11-13 |
| `tools/fit_pipeline.py` | 3800 | 用一对 RAW/JPEG 拟合「线性相机 RGB → sRGB JPEG」渲染链。 | 11-10 |
| `tools/flash_gate.py` | 4678 | flash_gate.py —— 上卡前的硬门（2026-11-16 立规）。 | 11-22 |
| `tools/four_preview.py` | 6069 | 四个候选观感：真 3D LUT vs 我们的「tone 表 + 六轴」近似，并排看差距。 | 11-13 |
| `tools/fuji_fit.py` | 14821 | 把富士 LUT 的观感翻译成我们固件能用的参数（按已知色块拟合，不走照片）。 | 11-12 |
| `tools/fuji_grade.py` | 2606 | 参数化「胶片感」调色，直接作用于 sRGB 照片（不是 LUT 套用）。 | 11-10 |
| `tools/func_bounds.py` | 22399 | Function boundaries for the E-M10 II image (PEN-F atlas, table 2). | 11-15 |
| `tools/fwscan.py` | 8957 | Read-only structural scanning of decoded Olympus firmware blocks. | 11-09 |
| `tools/gen_dir_index.py` | 4474 | 生成 docs/map/INDEX.md 与 docs/color/INDEX.md（一次性脚本） | 11-17 |
| `tools/gen_packages.py` | 4797 | 生成 docs/firmware/PACKAGES.md：A 段人工结论表 + B 段从 FLASH-LOG 自动流水。 | 11-21 |
| `tools/group_runtime.py` | 1130 |  | 11-15 |
| `tools/groups.py` | 859 | Enumerate tone group tables (each 0x20 = 8 pointers) from 0x60600920 upward. | 11-15 |
| `tools/hsl_sim.py` | 12011 | hsl_sim.py — 在本地 MN10300 harness 里复现「E-M10 II COLOR 表被 install 读出」。 | 11-14 |
| `tools/hsl_upload_test.py` | 12922 | hsl_upload_test.py — 用离线 MN10300 harness 验证「10 张 HSL 表能不能喂进 ISP 上传通道」。 | 11-12 |
| `tools/imm16scan.py` | 886 | imm16scan.py <imm16 hex>  -- find any 2-byte little-endian occurrence that is li | 11-12 |
| `tools/index_solve.py` | 12127 | index_solve.py — 用 L58 锯齿探针求解 tone 表输入索引的仿射模型。 | 11-22 |
| `tools/isp_map.py` | 1082 | isp_map.py  -- find every write to the 0x9E00xxxx ISP window. | 11-12 |
| `tools/judge_chart.py` | 3662 | 色卡判读：稳健取样 + 逐块 ΔE。 | 11-21 |
| `tools/judge_s67.py` | 2013 | S67 判读（绝对判据，不依赖与历史照片对比）。 | 11-14 |
| `tools/knob_preview.py` | 5429 | 四旋钮曲线（黑/白/阴影/高光）+ 对比度联动的饱和度，从 RAW 渲染多组方案。 | 11-13 |
| `tools/legal_audit_deep.py` | 5331 | Deep provenance + risk audit (2026-11-22). | 11-22 |
| `tools/license_audit.py` | 2447 | Code-provenance audit: how much of our tools/ resembles third-party repos. | 11-22 |
| `tools/look_analysis.py` | 3961 | 逐个分析 11 个富士观感：中性轴 tone 形状 + 颜色统计。输出 markdown 表。 | 11-13 |
| `tools/look_capacity.py` | 1757 | 对全部富士观感做"可交付能力"分析：只用 tone+色度（六轴已作废）时的残差。 | 11-15 |
| `tools/look_ends_desat.py` | 3822 | 两端去饱和：照 handoff-notes 的量化结论，高光/暗部都往中性拉。 | 11-13 |
| `tools/look_knobs.py` | 4682 | 在 LUT 结果上叠四旋钮（黑白场/阴影/高光/饱和），出阶梯图。 | 11-13 |
| `tools/look_preview.py` | 2653 | 离线预览：把固件里的色度曲线直接作用到一张已有照片上。 | 11-11 |
| `tools/look_sheet.py` | 5544 | 把 11 个富士观感套在同一张 RAW 上，出联络表（每格标名字+关键参数）。 | 11-13 |
| `tools/look_spec4.py` | 34347 | 把四种富士胶片观感落成「可直接写进固件」的规格：tone 256 + 色度块 100 B。 | 11-15 |
| `tools/look_spec4_check.py` | 6367 | 为文档收集事实：读 .tmp/looks_final/<look>.json，独立解码 + 独立模拟，打印/doc 用的表。 | 11-15 |
| `tools/look_spec_matrix.py` | 31370 | 在 tone + 100 B 色度块之外再加一层 3×3 矩阵，重算四套 look。 | 11-15 |
| `tools/lut_analyze.py` | 6197 | 解析 .cube LUT，给出「它把颜色怎么改了」的可读结论。 | 11-12 |
| `tools/lut_apply.py` | 2173 | 把 .cube 3D LUT 应用到一张 sRGB 图片上（三线性插值 + 可选曝光补偿）。 | 11-10 |
| `tools/lut_fit.py` | 4385 | 把富士 LUT 当成「显示域里的映射」来测：它让颜色怎么变。 | 11-12 |
| `tools/lut_remap709.py` | 3484 | 把「F-Log2 -> Rec.709」的 cube 重映射成「Rec.709 -> 风格」的 cube。 | 11-10 |
| `tools/lut_to_patch.py` | 1119 | Turn a 256-entry 10-bit LUT (text, hex words) into an mkpatch patch file. | 11-12 |
| `tools/make_chroma_target.py` | 3024 | 生成色度靶图:同一亮度下 8 色相 × 4 饱和度,用来反推色度块是否生效。 | 11-15 |
| `tools/make_release_patch.py` | 2121 | Generate a single patch file (blk N:OFF=HEX lines) from two images. | 11-22 |
| `tools/make_screen_target.py` | 3468 | 生成 B01 用的屏幕测试图（横板 1920×1080）。 | 11-15 |
| `tools/mkpatch.py` | 11761 | Apply byte patches to an OE update container and repair the trailer checksums. | 11-14 |
| `tools/mn10300_asm.py` | 45134 | mn10300_asm.py -- MN10300 / AM33-2 汇编器（编码器）. | 11-09 |
| `tools/mn10300_dis.py` | 34556 | MN10300 / AM33 / AM33-2 disassembler for the Olympus E-M10 Mark II firmware. | 11-12 |
| `tools/mode_dispatch.py` | 615 | Extract the mode -> handler table that ends at 0x60601cc4 (8-byte entries). | 11-15 |
| `tools/mode_measure.py` | 2406 | Measure camera-JPEG vs raw-derived chroma mapping for a set of ORF/JPG pairs. | 11-11 |
| `tools/mode_param_scan.py` | 2054 | 找「画质模式的参数表」：每个模式一组 (contrast, saturation, sharpness) 小整数。 | 11-11 |
| `tools/mx_dis.py` | 710 | Disassemble a RAM range:  python tools/mx_dis.py 0x604B6900 0x604B6F00 [outfile] | 11-13 |
| `tools/mx_probe1.py` | 1821 | Probe 1: dump the 8 colour matrices at 0x605ECECA and hunt for readers. | 11-13 |
| `tools/mx_xref.py` | 2341 | Code xref helpers for the E-M10 II image (MN10300/AM33-2). | 11-13 |
| `tools/oc_xref.py` | 2719 | opench helper: scan call/jmp/imm32/u32 refs in the decoded block0 image. | 11-15 |
| `tools/odd_slot_hunt.py` | 22711 | odd_slot_hunt.py —— 谁读 tone 组表（0x60600920 + 0x20*i）的**奇数槽** | 11-15 |
| `tools/offline_from_l58.py` | 5357 | 用 L58 标定出的索引仿射 + 历次真机数据（L52/L53/L54/L55）， | 11-21 |
| `tools/offline_newton_cal.py` | 4811 | 离线牛顿（带 L58 标定索引）：只算，不刷机。输出候选包与预测残差。 | 11-21 |
| `tools/offline_solve.py` | 5092 | 离线闭环：用已实测的 f(T)->Y 反解四张 tone 表，并在本地预测输出。 | 11-21 |
| `tools/oly_container.py` | 9163 | Offline Olympus update inspection. No device access or flashing. | 11-22 |
| `tools/omemu_winfix.py` | 4023 | Make flatpanda/om-emu run on Windows and teach modelcheck a patch overlay. | 11-09 |
| `tools/orf_probe.py` | 1914 | ORF 探针：读元数据、导出线性 TIFF 与 sRGB JPEG（no_auto_bright），供与相机直出 JPEG 对比。 | 11-10 |
| `tools/pack_look.py` | 2657 | 把「一条全局 tone 曲线 + 每档六轴」打成一个 patch 文件（配合 tools/mkpatch.py）。 | 11-14 |
| `tools/patch_check.py` | 4833 | patch_check.py —— 出包前的三道硬门（离线，不需要刷机）。 | 11-19 |
| `tools/pm_find.py` | 1143 | Byte-pattern / dword-sequence search over the decoded image. | 11-13 |
| `tools/pm_gain5.py` | 953 | Scan whole image for 5-term geometric ramps with ratio ~1.065 (contrast +/-6.5%/ | 11-13 |
| `tools/pm_grepdis.py` | 693 | Search .tmp/full_dis.txt (huge linear disassembly) for a regex, with line contex | 11-13 |
| `tools/pm_hex.py` | 503 | Dump hex + ascii for RAM ranges. | 11-13 |
| `tools/pm_range.py` | 664 | Extract lines from .tmp/full_dis.txt whose leading address is within [lo,hi]. | 11-13 |
| `tools/pm_scan.py` | 962 | Scan full_dis.txt for a regex, print hits whose address is inside given regions. | 11-13 |
| `tools/pm_strdump.py` | 861 | Dump the ****_PARAM debug field-name region as C strings with addresses. | 11-13 |
| `tools/pm_strscan.py` | 1056 | Scan block0 for debug strings related to colorTone / picture mode. | 11-13 |
| `tools/pm_tabA.py` | 642 | Dump settings registry table A (93 x 20B @ file 0x36F000) with name strings. | 11-13 |
| `tools/pm_taba2.py` | 890 | Dump settings table A (93 rows) with name_struct words and list arrays. | 11-13 |
| `tools/pm_tabb.py` | 1365 | Dump settings table B rows and their value arrays, focusing on small element cou | 11-13 |
| `tools/pm_u16.py` | 574 |  | 11-13 |
| `tools/pm_xref32.py` | 688 | Find 32-bit immediate references to given RAM addresses inside block0. | 11-13 |
| `tools/preflight_landing.py` | 2899 | Landing preflight: static checks only, and it refuses to certify. | 11-19 |
| `tools/preview_on_jpeg.py` | 2746 | 在真实照片上直接预览曲线改动（不刷机）。 | 11-12 |
| `tools/preview_trace2.py` | 7055 | preview_trace2.py — 只读静态追踪：预览链的 tone 表到底由谁写、写进哪个字段。 | 11-22 |
| `tools/probe_auto.py` | 2847 | 自动补边界的探针：跑到未映射地址 → 把该地址当普通 RAM 补上 → 重跑，直到跑通。 | 11-12 |
| `tools/probe_target.py` | 4689 | 按靶图已知几何测量照片里的色块（不依赖外部库，只用 numpy + PIL）。 | 11-15 |
| `tools/ptr_chain.py` | 1518 | Walk pointer ancestry upward: who points at X, and at that, up to N levels. | 11-15 |
| `tools/ptrfind.py` | 650 | ptrfind.py <addr...>  -- u32 little-endian references, written to analysis/ptrfi | 11-12 |
| `tools/ramp_curve.py` | 6489 | 从"满幅 256 级灰阶 ramp"照片里抽出完整色调曲线。 | 11-11 |
| `tools/rangescan.py` | 620 | rangescan.py <lo> <hi>  -- list all u32 values in [lo,hi] found in the image, gr | 11-12 |
| `tools/rawpipe.py` | 25035 | rawpipe —— 「ORF RAW → 相机 JPG」纯离线复现管线（库 + CLI）。 | 11-15 |
| `tools/rawpipe_check.py` | 35038 | rawpipe_check —— 用真实配对照片验证「ORF → 相机 JPG」复现精度。 | 11-15 |
| `tools/read_b01_probe.py` | 4230 | 读 B01 探针的四组 ORF+JPG，反查「画面模式 → tone 目录」。 | 11-15 |
| `tools/read_dial_probe.py` | 5549 | read_dial_probe.py —— 读 L37_dialprobe 写在 EXIF UserComment 里的拨盘快照 | 11-21 |
| `tools/read_keyprobe.py` | 3420 | 读 B20 探针包拍出来的灰卡照片：反推每个模式命中了哪张探针表（= 哪个 filter id）。 | 11-15 |
| `tools/read_modeprobe.py` | 3467 | 读 B24b/B24c/B24d 模式探针的照片，解出每个档位的 k。 | 11-15 |
| `tools/read_probe.py` | 2060 | Read B75A/B75B trace records from a JPEG's EXIF UserComment. | 11-17 |
| `tools/read_slotprobe.py` | 3601 | 读 B18 槽位探针的四组 ORF+JPG，反查「画面模式 → (dir, slot)」。 | 11-15 |
| `tools/read_zonal.py` | 25206 | read_zonal.py — 读一张 B02 色相靶照片，反推「12 区 ↔ 色相带」的映射。 | 11-15 |
| `tools/ref_classify.py` | 1623 | Classify reference sites as code/data and annotate the immediate instruction. | 11-15 |
| `tools/ref_index.py` | 2243 | Build a static reference index: table base -> every 4-byte pointer to it. | 11-15 |
| `tools/ref_scan.py` | 1447 | Unaligned 4-byte pointer scan (catches immediates inside instructions). | 11-15 |
| `tools/render_compare.py` | 7619 | 把 ORF 用三条流水线各渲一遍：原厂近似 / 富士 LUT / 我们的补丁。 | 11-12 |
| `tools/render_from_raw.py` | 2860 | 从 RAW 渲染候选曲线（不做 LUT-on-JPEG），并与相机实际 JPEG 对比。 | 11-12 |
| `tools/render_with_matrix.py` | 3853 | 任务 D：把 3x3 色彩矩阵接进离线渲染（相机链：WB -> 矩阵 -> tone 表）。 | 11-13 |
| `tools/s14_probe_gen.py` | 3902 | S14: 组探针 —— 给 8 个组各一个独特的、一眼能认出来的色度签名。 | 11-11 |
| `tools/s16_look_gen.py` | 2854 | S16: 第一个「真 look」—— 只改 0x605D4829 这一块，全局生效。 | 11-11 |
| `tools/s20_look_gen.py` | 1939 | S20：把全局色度块 0x605D4829 改成一套「暖调淡彩」look。 | 11-11 |
| `tools/s23_gen.py` | 2905 | S23: install a computed tone LUT and point every mode at it. | 11-12 |
| `tools/s24_gen.py` | 4417 | S24: build a 256-entry 10-bit tone LUT from the merged measured curve T. | 11-12 |
| `tools/s26_probe_gen.py` | 2179 | S26：给 33 张 tone 表各一个不同的亮度增益，用来一次性测出「模式 -> 表」的映射。 | 11-12 |
| `tools/s52_decode.py` | 2716 | S52 判读：在一组同场景照片里，按"相对影调"认签名。 | 11-13 |
| `tools/scan_4788.py` | 1094 | Find every reference to settings+0x4788 / neighbouring offsets. | 11-13 |
| `tools/scan_le.py` | 3515 | Little-endian structural scan of the E-M10 II main component. | 11-09 |
| `tools/scan_struct.py` | 5730 | Offline structural scan of the decoded E-M10 II main component. | 11-09 |
| `tools/scene_ab.py` | 2385 | Compare two real-scene shots (same framing, different firmware). | 11-14 |
| `tools/screen_curve.py` | 4054 | 从平板标板照片里按透视变换取出 21 级灰阶曲线。 | 11-11 |
| `tools/scrub_release.py` | 1969 | Scrub release docs: absolute paths, firmware blobs, risky wording, long hex. | 11-22 |
| `tools/second_verify.py` | 10359 | second_verify.py —— 固件包的「二次核验」（独立于构建脚本） | 11-16 |
| `tools/segments.py` | 25819 | Segment map for the E-M10 II firmware image (PEN-F atlas, table 1). | 11-15 |
| `tools/sem_store_scan.py` | 2249 | sem_store_scan.py — 全镜像线性扫描 (disp,An) 读写指令。 | 11-15 |
| `tools/sem_tone_maps.py` | 3566 | sem_tone_maps.py — 两张哈希派发表 + bank2 曲线族的机械映射。 | 11-15 |
| `tools/six_callers.py` | 602 |  | 11-12 |
| `tools/six_calls.py` | 882 |  | 11-12 |
| `tools/six_dump.py` | 931 | Dump the 0x30-stride table at 0x605ECFxx and find its referrers. | 11-12 |
| `tools/six_dumptab.py` | 844 |  | 11-12 |
| `tools/six_layout.py` | 841 | Precise layout of the preset parameter table around 0x605ECF00. | 11-12 |
| `tools/six_order.py` | 1837 | six_order.py | 11-12 |
| `tools/six_probe.py` | 1447 | Probe EM10-II firmware for six-axis hue/sat signatures (read-only). | 11-12 |
| `tools/six_ptrs.py` | 1275 |  | 11-12 |
| `tools/six_ptrtab.py` | 668 |  | 11-12 |
| `tools/six_reader_scan.py` | 1166 | Find code that reads the six-axis param block (+108..+128). | 11-12 |
| `tools/six_refmap.py` | 844 |  | 11-12 |
| `tools/solve_mp.py` | 3592 | 用 M/P 各自的 EV 三点扫描，离线拟合 f_T->Y 并反解表；V/N 保持 L55。 | 11-21 |
| `tools/solve_tonepatch.py` | 4325 | 按索引做局部线性标定 + 样条锚点求解，输出 build/<firmware>.BIN（不刷机）。 | 11-21 |
| `tools/split_judge.py` | 1229 | Judge a tone-group split build against the official baseline. | 11-14 |
| `tools/strdump.py` | 1120 | strdump.py <RAMstart> <RAMend> [outfile] | 11-12 |
| `tools/strength_preview.py` | 3978 | 多张 RAW 的四联对比：相机直出 / CC 强度 0.6 / 1.0 / 1.35 / 1.7。 | 11-13 |
| `tools/strfind.py` | 1268 | strfind.py <pattern> [pattern2 ...] | 11-12 |
| `tools/tone44_to_patch.py` | 1134 | 把一条 256 项 10 位 LUT 原位写进 44 张 tone 表（0x605D8344 + k*0x200）。 | 11-12 |
| `tools/tone_fit.py` | 4110 | Merge several raw/JPEG pairs into one tone curve T: raw-linear -> JPEG luma. | 11-12 |
| `tools/tone_group_ladder.py` | 2246 | Give every tone-table group its own brightness, so ONE flash reveals which | 11-14 |
| `tools/tone_group_split.py` | 1777 | Darken selected tone-table groups (by group index) to probe which group each pic | 11-14 |
| `tools/tone_lut_from_T.py` | 3101 | Build a 256-entry 10-bit tone LUT from the measured raw->JPEG curve T. | 11-12 |
| `tools/tone_lut_gen.py` | 3644 | 生成色调 LUT 补丁（0x605D8144 恒等表 + 三张 gradation 表）。 | 11-12 |
| `tools/tone_probe.py` | 6705 | Recover the camera's raw->JPEG tone curve T by stacking exposures. | 11-12 |
| `tools/verify_b22_looks.py` | 16346 | B22 四套 look 包：独立回读校验（不复用出包器代码）。 | 11-15 |
| `tools/verify_b32_chain.py` | 22637 | verify_b32_chain.py —— B32_looks_guard 离线全链验证：派发 → 算 k → 写 stash → 上传器读 stash →  | 11-15 |
| `tools/verify_b37_chain.py` | 12949 | verify_b37_chain.py —— B37 / B38 的端到端离线验证（libsim）。 | 11-16 |
| `tools/verify_b48.py` | 5470 | verify_b48.py —— B48 离线整链验证：派发 → k → stash → 上传器按 k 选表 → 拷进 dest。 | 11-16 |
| `tools/verify_b51.py` | 10373 | verify_b51.py —— B51 离线整链验证：派发 → k → stash → tone 表 / 矩阵表按档分派。 | 11-16 |
| `tools/verify_b52.py` | 10798 | verify_b52.py —— B52 离线整链验证：只验"完整 32 位 key 抄进 EXIF"，画面必须不变。 | 11-16 |
| `tools/verify_b53.py` | 13327 | verify_b53.py —— B53 离线整链验证：tone 表走 bank2、池区零改动、EXIF 写 k 与完整 key。 | 11-16 |
| `tools/verify_b54.py` | 12655 | verify_b54.py —— B54 离线整链验证（B48 几何 + 矩阵极端四档） | 11-16 |
| `tools/verify_b56.py` | 10343 | verify_b56.py —— B56 离线整链验证（改指针做第一级替换 + 真矩阵按档） | 11-16 |
| `tools/verify_b57.py` | 14579 | verify_b57.py —— B57 离线整链验证（第一级指针 + 第二级恒等 + 真矩阵） | 11-16 |
| `tools/verify_b58.py` | 13075 | verify_b58.py —— B58 离线整链验证（B48 几何 + 矩阵极端四档） | 11-16 |
| `tools/verify_b59.py` | 13199 | verify_b59.py —— B59 离线整链验证（B48 几何 + 矩阵极端四档） | 11-16 |
| `tools/verify_b60.py` | 13036 | verify_b60.py —— B60 离线整链验证（B59 全链 + 色度钩子） | 11-16 |
| `tools/verify_b61.py` | 14835 | verify_b61.py —— B61 离线整链验证（B59 全链 + 色度双写） | 11-16 |
| `tools/verify_b62.py` | 6721 | verify_b62.py —— B62 离线验证 = B61 的 verify_b61.py + 闸门翻转三条断言 | 11-16 |
| `tools/verify_b63.py` | 14323 | verify_b63.py —— B63 离线整链验证（B59 回归 + slot1 写入） | 11-16 |
| `tools/verify_b64.py` | 15023 | verify_b64.py — B64 offline full-chain verification (B59 regression + slot1 writ | 11-17 |
| `tools/verify_b65.py` | 13986 | verify_b65.py — B65 offline full-chain verification (B59 regression + guarded sl | 11-17 |
| `tools/verify_b66.py` | 14040 | verify_b65.py — B65 offline full-chain verification (B59 regression + guarded sl | 11-17 |
| `tools/verify_b67.py` | 17639 | verify_b67.py — B67 离线验证：成片色度选择器钩子 + SU 桩（不再写 slot1） | 11-17 |
| `tools/verify_b68.py` | 17696 | verify_b68.py — B67 离线验证：成片色度选择器钩子 + SU 桩（不再写 slot1） | 11-17 |
| `tools/verify_b69.py` | 17815 | verify_b69.py — B69 离线验证：成片色度选择器钩子 + SU 桩（不再写 slot1） | 11-17 |
| `tools/verify_b70.py` | 3798 | verify_b70.py -- read build/<firmware>.BIN back and prove the ten-slot pointer probe. | 11-19 |
| `tools/verify_b71.py` | 3644 | verify_b71.py — B71 HSL 头部指针注入钩的离线验证 | 11-17 |
| `tools/verify_b76.py` | 3259 | verify_b76.py — B76 HSL 双点追踪（B 只置标志，不写 EXIF） | 11-17 |
| `tools/verify_b77.py` | 5129 | verify_b77.py — B77：把真 HSL 表内容写进标准档实际使用的平表 0x605F0798 | 11-17 |
| `tools/verify_b78.py` | 1910 | verify_b78.py — B78 标准档强开 HSL（自造数据集 = n4 清零 + 钩 0x604B974D） | 11-17 |
| `tools/verify_b79.py` | 2712 | verify_b79.py — B79 = 完整 B69 补丁集 + HSL 强开钩（置 bit2、装 n4 零表） | 11-18 |
| `tools/verify_b80.py` | 2495 | verify_b80.py — B80 = B79 + 置脏位 0x10000（强制走 0x9E00CC00 第二路） | 11-18 |
| `tools/verify_b81.py` | 1911 |  | 11-18 |
| `tools/verify_b82.py` | 2024 |  | 11-18 |
| `tools/verify_b83.py` | 2007 |  | 11-18 |
| `tools/verify_b86.py` | 2165 |  | 11-18 |
| `tools/verify_b90.py` | 3467 | verify_b90.py — B90 离线验证：B69 + SD 桩键==2 清 stash（i-e 回原厂） | 11-18 |
| `tools/verify_b93.py` | 2713 | verify_b93.py — B93 = 完整 B69 补丁集 + HSL 强开钩（置 bit2、装 n4 零表） | 11-18 |
| `tools/verify_b95.py` | 19038 | verify_b95.py -- read B95 back and prove the four looks landed. | 11-18 |
| `tools/verify_base1.py` | 2580 | verify_base1.py -- BASE1 静态 + libsim 桩行为 | 11-20 |
| `tools/verify_beta_modeprobe.py` | 8513 | verify_beta_modeprobe.py - 用 libsim 离线验证 B24 各变体的桩与探针表选择。 | 11-15 |
| `tools/verify_guard.py` | 7915 | verify_guard.py —— 守卫探针的离线验收（不碰硬件、不写 build/）。 | 11-21 |
| `tools/verify_hook_look4.py` | 11142 | B13 模拟器验证：上传器 0x60479960 → 0x60479A77，两件事。 | 11-15 |
| `tools/verify_hook_look4b.py` | 12518 | B14 模拟器验证：上传器 0x60479960 → 0x60479A77。 | 11-15 |
| `tools/verify_l12.py` | 3761 | verify_l12.py -- static verification for the L12 six-axis probe. | 11-19 |
| `tools/verify_l13.py` | 3331 | verify_l13.py -- static verification for the L13 six-axis probe. | 11-19 |
| `tools/verify_l14.py` | 3334 | verify_l14.py -- static verification for the L14 six-axis probe. | 11-19 |
| `tools/verify_l15.py` | 3527 | verify_l15.py -- static verification for the L15 six-axis probe. | 11-19 |
| `tools/verify_l16.py` | 3474 | verify_l16.py -- L16（标准四档六轴探针）静态核验。 | 11-20 |
| `tools/verify_l16b.py` | 3473 | verify_l16.py -- L16（标准四档六轴探针）静态核验。 | 11-20 |
| `tools/verify_l16c.py` | 3473 | verify_l16.py -- L16（标准四档六轴探针）静态核验。 | 11-20 |
| `tools/verify_l16d.py` | 3473 | verify_l16.py -- L16（标准四档六轴探针）静态核验。 | 11-20 |
| `tools/verify_l16e.py` | 3473 | verify_l16.py -- L16（标准四档六轴探针）静态核验。 | 11-20 |
| `tools/verify_l17.py` | 3471 | verify_l16.py -- L16（标准四档六轴探针）静态核验。 | 11-20 |
| `tools/verify_l18.py` | 1694 | verify_l18.py -- L18 = stock + 六轴探针（0/1024/2048/3072）静态校验 | 11-20 |
| `tools/verify_l2.py` | 4085 | verify_l2.py -- read build/L2.BIN back and prove the ART key probe. | 11-19 |
| `tools/verify_l20.py` | 19207 | verify_l20.py -- read B95 back and prove the four looks landed. | 11-21 |
| `tools/verify_l21.py` | 2735 | L21 static check: four tone tables = f^-1(OETF(L20 tables)); everything else ==  | 11-21 |
| `tools/verify_l22.py` | 2016 | L22 静态校验：k1/k2 的 tone 表按实测反查改写；k3/k4 与 L20 逐字节相同；其余全同。 | 11-21 |
| `tools/verify_l23.py` | 2542 | L23 静态校验：四张 tone 表逐点等于 (D_ref-b)/a（逐档实测仿射求逆）；其余与 L20 相同。 | 11-21 |
| `tools/verify_l24.py` | 2685 | L23 静态校验：四张 tone 表逐点等于 (D_ref-b)/a（逐档实测仿射求逆）；其余与 L20 相同。 | 11-21 |
| `tools/verify_l25.py` | 2702 | L23 静态校验：四张 tone 表逐点等于 (D_ref-b)/a（逐档实测仿射求逆）；其余与 L20 相同。 | 11-21 |
| `tools/verify_l26.py` | 2894 | L23 静态校验：四张 tone 表逐点等于 (D_ref-b)/a（逐档实测仿射求逆）；其余与 L20 相同。 | 11-21 |
| `tools/verify_l27.py` | 2861 | L23 静态校验：四张 tone 表逐点等于 (D_ref-b)/a（逐档实测仿射求逆）；其余与 L20 相同。 | 11-21 |
| `tools/verify_l3.py` | 4374 | verify_l3.py -- read build/L3.BIN back: L2 + HSL dataset swap. | 11-19 |
| `tools/verify_l31.py` | 2263 | L31 静态校验：L30 全量 + N 档槽位 1 字节（0x605E9CCC: 01 -> 02）。 | 11-21 |
| `tools/verify_l32.py` | 2715 | L32 静态校验：L31 全量 + k=1(V) tone 表换成"恒等 + 4 处尖峰"探针表。 | 11-21 |
| `tools/verify_l33.py` | 2136 | L33 静态校验：L31 全量 + tone bend 门一个字节（0x604B9F1F: C8 -> CA）。 | 11-21 |
| `tools/verify_l37.py` | 6123 | verify_l37.py —— L37_dialprobe 的静态校验（只读，不写包） | 11-21 |
| `tools/verify_l39.py` | 10743 | verify_l39.py —— L40_keyprobe 的静态校验（只读，不写包、不 import build_l39） | 11-21 |
| `tools/verify_l4.py` | 2712 | verify_l4.py -- read build/L4.BIN back: L2 looks + runtime HSL dataset probe. | 11-19 |
| `tools/verify_l42.py` | 2896 | L42 静态校验：L39 全量 + k2(Natural) tone 表整体乘 0.6。 | 11-21 |
| `tools/verify_l43.py` | 2864 | L43 静态校验：L39 全量 + 四张 tone 表全部乘 0.6。 | 11-21 |
| `tools/verify_l44.py` | 2233 | L44 静态校验：四张 tone 表全部替换为 8 级阶梯，其余与 L39 一致。 | 11-21 |
| `tools/verify_l48.py` | 1379 | 静态校验 L48：相对 L47 只应改变四张 tone 表（端点归一）。 | 11-21 |
| `tools/verify_l5.py` | 2091 | verify_l5.py -- read build/L5.BIN back: L4 probe + menu order change. | 11-19 |
| `tools/verify_l52.py` | 1437 | 静态校验 L48：相对 L47 只应改变四张 tone 表（端点归一）。 | 11-21 |
| `tools/verify_l53.py` | 1436 | 静态校验 L48：相对 L47 只应改变四张 tone 表（端点归一）。 | 11-21 |
| `tools/verify_l54.py` | 1439 | 静态校验 L48：相对 L47 只应改变四张 tone 表（端点归一）。 | 11-21 |
| `tools/verify_l55.py` | 1358 | L55 静态校验：与 L54 只差四张 tone 表；表单调、端点、N 槽、弯曲门、钩点与 L54 相同。 | 11-21 |
| `tools/verify_l58.py` | 1709 | L58 静态校验：只改四张 tone 表；表为 0/1023 交替；钩点/门/槽与 L55 一致。 | 11-21 |
| `tools/verify_l6.py` | 2642 | verify_l6.py -- read build/L6.BIN back: L2 looks + selector-entry key probe. | 11-19 |
| `tools/verify_l62.py` | 2334 | verify_l62.py -- 静态校验 L62 相对 L55 只改了 V 档色度块的 y/z | 11-22 |
| `tools/verify_l7.py` | 4269 | verify_l7.py -- read build/L7.BIN back: L2 + HSL dataset swap at n0. | 11-19 |
| `tools/verify_l8.py` | 4636 | verify_l8.py -- read build/L8.BIN back and prove the ART1/ART2 routing probe. | 11-19 |
| `tools/verify_l9a.py` | 4646 | verify_l8.py -- read build/L9a.BIN back and prove the ART1/ART2 routing probe. | 11-19 |
| `tools/verify_l9b.py` | 3707 | verify_l9b.py -- L9b = L1 + 4 bytes: ART2 cell slot0  n0 -> n6. | 11-19 |
| `tools/verify_m1.py` | 11832 | verify_m1.py -- independent verifier for build/M1_sdprobe.BIN. | 11-22 |
| `tools/verify_preview_fix.py` | 12686 | verify_preview_fix.py -- 预览表指针补丁的静态审计（只读，不改任何东西） | 11-22 |
| `tools/verify_preview_permode.py` | 26031 | verify_preview_permode.py — 校验 build/PREVIEW_PERMODE.BIN（预览分档取表桩）。 | 11-22 |
| `tools/verify_release.py` | 3135 | verify_release.py -- 成品包的静态审计：从 stock 起的每一处改动都必须落在白名单里 | 11-22 |
| `tools/verify_ui_names.py` | 15052 | Audit the B95 UI-name patch: pointers, UTF-16 text, NUL terminators, and "nothin | 11-18 |
| `tools/verify_ui_scp.py` | 11423 | Audit the SCP (quick-select bar) name patch together with the B95 menu patch. | 11-19 |
| `tools/verify_y1.py` | 2786 | verify_y1.py -- Y1: SD 抓键 + SU 写 EXIF（stock 画质） | 11-20 |
| `tools/watch_field.py` | 36974 | watch_field.py — 用 libsim 的寄存器轨迹反推"这段代码碰了哪块内存"。 | 11-21 |
| `tools/wb_probe.py` | 25489 | wb_probe —— 相机 JPG 的白平衡到底记在哪里，rawpipe 的白平衡环该怎么写。 | 11-15 |
| `tools/which_table.py` | 2482 | 哪张 tone 表被用上了？—— 用 ORF 反推，跟 JPG 的亮度比。 | 11-16 |
| `tools/xref_call.py` | 1594 | 扫描 block0 里所有 `call (d32,PC)`（操作码 0xDD）的目标，列出命中指定地址的调用点。 | 11-10 |
| `tools/zonal_build.py` | 36494 | zonal_build.py — 12 区色相参数 → 喂给 ISP 的十张表（+ 掩码）的合成器， | 11-16 |
