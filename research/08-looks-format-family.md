# 08 · LOOKS 文件格式与家族

> Status: 格式已定；apply 未打通；loader 冻结
> Evidence: 静态/构建验证 + 真机只读
> 适用范围：E-M10 II / E120 1400 0000 0000

## 问题

怎么把四档 look 放进一个卡上文件，既不重刷固件，又能扩展成多个“家族”？

## 结果

### 1. 目录与命名

卡根固定名：

```text
SELECT.TXT      2 B，首字节 '1' 或 '2'
LOOKS1.BIN      2,880 B，家族 1
LOOKS2.BIN      2,880 B，家族 2
```

- fileif 没有目录枚举，只能按固定名 `fopen`；
- 盘符 A:..F: 动态枚举，不硬编码；
- `SELECT.TXT` 只读前 2 B；缺失/空/非法 → `F=0`，按家族 1。

### 2. 文件结构

```text
0x000  64 B 头
0x040  4 × 32 B 槽表 = 128 B
0x0C0  128 B 保留/对齐空隙
0x140  4 × 640 B 槽载荷 = 2560 B
0xB40  文件结束（2880 B）
```

也就是：

- 头 64 B；
- 槽表 4×32 = 128 B；
- 从 `0x0C0` 到 `payload_off=0x140` 有 128 B 空隙；
- 载荷 4×640 = 2560 B；
- 总计 64+128+128+2560 = 2880 B。

### 3. 头字段

- magic `EM10LK01`
- version = 1
- header_size = 0x40
- slot_count = 4
- slot_size = 0x280（640 B）
- payload_off = 0x140
- file_size = 0xB40（2880 B）
- default_slot
- 头 CRC-32：借 reserved1[0..3]@0x24，覆盖 `0x00..0x23` 36 B
- 不做全文件 CRC（当前只读头，不读全 payload）

### 4. 槽内容

每槽 640 B：

- tone 512 B（u16×256）
- matrix 18 B（int16 3×3，Q8）
- chroma 100 B
- pad 10 B

映射：

- look_id 1..4 对应参数表 k 1..4（V/N/M/P）；
- 槽表 `[2,1,2,3,2,2,2,2]` 是 8 B 常量映射；
- `default_slot` 只决定“先读哪一项”，不是原厂语义；
- 真正的 k→槽交给 apply 阶段。

### 5. 家族

- 每个家族 4 槽；
- `LOOKS1.BIN` / `LOOKS2.BIN` 各 2,880 B；
- 换家族只改 `SELECT.TXT`，不刷固件；
- 机内没有文本编辑器，改 TXT 需要关机→拔卡→改→插卡→开机。

## 已验证

- 头结构、CRC、槽表、`SELECT.TXT` 读取：真机 Stage L 通过；
- 家族 1/2 切换：`F=1/2` 真机通过；
- 读 16 B `P4TEST`、读 64 B 头：真机通过；
- **apply 未打通**：tone 512 B 从未在真机上成功写入并生效。

## 未证

- 640 B 槽的完整 apply；
- 矩阵/色度在 LOOKS 路径上的实际消费；
- 预览/成片两个窗口是否都吃到；
- 色度 100 B 的格式是否与参数块完全一致；
- 头 CRC 的 reserved 区域是否会被原厂改写。

## 为什么冻结

T0bA 在 JPEG 写卡事务里读 1.8 KB 并写 tone，真机重启；loader 冻结。
格式本身不背这个锅，是“读卡时机”失败。

## 相关

- `research/04-looks-loader-freeze.md`
- `research/10-hsl-table-format.md`
- `docs/releases/2026-11-24.md`
