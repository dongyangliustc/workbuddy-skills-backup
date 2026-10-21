# Claim 元数据 Schema（通用版）

原子主张（claim）是知识库的最小可信单元。每条主张卡携带以下字段；**禁止把"来源属性"与"核实状态"合并成单一标签**，二者是正交轴。

## 1. 主张卡字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | str | ✅ | `clm-XXXX` 递增编号 |
| `statement` | str | ✅ | 原子断言，一句可验证的主张 |
| `source_context` | str | ✅ | 出处（文档/对话/日期） |
| `claim_kind` | enum | ✅ | fact / derivation / hypothesis / workflow（见 §3） |
| `provenance` | enum | ✅ | 维度 A：来源属性（见 §2.1） |
| `epistemic_status` | enum | ✅ | 维度 B：核实状态（见 §2.2） |
| `project_module` | list[str] | 建议 | 所属模块/主题标签 |
| `evidence_chain` | list[str] | 建议 | 支撑证据的 source_id 链 |
| `anchor_locator` | str\|null | 建议 | 锚点物理位置（文件+定位） |
| `last_verified` | date | ✅ | 最近核实日期 |
| `reviewer` | str | ✅ | 核实人/机构 |

## 2. 双轴定义

### 2.1 维度 A —— 来源属性 provenance

| 值 | Tier | 定义 | 先验可信度 |
|---|---|---|---|
| `literature` | T0 | 外部发表/同行评审（可溯源、有身份） | 高（仍须核实） |
| `own_publication` | T0* | 自有已发表（等同 T0 纳入注入门） | 高 |
| `synth_summary` | T1 | 基于 T0 的可追溯派生/综述（锚点=文献链） | 中高 |
| `ideation` | T2 | 自产共识/猜想（对话产物，暂无外部锚点） | 低（原点） |

### 2.2 维度 B —— 核实状态 epistemic_status 与 Rank

| 状态 | 含义 | Rank |
|---|---|---|
| `benchmark_verified` | 数值/自洽性基准通过 | 3 |
| `literature_supported` | 多源独立支撑 | **2（注入门槛）** |
| `consistent` | 单源一致，待加固 | 1 |
| `unverified` | 未核实（原点） | 0 |
| `conflicting` | 与文献冲突 → 人工裁决 | −1 |
| `refuted` / `superseded` / `stale` | 被证伪 / 被替代 / 会话内已修正 | −2 |

```
rank: benchmark_verified=3 > literature_supported=2 > consistent=1 > unverified=0
      > conflicting=-1 > {refuted, superseded, stale}=-2
```

**注入任何下游（代码上下文/对外交付）的硬门槛：rank ≥ 2。**

## 3. claim_kind 分轨（"支撑义务"不同）

| kind | 定义 | 是否强制文献锚定 | 可否注入下游 |
|---|---|---|---|
| `fact` | 事实断言（如物性、公式、结论） | 强制 | 可（须 rank ≥ 2） |
| `derivation` | 可复现推演（推导、化简、变换） | 推演可复现即可 | 可（须 rank ≥ 2） |
| `hypothesis` | 猜想/待验证假设（创新、融合提议） | 否 | **永不**（只进验证队列） |
| `workflow` | 流程/规范/约定（仅人类使用） | 否 | 否（可作为操作指引） |

**黄金法则（不可违背）：未经核实（rank ≥ 2）的主张永远不得影响代码决策或对外以"事实"交付。**

## 4. chunk 级继承规则（fail-closed）

当检索发生在 chunk 粒度（而非主张卡粒度）时：
- chunk 继承其内含所有主张中的**最差值**（最小 rank）；
- 一个 chunk 含未核实主张 → 该 chunk 整体按未核实处理；
- 宁可拖延交付，不可污染可信层。
