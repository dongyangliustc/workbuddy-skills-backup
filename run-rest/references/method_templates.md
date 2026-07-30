# 方法模板参考 — REST 高精度单点能

以 **R-xDH7/def2-QZVPP** 为核心特色方法，同时提供 XYG 系列和 sBGE2 等双杂化方法模板。

## 核心方法：R-xDH7

### 标准模板（推荐）

```toml
[ctrl]
xc = "R-xDH7"
basis_path = "/path/to/basis-set-pool/def2-QZVPP"
auxbas_path = "/path/to/basis-set-pool/def2-QZVPP-JKFIT"
```

- **描述**：重整化 XYG3 型双杂化泛函，7 参数优化，兼顾动态和静态相关
- **推荐基组**：def2-QZVPP + def2-QZVPP-JKFIT
- **应用场景**：有机分子、小分子体系的高精度热化学计算

### R-xDH7-SCC15（带静态相关校正）

```toml
[ctrl]
xc = "R-xDH7"
post_ai_correction = "SCC15"
basis_path = "/path/to/basis-set-pool/def2-QZVPP"
auxbas_path = "/path/to/basis-set-pool/def2-QZVPP-JKFIT"
```

- **描述**：R-xDH7 + 15 参数静态相关校正模型（机器学习优化）
- **适用**：存在显著静态相关的体系（解离曲线、双自由基等）
- **注意**：SCC15 需要 REST 编译时开启对应选项

### 线程数要求

REST 的 R-xDH7 为后自洽场双杂化方法，计算量较大。
- **最小线程数**：10（REST 强制建议）
- **推荐线程数**：16–32（取决于 CPU 核心数）
- **辅助基组必须**：eri_type 默认为 ri-v，需要 JKFIT 辅助基组

---

## 扩展方法模板

### XYG7/def2-QZVPP

```toml
[ctrl]
xc = "XYG7"
basis_path = "/path/to/basis-set-pool/def2-QZVPP"
auxbas_path = "/path/to/basis-set-pool/def2-QZVPP-JKFIT"
```

- **描述**：XYG3 系列的第七代泛函，7 参数优化
- **特点**：与 R-xDH7 属同一系列，参数化策略不同

### XYG3/def2-TZVPP

```toml
[ctrl]
xc = "XYG3"
basis_path = "/path/to/basis-set-pool/def2-TZVPP"
auxbas_path = "/path/to/basis-set-pool/def2-TZVPP-JKFIT"
```

- **描述**：原始 XYG3 双杂化方法
- **特点**：经典方法，计算成本较低

### sBGE2/def2-QZVPP

```toml
[ctrl]
xc = "sBGE2"
basis_path = "/path/to/basis-set-pool/def2-QZVPP"
auxbas_path = "/path/to/basis-set-pool/def2-QZVPP-JKFIT"
```

- **描述**：自洽 sBGE2 方法，BGE2 的自洽版本
- **特点**：无需 B3LYP 密度，完全自洽

### HF/def2-QZVPP（参考计算）

```toml
[ctrl]
xc = "HF"
basis_path = "/path/to/basis-set-pool/def2-QZVPP"
auxbas_path = "/path/to/basis-set-pool/def2-QZVPP-JKFIT"
```

- **描述**：Hartree-Fock 参考计算
- **用途**：作为双杂化方法的对比基准

---

## 基组对应关系

| 基组 | JKFIT 辅助基组 | 适用方法 | 说明 |
|------|---------------|---------|------|
| def2-SVP | def2-SVP-JKFIT | B3LYP 优化 | 小基组，快速 |
| def2-TZVP | def2-TZVP-JKFIT | XYG3 等 | 中等精度 |
| def2-TZVPP | def2-TZVPP-JKFIT | XYG3 等 | 平衡精度/速度 |
| **def2-QZVPP** | **def2-QZVPP-JKFIT** | **R-xDH7** | **高精度（推荐）** |
| def2-QZVP | def2-QZVP-JKFIT | R-xDH7/XYG7 | 另一种四zeta基组 |
| aug-cc-pVTZ | aug-cc-pVTZ-JKFIT | 双杂化方法 | 弥散函数，适合阴离子/弱相互作用 |
| aug-cc-pVQZ | aug-cc-pVQZ-JKFIT | 双杂化方法 | 高精度+弥散 |

## 基组池目录结构

REST 基组池的预期结构：

```
/path/to/basis-set-pool/
├── def2-QZVPP/            # 基组目录（包含基组文件）
│   ├── ...
└── def2-QZVPP-JKFIT/      # 辅助基组目录
    └── ...
```

基组池目录可通过 `$basis_set_pool` 占位符配置，运行时替换为实际路径。
