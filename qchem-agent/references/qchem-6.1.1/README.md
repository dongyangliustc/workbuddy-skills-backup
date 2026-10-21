# ⚠️ 此目录未纳入 Git 备份（体积原因）

本目录（Q-Chem 6.1.1 官方手册、模块文档与输入样例，约 39MB）为**第三方可再获取资源**，
已在同步到 GitHub（`dongyangliustc/workbuddy-skills-backup`）时排除，以保持仓库轻量。

## 恢复方式（按优先级）

1. **从本机其他安装复制**：Q-Chem 6.1.1 安装目录下的 `samples/` 与手册文件。
2. **官方渠道下载**：Q-Chem 官网 https://www.q-chem.com/ 的用户区提供手册与样例下载（需授权）。
3. **向 skill 作者索取**：`qchem-agent` 的 `references/modules.md` 列出了本目录的模块索引，
   可按索引逐个补齐。

## 排除规则

仓库根 `.gitignore` 中：
```
qchem-agent/references/qchem-6.1.1/*
!qchem-agent/references/qchem-6.1.1/README.md
```

本 README 本身会被备份（作为标记），其余文件不会。
