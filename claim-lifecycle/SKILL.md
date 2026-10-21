---
name: claim-lifecycle
description: 管理知识库中"猜想-核实-迭代"的主张（claim）生命周期。当用户提到主张抽取/打标/锚定/核实、知识库可信度、文献锚定、epistemic 门控、库检定期报告、核实周报、验证工单、追溯审计档案、主张 registry、把对话/笔记知识纳入证据库并分可信度管理、或需要把已核实结论注入代码上下文/教学/设计约束等下游时使用。面向任意知识库通用，不依赖本项目私有组件。This skill should be used when the user needs to capture, anchor, verify and gate atomic knowledge claims, or generate multi-mode deliveries (inspection reports, weekly reviews, verification tickets, audit trails, inject packs) from a claims registry.
---

# Claim Lifecycle（主张生命周期知识管理）

## Overview

把知识库从"文档入库即事实"升级为"**主张级采集 → 双轴打标 → 文献锚定 → 人工核实 → 门控 → 多形态产出**"。原子主张（claim）成为最小可信单元，机器只锚定、人工才升级，未核实内容永不进入代码决策或对外"事实"交付。

## 核心概念（速查）

- **双轴打标（正交，绝不合并）**：来源属性 `provenance`（literature / own_publication / synth_summary / ideation）× 核实状态 `epistemic_status`（unverified < consistent < literature_supported < benchmark_verified；负向 conflicting / refuted / superseded / stale）。
- **分轨**：`claim_kind` 决定"支撑义务"——fact 强制文献锚定、derivation 推演可复现、hypothesis **永不注入**、workflow 仅人类使用。
- **黄金法则**：未经核实（rank ≥ 2）的主张永远不得影响代码决策或对外以"事实"交付。
- **门控在产出/检索层，不在入库层**：入库不设限、出库严把关。
- 字段与受控值详见 `references/metadata_schema.md`；状态机与旅程详见 `references/lifecycle.md`。

## 生命周期流程（五阶段）

```
采集 Capture → 打标 Label → 锚定 Anchor → 核实 Verify(人工周报) → 产出 Deliver(多形态)
```

### 阶段 1 · 采集（Capture）
从对话/笔记/文档抽取原子主张卡。
- 运行 `scripts/extract_claims.py --file <doc> --out <registry>.json [--out-yaml <registry>.yaml] [--source-context ...]`。
- 机器仅做启发式分 kind（fact/derivation/hypothesis/workflow），`provenance` 默认 `ideation`、状态 `unverified`。
- 抽取后**人工确认**每条主张是否原子、是否值得入库。

### 阶段 2 · 打标（Label）
- 用 `scripts/validate_registry.py <registry>` 校验受控值与分轨一致性。
- 校对 provenance（自有已发表 → `own_publication`；教材转述 → `synth_summary`）。
- 规则自动推断可参考 `sources.yaml` 思路：`own_note→ideation/unverified/hypothesis`。

### 阶段 3 · 锚定（Anchor）
- 运行 `scripts/anchor_claims.py --registry <registry> --index <sqlite>`（本项目的 MCP 索引）
  或 `--corpus <txt目录>`（免依赖的 token-overlap 引擎）。
- 机器**保守三级**：T0/T0* 字面命中 score≥100 → 建议 literature_supported；有非自产弱锚 → consistent；无锚 → unverified。
- **防自锚定失真**：锚定自动排除 `ideation` chunk 与主张自身来源文档（见 `references/pitfalls.md` 陷阱 1）。
- 机器只写回"建议状态"——**升级必须人工**。

### 阶段 4 · 核实（Verify，人工周报门槛）
- 生成 `scripts/report_generator.py weekly --registry <registry> --out weekly.md`，人工逐条裁决：
  - Gained Evidence（是否上浮 / 批准注入）、Lost Evidence（降级/证伪）、Verification Debt（登记验证任务）。
