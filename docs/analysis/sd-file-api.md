# SD 卡文件读写 API —— 静态定位报告

目标：在**本机 E-M10 II（E120，v1.4）**固件里找到「SD 卡文件读写」的函数入口，
为「刷一次机，之后靠卡上文件迭代」选一个**安全、够高、别人也在用**的调用面。

- 材料：`<decoded-image>.bin`（blk0，payload 已 XOR 0xFF 还原）
- 地址规则：`RAM = 0x60021020 + payload_offset`（`docs/STATUS.md` 规则 5），本文所有地址都是 RAM 地址
- 指令索引：`.tmp/pv_idx.txt`（<size> 条，覆盖 `0x60021020..0x610B181B`）
- 工具：`tools/find_file_api.py`（本次新建，见 §1.3）
- 全程离线：没有连相机、没有碰 I:/J:、没有改动任何既有文件

---

## 0. 结论（TL;DR）

1. **找到了整条「文件 API 层」**：`0x60564xxx` 一带的 `fileif_*` 家族，共 22 个函数（fopen / fread / fwrite / fseek / ftell / fclose / filesize_get / exists / remove / mkdir / rmdir / attributes_* / get_media_status / mount / umount / format）。
   名字**不是猜的**：模块自己的错误串就叫 `fileif_fopen err.` / `fileif_fread %d` / `fileif_fwite err.`，
   而且这些串就打印在**对应调用点的紧后面**。22/22 全部验证过 `idx.fstart(addr)==addr`（真函数起点）。
2. **最有把握的四个**（写文件的最小充分集）：
   `fileif_fopen 0x6056570C` · `fileif_fread 0x605649BB` · `fileif_fwrite 0x605648AF` · `fileif_fclose 0x60564832`
   —— 它们的调用点数分别是 `12 / 33 / 38 / 65`，调用它们的函数数是 `8 / 20 / 15 / 37`。
3. **这一层之上已有现成使用者**（说明「别人也这么用」）：AVM 录像 `[AVM_FILE_IF]`、相册 `[ALBM]`、
   HTTP/CGI 下载、`play_rom` 加载、GPS 日志、SD 速度分级测试、`C:\dbgterm` 调试终端。
4. **再往下一层是 DOS 文件流（`0x60570xxx/0x60571xxx/0x60572xxx`）和 FAT 驱动（`0x6055xxxx`/`0x60629xxx`）**，
   不要直接调：参数是簇号/扇区号，且带 FSInfo 一致性校验。
5. **找不到的东西**（不编）：
   - 没有找到「目录枚举」的具名高层入口（`opendir/readdir` 等价物）。`DCFSEARCH_*` 那串名字**只是日志标签**，不是入口（所有 xref 都落在日志格式化函数 `0x605688BA` 里）。
   - debug shell（`filetest`/`dostest`/`dcftest`）**静态不可达**：`0x60561355` 没有任何 `call` 调用者，也没有 4 字节指针表引用 → 很可能是量产版摘掉的开发 shell（**未证**可达）。
   - 路径里的盘符字符是运行时取的（`%c:\DCIM\...`），具体值**未证**（见 §7.1 的处理办法）。

---

## 1. 方法与可复现性

### 1.1 三条判据（为什么这样找）

| # | 判据 | 做法 |
|---|---|---|
| 1 | **字符串锚点** | blk0 里的 ASCII 串本身就是 RAM 地址；引用点 = 4 字节小端立即数（mn10300 的 `mov imm32,aN` 编码成 `FC xx` + imm32，整镜像搜 4 字节小端实测 0~4 个命中、无噪声） |
| 2 | **函数边界** | mn10300 大量叶子函数**没有栈帧序言**（入口第一条可能就是 `mov a0,(8,SP)`），只能按 `ret/retf` 切：**函数起点 = 最近一条 ret/retf 之后的第一条指令** |
| 3 | **名字归属** | 每个 `fileif_*` 都有自己的错误/格式串，且**紧跟在它的调用点后面**（self-test 与 debug shell 都是「调用 → 打印结果」），所以「这个调用是哪个 API」是静态可证的 |

> 易错点：`mov 0x605648AF,a0` 这条指令的**地址**是 `imm − 2`（`FC xx` 两个字节）。
> 算引用函数时必须用 `fstart(imm − 2)`；直接 `fstart(imm)` 会落到下一条指令所在的函数里，差一条。

### 1.2 一次调用长什么样（静态已证）

`fileif_*` 每个函数都是同一个骨架：**先 call 一个 trace 闸门 `0x6056FBDF`，返回值 0 时直接 call 真正实现**：

```asm
; fileif_fopen 0x6056570C (17 条)
6056570C  mov   a1,(4,SP)        ; a1 = mode 串（"r" / "w" / "rde"）
6056570E  mov   a0,a2            ; a0 = path
6056570F  movhu d0,(8,SP)        ; d0 = flag（16bit）
60565712  call  0x6056FBDF       ; trace 闸门 -> d0 = 0/1/2
6056571A  cmp   0x0,d0 / beq ...
6056572F  call  0x60570DC0       ; 正常路径：真正的 open
```

闸门 `0x6056FBDF` 返回 `0` = 不记日志（直接调实现），`1`/`2` = 先分配 0x80 字节日志记录再 `call 0x6006D2DA`。
**这就是为什么每个 `fileif_*` 里看起来有两条路——不是两个函数，是「记日志/不记日志」。**

### 1.3 复现命令

