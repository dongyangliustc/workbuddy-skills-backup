# PES Sketch — 反应势能面草图生成器

> **版本**: 2.0.0 | **状态**: stable | **日期**: 2026-06-05

## 概述

从 YAML 配置文件生成反应势能面（PES）示意图，输出为包含原生 PowerPoint 形状的可编辑 `.pptx` 文件。配套 CLI 和 GUI 工具可对生成后的 PPTX 进行批量参数调整。

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r scripts/requirements.txt

# 2. 编写配置文件（参考 references/config_schema.yaml）
# 3. 生成 PES 草图
python scripts/pes_sketch.py my_reaction.yaml -o output.pptx

# 4. （可选）批量调整线宽
python scripts/pes_resizer.py output.pptx --set 2.5 --mode dashed
```

---

## 目录结构

```
pes-sketch/
├── SKILL.md                    # 本文件
├── .VERSION                    # 版本号与变更记录
├── scripts/
│   ├── requirements.txt        # 依赖锁定（python-pptx==1.0.2, PyYAML==6.0.2）
│   ├── pes_sketch.py           # 核心：YAML → PPTX 转换
│   ├── pes_resizer.py          # CLI：批量调整线宽
│   ├── pes_tuner.py            # GUI：桌面版全局调参（可打包为 .exe）
│   ├── pes_editor.py           # GUI：备选调参界面
│   └── PES_Tuner.bas           # [开发中] VBA 宏源码（暂未集成，独立存放）
├── references/
│   └── config_schema.yaml      # 完整 YAML 配置模板
├── dist/
│   └── PES_Tuner.exe           # Windows 桌面版可执行文件
└── assets/                     # 预留：示例图片等
```

---

## API 规范

### 1. `pes_sketch.py` — 核心生成器

**接口**:

```
python scripts/pes_sketch.py <config.yaml> [-o <output.pptx>] [--mode single|stepwise]
```

**参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `config.yaml` | path | ✅ | — | YAML 配置文件路径 |
| `-o, --output` | path | ❌ | 同目录 `pes_output.pptx` | 输出 PPTX 路径 |
| `--mode` | str | ❌ | `single` | `single`=单页全景；`stepwise`=分步多页 |

**输入格式** (`config.yaml`):

```yaml
title: "反应名称"
layout: "single"              # single | stepwise
axis:
  y_range: [-20, 40]          # 可选，自动计算
species:
  - name: "R"                 # 物种名称
    type: "well"              # well | barrier | bimolecular
    energy: 0.0               # 相对能量 kcal/mol
    image: "R.png"            # 可选：分子结构图路径
    color: "#1F4E79"          # 可选：自定义颜色
  - name: "TS1"
    type: "barrier"
    energy: 25.3
    connects: [0, 2]          # 或 ["R", "Int1"]（连接哪些物种）
```

**输出**:
- 16:9 幻灯片 (13.333" × 7.5")
- 全部为原生 PowerPoint 形状（可编辑）
- `single` 模式: 1-2 张幻灯片（1 张 PES 图 + 可选的图例页）
- `stepwise` 模式: N 步 + 1 张总览

**返回值**: 进程退出码 0（成功）/ 非 0（失败），失败时向 stderr 输出错误信息。

---

### 2. `pes_resizer.py` — CLI 线宽调整

**接口**:

```
python scripts/pes_resizer.py <input.pptx> [options]
```

**参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `input.pptx` | path | ✅ | 待处理的 PPTX |
| `--status` | flag | ❌ | 扫描并打印连接线统计 |
| `--set <pt>` | float | ❌ | 设置线宽（磅） |
| `--mode` | str | ❌ | `all`(默认) / `dashed` / `solid` |
| `--color <hex>` | str | ❌ | 按颜色过滤，如 `#000000` |
| `-o, --output` | path | ❌ | 输出路径（默认覆盖原文件） |

---

### 3. `pes_tuner.py` / `pes_editor.py` — GUI 调参工具

**接口**:

```
python scripts/pes_tuner.py                    # 启动后选择 PPTX 文件
```

或双击 `dist/PES_Tuner.exe` 直接运行。

**功能**:
- 两列布局界面（参数 | 值）
- 批量修改：字体、字号、横线宽、虚线宽
- 连接线端点重新对齐
- 支持覆盖原文件或另存

---

## 调用示例

```python
# 在 Python 中调用（通过 subprocess）
import subprocess, os

SKILL_DIR = os.path.expanduser("~/.workbuddy/skills/pes-sketch")

# 生成 PES 草图
result = subprocess.run([
    "python",
    os.path.join(SKILL_DIR, "scripts", "pes_sketch.py"),
    "reaction.yaml",
    "-o", "output.pptx",
    "--mode", "single"
], capture_output=True, text=True)

if result.returncode == 0:
    print(f"生成成功: output.pptx")
else:
    print(f"错误: {result.stderr}")
```

```bash
# 在 Bash 中调用
SKILL=~/.workbuddy/skills/pes-sketch

# 生成 + 调整
python $SKILL/scripts/pes_sketch.py config.yaml -o pes.pptx
python $SKILL/scripts/pes_resizer.py pes.pptx --status
python $SKILL/scripts/pes_resizer.py pes.pptx --set 2.0 --mode dashed -o pes_final.pptx
```

---

## 已知限制

1. **分支反应检测**: 仅自动检测线性连接的分支反应；复杂分支拓扑可能需手动调整配置
2. **分子结构图**: 需用户自行提供 PNG/SVG 图片，脚本仅嵌入不生成
3. **PPAM 加载项**: VBA 宏 (`PES_Tuner.bas`) 处于开发中状态，存在 Office 2016 环境兼容性问题，**当前不与核心脚本联动**。详见 `scripts/PES_Tuner.bas` 顶部注释

---

## 环境要求

| 组件 | 最低版本 |
|------|----------|
| Python | 3.10+ |
| python-pptx | 1.0.2 |
| PyYAML | 6.0.2 |
| OS | Windows / macOS / Linux |

---

## 维护与开发

- **代码风格**: PEP 8，中文注释
- **输出格式**: 全部使用 `Emu` 单位计算，Font 默认 Times New Roman，颜色默认 `#000000`
- **测试**: 使用 `references/config_schema.yaml` 中的示例配置进行冒烟测试
