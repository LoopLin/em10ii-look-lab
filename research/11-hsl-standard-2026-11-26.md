# 标准档 HSL：从“表没送到”到“页没被选中”

- Status：结论已定，补丁未做。
- Evidence：静态 + 真机探针；不含发行固件。
- 适用：E-M10 Mark II，E120 v1.4；地址只对本机型有效。

## 问题

标准档（P/A/S/M）的 12 区 HSL 改表不生效；早期容易误判为“表没有上传”或“选择器没调用”。

## 已证事实

1. 标准档 HSL 主体 `0x604B974D` 每帧执行；H1 `0x604B9754` 能读到 `ctx+0x0C=0xF00D`。
2. 标准帧 `ctx+0x3C0=0`，没有 look 对象，`O=0`。
3. 页选择字 `d3=*(ctx+8)` 的 bit3（`0x8`）/ bit16（`0x10000`）都不置位：
   - bit16 写者要求 `ctx+0x0C∈{0xA200,0xA600}`，标准 `0xF00D` 不满足；
   - bit3 在 stock 全镜像没有找到写者。
4. 分发器只在 bit3/bit16 置位时进入 HSL 页 A/B，所以标准帧不调 consumer `0x604790A2`、不调 uploader `0x60479960`。
5. 即使强塞 `O!=0`，没有 d3 位仍不进入 consumer/uploader；`mask 0x7DF` 也到不了 `dst+0x3F8`。

## 失败的尝试

### 四槽对象

给 4 个 Picture Mode 槽造 HSL 对象。QA 判 FAIL：

- 置位门把标准码 `0xF00D` 排除，目标档根本不写对象；
- 对象 `+0x38` bit28 在未采样的 100 B 块里，publisher 不可保证；
- 没有 d3 位写者，`F=07` 不可能。

未构建、未上机。

### A′ 直接调 publisher

在 H1 之前无条件调用 publisher A 并补 bit16。真机 boot loop：开机/LV 帧也执行，`out`/上下文未就绪。

结论：**结构修改必须先有“真实拍摄帧”判别，不能把初始化帧一起改。**

## 订正：H1 不是 ART 载体

B0-P1 的 16 张探针显示 H1 是标准 HSL 主体；ART 走 `0x604BA83F → 0x604B97F8 → 0x604B922D`。所以 H1 的 `O=0/M=FF` 不能判 ART HSL 死刑，只是测错站台。

## 下一步（只设计）

B3 分四层：P0 只读（对照标准/Color Creator/ART，抓对象）→ P1 对象 only（bit28=0，不发布）→ P2 页选择+bit16（真实拍摄帧）→ P3 表/mask。

P0 没有完成前，不构建 P1。

## 不要误解

- 这不是“标准档 HSL 已修好”；
- 也不是“ART HSL 已死”；
- 发行物仍是 `RELEASE_v1`，没有新 BIN。
