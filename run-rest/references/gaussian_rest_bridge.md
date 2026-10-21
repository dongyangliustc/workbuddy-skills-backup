# Gaussian ↔ REST 数据桥接

## 概述

run-REST 的核心任务是搭建 Gaussian 优化结果与 REST 高精度单点能之间的桥梁。
本文档说明两软件之间数据传递的关键技术细节。

---

## 几何提取模式

### 模式 A：仅结构需要（默认）

**来源**：Gaussian 输出文件 (.log/.out)

**方法**：解析 `Standard orientation` 坐标段，提取末态优化几何。

**适用场景**：
- R-xDH7/def2-QZVPP 单点能计算
- XYG3/XYG7 单点能计算
- 任何仅需要分子结构、不需要波函数信息的后处理方法

**命令**：
```bash
python scripts/extract_geom_from_log.py gaussian.log -o geom.xyz
```

**输出格式**（XYZ，Angstrom）：
```
12
Extracted from Gaussian output
   C    0.00000000   0.00000000   0.00000000
   O    0.00000000   0.00000000   1.20000000
   ...
```

**注意事项**：
- 对于优化任务，Gaussian 输出可能包含多组坐标（初始、中间步、末态），脚本自动取**最后一组** Standard orientation
- 对于单点能任务（无优化步骤），退化到 `Input orientation` 段
- 原子序数 → 元素符号的映射在脚本内硬编码，覆盖 H–Og

### 模式 B：波函数传递（预留）

⚠️ **此功能尚未实现，由外部 mokit 自动化智能体负责。**

**需求场景**：
- 需要在 REST 中复用 Gaussian 的波函数作为初猜
- 基组完全一致（def2-QZVPP）时的波函数继承
- 使用 `initial_guess = "read"` 从外部文件读取轨道

**未来接口设计**：
```
mokit-agent:
  - input:  Gaussian .chk 波函数文件
  - output: REST 可读的波函数/密度文件（如 molden 格式）
  - 此智能体尚未创建，预留占位
```

**当前回退行为**：当用户传入 `--chk` 参数时，run_rest_sp.py 会输出提示信息，然后回退到模式 A（从 .log 提取坐标）。

---

## 坐标单位与转换

| 软件 | 默认单位 | 支持单位 |
|------|---------|---------|
| Gaussian 输出 | Angstrom | Angstrom, Bohr |
| REST | Angstrom | Angstrom (`"angstrom"`), Bohr (`"bohr"`) |

- Gaussian 的 `Standard orientation` 坐标始终以 Angstrom 输出
- REST 的 `[geom]` 中通过 `unit = "angstrom"` 指定
- 无需额外单位转换

---

## 电荷和自旋多重度继承

Gaussian 输出文件的关键参数与 REST 的对应关系：

| 物理量 | Gaussian (.gjf 头部) | REST ([ctrl] 区块) |
|--------|---------------------|-------------------|
| 电荷 | `Charge` | `charge` |
| 自旋多重度 | `Multiplicity` | `spin` |
| 自旋极化 | `Multiplicity > 1` | `spin_polarization = true` |

**注意**：Gaussian 输出文件头部通常有：
```
 Charge =  0   Multiplicity = 1
```
提取此信息可用于自动填充 REST 的 charge/spin 参数。
需要向用户确认以解决歧义（不同片段可能给出不同值）。

---

## 文件格式桥接总结

```
Gaussian(.log) ──→ extract_geom_from_log.py ──→ XYZ 坐标 ──→ build_rest_input.py ──→ REST(.toml)
                                │                                                  │
                                └── 仅几何信息 ──────────────────────────────────→  [geom] 区块
Gaussian(.chk) ──→ [mokit] ──→ 波函数/密度 ──→ REST(read) ──→ [ctrl] 区块 (initial_guess="read")
                    (预留)
```
