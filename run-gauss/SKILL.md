---
name: run-gauss
version: 0.1.0
description: >
  Acts as a knowledge base providing environment checklists, directory/scratch management, and bash command templates.
  Use when you need to guide the execution of Gaussian computational chemistry jobs (.gjf) on local or remote/HPC environments.
source: |
  Author: light-cyan
  Original OpenClaw skill from: jinzhezenggroup/computational-chemistry-agent-skills
requirements: |
  - 本地运行：Gaussian 可执行文件（g16/g09）已安装并在 PATH 中
  - 远程/HPC 运行：需要 SSH 访问权限和调度器环境（Slurm/PBS/LSF/Bohrium）
  - 推荐配合 gjf-flux 使用（生成 .gjf 输入文件）
  - 推荐配合 dpdisp-submit 使用（作业调度提交）
license: LGPL-3.0-or-later
---

# run-gauss — Gaussian 计算执行知识库

提供在本地或远程/HPC 环境中运行 Gaussian 计算化学作业（`.gjf`）所需的环境检查、目录/scratch 管理和 bash 命令模板。

## 前置参数（需向用户确认）

执行前需收集以下参数：

### 运行环境

| 参数 | 类型 | 说明 |
|------|------|------|
| `run_location` | 枚举: `local` / `remote` / `hpc` | 运行位置，远程时需指定调度器类型（Slurm/PBS/LSF/Bohrium） |
| `env_setup_cmds` | 字符串 | 环境设置命令（如 `module load gaussian` / `source script`） |
| `gaussian_exec` | 字符串 | Gaussian 可执行文件（如 `g16`、`g09` 或绝对路径） |

### 目录配置

| 参数 | 类型 | 说明 |
|------|------|------|
| `local_work_dir` | 字符串 | 本地工作目录（远程时需指定） |
| `remote_work_dir` | 字符串 | 远程运行目录（远程时需指定） |
| `task_work_dir` | 字符串 | 单任务工作目录 |

### 文件配置

| 参数 | 类型 | 说明 |
|------|------|------|
| `input_gjf` | 路径 | Gaussian 输入文件 `.gjf` |
| `gaussian_log` | 路径 | Gaussian 输出日志文件 |
| `stdout_log` | 路径 | （可选）包装器标准输出日志 |
| `stderr_log` | 路径 | （可选）包装器标准错误日志 |

### Scratch 配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `scratch_dir` | 字符串 | `./scratch` | `GAUSS_SCRDIR` 目录，可使用 `$TMPDIR` 或站点提供的 scratch |
| `clean_scratch` | 布尔 | `true` | 运行完成后是否清理 scratch 目录 |

## 执行命令模板

```bash
<env_setup_cmds>
export GAUSS_SCRDIR=<scratch_dir>
mkdir -p "$GAUSS_SCRDIR"

<gaussian_exec> < <input.gjf> > <gaussian_log>

rm -rf "$GAUSS_SCRDIR"
```

## 输入文件生成

若尚无 `.gjf` 文件，使用 **gjf-flux** skill 来提取/组装 `.gjf` 段落和构建工作流。

```bash
# 查看 gjf-flux 技能详情
uvx gjf-flux --help
```

## 作业提交（推荐）

推荐使用 **dpdispatcher** 提交 Gaussian 计算到各类调度器。

通过 `dpdisp-submit` skill 提交作业，支持 Shell、Slurm、PBS、LSF、Bohrium 等多种后端。

## 参数传递总结

| 输入参数 | 在模板中的占位符 |
|----------|-----------------|
| 环境设置 | `<env_setup_cmds>` |
| Scratch 目录 | `<scratch_dir>` |
| Gaussian 可执行文件 | `<gaussian_exec>` |
| 输入文件 | `<input.gjf>` |
| 输出日志 | `<gaussian_log>` |

> 所有占位符需要在运行前替换为实际值。推荐在远程/HPC 环境下将模板写入作业脚本文件后通过调度器提交。