```bash
python tools/find_file_api.py report        # 91 条字符串锚点 + 候选函数 + 分层 + 触发点
python tools/find_file_api.py api           # fileif API 表（逐个校验是不是真函数起点）
python tools/find_file_api.py xref --str "fileif_fread %d"
python tools/find_file_api.py dis 0x6056570C
python tools/find_file_api.py callers 0x60564832
python tools/find_file_api.py chain 0x60EB8522 --depth 2
```

（`index` 子命令可从 `build/RELEASE_v1.BIN` 重建 `.tmp/pv_idx.txt`，很慢，一般不需要。）

---

## 2. 字符串锚点表

图例：**层** = 该串属于哪一层；**引用函数** = `fstart(imm−2)`，`-` 表示镜像里没有 4 字节立即数引用
（这种串通常是被运行时拼出来的 —— 例如 sprintf 的格式串放在数据段、由别的指针取）。

### 2.1 DCF 目录 / 盘符 / 路径模板

| RAM 地址 | 字符串 | 层 | 说明 | 引用函数 |
|---|---|---|---|---|
| `0x603994B8` | `DCIM       /100OLYMP` | dcf | 上电默认目录对 | `0x602BA431` |
| `0x603A9CD1` | `DCOLYMP    ` | firmup | 升级目录（8.3，无点） | `0x602CA8D5` `0x602CA962` `0x602CAAA6` `0x602CAAFC` |
| `0x60F6E868` | `DCOLYMP    ` | firmup | 同上（另一份常量） | `0x60EB8522` |
| `0x6062DA10` | `%c:\DCIM` | dcf | **设备侧路径模板**（盘符是 `%c`） | `0x6056BD34` `0x6056CC4C` |
| `0x6062DA1B` | `%c:\DCIM\101OLYM` | dcf | 目录名合法性检查用 | `0x6056BD34` |
| `0x6062DA3E` | `%c:\DCIM\101OLYMP` | dcf | 同上 | `0x6056BD34` |
| `0x6062DAB4` | `NON OLYMP FOLDER` | dcf | 目录名校验失败 | `0x6056BD34` |
| `0x6062D9FE` | `ILLEGAL DCIM FILE` | dcf | 目录名非法 | `0x6056BD34` |
| `0x605CDEDF` | `%c:\DCIM\%s\%s` | avm | 录像写文件的路径模板 | `0x6046433B` |
| `0x60627860` | `%c:\DCIM\%s\%s` | dcf | DCF 层的路径模板 | `0x605494CD` `0x6054953E` |
| `0x606318DF` | `%c:\DCIM\%3dOLYMP\%.12s` | album | 相册按目录号拼路径 | `0x6058163F` `0x605819DF` |
| `0x6062DCE0` | `%c:\DCIM\AAAAAAAA.DAT` | test | FAT 测试写文件 | `0x6056CC4C` |
| `0x60627944` | `:\DCIM\` | dcf | 路径片段 | `0x6054D660` |
| `0x60627921` | `OLYMP` | dcf | 目录名片段 | `0x6054C61C` |
| `0x60F4DD38` | `DCIM       /100OLYMP` | updater | 升级/出厂表里又一份 | `0x601C8CAC` |
| `0x60F4DD2C` | `100OLYMP` | updater | 同上 | `0x601C8CAC` |
| `0x60F4EF1C` | `MISC       /` | misc | MISC 卷目录对 | `0x60E54D41` |

> **陷阱**：`0x603B0F3C C:\DCIM\`、`0x603B0F69 C:\DCIM\100OLYMP\P1010001`、`0x605CD791 C:\DCIM\100OLYMP\%s`、
> `0x60630834 C:\DCIM\100OLYMP\P1010001.MOV` 这几条是**USB 主机侧（MSC/PTP 把卡当 U 盘）的路径仿真**，
> 不是设备内部文件 API 的路径。设备侧一律是 `%c:\...`。
> （前两条的引用函数是 `0x602D0D69`，正是 USB 描述符/路径那一块。）

### 2.2 升级（firmup）—— 本机唯一「官方保证读卡上我们文件」的路径

| RAM 地址 | 字符串 | 引用函数 |
|---|---|---|
| `0x60F6E88C` | `S120????BIN` | `0x601C8CAC` |
| `0x60F6E898` | `E120????BIN` | `0x601C8CAC` |
| `0x60F6E8A4` | `G120????BIN` / `0x60F6E8B0 H120????BIN` | `0x601C8CAC` |
| `0x60F6E874` | `[firmup]File Init Err.` | `0x60EB8522` |
| `0x60F6EAA2` / `0x60F6EAB5` | `[firmup]Mount Err:` / `[firmup]mount_ok` | `0x60EB8522` |
| `0x60F6EAFE` | `[firmup]...read file_size =` | `0x60EB8522` |
| `0x60F6EBD0` / `0x60F6EBEC` | `[firmup]file_mount/ start.` / `file_unmount/ start.` | `0x60EB8778` / `0x60EB87B8` |
| `0x60F6FD00` | `SYSTEM` | `0x60EBE26C` `0x60EBE4E3` `0x60EBE616` `0x60EBE721` |

`0x601C8CAC` 是个**文件名模板表的消费者**：上面那批 `E120????BIN`、`100OLYMP`、`C:\DCIM\100__TSB\FA000001.JPG`，
以及一大批出厂测试名（`CISO9181.JPG` `RESO7701.JPG` `GYRO9502.JPG` …）都在它里面被引用。
说明升级/出厂流程是**表驱动**的，不是散落在各处的 if。

### 2.3 文件系统 / FAT / 卷

| RAM 地址 | 字符串 | 层 | 引用函数 |
|---|---|---|---|
| `0x60629295` `0x6062929B` `0x606292A1` `0x606292A7` | `FAT12` `FAT16` `FAT32` `EXFAT` | fat | `0x60553DD1` |
| `0x60629256` | `There are some differnces between fat and fsinfo. (%d) -> (%d)` | fat | `0x605528EF` |
| `0x606291F5` `0x6062920F` `0x6062923F` | `NO_FAT_CHAIN ?? %08X %08X` / `fFAT_CHAIN ...` / `FAT_CHAIN ...` | fat | `0x60551F7B` |
| `0x6062CC0E` | ` VOLUME_INFO -` | fat | `0x605690B0` |
| `0x6062CC1D` `0x6062CC43` `0x6062CC69` | `SectorCluster,%d` / `SC_sht,%d` / `FATnum,%d` | fat | `0x605690B0` |
| `0x60AD783F` / `0x60AD784A` | `SDCARD   2` / `PENPAL   4` | mount | `0x606BE375` |
| `0x60AD7855` … `0x60AD78D8` | `getMountState result is MOUNT_STATE_{UNMOUNTED,MOUNTING,REC,PLAY}` | mount | `0x606BE375` |
| `0x6062C5A4`…`0x6062C608` | `DOSFSTRM_OPEN/CLOSE/FLUSH/WRITE/READ/SEEK`、`DOSMEM_READ/WRITE` | dosfs | `0x605688BA`（**日志名，不是入口**） |
| `0x6062C54B`…`0x6062C585` | `FAT_CHAIN_READ/READ2/WRITE/WRITE_EX` | fat | `0x605688BA`（同上） |
| `0x6062C614` `0x6062C623` `0x6062C634` | `DCFSEARCH_NAME / NUMBER / DATE` | dosfs | `0x605688BA`（同上） |

> **重要负结果**：所有 `DOSFSTRM_*` / `DOSMEM_*` / `FAT_CHAIN_*` / `DCFSEARCH_*` 的 xref **全部落在同一个函数 `0x605688BA`** ——
> 它是个「日志记录 ID → 名字」的格式化器（跳表在 `0x6062CF3C`，索引 `id−6`，`id` 6..0xfe）。
> 所以**不能**把这些名字当成 API 去反查地址；它们是 trace 标签。真的层在 `0x60570xxx..0x60572xxx`（见 §4）。

### 2.4 fileif 文件 API 层（★本次主角）

| RAM 地址 | 字符串 | 说明 | 引用函数 |
|---|---|---|---|
| `0x6062BC4A` | `fileif_fopen err.` | fopen 失败 | `0x60565A3C`（debug shell） |
| `0x6062BC6E` | `fileif_fread %d` | fread 返回字节数 | `0x60565A3C`、`0x60563FBA`（self-test） |
| `0x6062BBE2` | `@ fileif_fwite err.` | **fwrite（固件里拼错成 fwite）** | `0x60565A3C`、`0x60563FBA` |
| `0x6062BC5E` | `fileif_fseek %d` | fseek 结果 | `0x60565A3C` |
| `0x6062BAF8` / `0x6062BB29` / `0x6062BB3E` | `fileif_mkdir ret(%d)` / `fileif_rmdir ret(%d)` / `fileif_remove ret(%d)` | 目录/删文件 | `0x60565A3C` |
| `0x6062BCCF` / `0x6062BCF1` | `fileif_attributes_set ret(0x%08x)` / `..._read ret(0x%08x) attr(0x%08x)` | 属性 | `0x60565A3C` |
| `0x6062BD21` / `0x6062BD38` / `0x6062BD5A` | `fileif_filesize_get %s` / `0x%08x 0x%08x` / `err.` | 文件大小 | `0x60565A3C` |
| `0x6062B1E8` | `fileif.c` | 模块文件名（9 处引用） | `0x60564832` `0x605648AF` `0x605649BB` `0x60564E3E` `0x6056556D` … |
| `0x6062B2F5` | `fileiftest.c` | 自带 self-test 文件名 | `0x60567DBE` |
| `0x6062E3E5` | `FILEIF TEST COMPLETE. TOTAL:%d OK:%d NG:%d` | self-test 汇总 | `0x6056FA71` |
| `0x6062BDC1` | `fileif_test_main : %s : %s` | **debug shell 入口** | `0x60565A3C` |
| `0x6062CAEE` | ` fileif_get_media_status() = %X` | 介质状态 | `0x605690B0` |
| `0x6062BC48` / `0x6062BBD0` / `0x60F6A2FD` | `r` / `w` / `rde` | **fopen 的 mode 串**（`a1`） | `0x60565A3C` / `0x60E992B0` |

`fileif` 层的消费者遍布各业务模块，这也是判它「高层」的依据（调用点/函数数）：

| API | 调用点 | 函数数 |
|---|---|---|
| `fileif_fclose` `0x60564832` | 65 | 37 |
| `fileif_fwrite` `0x605648AF` | 38 | 15 |
| `fileif_exists` `0x6056561E` | 36 | 16 |
| `fileif_fread` `0x605649BB` | 33 | 20 |
| `fileif_mkdir` `0x60564C8D` | 28 | 9 |
| `fileif_filesize_get` `0x60565133` | 26 | 9 |
| `fileif_remove` `0x60564BF2` | 16 | 12 |
| `fileif_fopen` `0x6056570C` | 12 | 8 |

### 2.5 应用层锚点（谁在用文件）

| RAM 地址 | 字符串 | 层 | 引用函数 |
|---|---|---|---|
| `0x60F67D00` | `play_file_read in` | play | `0x60E8F995` |
| `0x60F67D39` | `play_file_create_file in` | play | `0x60E8FA94` |
| `0x60F67DED` / `0x60F67E1D` | `play_file_copy_file in` / `play_file_update_file in` | play | `0x60E8FDC5` / `0x60E90122` |
| `0x60F67C7E` / `0x60F67E51` | `play_file_size_check in` / `play_file_dcffnode_check in` | play | `0x60E8F8BB` / `0x60E901F7` |
| `0x60F620F4`…`0x60F6216C` | `write file error(dcf recnode / create file / fat chain write / cluster / dcfnum)` | record | `0x60E83071` |
| `0x60F67EFF` / `0x60F6811E` | `play_file_read_psdata in` / `play_gps_file_update in` | play | `0x60E90288` / `0x60E90B7A` |
| `0x605CD7AC` | `[AVM_FILE_IF] DivStop` | avm | `0x604633A8` `0x6046433B` |
| `0x605CD7E7` | `OLYMPUS%05d` | avm | `0x604633A8` |
| `0x60F6A1A1` | `play_rom_get_bgm_filelist in` | rom | `0x60E98C87` |
| `0x60F6A2E7` / `0x60F6A301` | `play_rom_load_file in` / `out:` | rom | `0x60E992B0`（fopen flag=`0x10`，mode=`"rde"`） |
| `0x60F51296` | `tpc_ptp_utl_fat_read:ERR 0x` | ptp | `0x60E5FA6B` |
| `0x60F51311` | `tpc_ptp_utl_find_dos_file:ERR 0x` | ptp | `0x60E5FD2E` |
| `0x60F513D8` | `tpc_ptp_utl_media_write:ERR 0x` | ptp | **（无直接引用）** |
| `0x60F36AAB` / `0x60F36975` / `0x60F369E6` | `.ORF` / `.JPG` / `.MOV` | http | `0x60E2CF3E` / `0x60E2CD2B` / `0x60E2CE30`（CGI 下载的类型判定） |
| `0x60F367FC` | `wlansd[0]=",DCIM,0,%d,%d,%d";` | http | `0x60E2C34E`（**WiFi 端的目录列表**，见 §4 负结果） |
| `0x60F349CC` | `/DCIM/%s,%s,%d,%d,%d,%d` | http | `0x60E29D46` |
| `0x6062D670` / `0x6062D8C1` | `xxxOLYMP` / `101OLYMP` | dcftest | `0x6056A394` `0x6056AA11` `0x6056AF22` `0x6056BD34` |
| `0x60303F15` | `USR> cam read1 [FileName] [hw] [vw] [bytes]` | factory | `0x60248416` |
| `0x60303F6C` | `USR> cam read2 ...` | factory | `0x60248560` |
| `0x60301A96` / `0x60301CC0` | `USR> cam still ...` / `USR> cam movie ...` | factory | `0x602457A2` / `0x60245BBF` |
| `0x60301958` | `Please poweron` | factory | 10 个函数共用（`0x60248416` 等） |
| `0x60172516` | `%c:\dbgterm` | dbg | 自引用：`fileif_mkdir` + 写日志 |

### 2.6 别被名字骗了（干扰项）

| RAM 地址 | 字符串 | 真相 |
|---|---|---|
| `0x60AE02F3` `0x60AE030B` `0x60AE0323` | `VOLUME_TARGET_ALL/:0` / `_DATA :1` / `_BGM :2` | **音频音量目标**，不是 FAT 卷 |
| `0x60F55F0E` / `0x60F56C0D` | `[VOLUME]get volume` / `vol value error.change AUD_VOL` | 同上 |
| `0x60BBC9F8` / `0x60D850EE` | `CBK_TIMER_SOUND_VOLUME` / `EVT_GUI_TIM_1S_END_VOLUME_SET` | 同上 |
| `0x60093D26`…`0x60094170` | `SYSTEM0 %-2d` … `SYSTEM4` | 传感器/系统寄存器名，不是 `SYSTEM` 目录 |
| `0x60AA0815` / `0x6011710A` | `CBC_SYSTEM_EVENT` / `[SYS WANA]FATAL_SYSTEM_ERROR` | 系统事件名 |
| `0x60B2CD51` | `CANON` | 厂商识别（PTP），不是卡上目录 |

---

## 3. 候选函数表

### 3.1 ★第一档：fileif 文件 API（推荐从这里下手）

| 地址 | 名字 | 调用约定（静态推断） | 判据 | 证据等级 |
|---|---|---|---|---|
| `0x6056570C` | `fileif_fopen` | `(path/*a0*/, flag/*d0*/, mode_str/*a1*/) → handle` | 自身错误串 `fileif_fopen err.`；17 条指令；正常路径 `call 0x60570DC0`；mode 串实测 `"r"`/`"w"`/`"rde"`；flag 见 0 与 0x10 | 入口/调用关系 **静态已证**；参数语义 **静态推断** |
| `0x60564829` | `fileif_open` 薄壳（flag=0） | `(path/*a0*/, mode_str/*a1*/) → handle` | 只有 3 条：`clr d0` / `call 0x6056570C` / `ret`；所有调用点前一条正好是 `mov 'r'/'w',a1` | **静态已证** |
| `0x605649BB` | `fileif_fread` | `(buf/*a0*/, size/*d0*/, n/*d1*/, handle/*a1*/) → n` | 调用点紧随 `fileif_fread %d`；实体 `0x60571FB1` | **静态已证** |
| `0x605648AF` | `fileif_fwrite` | `(buf/*a0*/, size/*d0*/, n/*d1*/, handle/*a1*/) → n` | 调用点紧随 `fileif_fwite err.`；实体 `0x60571917` | **静态已证** |
| `0x60564832` | `fileif_fclose` | `(handle/*a0*/) → err` | 65 调用点；实体 `0x60571318`；`fileif.c` 字符串引用处之一 | **静态已证** |
| `0x60564AB4` | `fileif_fseek` | `(handle/*a0*/, pos/*d0*/, whence/*d1*/)` | 调用点紧随 `fileif_fseek %d`；实体 `0x605722A1` | 已证入口；**参数顺序静态推断** |
| `0x60565203` | `fileif_ftell` | `(handle/*a0*/, out_ptr/*a1*/) → pos` | 调用点紧随 `ftell err`；实体 `0x6057287C` | **静态推断**（未见自身格式串） |
| `0x605652C2` | `fileif_fseek2` | `(handle, 32bit pos)` | 调用点紧随 `fseek2 err`；实体 `0x6057291C` | **静态推断** |
| `0x60565133` | `fileif_filesize_get` | `(path 或 handle, ...) → size` | 三条自身格式串；26 调用点 | **静态已证** |
| `0x6056561E` | `fileif_exists` | `(path) → bool` | `fileif_exists err=%d`；36 调用点 | **静态已证** |
| `0x60564BF2` | `fileif_remove` | `(path)` | `fileif_remove ret(%d)` | **静态已证** |
| `0x60564C8D` | `fileif_mkdir` | `(path)` | `fileif_mkdir ret(%d)` | **静态已证** |
| `0x60564D33` | `fileif_rmdir` | `(path)` | `fileif_rmdir ret(%d)` | **静态已证** |
| `0x60564E3E` / `0x60564EF2` | `fileif_attributes_read` / `_set` | `(path, attr)` | 自身格式串 | **静态已证** |
| `0x60564F95` | `fileif_filedatetime_read` | `(path, out_dt)` | 自身格式串 | **静态已证** |
| `0x6056556D` | `fileif_get_media_status` | `(void) → status` | `fileif_get_media_status() = %X` | **静态已证** |
| `0x605655EC` | 盘符取回（打印 `DRIVE (%c)`） | `(void) → char` | 用来拼 `%c:` | **静态推断** |
| `0x60565346` / `0x605653BE` | `fileif_mount` / `fileif_umount` | mount: 5 调用点/3 函数（debug shell + `0x6056A0E8` + `0x6056BD34`）；umount: 只有 shell 1 处 | **静态推断** |
| `0x60565432` | `fileif_format` | 7 调用点/5 函数（shell + `0x6056AF22` `0x6056BD34` `0x6056CC4C` `0x6056D4BF` 这几个测试/校验函数）；**危险，不要碰** | 静态推断 |
| `0x6056FBDF` | trace 闸门 | `(void) → 0/1/2` | 每个 fileif 入口都先调它 | **静态已证**（控制流） |

### 3.2 第二档：业务层封装（能调，但参数/状态多）

| 地址 | 名字 | 判据 | 证据等级 |
|---|---|---|---|
| `0x60E8F995` | `play_file_read` | trace 串在函数第一条；9 处 5 参 call | 已证入口；语义推断 |
| `0x60E8FA94` | `play_file_create_file` | trace 串 + `check media full`/`get DCFNUM`/`create file`/`add DCF file list` 四段 | 已证入口 |
| `0x60E8F8BB` / `0x60E8FDC5` / `0x60E90122` / `0x60E901F7` | `play_file_size_check` / `copy_file` / `update_file` / `dcffnode_check` | trace 串 | 已证入口 |
| `0x60E83071` | 拍摄写卡汇聚点（五段 `write file error(...)`） | 5 条串同函数 | 已证入口 |
| `0x60EB8522` | `firmup` 主流程（`DCOLYMP` / `E120????BIN` / `read file_size`） | 串 xref | 已证入口 |
| `0x60EB8778` / `0x60EB87B8` | `firmup file_mount` / `file_unmount` | `[firmup]file_mount/ start.` | 已证入口 |
| `0x604633A8` | `[AVM_FILE_IF]` 命令分发（`STREAM_OPEN/CLOSE/READ`） | 串 xref；814 条指令 | 已证入口 |
| `0x60463CC1` / `0x60463E82` / `0x60464035` | AVM `File Error : Open` / `: Write` / `File Close` | 串 xref | 已证入口 |
| `0x6057FF6E` | `[ALBM][INIT]`（exists / remove / read `.BIN`） | 串 xref | 已证入口 |
| `0x60E2B953` | `cgi_send_file`（HTTP 下载） | 串 xref | 已证入口 |
| `0x60E2DBF7` | `fileif_path = ` / `fileif_attributes_read err` 所在（1174 条） | 串 xref | 已证入口 |
| `0x60E5FA6B` / `0x60E5FD2E` / `0x60E5FC05` / `0x60E5FE3D` / `0x60E60647` | PTP `fat_read` / `find_dos_file` / `DeleteDosFile` / `create_dos_dir` / `dcf_mount` | trace 串 | 已证入口 |
| `0x60E992B0` | `play_rom_load_file`（32 条，fopen flag `0x10` mode `"rde"`） | 串 xref + 完整调用序列 | 已证入口 + 已证用法 |

### 3.3 第三档：驱动内部（**禁止直接调**）

| 地址 | 名字 | 为什么不要碰 |
|---|---|---|
| `0x60570DC0` | DOS 流 open 实体 | 参数含内部句柄/缓冲区，fileif 已封装 |
| `0x60571318` `0x60571917` `0x60571FB1` | close / write / read 实体 | 同上 |
| `0x605722A1` `0x6057287C` `0x6057291C` | seek / tell / seek2 实体 | 同上 |
| `0x6057256F` `0x6057257C` `0x60572664` `0x6057268F` `0x60572723` | remove / mkdir / rmdir / attr_read / attr_set 实体 | 同上 |
| `0x60551F7B` | FAT 簇链校验/错误打印 | 参数是簇号；失败会打到 fsinfo 校验 |
| `0x60553DD1` | FAT12/16/32/EXFAT 判定 | 只读，但纯内部 |
| `0x605690B0` | 卷信息打印 | 只读调试 |
| `0x60560ECD` `0x60560EE9` `0x6056102D` `0x605610C3` `0x60561355` | FAT 模块调试命令（mount/umount/rawread/write/fullwritetest） | **`fullwritetest` 会整卡写**，绝对不要碰 |
| `0x605688BA` | 日志名格式化器 | 不是 API |

### 3.4 判据是怎么用的（给下一位）

找一组 open/read/write/seek/close 的**通用**判据，按可信度从高到低：

1. **模块自己的错误/格式串紧跟在调用点后**（最强）——`fileif_*` 全靠这条。
2. **函数入口第一条 mov 一个 trace 串**（`xxx in` / `xxx out`）——`play_file_*`、`tpc_ptp_utl_*`、`[ALBM][INIT]` 靠这条。
3. **同一函数里成组出现同一业务的错误串**（五段 `write file error(a/b/c/d/e)`）——拍摄写卡汇聚点靠这条。
4. **mode 常量**：`fileif_fopen` 的 `a1` 只见到 `"r"` / `"w"` / `"rde"`，`d0` 只见到 `0` 与 `0x10`；
   没有见到 `0/1/2` 那种数字 mode —— 这套固件的 mode 是**字符串**，不是数字。
5. **返回值检查模式**：`call` 后紧跟 `mov a0,a2 / cmp 0x0,a2 / beq <错误串>` 是标准「句柄空 = 失败」。
6. **批量读写循环**：`fileif_fread(...,1,len,...)` 外面套 `while (n>0)` —— 在 `0x60E992B0`、
   `0x60464035` 一带可见。

---

## 4. 分层图

```
应用层
  ├ A 回放/编辑   play_file_read 0x60E8F995 / create 0x60E8FA94 / update / copy / size_check
  ├ B 拍摄写卡    0x60E83071（dcf recnode → create file → fat chain write → cluster → dcfnum）
  ├ C 升级        firmup 0x60EB8522 → file_mount 0x60EB8778（只读 DCOLYMP/E120????.BIN）
  ├ D 影片/AVM    [AVM_FILE_IF] 0x604633A8 / 0x60463CC1 / 0x60463E82 / 0x60464035
  ├ E 相册        [ALBM][INIT] 0x6057FF6E / 0x605814FB
  ├ F HTTP/CGI    cgi_send_file 0x60E2B953 / 0x60E2DBF7 / wlansd 列表 0x60E2C34E
  └ G PTP/USB     tpc_ptp_utl_*（0x60E5F98F / FA6B / FC05 / FD2E / FE3D / 0x60E60647）
        │  业务参数：DCF 号、目录号、缓冲区、长度
        ▼
