# M1 探针包 —— E-M10 II 一次开机带回 fileif 三问

基线：`build/RELEASE_v1.BIN`（sha256 `0b08a232cee544c5724e8ed661fcad97d75d1e9f9886229c30ea01d2280cafad`）。
产物：`build/M1_sdprobe.BIN`（sha256 `99d7438748cb7fed21ae8815edcf13dae12851aaddfa0121dd17cf811f980fe0`）+ `build/M1_sdprobe.sim.json`（`closed: true`）。
M1 = RELEASE_v1 + 下面声明的 2 处补丁；没有刷机、没有碰 I:/J:、没有改任何已有脚本。

## 1. 改动清单

| 地址 | 长度 | 旧值 | 新值 | 说明 |
|---|---:|---|---|---|
| `0x60479A2B` | 5 B | `DC A9 52 16 00`（jmp `0x605DECD4`） | `DC 2D 92 1F 00`（jmp `0x60672C58`） | 复用已有 SU 钩，改指 M1 桩；只有 `0x60479A2C..2E` 三个字节不同 |
| `0x60672C58` | 880 B | 全 `FF` | 桩代码 + 字面量（见 §4/§5） | 落在登记口袋 `0x60672C20..0x60672FF6` 内，尾部余 46 B |

- 桩范围 `0x60672C58..0x60672FC8`；口袋 `0x60672C20..0x60672FF6`（982 B）在 `docs/map/mem-regions.md:305` 被列为**首选**可写区，`docs/map/mem-regions.md:225`、`docs/analysis/guard-probe-design.md:13` 和 `:67` 同样登记；`tools/build_guard.py` 用的也是这个口袋（注释「982 B，L55 全 FF、无真引用」）。
- 口袋在 RELEASE_v1 与 stock 中逐字节全 `FF`；全镜像 u32 引用普查为 0（仅有的两个错位命中指向 `0x60673000`，在口袋右侧之外）。
- 文件里 `GUARD/BUF/MS/P0..P4/IDX/RD/WR`（`0x60672C20..0x60672C57`）保持 `FF`，只在运行时写；`verify_m1.py` 会断言这一点。
- 容器 block0 的 trailer 加法校验和被重算（`a55ee449 -> b5d7b314`）；header、block1、block2 与 RELEASE_v1 逐字节相同。
- 相对 RELEASE_v1 的 payload 差异共 17 段 / 868 B：钩点 3 B + 桩中非 FF 的 865 B（桩占 880 B，其余是与背景相同的 FF 对齐字节），全部落在上面两段声明范围内。

## 2. 为什么挂在 SU 钩 `0x60479A2B`

1. **不改新钩子。** SD 桩 `0x605DED44` 只有 19 B，装不下五条路径 + 格式化代码；SU 桩 `0x605DECD4` 所在链已经是 RELEASE_v1 的既有钩子，直接把它的入口远跳改到 M1 桩、桩尾再跳回 `0x605DECD4`，原有 `k=N\0` / key / tone 选表行为逐字节不变。
2. **已有真机证据。** SU 钩在 B69 等包里已被真机验证会写 EXIF（`k=N\0` + key），说明这个上下文能安全调 `fileif_*` 之外的基础设施；M1 只是把「写 EXIF」扩展成「调 fileif + 写 EXIF」。
3. **一次性。** 桩在口袋里用 `GUARD`（`0x60672C20`）做状态：`0` → 运行前置 `1`，写完置 `2`；之后每次进钩子只做「恢复寄存器 + 跳回 SU 桩」。即使探测过程中某条路径卡住，也不会每帧重入（不会死循环）。

### 四条路径的覆盖

SU 钩在 tone 上传器 `0x60479960` 内部，上传器只有两个 caller：`0x60478A73`（窗口 A）与 `0x60478A95`（窗口 B），正常拍照流程每帧都会走到。

| 路径 | 是否经过 `0x60479A2B` | 说明 |
|---|---|---|
| capture | 正常路径经过 | 单拍/连拍取景与成像都走上传器 |
| preview | 正常路径经过 | live view 每帧刷新 tone |
| recording | 正常路径经过 | 录像时同样逐帧上传 |
| compact | 正常路径经过 | 与其它模式共用上传器 |

