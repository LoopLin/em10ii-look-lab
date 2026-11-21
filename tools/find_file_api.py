#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""find_file_api.py — 离线定位 E-M10 II (E120 v1.4) 固件里的 SD 卡文件读写 API。

只用静态材料，不碰硬件、不碰卡：

  * 镜像     analysis/00_0100_40300020.decoded.bin   (blk0, 已 XOR 0xFF 还原)
  * 地址规则  RAM = 0x60021020 + payload_offset      (docs/STATUS.md 规则 5)
  * 指令索引  .tmp/pv_idx.txt   每行  ADDR|MNEMONIC|OPERANDS
              缺失时可用 `index` 子命令重建（调 mn10300-elf-objdump，很慢）

判据（为什么这样找）
--------------------
1. 字符串锚点：blk0 里的 ASCII 串就是 RAM 地址；引用点 = 4 字节小端立即数。
   mn10300 的 `mov imm32,aN` 编码成 `FC xx <imm32>`，所以整镜像搜 4 字节
   小端就能拿到干净的 xref（实测字符串地址命中数 0~4 个，无噪声）。
2. 函数边界：mn10300 大量叶子函数没有栈帧序言，只能靠 `ret/retf` 切分。
   于是「函数起点 = 最近一条 ret/retf 之后的第一条指令」。
   这个规则对 trace 打印函数（入口第一条就是 mov <string>,a0）特别好用：
   `xxx in` / `xxx out` 这种 trace 串天然标出了函数入口。
3. 分层：从已证实的消费者（固件升级 firmup / 回放 play_file_* / PTP 传输）
   顺着 call 往下走，得到的调用链比从 FAT 驱动往上猜可靠。

子命令
------
  report                      打印本文档用的三张表（字符串 / 候选函数 / 调用链）
  strings [--all] [--kw K]    抽 ASCII 串并分类
  xref <hex|--str TEXT>       4 字节小端 xref，并映射到所属函数
  func <hex>                  打印函数边界（起点/终点/长度）
  dis <hex|--str TEXT> [--n N]  反汇编该函数（call 目标自动解析成函数起点）
  callers <hex>               谁调用它
  chain <hex> [--depth D]     向下 BFS 调用链，列出每个函数引用到的字符串

例
  python tools/find_file_api.py report
  python tools/find_file_api.py xref --str "play_file_read in"
  python tools/find_file_api.py dis 0x60E8F995 --n 60
  python tools/find_file_api.py chain 0x60EB8778 --depth 2