★ fileif 文件 API 层  0x60564xxx（★推荐调用面★）
     fopen 0x6056570C / 薄壳 0x60564829      fclose 0x60564832
     fread 0x605649BB                        fwrite 0x605648AF
     fseek 0x60564AB4 / ftell 0x60565203 / fseek2 0x605652C2
     filesize_get 0x60565133 / exists 0x6056561E
     mkdir 0x60564C8D / rmdir 0x60564D33 / remove 0x60564BF2
     attributes_read 0x60564E3E / _set 0x60564EF2 / filedatetime_read 0x60564F95
     get_media_status 0x6056556D / mount 0x60565346 / umount 0x605653BE / format 0x60565432
     ↑ 每个入口先 call trace 闸门 0x6056FBDF 再 call 实现
        │  路径串 "%c:\..."（盘符来自 0x605655EC）+ 文件句柄
        ▼
DOS 文件流层  0x60570xxx / 0x60571xxx / 0x60572xxx
     open 0x60570DC0   close 0x60571318   write 0x60571917   read 0x60571FB1
     seek 0x605722A1   tell 0x6057287C    seek2 0x6057291C
     remove 0x6057256F mkdir 0x6057257C   rmdir 0x60572664
     attr_read 0x6057268F  attr_set 0x60572723
        │  簇号、扇区号
        ▼
