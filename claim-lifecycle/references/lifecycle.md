# Claim 生命周期与状态机（通用版）

任何知识库都可把内容管理从"**文档入库即事实**"升级为"**主张级采集 → 双轴打标 → 锚定 → 核实 → 门控 → 多形态产出**"的生命周期。

## 1. 五阶段旅程

```
采集 Capture → 打标 Label → 锚定 Anchor → 核实 Verify → 产出 Deliver
```

| 阶段 | 产物 | 主体 |
|---|---|---|
| 1 采集 | 原子主张卡（statement + kind + 出处） | 工具抽取 + 人工确认 |
| 2 打标 | 双轴标签（provenance × epistemic_status） | 规则（来源推断）+ 人工 |
| 3 锚定 | 证据链（evidence_chain + anchor_locator） | 工具保守锚定（机器只筛选） |
| 4 核实 | 状态升级/降级 | **人工在周报复核门槛** |
| 5 产出 | 多形态交付（注入包/报告/档案） | 门控放行 rank ≥ 2 的结论 |

## 2. 主张状态机

```
[*] --> claimed
claimed --> anchored: 至少一次检索锚定
anchored --> literature_supported: 多源独立支撑
anchored --> consistent: 单源一致，待加固
anchored --> benchmark_verified: 数值基准通过
anchored --> conflicting: 与文献冲突
anchored --> unsupported: 无锚点
unsupported --> validation: 登记验证任务（基准/推演/查新）
validation --> anchored: 新证据回收
consistent --> literature_supported: 人工复核上浮
literature_supported --> injected: 可注入下游
benchmark_verified --> injected: 可注入下游
conflicting --> refuted: 人工裁决证伪
refuted --> [*]
superseded --> [*]
stale --> [*]
```

## 3. 机器职责边界（关键）

机器只做两件事：
1. **保守锚定**：查找证据并给出**建议**状态（从不自动升级到 `literature_supported`/`benchmark_verified`）；
2. **门控放行**：查询侧按 `min_epistemic_status` 过滤。

**升级/降级一律由人工裁决**（周报复核门槛）。机器若自动升级，会把自身来源的弱命中变成"自我背书"，导致验证失真。

## 4. 门控规则

- 门控位置：**检索/产出层**，不在入库层——入库不设限、出库严把关。
- 普通信息类查询：全量召回（自产知识对阅读/写作有价值），但结果**带标签展示**。
- 代码上下文注入 / 对外事实交付：强制 `rank ≥ 2` 且 `provenance ∈ {literature, own_publication}`。
- `hypothesis` 永不注入——登记进验证队列（debt），跑基准/推演/查新后再走状态机。

## 5. 核实节奏

- **周报**：gained / lost evidence、升级建议、verification debt（未核实假设队列）。
- **定时库检**：库存体检——可信度分布、债务统计、漂移预警、模块健康度。
- **追溯审计**：按主张导出全链（statement → 锚点 → 证据链 → 裁决历史）。
