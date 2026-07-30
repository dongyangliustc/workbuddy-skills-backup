---
name: gaussian-agent
version: 1.0.0
description: >
  综合性 Gaussian 计算化学智能体。整合 Sobko MCP 知识库（思想家公社 577 篇学术帖 + Multiwfn 手册）的检索能力与 gjf-flux / run-gauss / dpdata-driver 的实操工具链，实现从知识查询、输入文件生成、计算执行到数据标注的全流程覆盖。
  适用于 Gaussian 计算化学任务的自动化，尤其适合需要查阅关键字用法、泛函/基组选型建议、错误排查和波函数分析的场景。
requirements: |
  核心依赖：
  - Python 3.10+
  - `uv` installed and available in PATH
  - pip install uv rdkit ase dpdata numpy

  知识库 (Sobko MCP)：
  - git clone https://github.com/WangGroupFDU/Sobko_MCP_project.git
  - 项目约 650 MB（含预构建索引），克隆后无需联网即可启动
  - 基础模式仅需 Python 标准库（BM25 检索）
  - 增强模式需安装 `mcp` 和 `FlagEmbedding` 可选包

  其他技能：
  - gjf-flux (v0.1.0): Gaussian .gjf 文件组装与提取
  - run-gauss (v0.1.0): Gaussian 执行环境知识库
  - dpdata-driver (v1.0): dpdata Python Driver 能量/力标注
---

# Gaussian Agent — 计算化学综合智能体

将知识检索与实操工具链整合为统一的 Gaussian 计算化学工作流。

## 架构总览

```
用户提问
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  gaussian-agent                                       │
│                                                       │
│  ├─ Sobko MCP (知识引擎)                               │
│  │   ├─ sobko_search  → 检索 Gaussian 关键字/方法/基组  │
│  │   ├─ sobko_fetch   → 展开原文上下文                  │
│  │   ├─ sobko_get_image → 获取配图                     │
│  │   └─ sobko_trace_source → 追溯来源权威性             │
│  │                                                     │
│  ├─ gjf-flux (输入文件组装)                             │
│  │   ├─ assemble directives → %chk / %mem / %nproc     │
│  │   ├─ assemble route      → #P route section          │
│  │   ├─ assemble molecules  → 分子坐标块                │
│  │   ├─ assemble job        → 完整 .gjf 合成            │
│  │   ├─ assemble tasks      → Link1 多步作业            │
│  │   └─ extract             → 反向提取验证              │
│  │                                                     │
│  ├─ run-gauss (执行环境)                                │
│  │   ├─ 环境检查清单 / scratch 管理 / 命令模板           │
│  │   └─ 支持 local / HPC (Slurm/PBS/LSF/Bohrium)       │
│  │                                                     │
│  ├─ dpdata-driver (数据标注)                            │
│  │   ├─ System.predict(driver="gaussian")              │
│  │   ├─ System.predict(driver="ase", calculator=...)    │
│  │   └─ 标注能量 / 力 / 维里量                          │
│  │                                                     │
│  └─ 分子结构生成 (可选)                                 │
│      ├─ ASE:  ase.build.molecule("CH4")  ~80种常见分子  │
│      ├─ RDKit:  SMILES → ETKDGv3 → MMFF94s (任意有机)  │
│      └─ 第一性原理: VSEPR + 标准键长 (零依赖)           │
└─────────────────────────────────────────────────────┘
```

## 工作流

### 工作流 A：知识查询主导

当用户询问 Gaussian 语法、方法选型、错误排查时，优先使用 Sobko MCP 检索：

```
step 1. sobko_search(query=用户问题, top_k=8)
step 2. 阅读检索结果中的 evidence snippets
step 3. 若需完整上下文 → sobko_fetch(chunk_id=...)
step 4. 若需追溯来源 → sobko_trace_source(chunk_id=...)
step 5. 综合知识回答用户
```

### 工作流 B：输入文件生成主导

当用户需要生成 Gaussian 输入文件时：