"""
import argparse
import bisect
import json
import os
import struct
import subprocess
import sys

BASE = 0x60021020
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "analysis", "00_0100_40300020.decoded.bin")
IDX = os.path.join(ROOT, ".tmp", "pv_idx.txt")
OBJDUMP = os.path.join(ROOT, ".tmp", "olya", "toolchains", "mn10300-elf", "bin",
                       "mn10300-elf-objdump.exe")

# ----------------------------------------------------------------------------
# 字符串锚点表：本次分析实际核对过的（地址已用 strfind 式扫描确认）
# ----------------------------------------------------------------------------
ANCHORS = [
    # (addr, text, layer, note)
    (0x6062C54B, "FAT_CHAIN_READ", "fat", "FAT 链读（debug trace 名）"),
    (0x6062C55A, "FAT_CHAIN_READ2", "fat", "FAT 链读变体"),
    (0x6062C56A, "FAT_CHAIN_WRITE", "fat", "FAT 链写"),
    (0x6062C585, "FAT_CHAIN_WRITE_EX", "fat", "FAT 链写扩展"),
    (0x6062C5A4, "DOSFSTRM_OPEN", "dosfs", "DOS 文件流 open"),
    (0x6062C5B2, "DOSFSTRM_CLOSE", "dosfs", "DOS 文件流 close"),
    (0x6062C5C1, "DOSFSTRM_FLUSH", "dosfs", "DOS 文件流 flush"),
    (0x6062C5D0, "DOSFSTRM_WRITE", "dosfs", "DOS 文件流 write"),
    (0x6062C5DF, "DOSFSTRM_READ", "dosfs", "DOS 文件流 read"),
    (0x6062C5ED, "DOSFSTRM_SEEK", "dosfs", "DOS 文件流 seek"),
    (0x6062C5FB, "DOSMEM_WRITE", "dosfs", "整扇区/内存级写"),
    (0x6062C608, "DOSMEM_READ", "dosfs", "整扇区/内存级读"),
    (0x6062C614, "DCFSEARCH_NAME", "dosfs", "按文件名找 DCF 项"),
    (0x6062C623, "DCFSEARCH_NUMBER", "dosfs", "按编号找"),
    (0x6062C634, "DCFSEARCH_DATE", "dosfs", "按日期找"),
    (0x6062C465, "DCFPMARK_READ", "dosfs", "DCF 保护标记读"),
    (0x6062C473, "DCFPMARK_WRITE", "dosfs", "DCF 保护标记写"),
    (0x6062C2A0, "LOG_READY", "dosfs", "日志层 ready"),
    (0x6062C2DF, "LOG_READ", "dosfs", "日志层读"),
    (0x6062C2E8, "LOG_WRITE", "dosfs", "日志层写"),
    (0x606291F5, "NO_FAT_CHAIN ?? %08X %08X", "fat", "FAT 驱动错误打印"),
    (0x6062920F, "fFAT_CHAIN ?? %08X %08X", "fat", "FAT 驱动错误打印"),
    (0x60629256, "There are some differnces between fat and fsinfo. (%d) -> (%d)", "fat",
     "FAT 与 FSInfo 不一致校验"),
    (0x60629295, "FAT12", "fat", "FAT 类型判定"),
    (0x6062929B, "FAT16", "fat", "FAT 类型判定"),
    (0x606292A1, "FAT32", "fat", "FAT 类型判定"),
    (0x606292A7, "EXFAT", "fat", "FAT 类型判定（exFAT 分支存在）"),
    (0x6062CC0E, " VOLUME_INFO -", "fat", "卷信息打印"),
    (0x6062CC1D, "  SectorCluster,%d", "fat", "卷信息：簇大小"),
    (0x6062CC30, "  bytesSector  ,%d", "fat", "卷信息：扇区字节"),
    (0x6062CC43, "  SC_sht       ,%d", "fat", "卷信息：簇→扇区移位"),
    (0x6062CC69, "  FATnum       ,%d", "fat", "卷信息：FAT 份数"),
    (0x606299BE, "mount", "fat_dbg", "FAT 模块调试命令名"),
    (0x606299C4, "umount", "fat_dbg", "FAT 模块调试命令名"),
    (0x606299FB, "rawread", "fat_dbg", "裸扇区读调试命令"),
    (0x60629A1F, "write", "fat_dbg", "调试命令"),
    (0x60629ADE, "fullwritetest", "fat_dbg", "整卡写测试（危险）"),
    (0x6062BE3E, "open", "fat_dbg", "调试命令"),
    (0x6062BE53, "seek2", "fat_dbg", "调试命令"),
    (0x60F67C7E, "play_file_size_check in", "play_file", "回放层文件大小检查入口"),
    (0x60F67D00, "play_file_read in", "play_file", "回放层读文件入口"),
    (0x60F67D39, "play_file_create_file in", "play_file", "回放层建文件入口"),
    (0x60F67DED, "play_file_copy_file in", "play_file", "回放层复制文件入口"),
    (0x60F67E1D, "play_file_update_file in", "play_file", "回放层更新文件入口"),
    (0x60F67E51, "play_file_dcffnode_check in", "play_file", "DCF 节点校验入口"),
    (0x60F67ECF, "dcfps_read_modeinfo in", "play_file", "PS 模式信息读"),
    (0x60F67EFF, "play_file_read_psdata in", "play_file", "PS 数据读"),
    (0x60F680F0, "play_gps_file_read in", "play_file", "GPS 日志读"),
    (0x60F6811E, "play_gps_file_update in", "play_file", "GPS 日志写"),
    (0x60F67D52, "    check media full", "play_file", "建文件前容量检查"),
    (0x60F67D8E, "    get DCFNUM", "play_file", "取 DCF 编号"),
    (0x60F67DAC, "    create file", "play_file", "真正建文件"),
    (0x60F67DBC, "    add DCF file list", "play_file", "挂进 DCF 列表"),
    (0x60F620F4, "write file error(dcf recnode)", "record", "拍摄写卡失败点"),
    (0x60F62112, "write file error(create file)", "record", "拍摄写卡失败点"),
    (0x60F62130, "write file error(fat chain write)", "record", "拍摄写卡失败点"),
    (0x60F62152, "write file error(cluster)", "record", "拍摄写卡失败点"),
    (0x60F6216C, "write file error(dcfnum)", "record", "拍摄写卡失败点"),
    (0x60F632D9, "    write media ", "media", "媒体写 trace"),
    (0x60F6331B, "    read media ", "media", "媒体读 trace"),
    (0x60F634FB, "JPEG save write media", "media", "JPEG 存盘"),
    (0x60F638CB, "write_media  ", "media", "write_media trace"),
    (0x60F51219, "tpc_ptp_utl_fat_chain_read:ERR status 0x", "ptp", "PTP 层 FAT 链读"),
    (0x60F51296, "tpc_ptp_utl_fat_read:ERR 0x", "ptp", "PTP 层 FAT 读"),
    (0x60F512CE, "tpc_ptp_utl_DeleteDosFile:ERR 0x", "ptp", "PTP 层删文件"),
    (0x60F51311, "tpc_ptp_utl_find_dos_file:ERR 0x", "ptp", "PTP 层找文件"),
    (0x60F51355, "tpc_ptp_utl_create_dos_dir:ERR 0x", "ptp", "PTP 层建目录"),
    (0x60F5139C, "tpc_ptp_utl_dcf_mount:ERR 0x", "ptp", "PTP 层挂载"),
    (0x60F513D8, "tpc_ptp_utl_media_write:ERR 0x", "ptp", "PTP 层媒体写"),
    (0x60F6E868, "DCOLYMP    ", "firmup", "升级目录名（8.3，无点）"),
    (0x60F6E88C, "S120????BIN", "firmup", "升级文件模式"),
    (0x60F6E898, "E120????BIN", "firmup", "升级文件模式（本机）"),
    (0x60F6E874, "[firmup]File Init Err.", "firmup", "升级文件初始化失败"),
    (0x60F6EAA2, "[firmup]Mount Err:", "firmup", "升级挂载失败"),
    (0x60F6EAB5, "[firmup]mount_ok", "firmup", "升级挂载成功"),
    (0x60F6EAFE, "[firmup]...read file_size =", "firmup", "读到文件大小"),
    (0x60F6EBD0, "[firmup]file_mount/ start.", "firmup", "升级挂载起点"),
    (0x60F6EBEC, "[firmup]file_unmount/ start.", "firmup", "升级卸载起点"),
    (0x60AD783F, "SDCARD   2", "mount", "卷名 SDCARD"),
    (0x60AD784A, "PENPAL   4", "mount", "卷名 PENPAL"),
    (0x60AD7855, "getMountState result is MOUNT_STATE_UNMOUNTED", "mount", "挂载状态"),
    (0x60AD78B0, "getMountState result is MOUNT_STATE_REC", "mount", "挂载状态"),
    (0x60AD78D8, "getMountState result is MOUNT_STATE_PLAY", "mount", "挂载状态"),
    (0x60B9E41C, "getMountState", "media_api", "媒体控制 API 名"),
    (0x60B9E3E6, "isMediaWriting", "media_api", "媒体控制 API 名"),
    (0x60B9E3D8, "isMediaAccess", "media_api", "媒体控制 API 名"),
    (0x603B0F3C, "C:\\DCIM\\", "hostpath", "USB 主机路径仿真"),
    (0x603B0F4E, "C:\\DCIM\\100OLYMP\\", "hostpath", "USB 主机路径仿真"),
    (0x605CD791, "C:\\DCIM\\100OLYMP\\%s", "avm", "录像写文件路径"),
    (0x605CD7AC, "[AVM_FILE_IF] DivStop", "avm", "录像文件 I/F"),
    (0x605CD7E7, "OLYMPUS%05d", "avm", "录像文件名"),
]

# ----------------------------------------------------------------------------
# 候选函数表：地址 = 本次静态分析得出的入口（trace 串所在函数起点 / 调用点解析）
# 每条: addr, name, layer, how(判据), grade(证据等级)
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# fileif 文件 API 层（0x60564xxx）。这一层是「别人也这么用」的高层：
# 模块自己的错误/格式串就叫 fileif_xxx，self-test 与 debug shell 都按名字打印，
# 所以名字→地址的对应不是猜的（见每条的 evidence）。
# 调用约定是 mn10300 的标准寄存器传参，由 thunk 里的 mov 顺序反推（静态推断）。
# ----------------------------------------------------------------------------
FILEIF_API = [
    dict(addr=0x6056570C, name="fileif_fopen", args="(path /*a0*/, flag /*d0*/, mode_str /*a1*/) -> handle",
         note="实体；17 条指令；正常路径直接 call 0x60570DC0。mode_str 实测只见到 \"r\" / \"w\" / \"rde\"；"
              "flag 见 0 / 0x10 两种（0x10 = play_rom 只读加载）",
         evidence="静态已证（入口/指令）+ 静态推断（语义）"),
    dict(addr=0x60564829, name="fileif_open_r（flag=0 的薄壳）",
         args="(path /*a0*/, mode_str /*a1*/) -> handle",
         note="3 条指令：clr d0 / call 0x6056570C / ret。所有 call 点都紧邻一个 'r' 或 'w' 串",
         evidence="静态已证"),
    dict(addr=0x605649BB, name="fileif_fread", args="(buf /*a0*/, size /*d0*/, n /*d1*/, handle /*a1*/) -> n",
         note="49 条指令；正常路径 call 0x60571FB1。调用点紧随 'fileif_fread %d' 打印",
         evidence="静态已证（名字来自自身格式串 + 调用点相邻）"),
    dict(addr=0x605648AF, name="fileif_fwrite", args="(buf /*a0*/, size /*d0*/, n /*d1*/, handle /*a1*/) -> n",
         note="49 条指令；正常路径 call 0x60571917。调用点紧随 'fileif_fwite err.'（固件里 fwrite 拼错成 fwite）",
         evidence="静态已证（名字来自自身格式串 + 调用点相邻）"),
    dict(addr=0x60564AB4, name="fileif_fseek", args="(handle /*a0*/, pos /*d0*/, whence /*d1*/)",
         note="40 条指令；正常路径 call 0x605722A1。调用点紧随 'fileif_fseek %d' 或 'Seek error!!!'",
         evidence="静态已证"),
    dict(addr=0x60565203, name="fileif_ftell", args="(handle /*a0*/, out_ptr /*a1*/) -> pos",
         note="17 条指令；正常路径 call 0x6057287C。调用点紧随 'ftell err'",
         evidence="静态推断（名字来自相邻串，未见自身格式串）"),
    dict(addr=0x605652C2, name="fileif_fseek2（32bit 绝对定位）",
         args="(handle, ...)", note="46 条指令；正常路径 call 0x6057291C。调用点紧随 'fseek2 err'",
         evidence="静态推断"),
    dict(addr=0x60564832, name="fileif_fclose", args="(handle /*a0*/) -> err",
         note="39 条指令；正常路径 call 0x60571318；65 个调用点 / 37 个函数，是这一层用得最多的收尾调用",
         evidence="静态已证"),
    dict(addr=0x60565133, name="fileif_filesize_get", args="(path 或 handle, ...) -> size",
         note="调用点紧随 'fileif_filesize_get 0x%08x 0x%08x' / 'fileif_filesize_get err.'",
         evidence="静态已证"),
    dict(addr=0x6056561E, name="fileif_exists", args="(path) -> bool",
         note="调用点紧随 'fileif_exists err=%d'", evidence="静态已证"),
    dict(addr=0x60564BF2, name="fileif_remove", args="(path)", note="调用点紧随 'fileif_remove ret(%d)'",
         evidence="静态已证"),
    dict(addr=0x60564C8D, name="fileif_mkdir", args="(path)", note="调用点紧随 'fileif_mkdir ret(%d)'",
         evidence="静态已证"),
    dict(addr=0x60564D33, name="fileif_rmdir", args="(path)", note="调用点紧随 'fileif_rmdir ret(%d)'",
         evidence="静态已证"),
    dict(addr=0x60564E3E, name="fileif_attributes_read", args="(path, out_attr)",
         note="调用点紧随 'fileif_attributes_read ret(0x%08x) attr(0x%08x)'", evidence="静态已证"),
    dict(addr=0x60564EF2, name="fileif_attributes_set", args="(path, attr)",
         note="调用点紧随 'fileif_attributes_set ret(0x%08x)'", evidence="静态已证"),
    dict(addr=0x60564F95, name="fileif_filedatetime_read", args="(path, out_dt)",
         note="调用点紧随 'fileif_filedatetime_read error!!! err=%d'", evidence="静态已证"),
    dict(addr=0x6056556D, name="fileif_get_media_status", args="(void) -> status",
         note="'fileif_get_media_status() = %X' 是对它的打印；也打印 'MEDIA STATUS (%d) : 0x%08x'",
         evidence="静态已证"),
    dict(addr=0x605655EC, name="drive 字母取回（打印 'DRIVE (%c)'）",
         args="(void) -> char", note="拼接 %c:\\... 路径时用它拿盘符，比硬编码盘符稳",
         evidence="静态推断"),
    dict(addr=0x60565346, name="fileif_mount", args="(void)", note="debug shell 的 mount 子命令调它",
         evidence="静态推断"),
    dict(addr=0x605653BE, name="fileif_umount", args="(void)", note="debug shell 的 umount 子命令调它",
         evidence="静态推断"),
    dict(addr=0x60565432, name="fileif_format", args="(void 或 参数)", note="debug shell 的 format 子命令调它；危险",
         evidence="静态推断"),
    dict(addr=0x6056FBDF, name="trace 闸门（每个 fileif_* 入口都先调它）",
         args="(void) -> 0/1/2",
         note="返回 0 走正常路径（直接 call DosFat 实现），1/2 会先分配 0x80 字节日志记录再 call 0x6006D2DA。"
              "这就是为什么每个 fileif_* 都有『两条路』——不是两个函数",
         evidence="静态已证（控制流）；语义静态推断"),
]

CANDIDATES = [
    # --- fileif 文件 API 层（最推荐：高层、有名字、用的人多）---
    dict(addr=0x6056570C, name="fileif_fopen", layer="fileif-api",
         how="自身错误串 'fileif_fopen err.'；12 个调用点 / 8 个函数；正常路径 call 0x60570DC0",
         grade="静态已证（入口/调用关系）/ 静态推断（参数语义）"),
    dict(addr=0x605649BB, name="fileif_fread", layer="fileif-api",
         how="调用点紧随 'fileif_fread %d'；33 个调用点 / 20 个函数（AVM 录像 / 相册 / CGI / play_rom / PTP 无关）",
         grade="静态已证"),
    dict(addr=0x605648AF, name="fileif_fwrite", layer="fileif-api",
         how="调用点紧随 'fileif_fwite err.'；38 个调用点 / 15 个函数（录像、GPS 日志、dbgterm、SD 速度测试）",
         grade="静态已证"),
    dict(addr=0x60564832, name="fileif_fclose", layer="fileif-api",
         how="65 个调用点 / 37 个函数；正常路径 call 0x60571318", grade="静态已证"),
    dict(addr=0x60564AB4, name="fileif_fseek", layer="fileif-api",
         how="调用点紧随 'fileif_fseek %d'；正常路径 call 0x605722A1", grade="静态已证"),
    dict(addr=0x6056561E, name="fileif_exists", layer="fileif-api",
         how="调用点紧随 'fileif_exists err=%d'；36 个调用点 / 16 个函数", grade="静态已证"),
    dict(addr=0x60565133, name="fileif_filesize_get", layer="fileif-api",
         how="'fileif_filesize_get %s' / '0x%08x 0x%08x' / 'err.' 三条串；26 个调用点 / 9 个函数",
         grade="静态已证"),
    # --- 回放层文件 API（最接近「应用层可见的文件句柄 API」）---
    dict(addr=0x60E8F8BB, name="play_file_size_check", layer="app-file",
         how="trace 串 'play_file_size_check in' 是该函数第一条 mov；唯一 xref",
         grade="静态已证（函数入口）/ 语义静态推断"),
    dict(addr=0x60E8F995, name="play_file_read", layer="app-file",
         how="trace 串 'play_file_read in' 位于函数入口；9 个 caller 用 5 参 call",
         grade="静态已证（函数入口）/ 语义静态推断"),
    dict(addr=0x60E8FA94, name="play_file_create_file", layer="app-file",
         how="trace 串 'play_file_create_file in'；串内含 check media full / get DCFNUM / create file / add DCF file list 四段",
         grade="静态已证（函数入口）"),
    dict(addr=0x60E8FDC5, name="play_file_copy_file", layer="app-file",
         how="trace 串 'play_file_copy_file in'", grade="静态已证（函数入口）"),
    dict(addr=0x60E90122, name="play_file_update_file", layer="app-file",
         how="trace 串 'play_file_update_file in'", grade="静态已证（函数入口）"),
    dict(addr=0x60E901F7, name="play_file_dcffnode_check", layer="app-file",
         how="trace 串 'play_file_dcffnode_check in'", grade="静态已证（函数入口）"),
    # --- 拍摄写卡路径 ---
    dict(addr=0x60E83071, name="rec write-file 汇聚点（dcf recnode/create/fat chain/cluster/dcfnum 五段错误打印）",
         layer="record", how="'write file error(fat chain write)' 等 5 条串在同一函数内",
         grade="静态已证（函数入口）"),
    # --- 升级路径（已知能读卡！）---
    dict(addr=0x60EB8778, name="firmup file_mount", layer="firmup",
         how="'[firmup]file_mount/ start.' 位于函数入口；升级流程必然读 DCOLYMP/E120*.BIN",
         grade="静态已证（函数入口）"),
    dict(addr=0x60EB8522, name="firmup 主流程（引用 DCOLYMP / E120????BIN）", layer="firmup",
         how="0x60F6E868 'DCOLYMP    ' / mount_ok / Mount Err / read file_size 的 xref 都在此函数", grade="静态已证（函数入口）"),
    # --- PTP/USB 传输层（把卡当 U 盘/TWAIN 对象用的那一套）---
    dict(addr=0x60E5FA6B, name="tpc_ptp_utl_fat_read", layer="ptp",
         how="trace 串 'tpc_ptp_utl_fat_read:ERR 0x'", grade="静态已证（函数入口）"),
    dict(addr=0x60E5FD2E, name="tpc_ptp_utl_find_dos_file", layer="ptp",
         how="trace 串 'tpc_ptp_utl_find_dos_file:ERR 0x'", grade="静态已证（函数入口）"),
    dict(addr=0x60E60647, name="tpc_ptp_utl_dcf_mount", layer="ptp",
         how="trace 串 'tpc_ptp_utl_dcf_mount:ERR 0x'", grade="静态已证（函数入口）"),
    # --- 挂载/媒体层 ---
    dict(addr=0x606BE375, name="getMountState 打印点所在函数（挂载状态机）", layer="mount",
         how="'getMountState result is MOUNT_STATE_UNMOUNTED' 唯一 xref", grade="静态已证"),
    # --- FAT 驱动内部（不要直接调）---
    dict(addr=0x60551F7B, name="FAT 驱动：簇链校验/错误打印", layer="fat",
         how="'NO_FAT_CHAIN ?? %08X %08X' 唯一 xref", grade="静态已证（函数入口）"),
]

LAYERS = r"""
应用层 (GUI / 拍摄 / 回放 / 升级 / PTP / HTTP)
  |  events, dcffnode(目录+文件号), API_* 返回值
  v