**但这不是保证。** `docs/map/tone-chain.md` §0 第 8 条与 §7 第 7 条列出可跳过插入点的分支：`0x604799E5`、`0x60479A0A`、`0x60479A18`、`0x60479A1B`；其中后两条是 `beq 0x60479A59` 直接跳到 twin path。`patch_check --reach 0x60479960 0x60479A2B` 对 M1 判 FAIL，正是这两条直接跳过（工具只报这两条 `beq`；文档另列两条 `btst/bne` 形态）。**结论：四条路径在正常路径都会经过；一旦某个模式命中 skip，M1 不会运行，该次拍到的 EXIF 里就没有 `M1ms=` 字段。** 这是复用既有钩子的已知代价，也是本包最大的可达性风险之一。

## 3. 桩的逻辑

```
进入 0x60672C58
  保存 a3 / a0 / d2（三次 push）
  读 GUARD；若 ==1 或 ==2 → 恢复 + jmp 0x605DECD4
  GUARD = 1
  IDX = 5（"未成功"）
  d0=0；call fileif_get_media_status()        → MS
  依次试 5 条路径（固定 5 次，无循环等待）：
      a0=path, d0=0, a1="rb"
      call fileif_fopen(path, 0, "rb")        → Pn
      成功 = (a0 != 0 && a0 != 0xFFFFFFFF)
      第一个成功：IDX=n，跳 found；失败：继续下一条
  found:
      a2=handle
      call fileif_fread(BUF, 1, 16, handle)   → RD
      call fileif_fclose(handle)
  写：
      call fileif_fopen("OUT.BIN", 0, "wb")   → WR
      成功：call fileif_fwrite("EM10M1\0\0", 1, 8, handle) → WR；fclose
  格式化：
      在 0x61894CA0 写 95 字符 + NUL
      GUARD = 2
  恢复 d2 / a0 / a3；jmp 0x605DECD4（原 SU 桩，继续写 k=N 与选 tone）
```

- 用到的 fileif 入口与调用帧（按各自 `ret ...,imm8` 反推，已逐条断言）：

| 函数 | 地址 | 调用写法 | 返回 |
|---|---|---|---|
| get_media_status | `0x6056556D` | `call ...,[d2,a2],0xc` | d0（u16） |
| fopen | `0x6056570C` | `call ...,[d2,d3,a2,a3],0x2c` | a0=handle |
| fread | `0x605649BB` | `call ...,[d2,d3,a2,a3],0x18` | d0=读到的元素数 |
| fclose | `0x60564832` | `call ...,[d2,a2,a3],0x10` | d0 |
| fwrite | `0x605648AF` | `call ...,[d2,d3,a2,a3],0x18` | d0=写出的元素数 |

- `get_media_status` 的 `ret` 不保 `a3`，所以桩显式保存/恢复 `a3`；`d2` 是上传器要继续用的值，也保存/恢复。
- `fopen` 按反汇编的三参原型调用：`a0=path`、`d0=flag(0)`、`a1=mode`。如果真机原型其实是 `(path, mode)`，`a0/a1` 仍然正确，只是 `d0` 被忽略；两种原型都不会因为多传一个寄存器而出错。
- `fread/fwrite` 的 size=1、n=16/8，返回值即字节数；`fread` 返回值只取低 16 位写进 `rd=`。
- 五条路径按任务给定顺序，全部失败也会走完五次就转去写路径，不会无限循环。

## 4. 桩的反汇编

`mn10300-elf-objdump` 对 PC 相对 call 的显示会当成绝对地址，这里按最终 payload 重新反汇编；`DD` 行的 disp32 已由构建脚本回填为「目标 − 指令地址」。

