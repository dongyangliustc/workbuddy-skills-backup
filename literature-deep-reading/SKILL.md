---
name: literature-deep-reading
version: 1.0.0
agent_created: true
description: >-
  每次读文献的默认方法论（编排层）。把"读文献"拆成可审计的六阶段流水线：
  定位分级 → 获取 → 全文精读 → 批判性审阅 → 量化取证 → 主张固化归档，
  每阶段路由到既有专用技能（nature-reader / critical-paper-appraisal /
  chart-digitizer / cross-paper-curve-comparison / claim-lifecycle 等），
  避免阅读退化为"读摘要即总结"。支持三档深度：L0 速览 / L1 标准精读（默认）/
  L2 多篇主题综合（全文级跨论文综合，非摘要级）。当用户说"读文献、精读、
  帮我读这篇、深度读一下、吃透这篇、这篇讲什么、文献阅读、paper reading"
  或提出任何单篇/多篇论文理解需求时使用。本技能只做编排与纪律约束，
  不替代各阶段专用技能。
---

# Literature Deep Reading — 读文献默认方法论（编排层）

## 定位

本技能是文献阅读的**编排与纪律层**：决定读多深、按什么顺序读、每阶段调用哪个专用技能、产什么交付物、哪些步骤不可省。它自身不实现提取、翻译或审阅逻辑——那些在下游技能里。

设计动机：通用的"读文献"请求容易被降级为摘要式速读，丢失 Methods、局限性与数据一致性信息。本流水线以**全文 source map 为锚、批判性审阅为闸门、主张卡为固化形态**，保证读过的每一篇都留下可追溯、可核验的资产。

## 第一步：分诊（必须最先做）

按用户意图与场景确定深度档位，并向用户报一句分诊结果（档位 + 理由），允许廉价纠正：

| 档位 | 适用场景 | 交付物 | 耗时量级 |
|------|----------|--------|----------|
| **L0 速览** | 只需判断"值不值得读/和我有什么关系" | 速览卡（见下） | 分钟级 |
| **L1 标准精读**（默认） | 单篇要真正读懂、要引用、要作为工作锚点 | paper.md + appraisal.md（+ 可选主张卡） | 单次会话 |
| **L2 主题综合** | 多篇文献回答一个主题问题 | synthesis.md + claims registry | 多轮 |

分诊默认规则：未明确说"只看看摘要/快速了解" → L1；出现"几篇、综述、对比、这个领域" → L2；明确赶时间或明确说"大概看看" → L0。

**L0 速览卡格式**（此时允许不读全文，但必须明示"未读全文"）：核心结论一句话 / 证据类型（观测·计算·综述·观点）/ 关键数据一个 / 与用户工作的相关点一个 / 建议档位。

## 第二步：按档位执行

### L1 标准精读 — 六阶段流水线

```
阶段0 定位分级 → 阶段1 获取 → 阶段2 全文精读 → 阶段3 批判性审阅
                                        ↓
              阶段5 固化归档 ← 阶段4 量化取证（条件触发）
```

**阶段 0 · 定位分级**：确认来源（DOI / arXiv / 出版商链接 / 本地 PDF / 粘贴文本）。做分诊报档。

**阶段 1 · 获取**：本地文件跳过。否则调用 `nature-downloader`（机构访问，USTC 等预设）；失败降级 `scansci-pdf-agent`（多源）。仍失败则向用户要 PDF，不要凭记忆读"没拿到全文的论文"。

**阶段 2 · 全文精读**（不可省）：调用 `nature-reader`，产出全文 `paper.md` + `source_map.json`（S/C/F/T 块级锚点）+ 图表原位 + 术语表。纪律红线：
- 禁止跳过 Methods、limitations、data availability；
- 禁止把全文降级为要点列表或摘要；
- 论文过长时分页增量写 `paper.md` 并标注 pending blocks，而不是转摘要模式。