- 升级后才能由 `anchor_claims.py` 后的恢复状态自然达到 `literature_supported`、并由人工确认为最终状态。

### 阶段 5 · 产出（Deliver，多形态）
同一 registry 驱动所有下游，**先过门（rank ≥ 2 且 provenance ∈ {literature, own_publication}）再格式化**：
- `report_generator.py inspect --registry <registry> [--snapshot <prev>]` → **库检定期报告**（可信度分布、验证债务、漂移预警、模块健康度、环比）。
- `report_generator.py weekly --registry <registry>` → **核实周报**。
- `report_generator.py tickets --registry <registry>` → **验证工单**（按不确定度排序的可执行队列）。
- `report_generator.py audit --registry <registry> [--id <clm-xxxx>]` → **追溯审计档案**（全链导出）。
- `report_generator.py inject --mode code_context|teaching_material|design_constraint --registry <registry>` → **注入包**（代码上下文 / 教学素材 / 设计约束）。
- 各形态规格与模板见 `references/delivery_modes.md`；开箱模板见 `assets/claims_registry_template.json`。

## 快速开始（最小闭环）

```bash
# 1. 从对话文档抽取主张卡
python scripts/extract_claims.py --file notes/某对话.md \
    --out registry/claims.json --out-yaml registry/claims.yaml

# 2. 校验
python scripts/validate_registry.py registry/claims.json

# 3. 锚定（对本项目 MCP 索引，或任何文本语料）
python scripts/anchor_claims.py --registry registry/claims.json --index /path/materials.sqlite
#   免依赖回退：
python scripts/anchor_claims.py --registry registry/claims.json --corpus ./papers_txt

# 4. 库检定期报告 / 核实周报 / 追溯审计（人工周报复核后进入注入）
python scripts/report_generator.py inspect   --registry registry/claims.json --out inspect.md
python scripts/report_generator.py weekly    --registry registry/claims.json --out weekly.md
python scripts/report_generator.py audit     --registry registry/claims.json --out audit.md
python scripts/report_generator.py inject    --mode teaching_material --registry registry/claims.json
```

## 触发指引

用户请求涉及以下任一场景即应用本技能：
- "把对话知识打上可信度标签 / 主张抽取 / claim 管理"
- "进行文献锚定 / 核实迭代 / epistemic 门控"
- "生成本次库检定期报告 / 核实周报 / 验证工单 / 追溯审计档案"
- "把已核实结论注入代码上下文 / 做成教学素材 / 作为设计约束"
- 任何"未审查知识不得影响决策"的知识库治理诉求

## 资源索引

- `scripts/extract_claims.py` —— 对话/笔记 → 主张卡（stdlib，跨项目可移植）。
- `scripts/anchor_claims.py` —— 保守锚定（MCP 索引或免依赖语料两种后端）。
- `scripts/report_generator.py` —— 多形态产出（inspect/weekly/tickets/audit/inject）。
- `scripts/validate_registry.py` —— registry 校验（受控值 + 分轨一致性，可作 CI）。
- `references/metadata_schema.md` —— 字段、双轴、受控值、chunk 继承规则。
- `references/lifecycle.md` —— 五阶段旅程与状态机、机器职责边界、门控规则。
- `references/delivery_modes.md` —— 8 种产出形态的规格与模板要点。
- `references/pitfalls.md` —— 工程经验（自锚定失真、弱锚漂移、门控位置等）。
- `assets/claims_registry_template.json` —— 主张注册表开箱模板。

## 注意

- 脚本为纯 Python 标准库（除 `--index` 后端在可导入 `course_material_mcp` 时增强）；`--corpus` 后端零第三方依赖。
- 本技能面向**通用知识库**；原项目（spherical-wave literature）的既有实现是它的首个生产案例，细节差异可对照 `references/pitfalls.md`。
- 核实是一门终身工作：宁可拖延交付，不可污染可信层。
