# 多形态产出规格（Delivery Modes）

核实后的主张**不是只服务代码上下文注入**。同一套 `rank ≥ 2` 门控结论可驱动多种下游交付。所有产出必须先过门，再格式化为目标形态。

## 产出形态矩阵

| 形态 | 目标消费者 | 内容 | 示例 |
|---|---|---|---|
| `code_context` | 代码 Agent / 下游推理 | 只放行 `rank ≥ 2` 且 `provenance ∈ {literature, own_publication}` 的结论，注入对话上下文 | Phase G 技术决策注入包 |
| `inspection_report` | 知识库维护者（定期） | 库存体检：可信度分布、验证债务、漂移预警、模块健康度 | 周度/月度库检报告 |
| `weekly_review` | 人工审阅者（周报） | gained / lost evidence、升级建议、待裁决队列 | 主张核实周报 |
| `verification_tickets` | 验证执行者 | 把 hypothesis / debt 排期成可执行验证动作 | 验证任务工单队列 |
| `audit_trail` | 审计/追溯 | 每条主张 provenance 全链导出（statement→锚点→证据链→裁决史） | 合规追溯文档 |
| `teaching_material` | 教学/读者 | 通过门结论整理为讲义要点、综述素材 | 方法学讲义 |
| `design_constraint` | 设计/开发 | 通过门结论作为设计约束卡 | 数值基准要求卡 |

## 各形态模板要点

### code_context（原核心形态）
- 仅输出 `rank ≥ 2` 结论；每条附 `[source_id · locator]`。
- 禁止携带 `hypothesis` / `unverified`。

### inspection_report（库检定期报告——本次新增重点）
结构：
1. **概览**：总数、provenance 分布、epistemic 分布、claim_kind 分布。
2. **验证债务**：`unverified` + `consistent` 且无基准的主张清单（按模块）。
3. **漂移预警**：`conflicting / refuted / superseded / stale` 主张及影响。
4. **模块健康度**：每模块的 `(达门槛数 / 总数)` 与债务。
5. **变化对比**：与上期（historical snapshot）对比的净新增 / 升级 / 降级。

### weekly_review（核实周报）
- Gained Evidence（本周新增锚点/升级）表。
- Lost Evidence / 冲突（降级/证伪）表。
- Verification Debt（未核实假设队列，按优先级）。

### verification_tickets（验证工单）
每个条目：`[queue] claim_id | statement | kind | module | 建议验证手段(基准/推演/查新) | 关联证据`。按 rank 升序（最不确定的优先）。

### audit_trail（追溯审计档案）
按主张展开全链：statement → source_context → provenance/kind → evidence_chain（每个 source_id + locator）→ last_verified → reviewer。可一键导出一份或多份。

### teaching_material / design_constraint
只取达门槛结论，去除内部 locator 细节，面向读者/工程师表述。

## 通用实现原则
1. **同一数据源**：所有形态都从同一份 claims registry + chunk 索引驱动，避免各形态口径漂移。
2. **门控前置**：格式化的前提是先做 `epistemic rank` 过滤，不是先排版再过滤。
3. **可引用**：每条交付结论必须可回溯（evidence_chain → 原始 chunk 定位）。