FAT 驱动  0x6055xxxx / 0x60629xxx
     FAT12/16/32/EXFAT 判定 0x60553DD1   簇链校验 0x60551F7B
     fsinfo 一致性 0x605528EF            VOLUME_INFO 0x605690B0
     调试命令 mount/umount/rawread/write/fullwritetest 0x60560ECD..0x60561355
        │
        ▼
块设备 / SD 控制器（SDCARD 卷 0x60AD783F）
```

**DCF 语义层**（在 fileif 之上、业务之下）：`dcfnum_exec`、`dcfsearch_exec 0x60567BE0`、
`dcf_get_index_from_path`、`dcf_get_filename_from_index`、`dcf_all_copy_job`（都在 `0x6056xxxx` 与 `0x6054xxxx`）。
它管的是「DCF 编号 ↔ 文件名」，**不管字节流**——所以我们要的字节流接口就是 fileif。

---

## 5. 触发点候选

原则：**优先选「已经在跑、别人也在用、失败只是返回错误码」的点**，最不济才碰升级路径。
（Caduceus 用半按快门，是因为它需要「用户在场 + 一次性 + 相机就绪」；我们的场景是**反复迭代**，
所以更想要「每次开机必跑、且跑在只读阶段」的点。）

| # | 触发点 | 为什么是它 | 风险 | 证据等级 |
|---|---|---|---|---|
| T1 | **挂载完成观察者**：`MountMediaObserver notify success! 0x%04x`（`0x60B946EF`）、`regist rec mount event 0x%08x slot%d`（`0x60B945AC`）、`BootMount Start@Cmp`（`0x60B5CBD2`）这一片 | 卡刚挂上、文件系统就绪、还没进拍摄/回放业务；观察者是「通知型」回调，插一句只读检查污染最小 | 这是**设计建议**不是既有调用链：需要自己找回调表槽位；回调里做耗时 I/O 可能拖慢开机（要限时/限次） | 串/函数位置 **静态已证**；「适合做触发点」**静态推断** |
| T2 | **回放读文件**：`play_file_size_check 0x60E8F8BB → play_file_read 0x60E8F995` | 用户在回放里选中一张卡上的图就跑；只读、返回值有检查、完全不写卡 | 需要合法 dcffnode（目录号+文件号）；文件必须能被当图片解析，否则提前走错误分支；挂在中间可能干扰缩略图缓存 | 调用链 **静态已证**；用法 **静态推断** |
| T3 | **相册初始化**：`0x6057FF6E`（`[ALBM][INIT]` 里就在做 exists / size 0 file delete / file read） | 它本身已经是「扫卡上文件」的循环，最接近我们想要的「读目录」 | **它会删 0 字节文件**（`[ALBM][INIT]size 0 file delete`）；动它的循环容易误删/误判；还会碰相册 DB | 串/位置 **静态已证**；副作用 **静态已证（串存在）** |
| T4 | **升级流程**：`firmup 0x60EB8522 → file_mount 0x60EB8778 → 读 E120????.BIN` | 本机唯一「官方保证读卡上我们自己的文件」的路径；卡已挂、缓冲区已备、错误串齐全 | 进入点由「开机 + 卡上 `DCOLYMP/E120????.BIN` 命名」决定；任何跳转错误都落进升级判定，**砖风险最高**。只适合一次性只读验证 | 全链 **静态已证** |
| T5 | **fileif debug shell**：`fileif_test_main 0x60565A3C`（由 `0x60561355` 的 `filetest`/`dostest`/`dcftest` 派发；子命令 read/write/mkdir/cp/size/attr/search/dcfnum/open/seek2/dcf/media…） | 现成的命令解释器，等于把整个 fileif API 变成可交互命令 —— 若可达就是最省事的迭代口 | **静态不可达**：`0x60561355` 既无 `call` 调用者，也无 4 字节指针表引用 → 很可能是量产版摘掉的开发 shell。**未证**，不要把它写进计划 | 函数/子命令 **静态已证**；可达性 **未证** |

> 关于「半按快门」这类物理触发的替代：本机有一个 **`USR>` 出厂/调整命令台**
> （`0x601AD498 USR>`，xref `0x6018C8B5`；命令如 `cam read1 [FileName] [hw] [vw] [bytes]`、`cam still`、`cam movie`、`cam exp`），
> 其中 `cam read1/read2` 会**把传感器数据写成卡上文件**（`0x60248416` / `0x60248560`，串里有 `Read Bytes [%d]`、`Read OK`）。
> 这条通道如果能进，就是一个「用户在场、一次性、天然带文件参数」的触发点 —— 但**怎么进这个 USR 台未证**（串只在 `0x6018C8B5` 一处被引用）。

---

## 6. 证据等级与负结果

**等级定义**

- **静态已证**：字节层面能验证 —— 字符串字节、4 字节立即数 xref、`fstart(addr)==addr`、`call` 目标、指令序列。
- **静态推断**：控制流/串都在，但语义靠命名与上下文推（例如参数顺序、`fileif_ftell` 的名字）。
- **未证**：没有静态证据，明确写出来，不编。

**负结果（本项目里明确的「找不到」）**

| 想找的东西 | 结论 |
|---|---|
| 目录枚举 API（`opendir`/`readdir` 等价、一次列完整个目录） | **没找到具名高层入口**。`DCFSEARCH_NAME/NUMBER/DATE` 只是日志标签（xref 全在 `0x605688BA`）；DCF 检索实体是 `0x60567BE0`（`dcfsearch_exec`），DCF 语义层，不适合直接调。HTTP 侧有 `wlansd[]` 目录串（`0x60E2C34E`），但那是拼 WiFi 页面，不是可调 API |
| `fileif` 之外的「整目录读」 | 没有。`fileif` 只有单文件 exists/size/attr |
| debug shell 可达性 | **未证**（0 调用者 + 0 指针引用） |
| 盘符字符的具体值 | **未证**（路径模板是 `%c:\...`，盘符运行时给；`0x605655EC` 打印 `DRIVE (%c)`，但没追到它的来源常量） |
| exFAT 专用的文件 API 分支 | **未证**。镜像里有 `EXFAT` 串（`0x606292A7`）在 FAT 类型判定里，但没找到 exFAT 专用的 open/read 分支；**推测**与 FAT 共用 DOS 流层（`docs/firmware/sd-card.md` 也记着 exFAT 卡会让升级silently不启动） |
| `tpc_ptp_utl_media_write` 的函数地址 | **没定位到**（串 `0x60F513D8` 无 4 字节引用；可能是表格/间接调用） |

---

## 7. 下一步最小验证包

**共同前提**（三包都要）

1. 卡：SDHC ≤32GB，FAT16/FAT32（exFAT 不行，见 `docs/firmware/sd-card.md`）。
2. 路径：**不要硬编码盘符**。先用 `0x605655EC`（打印 `DRIVE (%c)`）取盘符，再拼 `"X:\SDAPI.TXT"`；
   这个函数在**40 个调用点 / 28 个函数**里被用来拼路径（实测），所以它就是「盘符来源」这条路最稳的入口；
   或者干脆复用设备自己的路径模板（`%c:\DCIM\%s\%s` @ `0x605494CD`）的做法。盘符的具体值 **未证**，别赌。
3. 参数：`fileif_fopen` 的 mode 是**字符串**（`"r"` / `"w"`），flag 用 `0`。
4. 只调 §3.1 的表；**不碰** §3.3。
5. 结果回读通道：沿用本工程既有做法（`docs/EXIF-PROBE.md` 里那套 `0x61894C90` UserComment 通道，
   以及既有桩位 `0x604B811D → 0x605DED44` / `0x60479A2B → 0x605DECD4` 的「改 call 指向桩」手法），
   这样不用新开信息通道。

### 包 A（**先做这个**，只读，零写卡风险）

目的：证明「我们能拿到句柄、能问出大小」，且**确认盘符与路径格式**。

```
exists = fileif_exists("X:\SDAPI.TXT")            ; 0x6056561E
if (exists) sz = fileif_filesize_get("X:\SDAPI.TXT")   ; 0x60565133
```

- 判定：`exists` 返回 1 或 0 都是**成功**（0 只是文件不在）；返回错误码/负数 = 失败。
- 回读：`exists` 与 `sz` 两个值。
- 为什么安全：两个函数都只读目录项，不写 FAT、不动 DCF。
- 时间：一次调用，微秒级，放在挂载后任何点都行。

### 包 B（写一个 8 字节文件）

```
h = fileif_fopen("X:\SDAPI.TXT", 0, "w")     ; 0x6056570C (或薄壳 0x60564829)
if (h) { n = fileif_fwrite("SDAPI001", 1, 8, h)   ; 0x605648AF
         fileif_fclose(h) }                        ; 0x60564832