```
step 1. 若用户不确定方法/基组/关键字
       → sobko_search 查询最佳实践

step 2. 确定分子结构
       ├─ ASE: ase.build.molecule("CH4")
       ├─ RDKit: Chem.MolFromSmiles("C") → ETKDGv3 → MMFF94s
       └─ 第一性原理：VSEPR + 标准键长手动构造

       ⚠️ 坐标格式要求：所有原子坐标必须使用 6 位小数格式（.6f），
       例如 `C     0.000000    0.000000    0.000000`。
       整数格式（如 `C 0 0 0`）会导致 Gaussian 解析失败。

step 3. gjf-flux 组装（必须按顺序执行）
       a. assemble directives → %chk / %mem / %nprocshared
       b. assemble route → #P 方法/基组/关键字
       c. assemble molecules → 格式化分子坐标（必须经过此步骤，
          不可直接将原始坐标片段传给 assemble job）
       d. assemble job → 合成完整 .gjf
       e. (可选) assemble tasks → 多步 Link1 作业

step 4. 验证
       gjf-flux extract route / molecule / title → 确认正确性
```

### 工作流 C：结果分析主导

当用户需要分析 Gaussian 计算结果时：

```
step 1. run-gauss → 检查环境、执行 .gjf 文件
step 2. dpdata-driver → 读取输出
       System.predict(driver="gaussian", ...)
step 3. 获取能量/力/维里量数据
step 4. 结合 Sobko MCP 知识解释结果
```

## Sobko MCP 配置与启动

### 首次设置

```bash
# 1. 克隆项目
cd C:/Users/Administrator.DESKTOP-7RU274I/WorkBuddy/2026-07-01-15-05-26
git clone https://github.com/WangGroupFDU/Sobko_MCP_project.git

# 2. 验证索引完整性
cd Sobko_MCP_project
python scripts/smoke_mcp.py

# 3. 启动 MCP server（stdio 模式）
python scripts/run_server.py
```

### 注册为 WorkBuddy MCP Connector

在 `~/.workbuddy/mcp.json` 中添加：

```json
{
  "mcpServers": {
    "sobko-kb": {
      "command": "python",
      "args": ["/absolute/path/to/Sobko_MCP_project/scripts/run_server.py"],
      "env": {
        "SOBKO_FORCE_BUILTIN_MCP": "1",
        "SOBKO_DISABLE_LOCAL_RERANKER": "1"
      }
    }
  }
}
```

重启 WorkBuddy 后 Sobko 的四个 MCP 工具将自动可用：
- `sobko_search` — 知识库检索
- `sobko_fetch` — 展开上下文
- `sobko_get_image` — 获取配图
- `sobko_trace_source` — 追溯来源

### BM25 基础模式 vs Hybrid 增强模式

| 模式 | 依赖 | 检索质量 |
|------|------|---------|
| BM25 基础 | Python 标准库 | 关键词匹配，结果稳定 |
| Hybrid 增强 | Ollama + bge-m3 + bge-reranker-v2-m3 | 语义检索，排序更精准 |

增强模式配置（`configs/default.json`）：

```json
{
  "embedding_api_base_url": "http://127.0.0.1:11434",
  "embedding_model": "bge-m3:latest",
  "rerank_api_base_url": "http://127.0.0.1:11434",
  "rerank_model": "dengcao/bge-reranker-v2-m3:latest"
}
```

## 内置知识图谱

本智能体覆盖的计算化学知识领域：

| 领域 | 来源 | 级别 |
|------|------|------|
| Gaussian 关键字语法 | Sobko MCP (思想家公社) | A 级权威 |
| DFT 泛函与基组选型 | Sobko MCP + AI 模型知识 | A/B 级 |
| 分子结构生成 | ASE / RDKit / 第一性原理 | 自动 |
| .gjf 文件格式 | gjf-flux SKILL.md | 格式规范 |
| Gaussian 执行环境 | run-gauss SKILL.md | 操作指南 |
| 数据标注与分析 | dpdata-driver | Python API |
| Multiwfn 波函数分析 | Sobko MCP (用户手册) | A 级权威 |

## 使用指南

### 推荐命令模式

```bash
# 查询知识 + 组装输入文件
"sobko_search: B3LYP/6-31G(d) 优化甲烷 → 用 gjf-flux 生成 .gjf"

# 排查错误
"sobko_search: Gaussian 错误 L502 → 分析原因"

# 完整工作流
"用 B3LYP/def2-TZVP 优化水分子, 生成 .gjf 文件"
```
