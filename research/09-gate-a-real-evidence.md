# 09 · 门 A 的真机证据

> Status: 未通过（机器口径 0/10；人工 2 covered / 7 partial / 1 missing）
> Evidence: 真机照片、视频、坏文件回放
> 适用范围：E-M10 II / E120 1400 0000 0000

## 问题

“门 A”要求 10 个核心用例都有真机、可复现、带时间/文件/屏幕/协议记录。
我们到底做到了几条？

## 原来的判断

早期以为“能开机、能拍、能回放”就算门 A 通过。
后来把 10 条拆开，才发现“能拍”只是第 5 条的一部分。

## 方法

1. 10 条用例逐条定义“最小证据”；
2. 用手机拍屏幕、相机产 JPEG/ORF、坏文件回放、卡上文件检查；
3. 证据归档到 `analysis/real-evidence-2026-11-23/`；
4. 按“真机/模拟/静态/推理”标级；
5. 不用“看起来正常”替代文件和时间。

## 结果

| # | 用例 | 状态 | 现有证据 |
|---|---|---|---|
| 1 | boot_segments | partial | 3 次开机到 LV；无串口/阶段串，毫秒时序没有 |
| 2 | live_view | partial | 视觉覆盖；无 `0x9E00A400/CC00` 实际值 |
| 3 | half_press | partial | AF/AE 画面变化；无按键→AE/AF 的毫秒时序 |
| 4 | full_press | partial | 快门/写卡可见；无单拍时序与逐帧对应 |
| 5 | still_write | covered | 9 组 JPG/ORF，EXIF 可读 |
| 6 | playback | covered | 正常回放 + 坏文件“该图像不能显示” |
| 7 | menu_ui | partial | 菜单视频；无槽位→画质映射 |
| 8 | usb_ptp_file_access | missing | 相机 USB 线特殊，硬件不可得 |
| 9 | mount_fs | partial | 插卡/无卡提示可见；无挂载时刻 |
| 10 | scene_art_color_switch | partial | 四档 look 有；ART 未覆盖 |

机器口径：**0/10**（每条都要求真机、可复现、带时间/文件/屏幕/协议记录；部分证据不够）。
人工口径：**2 covered / 7 partial / 1 missing**。

## 关键证据

- 9 组 JPG/ORF（V/N/M/P 与重复样本）；
- 7 段手机视频（开机、半按、全按、四档、回放、坏文件）；
- 3 份坏文件：截断/主图头坏/缩略图也坏；
- 回放坏文件出现“该图像不能显示”，可退出；
- 相机原图与 OM Workspace 导出对比：官方软件是重新渲染，不是只改元数据。

## 被推翻的结论

- “能拍就算 still_write 通过” → 不够；还要 JPEG 完整、EXIF 可读、文件落卡。
- “坏文件都会报错” → 错；保留 160×120 缩略图时相机会用缩略图回退显示。
- “官方软件导出=相机原图” → 错；像素平均差 19.3/255，阴影/高光曲线都变了。
- “USB/PTP 可以后补” → 当前硬件不可得，不是没录。

## 影响

- 门 A 仍是 0/10；不能拿“2 covered”冒充通过；
- 发行版不声称门 A 通过；
- 再补门 A 需要一次 30–45 分钟真机 session，且要接受 USB 仍 missing。

## 未证

- boot 的毫秒级阶段；
- LV 的寄存器实际值；
- 半按→AE/AF 的因果与时序；
- 单拍从快门到文件落卡的完整时间线；
- 菜单槽位映射；
- 挂载时刻/拔卡/坏卡。

## 相关

- `research/06-gates-and-evidence.md`
- `research/04-looks-loader-freeze.md`
- `docs/releases/2026-11-24.md`
