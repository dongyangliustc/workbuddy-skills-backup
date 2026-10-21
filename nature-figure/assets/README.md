# ⚠️ 此目录未纳入 Git 备份（体积原因）

本目录（`nature-figure` 绘图资源，chart-atlas 图集 PNG 等，约 34MB）为**可再生成/可再获取资源**，
已在同步到 GitHub（`dongyangliustc/workbuddy-skills-backup`）时排除，以保持仓库轻量。

## 恢复方式

1. **重新生成**：chart-atlas 图集可由 `nature-figure/scripts/` 中的脚本用 matplotlib 重新绘制。
2. **从已有本地副本复制**：本机 `~/.workbuddy/skills/nature-figure/assets/`（即本目录）。
3. **联系仓库所有者**（dongyangliustc）获取历史版本：2026-07-30 的旧备份 commit 中曾包含本目录。

## 排除规则

仓库根 `.gitignore` 中：
```
nature-figure/assets/*
!nature-figure/assets/README.md
```

本 README 本身会被备份（作为标记），其余文件不会。
