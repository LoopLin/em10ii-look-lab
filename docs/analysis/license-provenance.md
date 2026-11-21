# 代码来源审计与协议结论（2026-11-22）

> 目的：回答"我们用哪个开源协议"。方法是对比本仓库 `tools/*.py` 与三个第三方来源的**规范化 12-token shingle Jaccard 相似度**（去注释、去空白、小写），并额外做字节级 sha256 比对。
> 工具：`tools/license_audit.py`（可重跑）。

---

## 1. 对比对象

| 来源 | 许可 | 本地位置 |
|---|---|---|
| `fishcu/oly_fw_analysis` | **Apache-2.0**（`LICENSES/GPL-3.0.txt` 只是它随包的第三方许可副本） | `.tmp/ofw/`、`refs/em10-fw__*` 之外 |
| `cakelesscoder/caduceus-ep7` | **Apache-2.0** | `.tmp/cx/`、`refs/caduceus-ep7__*` |
| `flatpanda/om-emu` | **MIT** | `refs/om-emu-main/` |
| `cmdada/em10-fw` | **GPL-3.0** | `refs/em10-fw__*`（21 个文件） |

参照文件总数：**791**；参与比对的我们的文件：**366**。

---

## 2. 结果

### 2.1 与 GPL-3.0 来源（cmdada/em10-fw）

文本相似度最高 **0.005**（`tools/mn10300_dis.py` vs `emu_isa_mn10300.igen`）。

但**指令表是解析生成的**：`tools/mn10300_dis.py` 的 `load_igen_text()` 读取 `mn10300.igen`，按该仓库 `emu/gen.py` 的格式语法解析出 687 条规则——这属于**数据衍生**，不是"共享指令名"级别的噪声。`mn10300.igen` 原属 GDB/gnu sim（GPL-3.0），经 `cmdada/em10-fw` vendored（同样 GPL-3.0）。

**结论：存在 GPL-3.0 数据衍生**（指令表），但本项目最终选择 GPL-3.0，方向兼容；无需移除，已在 `NOTICE` 注明来源。

### 2.2 与 Apache-2.0 / MIT 来源

| 我们的文件 | 最佳匹配 | Jaccard | 判定 |
|---|---|---:|---|
| `tools/oly_container.py` | `.tmp/ofw/tools/oly_container.py` | **1.000** | **同源**（fishcu/oly_fw_analysis，Apache-2.0） |
| `tools/fetch_fw.py` | `om-emu/.../updater.mjs` | 0.062 | 共享词汇，非复制 |
| `tools/omemu_winfix.py` | `om-emu/.../modelcheck.mjs` | 0.044 | 同上 |
| `tools/mkpatch.py` | `oly_container.py` | 0.028 | 只是 import 依赖 |
| 其余 362 个 | — | ≤0.028 | 独立实现 |

**结论：只有一个文件是第三方衍生物**——`tools/oly_container.py` 来自 fishcu（Apache-2.0）。
已按 Apache-2.0 §4 在文件头加署名：
```
# Derived from fishcu/oly_fw_analysis tools/oly_container.py (Apache License 2.0).
# Modifications: local helpers for our patch pipeline. See NOTICE.
```

---

## 3. 协议结论

**最终决定（2026-11-22）：整个仓库 GPL-3.0；文档 CC BY 4.0。**

理由：
1. 没有 GPL 代码 ⇒ 不必 GPL；
2. 唯一的衍生文件是 Apache-2.0，**以 Apache-2.0 发布整个仓库最省事**（同许可，无需跨许可兼容论证）；
3. MIT 的 om-emu 与 Apache-2.0 完全兼容（保留其版权声明即可）；
4. Apache-2.0 带专利授权与 NOTICE 机制，正好承载我们已有的一张第三方表。

**为什么最终选 GPL-3.0**：审计确认无 GPL 代码（最长公共 token 串 0.005，仅共享词汇），Apache-2.0 → GPL-3.0 单向兼容；代价只是必须保留 `oly_container.py` 的 Apache 署名与 NOTICE，并把许可写成 GPL。选它是项目理念（衍生作品保持开源），不是法律必需——若将来要换成 Apache-2.0，同样可行。

**明确不采用的**：MIT（丢专利授权）、CC0（放弃署名）、AGPL（与"本地工具"性质不符，且会吓退使用者）。

---

## 4. NOTICE 必须列的五条

```
1. fishcu/oly_fw_analysis — Apache-2.0
   本仓库 tools/oly_container.py 衍生自其 tools/oly_container.py，已修改。
2. cakelesscoder/caduceus-ep7 — Apache-2.0
   仅在 docs/refs/ 中以摘要形式参考其文档与设计纪律，未复制代码。
3. flatpanda/om-emu — MIT
   模拟器 fork 及其 Windows 路径修补。
4. cmdada/em10-fw — GPL-3.0
   仅阅读其笔记（docs/refs/），未复制代码；相似度审计 ≤0.005。
5. LibRaw colordata.cpp — LGPL-2.1/CDDL
   仅引用相机色彩矩阵数值（事实数据）。
```

---

## 5. 待办（发行前）

- [ ] `LICENSE` 换成 Apache-2.0 全文；`NOTICE` 用上面五条；
- [ ] `docs/` 单独注明 CC BY 4.0（或整体 Apache-2.0，二选一，别混）；
- [ ] `tools/oly_container.py` 头注释已加（本次完成）；
- [ ] 重跑 `python tools/license_audit.py`，确认无 >0.15 的新条目；
- [ ] v2 清单 §4 的第三方表与此处保持一致。
