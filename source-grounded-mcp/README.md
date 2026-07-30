# Source-Grounded MCP Skill

## 中文说明

### 1. 功能概述

本 Skill 用于根据用户指定的本地资料目录，创建、验证并注册一个可运行的课程资料 MCP。配置完成后，Codex 可通过该 MCP 检索经审核的资料，并返回包含 PDF 页码或文件行号的可追溯证据。

支持的输入格式包括可提取文字的 PDF、TXT 和 Markdown。扫描版 PDF 应先完成 OCR 并核验文本质量。当前版本暂不支持 Excel、数据库、Word 和 PowerPoint 原文件。

用户无需手工编辑 JSON 或 YAML。用户职责与自动化流程如下：

| 用户职责 | 自动化流程 |
| --- | --- |
| 准备资料文件夹 | 扫描文件并检查格式 |
| 定义课件、教材与补充资料的来源类别 | 生成最终来源配置 |
| 审核 Codex 展示的中文资料表 | 创建 MCP 项目并安装依赖 |
| 在新 task 中提出课程问题 | 建索引、测试并注册到 Codex |

### 2. 安装 Skill

将 GitHub 仓库克隆到 Codex 的个人 Skill 目录：

```bash
git clone https://github.com/Zhang-Hengjia/source-grounded-mcp.git ~/.codex/skills/source-grounded-mcp
```

安装完成后，重启 Codex Desktop 或新建 task，以重新加载该 Skill。

### 3. 准备资料

将所有允许引用的资料放在一个专用文件夹中，例如：

```text
/Users/alice/Documents/my-course-materials/
├── 教师课件.pdf
├── 课程指定教材.pdf
└── 补充讲义.md
```

目录与文件名称不受限制，适用课程不限于化学动力学，也不要求采用特定的子目录结构。

### 4. 创建课程资料 MCP

在新的 Codex task 中提交以下请求：

```text
使用 $source-grounded-mcp 为我创建一个本地课程资料 MCP，并完成到注册进 Codex。

资料目录：/Users/alice/Documents/my-course-materials
项目目录：/Users/alice/Documents/my-course-mcp
MCP 名称：my_course

资料规则：
- “教师课件.pdf”是教师课件，优先级最高；
- “课程指定教材.pdf”是主教材，优先级第二；
- “补充讲义.md”是补充资料，优先级第三。

不要让我手工编辑 JSON 或 YAML。如果有文件无法判断，请先用中文表格让我确认。
```

若来源分类尚未确定，可仅提供资料目录、项目目录和 MCP 名称，并增加以下要求：

```text
请先扫描资料，用“文件名、显示标题、资料类别、优先级、是否可提取文字”五列表格让我确认。
```

Codex 将自然语言来源规则转换为内部配置。默认分级建议如下：

| 资料类别 | 内部类型 | 优先级 |
| --- | --- | ---: |
| 教师课件 | `teacher_slides` | 3 |
| 课程指定主教材 | `textbook` | 2 |
| 补充教材、讲义或参考资料 | `supplementary_textbook` | 1 |

上述分级仅为默认建议，最终来源权威顺序由用户确认。

### 5. 自动化执行流程

Skill 包含经过测试的项目模板及两个自动化脚本。Codex 将执行：

```bash
python ~/.codex/skills/source-grounded-mcp/scripts/create_source_manifest.py \
  "/Users/alice/Documents/my-course-materials" \
  --output "/tmp/my-course-inventory.json"

python ~/.codex/skills/source-grounded-mcp/scripts/scaffold_course_mcp.py \
  "/Users/alice/Documents/my-course-mcp"
```

来源表确认后，Codex 将生成 `config/sources.yaml`，并执行：

```bash
cd "/Users/alice/Documents/my-course-mcp"
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -v
.venv/bin/course-material-mcp ingest
.venv/bin/course-material-mcp search "测试关键词"
```

项目测试和检索验证通过后，执行以下注册命令：

```bash
codex mcp add my_course -- \
  /Users/alice/Documents/my-course-mcp/.venv/bin/course-material-mcp \
  serve \
  --config /Users/alice/Documents/my-course-mcp/config/sources.yaml \
  --index /Users/alice/Documents/my-course-mcp/data/index/materials.sqlite

codex mcp get my_course
```

以上命令由 Codex 自动执行，此处列出仅用于说明项目生成、验证和注册过程。

### 6. 查询与回答

注册成功后，重启 Codex Desktop 或新建 task，并提交以下查询请求：

```text
请调用 my_course 查询指定课程资料，解释“某个课程概念”。

要求：
1. 资料中的内容标为 [资料事实]，并给出资料标题和精确页码或文件行号；
2. 你补充的解释标为 [模型推理]；
3. 如果资料没有直接依据，明确标为 [资料不足]。
```

MCP 提供三个只读工具：`search_sources`、`read_source_segment`、`list_source_policy`。该 MCP 不访问来源清单之外的文件，也不修改来源资料。

### 7. 来源更新

将新增文件置于资料目录后，提交以下更新请求：

```text
使用 $source-grounded-mcp 更新 my_course。请扫描新增资料，先让我确认分类和优先级，然后更新配置、重新建索引并验证检索。
```