```
  60672C58  F8 FE FC                  add 0xfc,SP
  60672C5B  4F 00                     mov a3,(0,SP)
  60672C5D  F8 FE FC                  add 0xfc,SP
  60672C60  43 00                     mov a0,(0,SP)
  60672C62  F8 FE FC                  add 0xfc,SP
  60672C65  4A 00                     mov d2,(0,SP)
  60672C67  FC DE 20 2C 67 60         mov 0x60672c20,a2
  60672C6D  76                        mov (a2),d1
  60672C6E  A5 01                     cmp 0x1,d1
  60672C70  C8 0B                     beq (0x60672c7b,PC)
  60672C72  A5 02                     cmp 0x2,d1
  60672C74  C8 07                     beq (0x60672c7b,PC)
  60672C76  85 01                     mov 0x1,d1
  60672C78  66                        mov d1,(a2)
  60672C79  CA 16                     bra (0x60672c8f,PC)
  60672C7B  5A 00                     mov (0,SP),d2
  60672C7D  F8 FE 04                  add 0x4,SP
  60672C80  5C 00                     mov (0,SP),a0
  60672C82  F8 FE 04                  add 0x4,SP
  60672C85  5F 00                     mov (0,SP),a3
  60672C87  F8 FE 04                  add 0x4,SP
  60672C8A  DC 4A C0 F6 FF            jmp 0x605decd4
  60672C8F  85 05                     mov 0x5,d1
  60672C91  FC 85 4C 2C 67 60         mov d1,(0x60672c4c)
  60672C97  00                        clr d0
  60672C98  DD D5 28 EF FF A0 0C      call 0x6056556d,[d2,a2],0xc
  60672C9F  FC 81 34 2C 67 60         mov d0,(0x60672c34)
  60672CA5  FC DC 48 2F 67 60         mov 0x60672f48,a0
  60672CAB  00                        clr d0
  60672CAC  FC DD 9C 2F 67 60         mov 0x60672f9c,a1
  60672CB2  DD 5A 2A EF FF F0 2C      call 0x6056570c,[d2,d3,a2,a3],0x2c
  60672CB9  FC 80 38 2C 67 60         mov a0,(0x60672c38)
  60672CBF  B0 00                     cmp 0x0,a0
  60672CC1  C8 12                     beq (0x60672cd3,PC)
  60672CC3  F1 D1                     mov a0,d1
  60672CC5  29 01                     add 0x1,d1
  60672CC7  C8 0C                     beq (0x60672cd3,PC)
  60672CC9  00                        clr d0
  60672CCA  FC 81 4C 2C 67 60         mov d0,(0x60672c4c)
  60672CD0  CC BA 00                  jmp 0x60672d8a
  60672CD3  FC DC 51 2F 67 60         mov 0x60672f51,a0
  60672CD9  00                        clr d0
  60672CDA  FC DD 9C 2F 67 60         mov 0x60672f9c,a1
  60672CE0  DD 2C 2A EF FF F0 2C      call 0x6056570c,[d2,d3,a2,a3],0x2c
  60672CE7  FC 80 3C 2C 67 60         mov a0,(0x60672c3c)
  60672CED  B0 00                     cmp 0x0,a0
  60672CEF  C8 13                     beq (0x60672d02,PC)
  60672CF1  F1 D1                     mov a0,d1
  60672CF3  29 01                     add 0x1,d1
  60672CF5  C8 0D                     beq (0x60672d02,PC)
  60672CF7  80 01                     mov 0x1,d0
  60672CF9  FC 81 4C 2C 67 60         mov d0,(0x60672c4c)
  60672CFF  CC 8B 00                  jmp 0x60672d8a
  60672D02  FC DC 5B 2F 67 60         mov 0x60672f5b,a0
  60672D08  00                        clr d0
  60672D09  FC DD 9C 2F 67 60         mov 0x60672f9c,a1
  60672D0F  DD FD 29 EF FF F0 2C      call 0x6056570c,[d2,d3,a2,a3],0x2c
  60672D16  FC 80 40 2C 67 60         mov a0,(0x60672c40)
  60672D1C  B0 00                     cmp 0x0,a0
  60672D1E  C8 12                     beq (0x60672d30,PC)
  60672D20  F1 D1                     mov a0,d1
  60672D22  29 01                     add 0x1,d1
  60672D24  C8 0C                     beq (0x60672d30,PC)
  60672D26  80 02                     mov 0x2,d0
  60672D28  FC 81 4C 2C 67 60         mov d0,(0x60672c4c)
  60672D2E  CA 5C                     bra (0x60672d8a,PC)
  60672D30  FC DC 67 2F 67 60         mov 0x60672f67,a0
  60672D36  00                        clr d0
  60672D37  FC DD 9C 2F 67 60         mov 0x60672f9c,a1
  60672D3D  DD CF 29 EF FF F0 2C      call 0x6056570c,[d2,d3,a2,a3],0x2c
  60672D44  FC 80 44 2C 67 60         mov a0,(0x60672c44)
  60672D4A  B0 00                     cmp 0x0,a0
  60672D4C  C8 12                     beq (0x60672d5e,PC)
  60672D4E  F1 D1                     mov a0,d1
  60672D50  29 01                     add 0x1,d1
  60672D52  C8 0C                     beq (0x60672d5e,PC)
  60672D54  80 03                     mov 0x3,d0
  60672D56  FC 81 4C 2C 67 60         mov d0,(0x60672c4c)
  60672D5C  CA 2E                     bra (0x60672d8a,PC)
  60672D5E  FC DC 80 2F 67 60         mov 0x60672f80,a0
  60672D64  00                        clr d0
  60672D65  FC DD 9C 2F 67 60         mov 0x60672f9c,a1
  60672D6B  DD A1 29 EF FF F0 2C      call 0x6056570c,[d2,d3,a2,a3],0x2c
  60672D72  FC 80 48 2C 67 60         mov a0,(0x60672c48)
  60672D78  B0 00                     cmp 0x0,a0
  60672D7A  C8 31                     beq (0x60672dab,PC)
  60672D7C  F1 D1                     mov a0,d1
  60672D7E  29 01                     add 0x1,d1
  60672D80  C8 2B                     beq (0x60672dab,PC)
  60672D82  80 04                     mov 0x4,d0
  60672D84  FC 81 4C 2C 67 60         mov d0,(0x60672c4c)
  60672D8A  92                        mov a0,a2
  60672D8B  FC DC 24 2C 67 60         mov 0x60672c24,a0
  60672D91  80 01                     mov 0x1,d0
  60672D93  85 10                     mov 0x10,d1
  60672D95  99                        mov a2,a1
  60672D96  DD 25 1C EF FF F0 18      call 0x605649bb,[d2,d3,a2,a3],0x18
  60672D9D  FC 81 50 2C 67 60         mov d0,(0x60672c50)
  60672DA3  98                        mov a2,a0
  60672DA4  DD 8E 1A EF FF B0 10      call 0x60564832,[d2,a2,a3],0x10
  60672DAB  FC DC 9F 2F 67 60         mov 0x60672f9f,a0
  60672DB1  00                        clr d0
  60672DB2  FC DD A7 2F 67 60         mov 0x60672fa7,a1
  60672DB8  DD 54 29 EF FF F0 2C      call 0x6056570c,[d2,d3,a2,a3],0x2c
  60672DBF  FC 80 54 2C 67 60         mov a0,(0x60672c54)
  60672DC5  B0 00                     cmp 0x0,a0
  60672DC7  C8 29                     beq (0x60672df0,PC)
  60672DC9  F1 D1                     mov a0,d1
  60672DCB  29 01                     add 0x1,d1
  60672DCD  C8 23                     beq (0x60672df0,PC)
  60672DCF  92                        mov a0,a2
  60672DD0  FC DC AA 2F 67 60         mov 0x60672faa,a0
  60672DD6  80 01                     mov 0x1,d0
  60672DD8  85 08                     mov 0x8,d1
  60672DDA  99                        mov a2,a1
  60672DDB  DD D4 1A EF FF F0 18      call 0x605648af,[d2,d3,a2,a3],0x18
  60672DE2  FC 81 54 2C 67 60         mov d0,(0x60672c54)
  60672DE8  98                        mov a2,a0
  60672DE9  DD 49 1A EF FF B0 10      call 0x60564832,[d2,a2,a3],0x10
  60672DF0  FC DC A0 4C 89 61         mov 0x61894ca0,a0
  60672DF6  FC DE B2 2F 67 60         mov 0x60672fb2,a2
  60672DFC  85 05                     mov 0x5,d1
  60672DFE  DD 3D 01 00 00 00 00      call 0x60672f3b,[],0x0
  60672E05  FC A6 34 2C 67 60         mov (0x60672c34),d2
  60672E0B  F8 C2 10                  asl 0x10,d2
  60672E0E  85 04                     mov 0x4,d1
  60672E10  DD 0F 01 00 00 00 00      call 0x60672f1f,[],0x0
  60672E17  FC DE B7 2F 67 60         mov 0x60672fb7,a2
  60672E1D  85 03                     mov 0x3,d1
  60672E1F  DD 1C 01 00 00 00 00      call 0x60672f3b,[],0x0
  60672E26  FC A4 4C 2C 67 60         mov (0x60672c4c),d0
  60672E2C  A0 05                     cmp 0x5,d0
  60672E2E  C8 06                     beq (0x60672e34,PC)
  60672E30  28 30                     add 0x30,d0
  60672E32  CA 04                     bra (0x60672e36,PC)
  60672E34  80 58                     mov 0x58,d0
  60672E36  F0 50                     movbu d0,(a0)
  60672E38  41                        inc a0
  60672E39  FC DE BA 2F 67 60         mov 0x60672fba,a2
  60672E3F  85 04                     mov 0x4,d1
  60672E41  DD FA 00 00 00 00 00      call 0x60672f3b,[],0x0
  60672E48  FC A6 50 2C 67 60         mov (0x60672c50),d2
  60672E4E  F8 C2 10                  asl 0x10,d2
  60672E51  85 04                     mov 0x4,d1
  60672E53  DD CC 00 00 00 00 00      call 0x60672f1f,[],0x0
  60672E5A  FC DE BE 2F 67 60         mov 0x60672fbe,a2
  60672E60  85 03                     mov 0x3,d1
  60672E62  DD D9 00 00 00 00 00      call 0x60672f3b,[],0x0
  60672E69  FC A6 24 2C 67 60         mov (0x60672c24),d2
  60672E6F  85 08                     mov 0x8,d1
  60672E71  DD AE 00 00 00 00 00      call 0x60672f1f,[],0x0
  60672E78  FC A6 28 2C 67 60         mov (0x60672c28),d2
  60672E7E  85 08                     mov 0x8,d1
  60672E80  DD 9F 00 00 00 00 00      call 0x60672f1f,[],0x0
  60672E87  FC DE C1 2F 67 60         mov 0x60672fc1,a2
  60672E8D  85 04                     mov 0x4,d1
  60672E8F  DD AC 00 00 00 00 00      call 0x60672f3b,[],0x0
  60672E96  FC A6 54 2C 67 60         mov (0x60672c54),d2
  60672E9C  85 08                     mov 0x8,d1
  60672E9E  DD 81 00 00 00 00 00      call 0x60672f1f,[],0x0
  60672EA5  FC DE C5 2F 67 60         mov 0x60672fc5,a2
  60672EAB  85 03                     mov 0x3,d1
  60672EAD  DD 8E 00 00 00 00 00      call 0x60672f3b,[],0x0
  60672EB4  FC A6 38 2C 67 60         mov (0x60672c38),d2
  60672EBA  85 08                     mov 0x8,d1
  60672EBC  DD 63 00 00 00 00 00      call 0x60672f1f,[],0x0
  60672EC3  FC A6 3C 2C 67 60         mov (0x60672c3c),d2
  60672EC9  85 08                     mov 0x8,d1
  60672ECB  DD 54 00 00 00 00 00      call 0x60672f1f,[],0x0
  60672ED2  FC A6 40 2C 67 60         mov (0x60672c40),d2
  60672ED8  85 08                     mov 0x8,d1
  60672EDA  DD 45 00 00 00 00 00      call 0x60672f1f,[],0x0
  60672EE1  FC A6 44 2C 67 60         mov (0x60672c44),d2
  60672EE7  85 08                     mov 0x8,d1
  60672EE9  DD 36 00 00 00 00 00      call 0x60672f1f,[],0x0
  60672EF0  FC A6 48 2C 67 60         mov (0x60672c48),d2
  60672EF6  85 08                     mov 0x8,d1
  60672EF8  DD 27 00 00 00 00 00      call 0x60672f1f,[],0x0
  60672EFF  00                        clr d0
  60672F00  F0 50                     movbu d0,(a0)
  60672F02  FC DE 20 2C 67 60         mov 0x60672c20,a2
  60672F08  85 02                     mov 0x2,d1
  60672F0A  66                        mov d1,(a2)
  60672F0B  5A 00                     mov (0,SP),d2
  60672F0D  F8 FE 04                  add 0x4,SP
  60672F10  5C 00                     mov (0,SP),a0
  60672F12  F8 FE 04                  add 0x4,SP
  60672F15  5F 00                     mov (0,SP),a3
  60672F17  F8 FE 04                  add 0x4,SP
  60672F1A  DC BA BD F6 FF            jmp 0x605decd4
  60672F1F  88                        mov d2,d0
  60672F20  F8 C8 1C                  asr 0x1c,d0
  60672F23  F8 E0 0F                  and 0xf,d0
  60672F26  28 30                     add 0x30,d0
  60672F28  A0 0A                     cmp 0xa,d0
  60672F2A  C0 04                     blt (0x60672f2e,PC)
  60672F2C  28 27                     add 0x27,d0
  60672F2E  F0 50                     movbu d0,(a0)
  60672F30  41                        inc a0
  60672F31  F8 C2 04                  asl 0x4,d2
  60672F34  29 FF                     add 0xff,d1
  60672F36  C9 E9                     bne (0x60672f1f,PC)
  60672F38  DF 00 00                  ret [],0x0
  60672F3B  F0 42                     movbu (a2),d0
  60672F3D  F0 50                     movbu d0,(a0)
  60672F3F  49                        inc a2
  60672F40  41                        inc a0
  60672F41  29 FF                     add 0xff,d1
  60672F43  C9 F8                     bne (0x60672f3b,PC)
  60672F45  DF 00 00                  ret [],0x0
```