[媒体/卷管理层]  getMountState / isMediaWriting / changeMedia / api_media_Mount_Media
  |  MOUNT_STATE_UNMOUNTED / REC / PLAY ；SDCARD / PENPAL 卷名
  v
[应用层各业务模块]
     A 回放/编辑:  play_file_read / play_file_create_file / play_file_update_file
                   play_file_copy_file / play_file_size_check / play_file_dcffnode_check
     B 拍摄:       rec 写卡汇聚点（dcf recnode → create file → fat chain write → cluster）
     C 升级:       firmup file_mount / file_unmount（只读 DCOLYMP/E120????.BIN）
     D 影片/AVM:   [AVM_FILE_IF] 0x604633A8 / 0x60463CC1 / 0x60463E82 / 0x60464035
     E 相册:       [ALBM] 0x6057FF6E / 0x605814FB
     F HTTP/CGI:   cgi_send_file 0x60E2B953 / 0x60E2DBF7（web 端下载、上传）
     G PTP/USB:    tpc_ptp_utl_find_dos_file / fat_read / media_write / dcf_mount
  |  业务参数（DCF 号、目录号、缓冲区、长度）
  v
[fileif 文件 API 层]  0x60564xxx —— ★推荐的调用面★
     fileif_fopen 0x6056570C / 薄壳 0x60564829 / fclose 0x60564832
     fileif_fread 0x605649BB / fileif_fwrite 0x605648AF
     fileif_fseek 0x60564AB4 / ftell 0x60565203 / fseek2 0x605652C2
     fileif_filesize_get 0x60565133 / exists 0x6056561E
     fileif_mkdir 0x60564C8D / rmdir 0x60564D33 / remove 0x60564BF2
     fileif_attributes_read 0x60564E3E / _set 0x60564EF2 / filedatetime_read 0x60564F95
     fileif_get_media_status 0x6056556D / mount 0x60565346 / umount 0x605653BE / format 0x60565432
     每个函数入口先 call 0x6056FBDF（trace 闸门）再 call 具体实现
  |  路径串（"A:\\DCIM\\..." 形式，盘符来自 0x605655EC）+ 文件句柄
  v