**阶段 3 · 批判性审阅**（不可省）：调用 `critical-paper-appraisal`，基于阶段 2 的 source map 产出 `appraisal.md`。审阅分两步：**七维总体审查所有论文必过**；**专项审查仅当论文命中已注册领域模块（当前：计算化学/天体化学）才启用**，未命中时如实记录"未启用"，禁止强套领域尺子。产出：编号 findings（P0 致命 / P1 需谨慎 / P2 轻微）+ 三档 verdict（可作硬锚点 / 谨慎引用 / 不建议引用）。findings 必须引用块锚点，不接受无锚点指控。

**阶段 4 · 量化取证**（条件触发，按需进入）：
| 触发条件 | 技能 |
|----------|------|
| 需要从论文插图取数值 | `chart-digitizer` |
| 与自有计算量做定量比对 | `cross-paper-curve-comparison` |
| 引用的关键参数（μ、Q、常数）存疑 | `astro-column-density-provenance-audit` |
| 网页数据表取精确值 | `html-table-value-harvest` |
| 数据一致性怀疑（分项加总、归一化） | `chart-digitizer` 任务 F（恒等式审计） |

**阶段 5 · 固化归档**：
- 原子主张抽取入库 → `claim-lifecycle`（机器锚定 + 人工核实门控）；
- 项目存在受治理文献 registry → `literature-registry-intake`（PDF 归位 + 向量索引）；
- 目标是内化方法论/领域知识 → `storm-knowledge-crafter`（10 步闭环）或 `cangjie-skill`（拆书成技能）。
- 跨库复检已读内容 → `spherical-wave-literature` MCP（语义检索 + 源段回读）。

**交付物清单（L1）**：`paper.md`、`appraisal.md` 必产；claims registry、取证产物按触发情况产出。向用户汇报时先给 verdict 与 top findings，再给文件。

**最小不可省集**：即使用户赶时间，L1 下阶段 2 + 3 不可省——可以压缩篇幅（标注 pending blocks、findings 只报 P0/P1），但不允许跳过批判性审阅直接"读懂了"。

### L2 主题综合 — 多篇全文级

面向"这几篇对 XX 的结论是否一致/这个领域现状如何"类问题：

1. **逐篇走 L1 的阶段 1–3**（获取、精读、审阅），每篇产出 paper.md + appraisal.md。
2. **统一主张卡**：从每篇 source map 抽取原子主张，字段统一 `{id, statement, evidence_type, location(作者,年,块锚点), epistemic_status}`，格式对齐 `claim-lifecycle` 的 registry schema。
3. **综合矩阵**：按主题/方法/结论聚类，排成"论文 × 主题"矩阵；**结论冲突处显式单列**（对齐 claim-lifecycle 的 `conflicting` 状态），禁止 silently 平均矛盾结论。
4. **产出 `synthesis.md`**：主题叙事，每条断言必须带 `(作者, 年, 块锚点)` 内联引用；叙事只能连接 source map 中实际存在的块，禁止跨论文脑补证据链。
5. 先呈矩阵与冲突清单给用户裁决，再写叙事——综合的"综合方式"本身要过审。

## 与其他技能的边界

- `nature-reader` / `critical-paper-appraisal` 等是**执行层**，本技能是**编排层**：不要在本技能里重写提取或审阅逻辑。
- `skill-router-literature` 管"找文献"（检索/下载/发现层）；本技能管"读文献"（获得文献之后的理解链）。用户的意图是"读懂"而非"找到"时进本技能。
- `nature-reviewer` 模拟审稿**自己的**稿件；批判性审阅**别人的**论文走 `critical-paper-appraisal`。
- `literature-review`（摘要级元数据综合）只用于纯检索综述场景；要求全文级综合时用本技能 L2。
- `summarize` 仅作 L0 的辅助备选（Windows 下 CLI 可用性未验证）。

## 落地纪律

1. 每次阅读会话结束时，向用户报告：档位、各阶段产出文件路径、verdict、遗留 pending blocks。
2. 用户在会话间回看某篇时，先读已有 paper.md / appraisal.md 的 source map 增量回答，不要从头重读。
3. 阅读台账：完成 L1/L2 后在项目记忆中登记一行（日期 / 文献 / 档位 / verdict / 产出路径），供跨会话追溯。