`0x60672DFE` 那一条把 `M1ms=` 本身打进 EXIF，所以代码里没有「先拷贝固定前缀再写数字」的分支；其余 `puts` 调用写分隔符。`0x60672E05` 之后依次是 `ms`、`I`、`rd`、`d`、`wr`、`p`。

## 5. 字面量与运行时字段

| 标签 | 地址 | 字节 |
|---|---|---|
| `s_p0` | `0x60672F48` | `PING.BIN\0` |
| `s_p1` | `0x60672F51` | `\PING.BIN\0`（一个反斜杠） |
| `s_p2` | `0x60672F5B` | `A:\PING.BIN\0` |
| `s_p3` | `0x60672F67` | `DCOLYMP\EM10MOD\PING.BIN\0` |
| `s_p4` | `0x60672F80` | `A:\DCOLYMP\EM10MOD\PING.BIN\0` |
| `s_rb` | `0x60672F9C` | `rb\0` |
| `s_out` | `0x60672F9F` | `OUT.BIN\0` |
| `s_wb` | `0x60672FA7` | `wb\0` |
| `s_msg` | `0x60672FAA` | `45 4D 31 30 4D 31 00 00` = `EM10M1\0\0` |
| `s_l0` | `0x60672FB2` | `M1ms=` |
| `s_l1` | `0x60672FB7` | `,I=` |
| `s_l2` | `0x60672FBA` | `,rd=` |
| `s_l3` | `0x60672FBE` | `,d=` |
| `s_l4` | `0x60672FC1` | `,wr=` |
| `s_l5` | `0x60672FC5` | `,p=` |

