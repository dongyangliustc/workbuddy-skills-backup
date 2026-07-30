# 风暴知识工坊 (Storm Knowledge Crafter)

> 从资料索引到深度学习的全流程知识制作 Agent

## 中文说明

### 1. 功能概述

本 Skill 提供两大核心能力：

**层一：资料索引引擎** — 将本地 PDF/TXT/Markdown 资料转化为可检索的只读 MCP 知识库，每次回答附带精确的 PDF 页码或文件行号证据。

**层二：10步风暴学习法** — 融合斯坦福 STORM 多视角研究法（NAACL 2024）与大刘的完整闭环学习方法论，覆盖从入门到精通的全流程：
- 五视角拆解 → 矛盾图谱 → 综合简报 → 同行评审
- 资源筛选 → 学习阶梯 → 2小时核心20%
- 主动回忆测试 → 费曼循环补漏 → 速查表归档

### 2. 快速开始

#### 方式一：有资料文件（完整流程）

```text
使用 $storm-knowledge-crafter 系统学习【你的主题】。

我有资料文件夹：/path/to/materials
MCP 名称：my_knowledge_base

规则：
- "教材.pdf" 是主教材，最高优先级
- "课件.pdf" 是课件，第二优先级
- "笔记.md" 是补充资料，第三优先级

先建立索引，然后执行完整的10步学习闭环。
```

#### 方式二：无资料文件（纯学习方法）

```text
使用 $storm-knowledge-crafter 学习【你的主题】。

我没有现成的资料文件，请直接启动10步学习法，
从第1步五视角拆解开始。
```

### 3. 自动化执行流程

用户无需手工编辑 JSON 或 YAML。用户职责与自动化流程如下：

| 用户职责 | 自动化流程 |
| --- | --- |
| 准备资料文件夹 | 扫描文件并检查格式 |
| 定义课件、教材与补充资料的来源类别 | 生成最终来源配置 |
| 确认中文资料表 | 创建 MCP 项目并安装依赖 |
| 在新 task 中提出问题 | 建索引、测试并注册到 Codex |

Skill 包含经过测试的项目模板及两个自动化脚本：

```bash
python ~/.codex/skills/storm-knowledge-crafter/scripts/create_source_manifest.py \
  "/path/to/materials" --output "/tmp/inventory.json"

python ~/.codex/skills/storm-knowledge-crafter/scripts/scaffold_course_mcp.py \
  "/path/to/project"
```

来源表确认后，自动生成 `config/sources.yaml` 并执行索引构建、检索验证和 MCP 注册：

```bash
codex mcp add my_knowledge_base -- \
  /path/to/project/.venv/bin/course-material-mcp serve \
  --config /path/to/project/config/sources.yaml \
  --index /path/to/project/data/index/materials.sqlite
```

### 4. 输出惯例

所有基于资料的回答遵循以下标记：
- `[资料事实]` — 来自原始资料的确切引用
- `[模型推理]` — 基于资料的解释或推导
- `[资料不足]` — 当前资料无法支持该结论

### 5. 迭代使用

- 首次跑完10步 → 获得主题全景图 + 行动路线
- 需要复习 → 重跑第8步（测试）+ 第10步（速查表）
- 深入研究 → 重跑第1-4步（五视角更换专家阵容）
- 有新资料 → 更新 MCP 索引后，重跑第8-10步

---

## English Guide

### 1. Overview

**Storm Knowledge Crafter** is a full-cycle knowledge creation agent that combines:

- **Evidence Engine**: Convert local PDF/TXT/Markdown files into a searchable, read-only MCP knowledge base with citable evidence.
- **10-Step Learning Method**: A complete learning loop integrating Stanford's STORM multi-perspective research method with an advanced learning methodology.

### 2. Quick Start

With materials:
```text
Use $storm-knowledge-crafter to systematically learn 【your-topic】.

Materials directory: /path/to/materials
MCP name: my_knowledge_base

Policy: textbook.pdf is textbook (highest priority), slides.pdf are slides (second), notes.md is supplementary (third).
```

Without materials:
```text
Use $storm-knowledge-crafter to learn 【your-topic】. I have no local files, please start from Step 1 (multi-perspective analysis).
```