已登记文件的删除或重命名必须同时更新来源配置并重建索引，避免配置路径与实际文件不一致。

### 8. 重要限制

- 来源优先级只是权威顺序，不代表高优先级文件中的任何片段都能支持答案。
- 当前检索实现为可复现的关键词基线，不等同于语义检索。引入向量检索或 reranker 前，应基于标注测试集评估其实际增益。
- MCP 负责返回证据，但宿主模型仍可能产生错误解释。因此，回答应保留 `[资料事实]`、`[模型推理]`、`[资料不足]` 三类标记。

---

## English Guide

### 1. Overview

This Skill provides a reproducible workflow for creating and registering a local course-material MCP from an approved directory. Once configured, Codex can search the registered sources and return evidence with an exact PDF-page or file-line locator.

Supported inputs are selectable-text PDF, TXT, and Markdown files. Scanned PDFs require OCR. Excel, databases, Word files, and original PowerPoint files are not currently supported.

Manual editing of JSON or YAML is not required.

| User responsibility | Codex responsibility |
| --- | --- |
| Prepare the materials folder | Scan files and check supported formats |
| State which files are slides, textbooks, or supplementary sources | Generate the final source configuration |
| Confirm a plain-language source table | Create, install, test, index, and register the MCP |
| Ask course questions in a new task | Retrieve citable evidence through the MCP |

### 2. Install the Skill

Clone the GitHub repository into the personal Codex Skill directory:

```bash
git clone https://github.com/Zhang-Hengjia/source-grounded-mcp.git ~/.codex/skills/source-grounded-mcp
```

Restart Codex Desktop or open a new task.

### 3. Source preparation

Place approved materials in one dedicated folder. Folder and file names are unrestricted, and the course does not have to be chemical kinetics.

```text
/Users/alice/Documents/my-course-materials/
├── instructor-slides.pdf
├── required-textbook.pdf
└── supplementary-notes.md
```

### 4. Create a course-material MCP

Send this in a new task:

```text
Use $source-grounded-mcp to create a local course-material MCP and complete the setup through Codex registration.

Materials directory: /Users/alice/Documents/my-course-materials
Project directory: /Users/alice/Documents/my-course-mcp
MCP name: my_course

Source policy:
- instructor-slides.pdf is the highest-priority instructor source;
- required-textbook.pdf is the second-priority required textbook;
- supplementary-notes.md is the third-priority supplementary source.

Do not ask me to edit JSON or YAML. If any classification is ambiguous, show a plain-language confirmation table first.
```

If the user does not know the classification, ask Codex to show a five-column table: file, displayed title, source category, priority, and text-extraction status.

The proposed defaults are:

| Source category | Internal type | Priority |
| --- | --- | ---: |
| Instructor slides | `teacher_slides` | 3 |
| Required textbook | `textbook` | 2 |
| Supplementary material | `supplementary_textbook` | 1 |

The user confirms the final authority order.

### 5. Automated execution

The Skill bundles a tested project starter and two real scripts:

```bash
python ~/.codex/skills/source-grounded-mcp/scripts/create_source_manifest.py \
  "/Users/alice/Documents/my-course-materials" \
  --output "/tmp/my-course-inventory.json"

python ~/.codex/skills/source-grounded-mcp/scripts/scaffold_course_mcp.py \
  "/Users/alice/Documents/my-course-mcp"
```

After source confirmation, Codex writes `config/sources.yaml` and runs:

```bash
cd "/Users/alice/Documents/my-course-mcp"
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -v
.venv/bin/course-material-mcp ingest
.venv/bin/course-material-mcp search "representative query"
```

Codex then registers the server:

```bash
codex mcp add my_course -- \
  /Users/alice/Documents/my-course-mcp/.venv/bin/course-material-mcp \
  serve \
  --config /Users/alice/Documents/my-course-mcp/config/sources.yaml \
  --index /Users/alice/Documents/my-course-mcp/data/index/materials.sqlite

codex mcp get my_course
```

These commands are shown for transparency; Codex executes them during the workflow.

### 6. Query workflow

After registration, restart Codex Desktop or open a new task and submit the following query:

```text
Please call my_course and explain a course concept from the approved materials.

Requirements:
1. Label source-backed content as [资料事实] and cite the title plus exact page or line locator.
2. Label your added explanation as [模型推理].
3. If the materials provide no direct evidence, label that as [资料不足].
```

The MCP exposes only `search_sources`, `read_source_segment`, and `list_source_policy`. It cannot read files outside the reviewed policy or modify source documents.

### 7. Update sources

After adding a file to the materials directory, submit the following update request:

```text
Use $source-grounded-mcp to update my_course. Scan the new materials, let me confirm their category and priority, then update the configuration, rebuild the index, and verify retrieval.
```

### 8. Limitations

- Priority expresses source authority; it does not make every high-priority passage relevant.
- Retrieval is currently a deterministic lexical baseline, not semantic search. Add embeddings or a reranker only after evaluating a labelled question set.
- The MCP returns evidence, but the host model can still make interpretation errors. Preserve the `[资料事实]`, `[模型推理]`, and `[资料不足]` labels.