运行时字段（文件里仍是 FF）：

| 字段 | 地址 | 内容 |
|---|---|---|
| GUARD | `0x60672C20` | 0 未跑 / 1 运行中 / 2 已完成 |
| BUF | `0x60672C24..33` | fread 的 16 B |
| MS | `0x60672C34` | media status（d0 低 16 位） |
| P0..P4 | `0x60672C38/3C/40/44/48` | 五次 fopen 的 a0 原值 |
| IDX | `0x60672C4C` | 第一个成功的下标 0..4；5 = 全失败 |
| RD | `0x60672C50` | fread 返回值 |
| WR | `0x60672C54` | fopen/ fwrite 返回值 |

### EXIF 字段

写入点 `0x61894CA0`（即 UserComment 区 `0x61894C90 + 0x10`），长度正好 96 B（95 字符 + NUL），结束于 `0x61894CFF`，仍在 117 B 区内。

格式：

```
M1ms=%04X,I=%c,rd=%04X,d=%016llX,wr=%08X,p=%08X%08X%08X%08X%08X
```

- `ms`：media status 低 16 位，4 位十六进制。
- `I`：第一个成功的路径下标 `0..4`；全失败为 `X`。
- `rd`：fread 返回值低 16 位；`0010` 表示读满 16 B，`FFFF` 表示错误/≥65535。
- `d`：BUF 的前 8 B，按 2 个 u32 小端读数后各打 8 位十六进制（共 16 位）；所以**显示的字序与字节序相反**（例如字节 `41 42 43 44` 显示为 `44434241`）。后 8 B 不写，保证整条 ≤96 B。
- `wr`：写路径的返回值。fopen 失败时是 fopen 的 handle 返回值；fopen 成功时是 fwrite 的返回值（`00000008` = 写满 8 B）。
- `p`：P0..P4 各 8 位十六进制，按路径顺序拼接。`00000000` 失败；`FFFFFFFF` 表示「前面已经成功，这条没试」；其它非零值是 handle。