[DOS 文件流层]  0x60570xxx / 0x60571xxx / 0x60572xxx
     0x60570DC0 open / 0x60571318 close / 0x60571917 write / 0x60571FB1 read
     0x605722A1 seek / 0x6057287C tell / 0x6057291C seek2
     0x6057256F remove / 0x6057257C mkdir / 0x60572664 rmdir / 0x6057268F attr_read
     0x60572723 attr_set   （DOSFSTRM_* 这些名字只是日志标签，不是入口）
  |  簇号、扇区号
  v
[DOS/FAT 层]  DOSFSTRM_OPEN / READ / WRITE / SEEK / CLOSE / FLUSH
              DOSMEM_READ / WRITE（扇区级）
              DCFSEARCH_NAME / NUMBER / DATE（目录项检索）
              FAT_CHAIN_READ / READ2 / WRITE / WRITE_EX
  |  簇号、扇区号
  v
[FAT 驱动] FAT12/16/32/exFAT 判定 + FSInfo 一致性校验 + 卷信息
            (0x60629xxx 一带；VOLUME_INFO / "There are some differnces between fat and fsinfo")
  |
  v
[块设备 / SD 控制器]  SDCARD 卷、sdio 驱动
"""

# ----------------------------------------------------------------------------
# 触发点候选（安全等级是静态推理，不是实测）
# ----------------------------------------------------------------------------
TRIGGERS = [
    dict(name="卡上文件被固件升级流程读取（firmup）", point="firmup file_mount @0x60EB8778 → 读 DCOLYMP/E120????.BIN 段",
         why="本机唯一『官方保证会去卡上读我们自己的文件』的路径；卡已挂载、缓冲区已备好、错误串齐全（Mount Err / read file_size）",
         risk="进入点由『开机 + 卡上 DCOLYMP/E120????.BIN 命名匹配』决定；任何跳转错误都会落进升级判定，砖风险最高。只适合一次性只读验证"),
    dict(name="回放/相册（play_file_* / [ALBM]）", point="play_file_size_check 0x60E8F8BB → play_file_read 0x60E8F995；或相册初始化 0x6057FF6E",
         why="回放模式里选一张卡上的图就会走；相册 0x6057FF6E 本身就在做 exists/remove/fread（[ALBM][INIT]file read）",
         risk="要求 dcffnode（目录号+文件号）合法；相册路径会顺带删 0 字节文件（[ALBM][INIT]size 0 file delete），挂错点可能误删卡上文件"),
    dict(name="fileif debug shell（filetest / dostest / dcftest）", point="fileif_test_main 0x60565A3C，由 diag 派发器 0x60561355（correct/volinfo/filetest/...）调用",
         why="现成的命令解释器：read/write/mkdir/cp/size/attr/search/dcfnum 全都在里面，等于把整个 fileif API 变成可交互命令",
         risk="静态证据：0x60561355 没有任何 call 调用者、也没有 4 字节指针表引用 → 很可能是量产版被摘掉的开发用 shell（未证可达）。若可达，它就是最省事的迭代口"),
    dict(name="PTP/USB 传输层（tpc_ptp_utl_*）", point="tpc_ptp_utl_find_dos_file 0x60E5FD2E / tpc_ptp_utl_fat_read 0x60E5FA6B",
         why="把卡当 U 盘对象枚举/读取，参数就是 DOS 路径，函数名与语义最直白",
         risk="依赖 PTP 会话状态机；插 USB 后与 PC 争用媒体，误碰写接口有删文件风险"),
]

# ----------------------------------------------------------------------------


class Index:
    def __init__(self):
        self.addr, self.mn, self.ops = [], [], []
        self.ret_idx = []
        if not os.path.exists(IDX):
            sys.exit("缺少指令索引 %s\n先跑: python tools/find_file_api.py index" % IDX)
        with open(IDX, "r", encoding="latin-1") as f:
            for line in f:
                p = line.rstrip("\n").split("|")
                if len(p) != 3:
                    continue
                self.addr.append(int(p[0], 16))
                self.mn.append(p[1])
                self.ops.append(p[2])
        self.ret_idx = [i for i, m in enumerate(self.mn) if m.startswith("ret")]

    def i_at(self, a):
        """<= a 的最后一条指令下标；a 不在覆盖范围内返回 None。"""
        i = bisect.bisect_right(self.addr, a) - 1
        if i < 0 or self.addr[i] > a:
            return None
        return i

    def fstart(self, a):
        """函数起点 = 最近一条 ret/retf 之后的第一条指令。"""
        i = self.i_at(a)
        if i is None:
            return None
        j = bisect.bisect_left(self.ret_idx, i)
        if j == 0:
            return self.addr[0]
        k = self.ret_idx[j - 1] + 1
        return self.addr[k] if k < len(self.addr) else None

    def fend(self, a):
        """函数终点 = 从这里往后的第一条 ret/retf。"""
        i = self.i_at(a)
        if i is None:
            return None
        j = bisect.bisect_left(self.ret_idx, i)
        if j >= len(self.ret_idx):
            return self.addr[-1]
        return self.addr[self.ret_idx[j]]

    def body(self, a, limit=None):
        s = self.fstart(a)
        if s is None:
            return s, []
        i0 = bisect.bisect_left(self.addr, s)
        i1 = bisect.bisect_left(self.addr, self.fend(s)) + 1
        if limit:
            i1 = min(i1, i0 + limit)
        return s, [(self.addr[i], self.mn[i], self.ops[i]) for i in range(i0, min(i1, len(self.addr)))]

    def call_target(self, ops):
        """'call 0x604bc2fb,[d2,d3],0xc' -> 0x604bc2fb"""
        if not ops.startswith("0x"):
            return None
        return int(ops.split(",")[0], 16)


class Image:
    def __init__(self, path=IMG):
        self.d = open(path, "rb").read()

    def xrefs(self, target, width=4):
        pat = struct.pack("<I", target)
        out, o = [], 0
        while True:
            o = self.d.find(pat, o)
            if o < 0:
                return out
            out.append(BASE + o)
            o += 1

    def find_str(self, text):
        b = text.encode("latin-1")
        o = self.d.find(b)
        return BASE + o if o >= 0 else None

    def ascii_runs(self, minlen=4):
        runs, i, n = [], 0, len(self.d)
        d = self.d
        while i < n:
            if 0x20 <= d[i] < 0x7F:
                s = i
                while i < n and 0x20 <= d[i] < 0x7F:
                    i += 1
                if i - s >= minlen:
                    runs.append((BASE + s, d[s:i].decode("latin-1")))
            else:
                i += 1
        return runs


CATS = [
    ("dcf_dir", ("DCIM", "OLYMP", "DCOLYMP", "SYSTEM", "VOLUME", "/DCIM")),
    ("fat", ("FAT12", "FAT16", "FAT32", "EXFAT", "FSInfo", "FAT_CHAIN", "SectorCluster")),
    ("open_read_write", ("DOSFSTRM", "DOSMEM", "DCFSEARCH", "fopen", "_read", "_write",
                         "_open", "_close", "_seek", "file_open", "file_read", "file_write")),
    ("mount", ("mount", "Mount", "MOUNT", "umount", "Unmount", "UNMOUNT")),
    ("firmup", ("DCOLYMP", "firmup", "E120", "S120", "G120", "H120")),
    ("media_api", ("getMountState", "isMediaWriting", "isMediaAccess", "changeMedia",
                   "getMediaStateInfo", "getExistMedia")),
    ("ptp", ("tpc_ptp_utl", "getFileList", "getPartialFile")),
    ("http", ("fileif_", ".cgi", "do_file", "do_dir")),
]


def classify(s):
    for name, kws in CATS:
        for k in kws:
            if k in s:
                return name
    return "other"


# ----------------------------------------------------------------------------
def cmd_report(args):
    img = Image()
    idx = Index()
    print("# 1. 字符串锚点（%d 条，地址为 RAM 地址）\n" % len(ANCHORS))
    print("| RAM 地址 | 字符串 | 层 | 说明 | 引用函数 |")
    print("|---|---|---|---|---|")
    for a, s, layer, note in ANCHORS:
        xs = img.xrefs(a)
        fns = sorted({idx.fstart(x - 2) or 0 for x in xs})
        fs = ", ".join("0x%08X" % f if f else "?" for f in fns) if fns else "（无直接引用）"
        print("| 0x%08X | `%s` | %s | %s | %s |" % (a, s.replace("|", "\\|"), layer, note, fs))
    print("\n# 2. 候选函数（%d 条）\n" % len(CANDIDATES))
    print("| 地址 | 名字 | 层 | 判据 | 证据等级 |")
    print("|---|---|---|---|---|")
    for c in CANDIDATES:
        print("| 0x%08X | %s | %s | %s | %s |" % (c["addr"], c["name"], c["layer"],
                                                   c["how"], c["grade"]))
    print("\n# 3. 分层\n" + LAYERS)
    print("# 4. 触发点候选\n")
    for t in TRIGGERS:
        print("* **%s** — `%s`\n  * 为什么: %s\n  * 风险: %s" % (t["name"], t["point"],
                                                                t["why"], t["risk"]))


def cmd_api(args):
    """打印 fileif 文件 API 表，并逐个验证『这地址真的是函数起点』。"""
    idx = Index()
    print("| 地址 | 名字 | 参数（静态推断） | 校验 |")
    print("|---|---|---|---|")
    bad = 0
    for e in FILEIF_API:
        f = idx.fstart(e["addr"])
        ok = (f == e["addr"])
        if not ok:
            bad += 1
        print("| 0x%08X | `%s` | `%s` | %s |" % (e["addr"], e["name"], e["args"],
                                                "函数起点 ✓" if ok else "✗ 实际起点 0x%08X" % (f or 0)))
    print("\n校验失败 %d / %d" % (bad, len(FILEIF_API)))
    print("\n细节：")
    for e in FILEIF_API:
        print("* **0x%08X %s** — %s  \n  * 证据：%s" % (e["addr"], e["name"], e["note"], e["evidence"]))


def cmd_strings(args):
    img = Image()
    runs = img.ascii_runs(4)
    if args.kw:
        runs = [(a, s) for a, s in runs if args.kw in s]
    print("共 %d 条" % len(runs))
    groups = {}
    for a, s in runs:
        groups.setdefault(classify(s), []).append((a, s))
    for name, _ in CATS + [("other", ())]:
        rows = groups.get(name) or []
        if not rows or (name == "other" and not args.all):
            continue
        print("\n## %s (%d)" % (name, len(rows)))
        for a, s in rows:
            print("0x%08X  %s" % (a, s[:120]))


def cmd_xref(args):
    img = Image()
    idx = Index()
    if args.str:
        t = img.find_str(args.str)
        if t is None:
            sys.exit("找不到字符串: %r" % args.str)
        print("字符串 %r @ 0x%08X" % (args.str, t))
    else:
        t = int(args.hex, 16)
    xs = img.xrefs(t)
    print("xref 命中 %d 处" % len(xs))
    for x in xs:
        i = idx.i_at(x - 2)
        ctx = "%s %s" % (idx.mn[i], idx.ops[i]) if i is not None else "(数据/表)"
        print("  0x%08X  所属函数 0x%08X   %s" % (x, idx.fstart(x - 2) or 0, ctx))


def cmd_func(args):
    idx = Index()
    a = int(args.hex, 16)
    s, e = idx.fstart(a), idx.fend(a)
    print("0x%08X: 函数 0x%08X .. 0x%08X  (%d 字节)" % (a, s, e, e - s + 1))
    return s


def cmd_dis(args):
    idx = Index()
    a = int(args.hex, 16) if args.hex else 0
    if args.str:
        a = Image().find_str(args.str)
    s, body = idx.body(a, args.n)
    print("; 函数 0x%08X (起点) 共 %d 条" % (s, len(body)))
    for (ia, m, o) in body:
        extra = ""
        if m == "call":
            t = idx.call_target(o)
            if t:
                extra = "   ; -> 函数 0x%08X" % (idx.fstart(t) or 0)
        elif m in ("jmp", "bra", "beq", "bne", "bhi", "bls", "bge", "ble", "bgt", "blt"):
            extra = ""
        print("%08X|%s|%s%s" % (ia, m, o, extra))


def cmd_callers(args):
    idx = Index()
    a = int(args.hex, 16)
    pat = ("0x%08x" % a).encode()
    hits = []
    with open(IDX, "r", encoding="latin-1") as f:
        for line in f:
            if "|call|" in line and pat in line.lower().encode():
                hits.append(line.rstrip())
    print("call 到 0x%08X 的位置 %d 处" % (a, len(hits)))
    for h in hits:
        ia = int(h.split("|")[0], 16)
        print("  %s   (函数 0x%08X)" % (h, idx.fstart(ia) or 0))


def cmd_chain(args):
    img = Image()
    idx = Index()
    root = int(args.hex, 16)
    seen, frontier = {}, [root]
    for depth in range(args.depth + 1):
        nxt = []
        for f in frontier:
            s, body = idx.body(f)
            if s is None or s in seen:
                continue
            seen[s] = depth
            strs = []
            calls = []
            for (ia, m, o) in body:
                if m == "call":
                    t = idx.call_target(o)
                    if t:
                        fs = idx.fstart(t)
                        calls.append((ia, t, fs))
                        if fs and fs not in seen:
                            nxt.append(fs)
                elif m == "mov" and o.startswith("0x60") and "," in o:
                    # mov <imm>,reg —— imm 可能正好是字符串地址
                    try:
                        v = int(o.split(",")[0], 16)
                    except ValueError:
                        continue
                    if 0x60F00000 <= v < 0x61400000:
                        strs.append((ia, v))
            print("\n[depth %d] 0x%08X  (%d 条指令, %d 个 call)" % (depth, s, len(body), len(calls)))
            for ia, v in strs[:20]:
                raw = img.d.view if False else None
                s2 = _str_at(img, v)
                if s2:
                    print("    str @%08X: %r" % (v, s2[:90]))
            for ia, t, fs in calls[:40]:
                print("    call @%08X -> 0x%08X (函数 0x%08X)" % (ia, t, fs or 0))
        frontier = sorted(set(nxt))


def _str_at(img, a):
    off = a - BASE
    if off < 0 or off >= len(img.d):
        return None
    end = off
    while end < len(img.d) and 0x20 <= img.d[end] < 0x7F and end - off < 100:
        end += 1
    if end - off < 4:
        return None
    return img.d[off:end].decode("latin-1")


def cmd_index(args):
    """用 objdump 重建指令索引（很慢，一次性）。"""
    size = os.path.getsize(IMG)
    os.makedirs(os.path.dirname(IDX), exist_ok=True)
    CH = 0x40000
    with open(IDX, "w", encoding="latin-1") as out:
        off = 0
        while off < size:
            n = min(CH, size - off)
            # 对齐到 2 字节边界，mn10300 指令都是偶数长
            cmd = [OBJDUMP, "-D", "-b", "binary", "-m", "mn10300",
                   "--adjust-vma=0x%X" % (BASE + off),
                   "--start-address=0x%X" % (BASE + off),
                   "--stop-address=0x%X" % (BASE + off + n), IMG]
            r = subprocess.run(cmd, capture_output=True, text=True, errors="latin-1")
            for line in r.stdout.splitlines():
                if ":\t" not in line:
                    continue
                a, rest = line.split(":\t", 1)
                a = a.strip()
                if len(a) != 8 or not all(c in "0123456789abcdefABCDEF" for c in a):
                    continue
                parts = rest.split("\t")
                if len(parts) < 2:
                    continue
                mn = parts[1].strip()
                ops = parts[2].strip() if len(parts) > 2 else ""
                out.write("%s|%s|%s\n" % (a.upper(), mn, ops))
            off += n
            print("\r%d/%d" % (off, size), end="", file=sys.stderr)
    print("\n写出 %s" % IDX, file=sys.stderr)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="E-M10 II 固件 SD 文件 API 定位器")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("report").set_defaults(fn=cmd_report)
    sub.add_parser("api").set_defaults(fn=cmd_api)
    sub.add_parser("index").set_defaults(fn=cmd_index)

    p = sub.add_parser("strings")
    p.add_argument("--all", action="store_true")
    p.add_argument("--kw", default=None)
    p.set_defaults(fn=cmd_strings)

    for name, fn in (("xref", cmd_xref),):
        p = sub.add_parser(name)
        p.add_argument("hex", nargs="?")
        p.add_argument("--str", dest="str", default=None)
        p.set_defaults(fn=fn)

    p = sub.add_parser("func")
    p.add_argument("hex")
    p.set_defaults(fn=cmd_func)

    p = sub.add_parser("dis")
    p.add_argument("hex", nargs="?")
    p.add_argument("--str", dest="str", default=None)
    p.add_argument("--n", type=int, default=0)
    p.set_defaults(fn=cmd_dis)

    p = sub.add_parser("callers")
    p.add_argument("hex")
    p.set_defaults(fn=cmd_callers)

    p = sub.add_parser("chain")
    p.add_argument("hex")
    p.add_argument("--depth", type=int, default=2)
    p.set_defaults(fn=cmd_chain)

    args = ap.parse_args()
    if not getattr(args, "fn", None):
        ap.print_help()
        return
    args.fn(args)


if __name__ == "__main__":
    main()
