# tools/ 目录说明

> 这里只放**发行流程需要**的最小工具集。
> 历史构建/探针脚本（`build_bXX`、`verify_bXX`、`scan_*`、`six_*`、`pm_*` 等）不随发行版发布，见文末。

## 先看哪几个

| 文件 | 用途 |
|---|---|
| `mkpatch.py` | 把 `patches/*.patch` 应用到官方 stock，生成成品 |
| `build_release.py` | 从 stock 一次组装 `RELEASE_v1`（工作区用，需要 `stock/` 与 `.tmp/`） |
| `verify_release.py` | 静态审计：从 stock 起的每处改动都必须在白名单内 |
| `make_release_patch.py` | 从 stock 与成品生成 `.patch` 定义 |
| `flash_gate.py` | 写卡前判定；只认 `CARD_OK sha=…`，写后回读 |
| `audit_beta_pack.py` | 容器/块/sha/钩子结构审计 |
| `patch_check.py` | 钩点可达性与单条补丁检查 |
| `second_verify.py` | 相对 base 的二次差异审计 |
| `scrub_release.py` | 发布前文档脱敏/路径改写 |
| `gen_packages.py` | 生成包台账辅助 |
| `oly_container.py` | 容器解析/编码库（16 字节组置换 + XOR 0xFF） |
| `mn10300_dis.py` | MN10300 反汇编器（`patch_check` 需要） |

## 标准发行流程

在工作区（有 `stock/`、`build/`、`.tmp/`）执行：

```bat
python tools\build_release.py
python tools\verify_release.py
python tools\make_release_patch.py
python tools\mkpatch.py stock\OLY_E_120_1400_0000_0000.BIN ^
  --out .tmp\replay.bin --patch-file build\RELEASE_v1.patch
```

重放 sha 必须等于成品 sha，再把白名单文件同步到发行仓库。
完整规则见 `docs/release/UPLOAD-CHECKLIST.md`。

## 历史脚本

2026-11-24 结构整理前，`tools/` 有 369 个脚本。
其中 357 个是历史构建、验证和一次性探针，已从发行树移除，但仍可从 git 历史找回：

```bash
git show 53f6e70:tools/build_b90.py
```

工作区的 `tools/` 仍保留完整历史脚本；发行版不再携带。
如果某个历史脚本被重新需要，先在工作区验证，再按“新增工具”的规则加入这里的正式入口，不要直接恢复整个目录。

## 不要做

- 不要从工作区整目录 `cp -r tools` 过来；
- 不要把 `build/`、`stock/`、`.tmp/`、`photos/` 带进来；
- 不要把一次性探针放进这个目录；
- 不要让发行工具依赖工作区绝对路径。