解析示例（数值仅演示）：

```
M1ms=0001,I=3,rd=0010,d=4443424132313030,wr=00000008,p=00000000000000000000000000000002FFFFFFFF
```

读法：media status `0x0001`；第 4 条路径（`DCOLYMP\EM10MOD\PING.BIN`）打开成功；fread 读满 16 B（`d` 只展示前 8 B）；前 4 个字节是 `41 42 43 44`（显示为 `44434241`）；写入 8 B 成功；p0/p1/p2 打开失败，p3 handle=`0x00000002`，p4 因已成功未尝试。

一行正则：

```python
import re
m = re.search(rb'M1ms=([0-9A-F]{4}),I=([0-9A-FX]),rd=([0-9A-F]{4}),d=([0-9A-F]{16}),wr=([0-9A-F]{8}),p=([0-9A-F]{40})', uc)
```

取回方式：`exiftool -UserComment -b 照片.jpg`，或下面这段只读脚本（取 APP1 里 0x9286 的全部字节；`build/_uc.py` 只取前 40 B，会把 M1 字段截断，别用它读 M1）：

```python
import struct, sys
b = open(sys.argv[1], 'rb').read()
i = b.find(b'\xff\xe1')
L = int.from_bytes(b[i+2:i+4], 'big')
t = b[i+4:i+2+L][6:]            # 跳过 APP1 头和 "Exif\0\0"
bo = '<' if t[:2] == b'II' else '>'
u16 = lambda o: struct.unpack_from(bo+'H', t, o)[0]
u32 = lambda o: struct.unpack_from(bo+'I', t, o)[0]
def ents(off):
    for k in range(u16(off)):
        e = off + 2 + 12*k
        yield u16(e), u32(e+4), u32(e+8)   # tag, count, value
exif = next(v for tag, _, v in ents(u32(4)) if tag == 0x8769)
cnt, val = next((c, v) for tag, c, v in ents(exif) if tag == 0x9286)
uc = t[val:val+cnt]
print(uc.decode('ascii', 'replace'))
```

