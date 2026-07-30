---
name: run-rest
version: 0.1.0
description: >
  Bridge Gaussian geometry optimization output to REST R-xDH7/def2-QZVPP
  high-precision single-point energy calculation. Extract optimized geometry
  from Gaussian .log/.out, assemble REST TOML input, execute calculation,
  and extract final energies.
  This skill should be used when a Gaussian optimization job is complete and
  the user needs to perform a post-optimization high-level single-point energy
  calculation using REST (Rust-based Electronic Structure Toolkit), particularly
  with the R-xDH7 renormalized double-hybrid method.
agent_created: true
requirements: |
  - REST executable (Rust-based Electronic Structure Toolkit) installed
  - REST basis-set-pool accessible (def2-QZVPP + JKFIT)
  - Gaussian .log/.out output from a completed optimization
  - Python 3.8+ (standard library only, no extra dependencies)
source: |
  Custom skill for Gaussian -> REST workflow.
  R-xDH7 method developed by Ying Zhang group, Fudan University.
references: |
  - REST doc: https://rest-doc.readthedocs.io/
  - R-xDH7 paper: PMC11350721
license: LGPL-3.0-or-later
---

# run-REST -- Gaussian 优化后 REST 高精度单点能计算

在 Gaussian 优化完成后，衔接进行 REST 软件的 R-xDH7/def2-QZVPP 高精度单点能计算。

## 工作流总览

```
Gaussian(.log) -> [Step 1] 提取几何 -> [Step 2] 组装 TOML -> [Step 3] 执行 REST -> [Step 4] 提取能量
                                                                    ^
                                                              (由 run-gauss 知识库协助环境管理)
```

整体流程分为 4 步，由 `scripts/run_rest_sp.py` 端到端自动完成。

---

## 工作流详解

### Step 0: 前置检查

执行前确认以下条件：

1. **Gaussian 输出文件** (.log/.out) -- 优化任务已完成，文件存在且包含正常结束标识
2. **REST 可执行文件** -- `which rest` 或指定路径可访问
3. **REST 基组池** -- `def2-QZVPP` 和 `def2-QZVPP-JKFIT` 目录存在于基组池中
4. **Python 环境** -- Python 3.8+（标准库即可，无额外依赖）

### Step 1: 从 Gaussian 提取优化几何

提供两种模式：

#### 模式 A（默认）：仅提取结构

从 Gaussian 输出文件解析末态 `Standard orientation` 坐标，输出 XYZ 格式。

```bash
python scripts/extract_geom_from_log.py <gaussian.log> --output geom.xyz
```

#### 模式 B（预留）：波函数传递

!! 此模式由外部的 **mokit 自动化智能体** 处理，当前尚未创建。

- 场景：需要将 Gaussian 的波函数信息传递给 REST（如 `initial_guess = "read"`）
- 智能体接口已预留，但功能未实现
- 当用户尝试传入 `.chk` 文件时，当前会提示后回退到模式 A

### Step 2: 组装 REST TOML 输入

将提取的 XYZ 坐标与计算参数组装为 REST 的 TOML 输入文件。

```bash
python scripts/build_rest_input.py params.json --output input.toml
```

`params.json` 示例：

```json
{
    "method": "R-xDH7",
    "basis_path": "/opt/rest_workspace/rest/basis-set-pool/def2-QZVPP",
    "auxbas_path": "/opt/rest_workspace/rest/basis-set-pool/def2-QZVPP-JKFIT",
    "charge": 0,
    "spin": 1,
    "num_threads": 16,
    "frozen_core": true,
    "geom_xyz": "... (XYZ 坐标内容) ..."
}
```

### Step 3: 执行 REST 计算

```bash
rest < input.toml
```

- REST 输出写入标准输出，建议重定向到日志文件
- 工作目录管理可参考 `run-gauss` 技能的经验
- R-xDH7 为 post-HF 双杂化方法，计算量较大，建议多线程运行

### Step 4: 提取能量结果

从 REST 输出文件中解析 R-xDH7 总能量及各组分。

```bash
python scripts/extract_rest_energy.py rest_output.log
```

输出示例：

```
================================================
  REST R-xDH7 单点能计算结果
================================================
  R-xDH7 总能量 (E_total)       =  -XXX.XXXXXXXX  a.u.
================================================
```

使用 `--json` 选项可输出结构化 JSON（便于下游处理）：

```bash
python scripts/extract_rest_energy.py rest_output.log --json
```

---

## 端到端执行

推荐使用整合脚本一键完成所有步骤：

```bash
python scripts/run_rest_sp.py <gaussian.log> \
    --charge 0 --spin 1 \
    --basis-pool /opt/rest_workspace/rest/basis-set-pool \
    --rest-exec /opt/rest_workspace/rest/target/release/rest \
    [--method R-xDH7] \
    [--basis def2-QZVPP] \
    [--output-dir ./rest_sp] \
    [--num-threads 16]
```

脚本自动执行：环境检查 -> 几何提取 -> 输入生成 -> REST 执行 -> 能量解析 -> 结果报告。

---

## REST 执行的环境管理

REST 的执行环境管理（路径设置、模块加载、并行环境等）可参考 `run-gauss` 技能中关于本地/HPC 环境管理的经验：

- **本地运行**：确保 REST 可执行文件在 PATH 中，或直接使用绝对路径
- **远程/HPC 运行**：需要 SSH 访问和调度器（Slurm/PBS 等），可参考 run-gauss 的远程执行模板
- **线程设置**：R-xDH7 是多线程计算，`num_threads` 建议设为物理核心数的 50%-75%

## 参考资源

| 文件 | 内容 |
|------|------|
| `references/rest_input_spec.md` | REST TOML 输入格式完整规范 |
| `references/method_templates.md` | R-xDH7/XYG7/XYG3 等方法模板及基组推荐 |
| `references/gaussian_rest_bridge.md` | Gaussian <-> REST 桥接说明和文件格式对照 |

## 脚本资源

| 脚本 | 功能 |
|------|------|
| `scripts/extract_geom_from_log.py` | 从 Gaussian .log 提取优化几何（XYZ 格式） |
| `scripts/build_rest_input.py` | 组装 REST TOML 输入文件 |
| `scripts/extract_rest_energy.py` | 从 REST 输出解析能量各组分 |
| `scripts/run_rest_sp.py` | 端到端整合脚本 |

## 已知限制

- **mokit 波函数传递智能体尚未创建**：当前只支持从 .log 提取几何结构（模式 A）；波函数传递（模式 B）为预留接口，功能未实现
- **基组依赖**：REST 需要预装基组文件和辅助基组文件，非自动下载
- **REST 版本要求**：需支持 R-xDH7 方法的 REST 版本（建议 ≥ 0.1.0）
