# -*- coding: utf-8 -*-
"""生成 docs/map/INDEX.md 与 docs/color/INDEX.md（一次性脚本）"""
import pathlib
import re

MARK = re.compile('推翻|作废|撤回|未定|未证实|已废')


def rows(d):
    out = []
    for f in sorted(pathlib.Path(d).glob('*.md')):
        if f.name == 'INDEX.md':
            continue
        txt = f.read_text(encoding='utf-8', errors='replace')
        title = ''
        for line in txt.splitlines():
            if line.startswith('#'):
                title = line.lstrip('#').strip()
                break
        # 只认文件开头 25 行里的「横幅行」（以 > 开头），避免抓到正文里的噪点
        flag = ''
        head_lines = [l for l in txt.splitlines()[:25] if l.strip()]
        for line in head_lines:
            if line.lstrip().startswith('>') and MARK.search(line):
                flag = re.sub(r'[>*`]', '', line).strip()[:46]
                break
        out.append((f.name, title[:50], flag or '—'))
    return out


MAP_MAIN = ['CHAIN-ATLAS-2026-11-17.md', 'ATLAS.md', 'tone-dirs.md', 'mode-to-table.md',
            'chroma-consumers.md', 'chroma-mode-diff.md', 'exif-write.md']
MAP_SAFE = ['dead-zones.md', 'mem-regions.md', 'exec-map.md',
            'pitch-boot.md', 'hook-sites.md', 'hook-abi.md']


def build_map():
    m = rows('docs/map')
    idx = {fn: (ti, fl) for fn, ti, fl in m}
    t = ['# map/ 索引（结构图与地址图）', '',
         '> %d 份，都是「这台机器的某个结构长什么样」。按用途分三组：主线链路 / 通道细节 / 区域与安全。' % len(m),
         '> 第三列是文件里抓到的「推翻·未定」原文片段；有字样的按横幅重读，空的不代表已验证。', '',
         '## 一、主线链路（最常看）', '',
         '| 文件 | 是什么 | 备注 |', '| :-- | :-- | :-- |']
    for n in MAP_MAIN:
        if n in idx:
            t.append('| `%s` | %s | %s |' % (n, idx[n][0], idx[n][1]))
    t += ['', '## 二、通道细节', '', '| 文件 | 是什么 | 备注 |', '| :-- | :-- | :-- |']
    for fn, ti, fl in m:
        if fn in MAP_MAIN or fn in MAP_SAFE:
            continue
        t.append('| `%s` | %s | %s |' % (fn, ti, fl))
    t += ['', '## 三、区域与安全（上机前必看）', '',
          '| 文件 | 是什么 | 备注 |', '| :-- | :-- | :-- |']
    for n in MAP_SAFE:
        if n in idx:
            t.append('| `%s` | %s | %s |' % (n, idx[n][0], idx[n][1]))
    t += ['', '## 四、读法建议', '',
          '1. 改某条通道 → 先在 `CHAIN-ATLAS-2026-11-17.md`（综合层）定位它属于哪一环，再进各链小地图；`ATLAS.md` 只是骨架。',
          '2. 选落点 → 只看 `dead-zones.md` 与 `mem-regions.md`；不要相信「全 FF 就是空的」。',
          '3. 这些图都是**静态**结论；真机验证记录在 `docs/STATUS-2026-11-16.md`。', '']
    pathlib.Path('docs/map/INDEX.md').write_text('\n'.join(t), encoding='utf-8')
    return len(m)


def build_color():
    c = rows('docs/color')
    t = ['# color/ 索引（色彩数据与规格）', '',
         '> 28 份。按用途分三组：现行规格（可直接出包）/ 分析与推导（怎么算出来的）/ 其余参考。',
         '> 现行规格优先级：`look-spec-<look>.md` ＞ `look-spec-matrix.md` ＞ 其余；旧的 `looks-spec.md` 已归档到 `docs/archive/looks/`。', '',
         '## 一、现行规格（出包用）', '', '| 文件 | 是什么 | 备注 |', '| :-- | :-- | :-- |']
    for fn, ti, fl in c:
        if fn.startswith('look-spec'):
            t.append('| `%s` | %s | %s |' % (fn, ti, fl))
    t += ['', '## 二、分析与公式（怎么来的）', '', '| 文件 | 是什么 | 备注 |', '| :-- | :-- | :-- |']
    for fn, ti, fl in c:
        if not fn.startswith('look-spec'):
            t.append('| `%s` | %s | %s |' % (fn, ti, fl))
    t += ['', '## 三、读法建议', '',
          '1. 只想出包 → 只读 `look-spec-*.md` 与 `look-spec-matrix.md`，推导文档不用碰。',
          '2. 想改配方 → 口径来源是 `tone-calibration.md` 与 `fuji-*`；记住索引口径 **P=255**（相机索引 = 线性值 × 255）。',
          '3. 拿数字前先看第三列有没有「推翻/未定」。', '']
    pathlib.Path('docs/color/INDEX.md').write_text('\n'.join(t), encoding='utf-8')
    return len(c)


if __name__ == '__main__':
    print('map rows', build_map())
    print('color rows', build_color())