注意 UserComment 的 charset 字段（`0x61894C88`）是全 0，部分工具会把这段显示成二进制；用 `-b` 按字节抓出来再套上面的正则。

**k 字段**：`0x61894C90 +0` 的 `k=N\0` 与 `+4` 的 key 由链尾的既有 SU 桩 `0x605DECD4` 写；M1 只从 `+0x10` 开始写，物理上不触碰 `+0..+7`。这也是 B65/B73 已经真机验证过的共存方式（`docs/EXIF-PROBE.md`：`0x61894CA0` 是 B75 记录基址）。没有在桩里做「读 +0..+7 再回写」：现状在设计时已按 `docs/EXIF-PROBE.md` 和 B65/B73 的真机记录核对，而链尾 k 桩必然把 +0..+7 重写成新的 `k=N`，回写旧值没有意义、会多一次写；保留 k 的保证来自「只从 +0x10 起写」这一物理不重叠，而不是读-改-写。

## 6. 风险与失败模式

| # | 失败模式 | 会发生什么 | 检测 / 回退 |
|---|---|---|---|
| 1 | **口袋从未被真机执行过** | `0x60672C58` 第一次执行就取指异常 / 看门狗复位；表现为开机反复重启或黑屏 | 最大风险。口袋只有模拟器与 L55 静态证据（`docs/analysis/guard-probe-design.md` §9 明确列为未闭合）。回退：拔卡、重新刷 stock 或 RELEASE_v1；相机无持久状态被改 |
| 2 | 文件 I/O 在逐帧 tone 上传器里阻塞 | live view 卡顿、掉帧，极端情况看门狗 | GUARD 置 1 后不会重入；重启即恢复。若只是卡顿，等一次拍完读 EXIF |
| 3 | 第一次调用时 SD 还没挂载 | 五条 fopen 全失败，`I=X`、`p` 全 0；写也失败 | 只回答「这个时刻不可用」，不能回答「永远不可用」。本包一次性，不会重试；需要重刷一次或换更晚的钩子（M2） |
| 4 | 卡是 exFAT | 所有 fopen 失败（`docs/analysis/sd-file-api.md`：exFAT 不支持） | 换 FAT16/32、≤32 GB 卡；结果字段仍会写 |
| 5 | 字面路径 `A:` 与运行时盘符不符 | `p2`/`p4` 失败，`p0/p1/p3` 仍会试 | 这正是要测的；看 `p` 哪一位非零 |
| 6 | fopen 原型与静态推断不符 | 不会崩（多传的 d0 被忽略），但可能全部失败 | 用 `p` 的返回值区分「参数错」与「路径不存在」；下一包可改 `(path,mode)` 两参调用 |
| 7 | handle 恰好是 `0xFFFFFFFF` | 被当作失败，停止该路径 | 概率极低；`p` 里会看到 FFFFFFFF 与「未尝试」不可区分，需结合 `I` |
| 8 | fread 短读 / 出错 | `rd` < 16 或 `FFFF`；`d` 里是残留/部分数据 | 不影响后续写路径；按 `rd` 判断 |
| 9 | 写保护 / 卡满 | `wr` 是错误码；不会创建 `OUT.BIN` | 换可写卡重试 |
| 10 | 写成功但留下了 `OUT.BIN` | SD 根目录多一个 8 B 文件 | 正常预期；拍完手动删除即可 |
| 11 | EXIF 模板把字段覆盖/截断 | 照片里找不到 `M1ms=` | 换一张；`docs/EXIF-PROBE.md` 记录模板不会全清，B65/B73 字段可共存。仍读不到就说明模板路径被改写，需要下一包换写入点 |
| 12 | 96 B 越界 | 会踩到 `0x61894D04` 之后 | 构建时已断言 95+NUL=96；`verify_m1.py` 再断言格式串长度 |
| 13 | 容器校验和/签名被破坏 | 相机拒绝升级或刷机失败 | `audit_beta_pack` / `second_verify` 都过；不要用 `flash_gate` 之外的路径写卡 |