```

- 期望：卡上出现 `SDAPI.TXT`，长度 **8**，内容 `SDAPI001`。
- 失败模式判据：
  - `fopen` 返回 0 → 路径/盘符错（先回包 A 确认盘符），或目录不存在（用 `fileif_mkdir` 建）。
  - `fwrite` 返回 < 8 且 > 0 → 卡满（`check media full` 那条串在业务层做过检查）。
  - `fwrite` 返回 0 → 句柄无效/mode 串不对。
  - `fclose` 非 0 → FAT 回写失败，**下次开机卡可能不一致**（这时先备份卡）。
- 风险最低的落点：**不在拍摄/回放过程中**调，避免与 DCF 写卡并发。

### 包 C（读回校验，把「迭代」闭环）

```
h = fileif_fopen("X:\SDAPI.TXT", 0, "r")
n = fileif_fread(buf, 1, 8, h)      ; 0x605649BB
fileif_fclose(h)
比较 buf[0..7] == "SDAPI001"
```

- 这一步成功 = 读+写都通 = 之后所有「靠卡上文件迭代」的路都打开了。
- 进阶（不急）：`fileif_fseek 0x60564AB4` 做定位读、`fileif_attributes_read 0x60564E3E` 读时间戳。

### 包 A/B/C 的顺序理由

包 A 只依赖 `exists`/`filesize_get`，**连句柄都不需要**，出错也不会留下副作用 —— 用它先把「盘符 + 路径格式 + 调用约定」这三件未证的事一次钉死，再上写操作。

---

## 8. 待证清单（谁接着做，从这里开始）

1. **盘符字符**：追 `%c:` 的实参来源（从 `0x605494CD` 往上看 sprintf 参数），或用 `0x605655EC` 动态取。→ 包 A 的第一件事。
2. **`fileif_exists` / `filesize_get` 的确切参数个数与顺序**：目前只从调用点看出「一个路径串」，没验证是否有第二参数。→ 反汇编 `0x60565A3C` 的 `path` 子命令那一段（`0x60565E37` 附近）即可。
3. **debug shell 可达性**：如果哪天在 DIAG/工厂菜单里发现 `filetest` 字样，T5 立刻升值。
4. **挂载观察者槽位**：`0x60B945AC` / `0x60B946EF` 这一片是「注册/通知」对，找注册函数就能找回调表 → 决定 T1 能不能落地。
5. **exFAT**：只在 `0x606292A7` 见到类型串，没见到专用 open 分支；如果将来要用 128GB 卡，这条必须先证。

---

### 附：本文所有地址的机器可校验性

```bash
python tools/find_file_api.py api     # 22 条 fileif 地址逐个校验 fstart==addr
python tools/find_file_api.py report  # 91 条串锚点 + 22 条候选 + 分层 + 触发点
```

（`report` 的第三列「引用函数」就是本文表格里的那一列，全部由 4 字节立即数 xref 算出，可重跑复现。）
