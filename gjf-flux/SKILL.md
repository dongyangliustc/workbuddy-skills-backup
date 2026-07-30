---
name: gjf-flux
version: 0.1.0
description: >
  Assemble and extract Gaussian .gjf input file sections (directives, route, title, molecule blocks, appendices) and build single- or multi-step Link1 jobs from modular component files.
  Use when needed for generating, refactoring, templating, or scripting Gaussian job files.
requirements: |
  - `uv` installed and available in PATH
  - Python 3.8+ (managed by uv at runtime)
  - Optional — 分子结构生成（若需从 SMILES 或化学式自动构建初始几何）：
    - `pip install rdkit ase` （RDKit: SMILES→3D 构象生成；ASE: 内置~80种常见分子的坐标库）
  - 分子坐标也可通过第一性原理手动构造（VSEPR + 标准键长），无需任何额外依赖
source: |
  Author: light-cyan
  Repository: https://github.com/light-cyan/gjf-flux
  Original OpenClaw skill from: jinzhezenggroup/computational-chemistry-agent-skills
license: LGPL-3.0-or-later
---

# gjf-flux — Gaussian Job File Assembly & Extraction

`gjf-flux` 是一个命令行工作流工具，用于**模块化组装和提取 Gaussian `.gjf` 文件**：

- **提取**（Extract）：从现有 `.gjf` 中提取特定段落（包括 Link1 多步作业）。
- **组装**（Assemble）：将 directives/route/molecule/appendix 模块组装为完整的 `.gjf`，或将多个任务合并为 Link1 作业。

## 适用范围

在以下场景中使用此技能：

- 在多个计算中复用 Gaussian 输入文件的组成部分（如路由行、分子块、基组/约束附录）。
- 从较小的文件（片段、模板、参数化 directives）程序化地构建 `.gjf`。
- 通过提取特定段落来检查/比较 `.gjf` 文件。

## 解析模型（重要）

`gjf-flux` 假定**标准 Gaussian 输入布局**：

- Link1 步骤之间以空行 `--Link1--` 换行分隔。
- 每个 Link1 步骤内，块之间以空行分隔。
- **路由段（route section）** 从第一个以 `#` 开头的行开始，延续至后续行。
- **分子块（molecule block）** 检测逻辑：当某块的第一行形如成对整数（如 `0 1` 或 `0 1 0 1 0 1`），代表电荷/多重度对。

> ⚠️ 若 `.gjf` 偏离上述约定，提取可能失败或误分类块。

## 参数定义

### 1. 目标操作（必选）

| 参数 | 类型 | 说明 |
|------|------|------|
| `action` | 枚举: `extract` / `assemble` | 提取或组装操作 |

### 2. 文件路径

| 参数 | 类型 | 说明 |
|------|------|------|
| `input_file` | 字符串 | 现有 `.gjf` 文件路径（提取时） |
| `component_files` | 字符串数组 | 待组装的组件文件路径（组装时） |

### 3. Link1 多步作业

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `job_index` | 整数 | `0` | 选择 Link1 步骤（0-based） |

### 4. 分子块参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `charge` | 整数 | 总电荷（组装分子块时） |
| `multi` | 整数 | 自旋多重度（组装分子块时） |
| `as_fragment` | 布尔 | 是否为片段模式，分配 `Fragment=1,2,...` 标签 |

## 核心命令

### 1) 从 `.gjf` 提取段落

```bash
uvx gjf-flux extract <section_name> <FILE.gjf> [--job_index N]
```

`<section_name>` 可选值：
- `directives`
- `route`
- `title`
- `molecule` 或 `molecule-<idx>`
- `appendix` 或 `appendix-<idx>`

> `<idx>` 为 **0-based**。`--job_index` 选择 Link1 步骤（0-based，默认 `0`）。

