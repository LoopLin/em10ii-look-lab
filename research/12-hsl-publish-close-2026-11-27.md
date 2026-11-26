# HSL 发布时机：A3 稳、close 未命中（2026-11-27）

- Status：探针结论；HSL 仍未打通，发行物未变。
- Evidence：真机 + 静态；不含发行固件、照片或卡符。
- 适用：E-M10 Mark II，E120 v1.4；地址只对本机型有效。

## 问题

标准档 HSL 的页选择结论已经收窄到“页没被选中”。这一轮不再改表，而是找一个能安全执行 publisher 的时机：

- 能不能在 JPEG 写完、fileif 空闲之后发布？
- 发布点到底在哪个 close 路径？
- 如果发布点找不到，下一步换哪条载体？

## 已证事实

### 1. A3：fileif 忙门能避免“写卡中重启”

- 入口 `0x604B9786`；publisher 前读 `0xA1C1CECC`，`and 0xC0000000`。
- 忙则原样执行 stock 三条返回；空闲才进 publisher。
- 真机 96/97/98 同一次开机三张 Vivid，全部完整，无重启。
- 结论：**忙门解决了“第一张写卡中重启”**，但没有证明 HSL 生效；三张观感无明显变化。

### 2. A7N：入口钩本身永远在写卡事务里

EXIF 标记 `P02M`，flag 在 UserComment `+0x70`，branch 在 `+0x71`：

| 照片 | branch | 含义 |
|---|---|---|
| 117 | 无标记 | 第一张，armed=0 |
| 118 | `E1` | A 钩执行，armed=0 |
| 119 | `E2` | A 钩执行，armed=1，fileif 忙 |
| 062 | 无标记 | 第一张，armed=0 |
| 063 | `E1` | 同上 |
| 064–067 | `E2` | 同一次开机，全部忙 |

结论：`0x604B9786` 不在“拍摄前”，而在写卡事务内；armed 之后每次都被忙门挡回。这个入口**永远不可能 publish**。

### 3. A8/A8B：两个 fclose 点都不是 JPEG close

- A8：close 钩 `0x604638C6` → `0x605F2920`。
- A8B：close 钩 `0x6046405C` → `0x605F2920`。
- 两个包都先调用原 close `0x60564832`，再在 armed=1 时写 `close_seen`/`close_ret`，清 armed。
- EXIF 标记 `P02C` 在 UserComment `+0x6C`，`close_seen` 在 `+0x70`，`close_ret` 在 `+0x71`，flag 在 `+0x72`。

真机读数：

- A8：120 无标记；121 `close_seen=0`、flag `E1`；122 `close_seen=0`、flag `E2`。
- A8 复核：124 无标记；125 `E1`；126–129 全部 `E2`，`close_seen=0`。
- A8B：130 无标记；131 `E1`；132/133 `E2`，`close_seen=0`。

结论：`0x604638C6` 和 `0x6046405C` 都不是 JPEG close 的真正路径。继续在这两个点换钩子没有意义。

## 失败的尝试

- 在 `0x604B9786` 入口做 publish：被 fileif 忙门永久挡住；A3 只能证明“不重启”，不能证明“发布”。
- 在 `0x604638C6` 做 close 标记：`close_seen` 始终 0。
- 在 `0x6046405C` 做 close 标记：同样始终 0。

## 现在停在哪

- 不再盲试 fclose 站点。
- 下一步是静态读文件服务命令分发器 `0x604633A8` 的命令表，找真正的 close 命令处理函数。
- 在找到之前，不构建新的 publish 包，不写卡。

## 下一步

1. 静态：从 `0x604633A8` 的命令表出发，定位 close 命令处理函数；确认它是否在 JPEG 写完后执行、`out/ds` 是否仍有效。
2. 只读：先做 close 命中标记包，不 publish；确认 `close_seen=1` 后再谈 publisher。
3. 若 close 路线仍失败，按工作区 `docs/NOW.md` §7 的备选 1（look 对象载体）/备选 2（consumer 指针）评估；不直接开新包。

## 不要误解

- 这不是“HSL 已修好”；
- A3 只证明“写卡中不重启”，不证明颜色变化；
- A8/A8B 是探针失败，不是发行内容；
- 发行物仍是 `RELEASE_v1`，没有新 BIN。
