---
name: dpdata-driver
version: 1.0
description: >
  Use dpdata Python Driver plugins to label systems (energies/forces/virials) via System.predict(), list available drivers, and build Driver objects (ase/deepmd/gaussian/sqm/hybrid).
  Use when working with dpdata Python API (not CLI) and you need driver-based energy/force prediction, plugin registration keys, or examples of using dpdata with ASE calculators or DeePMD models.
requirements: |
  - dpdata Python package (`pip install dpdata`) or `uv` for running dpdata
  - Python 3.8+
source: |
  Author: njzjz-bot
  Original OpenClaw skill from: jinzhezenggroup/computational-chemistry-agent-skills
license: LGPL-3.0-or-later
---

# dpdata-driver — dpdata Python Driver 插件

通过 dpdata "driver 插件" **标注（label）** `dpdata.System`（预测能量/力/维里量），获得 `dpdata.LabeledSystem`。

## 核心概念

- **Driver** 将未标注的 `System` 转换为 `LabeledSystem`，计算：
  - `energies`（必需）
  - `forces`（可选但常见）
  - `virials`（可选）

在 dpdata 中，接口为：

```python
System.predict(*args, driver="dp", **kwargs) -> LabeledSystem
```

`driver` 可以是：
- **字符串键**（插件名称），如 `"ase"`, `"dp"`, `"gaussian"`
- **Driver 对象**，如 `Driver.get_driver("ase")(...)`

## 运行时查询支持的驱动键

在不确定当前 dpdata 版本/环境中存在哪些驱动时，通过运行时查询：

```python
import dpdata
from dpdata.driver import Driver

print(sorted(Driver.get_drivers().keys()))
```

`import dpdata` 确保内置插件在列出已注册驱动前完成加载。

在当前仓库中，支持的键包括：

| 驱动键 | 对应后端 |
|--------|---------|
| `ase` | ASE 计算器 |
| `dp` / `deepmd` / `deepmd-kit` | DeePMD-kit 模型 |
| `gaussian` | Gaussian 软件 |
| `sqm` | AmberTools SQM |
| `hybrid` | 多驱动混合 |

> 确切集合取决于 dpdata 版本和安装的 extras。

## 参数定义

### Driver 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `driver` | 字符串 / Driver 对象 | 是 | 驱动类型或实例，如 `"gaussian"`, `"ase"`, `Driver` 对象 |
| `calculator` | ASE 计算器 | 取决于驱动 | ASE 计算器实例（用于 ASE 驱动） |
| `dp_model` | 字符串 | 取决于驱动 | DeePMD 模型文件路径（用于 DP 驱动） |
| `executable` | 字符串 | 否 | 外部可执行文件路径（如 Gaussian 默认为 `g16`） |

### System 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `input_path` | 字符串 | 输入结构文件路径 |
| `input_format` | 字符串 | 输入格式（如 `"xyz"`, `"cif"`, `"vasp/poscar"`） |

## 最小工作流

```python
import dpdata
from dpdata.system import System

sys = System("input.xyz", fmt="xyz")
ls = sys.predict(driver="ase", calculator=...)  # 返回 dpdata.LabeledSystem
```

### 验证标注结果

```python
assert "energies" in ls.data
# 可选验证：
# assert "forces" in ls.data
# assert "virials" in ls.data
```

## 示例：使用 ASE 驱动 + ASE 计算器（可直接运行）

这是最简单的**完全可运行**示例，无需外部 QM 软件。

依赖声明（推荐使用 uv 内联脚本元数据）：

```python
# /// script
# requires-python = ">=3.8"
# dependencies = [
#   "dpdata",
#   "numpy",
#   "ase",
# ]
# ///
```

完整脚本：

```python
from pathlib import Path

import numpy as np
from ase.calculators.lj import LennardJones
from dpdata.system import System

# 写入一个小分子
Path("tmp.xyz").write_text("""2\n\nH 0 0 0\nH 0 0 0.74\n""")

sys = System("tmp.xyz", fmt="xyz")
ls = sys.predict(driver="ase", calculator=LennardJones())

print("energies", np.array(ls.data["energies"]))
print("forces shape", np.array(ls.data["forces"]).shape)
if "virials" in ls.data:
    print("virials shape", np.array(ls.data["virials"]).shape)
else:
    print("virials: <not provided by this driver/calculator>")
```

## 示例：传递 Driver 对象（而非字符串）

```python
from ase.calculators.lj import LennardJones
from dpdata.driver import Driver
from dpdata.system import System

sys = System("tmp.xyz", fmt="xyz")
ase_driver = Driver.get_driver("ase")(calculator=LennardJones())
ls = sys.predict(driver=ase_driver)
```

## 混合驱动（Hybrid Driver）

使用 `driver="hybrid"` 累加多个驱动的能量/力/维里量。

`HybridDriver` 接收 `drivers=[ ... ]`，每项为：
- `Driver` 实例
- 字典如 `{"type": "sqm", ...}`（type 为驱动键）

示例（结构仅作示意，可能需要外部可执行文件）：

```python
from dpdata.driver import Driver

hyb = Driver.get_driver("hybrid")(
    drivers=[
        {"type": "sqm", "qm_theory": "DFTB3"},
        {"type": "dp", "dp": "frozen_model.pb"},
    ]
)
# ls = sys.predict(driver=hyb)
```

## 注意事项

- 许多驱动需要额外依赖或外部程序：
  - `dp` 需要 `deepmd-kit` + 模型文件
  - `gaussian` 需要 Gaussian 软件及有效的可执行文件（默认 `g16`）
  - `sqm` 需要 AmberTools `sqm`
- 若只需文件格式转换，请使用 **dpdata CLI** skill 代替。
- Gaussian 驱动操作步骤：
  1. 先使用 `gjf-flux` skill 组装 `.gjf` 输入文件
  2. 再使用 `run-gauss` skill 指导执行
  3. 最后通过 `dpdata-driver` 读取结果并标注数据