示例：
```bash
# 提取第一个 Link1 步骤的路由行
uvx gjf-flux extract route input.gjf

# 提取步骤0的第二个分子块
uvx gjf-flux extract molecule-1 input.gjf

# 提取 Link1 步骤2的第一个附录块
uvx gjf-flux extract appendix-0 input.gjf --job_index 2
```

### 2) 组装 directives（Link0 命令）

```bash
uvx gjf-flux assemble directives --chk FILE --mem SIZE --nprocshared N
```

支持 `--key value` 键值对形式。

示例：
```bash
uvx gjf-flux assemble directives --chk job.chk --mem 16GB --nprocshared 16

# 重定向到文件供后续组合
uvx gjf-flux assemble directives --chk job.chk --mem 16GB --nprocshared 16 > directives.txt
```

### 3) 组装路由段（`#` 行）

```bash
uvx gjf-flux assemble route [-l p|n|t|""] <keywords...>
```

示例：
```bash
# #p Opt B3LYP/6-31G(d)
uvx gjf-flux assemble route -l p Opt B3LYP/6-31G(d)

# 带括号的关键字使用引号
uvx gjf-flux assemble route -l p "Opt(MaxCycle=100)" "Freq"

# 保存到文件
uvx gjf-flux assemble route -l p "Opt(MaxCycle=100)" "Freq" > route.txt
```

### 4) 合并分子片段为一个分子块

```bash
uvx gjf-flux assemble molecules <frag1.txt> <frag2.txt> ... [--as-fragment] [--charge INT] [--multi INT]
```

每个片段文件格式：
- 第1行：`charge multiplicity`（如 `0 1`）
- 后续行：原子坐标（Gaussian 格式）

模式：
- **默认**：合并为**单个**分子块。
- `--as-fragment`：分配 `Fragment=1,2,...` 标签并展开电荷/多重度头。

示例：
```bash
# 合并两个片段为单个分子块
uvx gjf-flux assemble molecules fragA.txt fragB.txt > molecule.txt

# 片段模式，覆盖总电荷/多重度
uvx gjf-flux assemble molecules fragA.txt fragB.txt --as-fragment --charge 0 --multi 1 > molecule.txt
```

### 5) 组装附录

```bash
uvx gjf-flux assemble appendices <app1.txt> <app2.txt> ...
```

示例：
```bash
uvx gjf-flux assemble appendices basis.txt modredundant.txt > appendix.txt
```

### 6) 组装完整单步 `.gjf`

```bash
uvx gjf-flux assemble job \
    --directives directives.txt \
    --route route.txt \
    --title "Your title" \
    --molecule molecule.txt [molecule2.txt ...] \
    [--appendices appendix.txt ...]
```

### 7) 合并多个任务为 Link1 多步作业

```bash
uvx gjf-flux assemble tasks step1.gjf step2.gjf [step3.gjf ...] > link1.gjf
```

## 端到端示例（带命令替换的一行命令）

以下示例展示从各组件一步构建完整单步作业：

> 注：使用 bash/zsh 进程替换 `<( ... )`。若不支持，先将各块重定向到文件。

```bash
# 1) 构建 directives 到文件
uvx gjf-flux assemble directives --chk job.chk --mem 16GB --nprocshared 16 > directives.txt

# 2) 使用内联路由/分子/附录块组装完整 .gjf
uvx gjf-flux assemble job \
    --directives directives.txt \
    --route <(uvx gjf-flux assemble route -l p "Opt(MaxCycle=100)" "Freq" B3LYP/6-31G(d)) \
    --title "Opt+Freq from extracted building blocks" \
    --molecule <( \
        gjf-flux assemble molecules \
        <(uvx gjf-flux extract molecule-0 reactant.gjf) \
        fragment_extra.xyz \
        --multi 1 \
    ) \
    --appendices \
    <(uvx gjf-flux extract appendix-1 reactant.gjf) \
    <(uvx gjf-flux extract appendix-0 reference.gjf) \
    app_manual.txt \
    > job.gjf
```

