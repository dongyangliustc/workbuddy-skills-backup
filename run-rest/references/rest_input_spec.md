# REST TOML 输入格式参考

REST（Rust-based Electronic Structure Toolkit）使用 TOML 格式的输入文件。
输入文件必须包含且仅包含 `[ctrl]` 和 `[geom]` 两个区块。

## [ctrl] 区块 — 计算控制

### 系统设置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `num_threads` | 整数 | 4 | OpenMP 线程数，**建议 ≥10** |
| `print_level` | 整数 | 1 | 输出详细等级 (0/1/2) |

### 计算任务

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `job_type` | 字符串 | `"energy"` | 任务类型: `energy`(单点能), `opt`(优化), `force`(力) |
| `opt_engine` | 字符串 | `"default"` | 优化引擎（job_type=opt 时使用） |
| `numeric_force` | 布尔 | `false` | 是否计算数值力（job_type=force 时使用） |

### 分子信息

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `charge` | 浮点数 | `0.0` | 体系总电荷 |
| `spin` | 整数 | `1` | 自旋多重度 (1=单重, 2=双重, ...) |
| `spin_polarization` | 布尔 | `false` | 是否启用自旋极化 |

### 计算方法

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `xc` | 字符串 | — | **必填** 交换相关泛函/方法名称 |
| `empirical_dispersion` | 字符串/null | `null` | 色散校正: `"D3"`, `"D3BJ"`, `"D4"`, 或 `null` |
| `post_ai_correction` | 字符串/null | `null` | AI 后处理校正（R-xDH7-SCC15 使用） |
| `post_xc` | 字符串/null | `null` | 后处理泛函 |
| `post_correlation` | 字符串/null | `null` | 后处理相关能方法 |

**支持的 `xc` 方法：**

| 类别 | 方法 |
|------|------|
| 波函数方法 | `HF`, `MP2` |
| LDA | 内置 LDA |
| GGA | `BLYP`, `PBE`, `xPBE`, `XLYP` |
| meta-GGA | `SCAN`, `M06-L`, `MN15-L`, `TPSS` |
| 杂化泛函 | `B3LYP`, `X3LYP`, `PBE0`, `M05`, `M05-2X`, `M06`, `M06-2X`, `SCAN0`, `MN15` |
| 双杂化泛函 | `XYG3`, `XYGJOS`, `XYG7`, `sBGE2`, `ZRPS`, `scsRPA`, **`R-xDH7`** |

### 基组配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `basis_path` | 字符串 | — | **必填** 基组文件目录路径 |
| `auxbas_path` | 字符串 | — | **必填** 辅助基组（JKFIT）目录路径（eri_type=ri-v 时需要） |
| `basis_type` | 字符串 | `"gaussian"` | 基组类型 |
| `eri_type` | 字符串 | `"ri-v"` | 电子排斥积分计算方法: `"ri-v"`(密度拟合, 默认), `"direct"`(直接积分) |

### SCF 控制

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `initial_guess` | 字符串 | `"sad"` | 初猜方式: `"sad"`, `"core"`, `"read"` |
| `mixer` | 字符串 | `"pulay-diis"` | 收敛辅助: `"pulay-diis"`, `"diis"`, `"damping"`, `"none"` |
| `max_scf_cycle` | 整数 | `100` | 最大 SCF 迭代次数 |

### Post-SCF 设置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `frozen_core_postscf` | 布尔 | `true` | 冻结 core 轨道（post-SCF 相关方法） |
| `frequency_points` | 整数 | `15` | 频率（数值）格点 |
| `lambda_points` | 整数 | `80` | lambda 格点 |

## [geom] 区块 — 分子几何

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | 字符串 | 否 | 分子名称（注释用） |
| `unit` | 字符串 | 是 | 坐标单位: `"angstrom"` 或 `"bohr"` |
| `position` | 字符串 | 是 | XYZ 坐标，多行 String 格式 |

### 完整示例

```toml
[ctrl]
num_threads = 16
print_level = 1
job_type = "energy"
charge = 0.0
spin = 1
spin_polarization = false
xc = "R-xDH7"
basis_path = "/opt/rest_workspace/rest/basis-set-pool/def2-QZVPP"
auxbas_path = "/opt/rest_workspace/rest/basis-set-pool/def2-QZVPP-JKFIT"
basis_type = "gaussian"
eri_type = "ri-v"
initial_guess = "sad"
mixer = "pulay-diis"
max_scf_cycle = 100
frozen_core_postscf = true

[geom]
name = "H2O"
unit = "angstrom"
position = """
O  0.00000000  0.00000000  0.11700000
H  0.00000000  0.75700000 -0.46800000
H  0.00000000 -0.75700000 -0.46800000
"""
```