### 回退

- 不刷机：删除 `build/M1_sdprobe.*` 即可，源树无其它改动。
- 已刷：重新刷 `build/RELEASE_v1.BIN`（同基线）或 `<official-firmware>.BIN`；M1 没有写 NVRAM、没有改 bootloader、没有持久化 guard（guard 在 RAM 口袋，断电即失效）。
- 相机侧唯一残留是 SD 根目录可能出现的 `PING.BIN` 不会被创建、`OUT.BIN` 可能被创建（8 B），删除即可。

## 7. 门与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 包体检 | `python tools/audit_beta_pack.py build/M1_sdprobe.BIN` | `[OK]`，len <size>，hook=8414，m0 未改，exit 0 |
| 二次核验 | `python tools/second_verify.py build/M1_sdprobe.BIN --base <official-firmware>.BIN --allow .tmp/m1allow.json` | PASS（block=0，warn=2）；相对 stock 170 段 / 3490 B；两条 warn 是槽 52/53 非单调（已被 RELEASE_v1 征用，预期）。不带 `--allow` 时仍 PASS，但会多出 20 条 zone 警告（16 条是 M1 桩的差异段、4 条是 RELEASE_v1 既有钩/表；`second_verify` 内置 `KNOWN_ZONES` 不含 `0x60672C20` 口袋，所以用 `--allow` 登记） |
| 可达性 | `python tools/patch_check.py --reach 0x60479960 0x60479A2B` | **FAIL**，两条 `beq 0x60479A59`（`0x60479A0A`、`0x60479A1B`）可跳过插入点；其余 9 条分支不跳过。继承自 <official-firmware>/RELEASE_v1，`docs/map/tone-chain.md` §0 第 8 条与 §7 第 7 条有记录；不通过加新钩子来「修」 |
| 远跳 | `python tools/patch_check.py --jump 0x60479A2B 0x60672C58 --blob .tmp/m1_build/m1_payload.bin` | PASS |
| 包内自检 | `python tools/verify_m1.py` | PASS，130 项 |

`verify_m1.py` 的关键断言：文件大小；3 个 block 的签名/长度/校验和；block0 header/trailer 元数据；block1/2 的 payload+header+trailer 与 RELEASE_v1 逐字节相同；只允许两段声明范围有差异；钩点旧值/新跳转；口袋首尾 FF；桩不越过 `0x60672FF6`；两条链跳都指向 `0x605DECD4`；27 条 call 的 `DD disp mask frame` 全部匹配；15 条字面量逐字节 + NUL；tone/矩阵/色度/其它钩子逐字节不变；EXIF 在 payload 之上且 96 B 放得下；sim.json 的 sha256/closed/verifier 与 BIN 一致。

**`flash_gate` 按任务要求没有运行。** 真要上机前再单独跑，并先确认 `sim.json` 的 sha256 与实际写卡文件一致。

## 8. 文件

- `tools/build_m1.py` —— 生成器；内嵌桩源码、汇编/链接/回填、两段补丁、容器校验和、sim.json。
- `tools/verify_m1.py` —— 上述 130 项断言。
- `build/M1_sdprobe.BIN` / `build/M1_sdprobe.sim.json` —— 产物。
- 本文件。