变体：
- 仅复用提取的分子块（不合并）：`--molecule <(uvx gjf-flux extract molecule-0 input.gjf)`
- 组装 Link1 工作流：先构建各步 `.gjf`，然后 `uvx gjf-flux assemble tasks step1.gjf step2.gjf > link1.gjf`

## 推荐工作流

1. 创建/派生组件块：
   - `directives.txt`（`assemble directives` 或手动）
   - `route.txt`（`assemble route`）
   - `molecule.txt`（`assemble molecules` 或从现有 `.gjf` 提取）
   - `appendix.txt`（可选）
2. 通过 `assemble job` 组装完整作业。
3. 若有多个步骤，每步构建一个 `.gjf`，然后用 `assemble tasks` 合并。
4. 从最终输出中提取关键段落进行验证。

## 常见陷阱

- **索引错误**：`job_index`、`molecule-<idx>` 和 `appendix-<idx>` 均为 **0-based**。
- **非标准 `.gjf` 格式**：异常的空行结构可能导致解析失败。
- **片段文件必须以 `charge multiplicity` 开头**：否则分子合并会失败。
- **坐标格式必须为 .6f 小数**：`C 0 0 0`（整数）或不定长小数均会导致 Gaussian 解析失败。必须使用 `C     0.000000    0.000000    0.000000` 格式，且**必须经过 `assemble molecules` 步骤格式化**。
- **关键字引号**：带括号的路由关键字应在 shell 中加引号。

## Agent 使用指引

- 若解析失败，优先请用户提供具体的 `.gjf` 示例。
- 组装时保持各组件文件小巧且目的专一，便于调试。
- 若用户需要可重复的流水线，建议将可复用组件（路由模板、基组附录、片段库）纳入版本控制。

## 分子坐标获取方式（前置步骤）

`assemble job` 所需的分子坐标可通过以下方式获取，按优先顺序排列：

| 方式 | 依赖 | 说明 |
|------|------|------|
| **ASE 内置分子库** | `pip install ase` | 一行代码：`ase.build.molecule("CH4")`。覆盖 ~80 种常见分子（CH₄, C₆H₆, H₂O, NH₃ 等），零手动输入 |
| **RDKit SMILES→3D** | `pip install rdkit` | 最通用方案：给定 SMILES（如 `"C"` 为甲烷），自动加氢 → ETKDGv3 嵌入 3D 坐标 → MMFF94s 力场优化，适用于任意有机分子 |
| **第一性原理手动构造** | 无（仅需 Python `math` 标准库） | 根据 VSEPR 理论、杂化轨道和标准键长直接计算坐标。例如甲烷：AX₄ 正四面体，sp³ C–H = 1.09 Å，键角 109.47°。无需任何额外依赖 |

> ⚠️ 分子坐标格式约束（重要）：
> - 所有坐标值**必须使用 6 位小数格式**（`.6f`），例如 `C     0.000000    0.000000    0.000000`
> - 整数格式（如 `C 0 0 0`）或不定长小数格式均会导致 Gaussian 解析失败
> - 生成坐标片段后，**必须经过 `assemble molecules` 步骤**再传给 `assemble job`，不可跳过
> - ASE 和 RDKit 为可选依赖，仅在需要自动生成坐标时安装。若已有现有分子结构文件（如 .xyz, .cif, .pdb），可直接作为片段输入，无需上述工具。

## 下一步工作流

生成 `.gjf` 文件后，完整的 Gaussian 计算工作流衔接如下：

1. **gjf-flux**（当前技能）→ 组装 `.gjf` 输入文件
2. **run-gauss** → 提供环境检查清单、目录/scratch 管理和 bash 命令模板，指导 `.gjf` 在本地或 HPC 环境中的执行
3. **dpdata-driver** → 通过 dpdata Python Driver 插件读取 Gaussian 输出，标注能量/力/维里量

请参阅对应技能获取详细指引。
