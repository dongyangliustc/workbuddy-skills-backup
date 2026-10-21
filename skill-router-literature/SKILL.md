---
name: skill-router-literature
description: >-
  文献检索、下载与深度阅读路由器。当用户提到搜文献、查文献、下载PDF、批量下载、
  图书馆访问、WebVPN、DOI下载、文献综述检索、读文献、精读论文等关键词时，
  自动路由到对应子技能。覆盖检索、机构下载、阅读理解、批判性审阅全链路。
agent_created: true
---

# 文献检索、下载与深度阅读路由器

将文献检索、下载与深度阅读相关请求路由到对应子技能。

## 路由表

| 用户意图 | 目标技能 | 触发关键词 |
|----------|----------|------------|
| **深度阅读编排（每次读文献的默认方法论，六阶段流水线）** | `literature-deep-reading` | 读文献, 精读, 帮我读这篇, 吃透这篇, 这篇讲什么, 文献阅读, paper reading, 多篇综合, 主题综合 |
| **单篇批判性方法学审阅（他人论文；P0/P1/P2 findings + 引用 verdict）** | `critical-paper-appraisal` | 这篇可信吗, 能不能引, 批判性审阅, 方法审查, overclaiming, 当锚点前先查 |
| 多源文献检索+引文审计 | `nature-academic-search` | 搜文献, 查文献, 文献检索, 引文核对, 他引判定 |
| 自动化文献发现流水线（搜索→评分→精读→交付） | `nature-literature-pipeline` | 文献pipeline, 每日文献, 自动文献发现 |
| **受治理文献库的发现层（候选库→状态机→人工审阅周报）** | `governed-literature-discovery` | 文献监控智能体, 每周文献周报, 定期论文发现, 候选文献库, 每周文献重复, 信噪比差 |
| **机构访问下载（Chrome 登录态，含中科大/浙大等预设）** | `nature-downloader` | 下载文献, 下载PDF, 图书馆, 机构访问, WebVPN, 中科大, 批量下载 |
| 多源PDF下载agent（scansci-pdf CLI） | `scansci-pdf-agent` | 下载文献, download paper, 批量下载, fetch DOI |
| 文献综述写作辅助 | `literature-review` | 文献综述, literature review, 找论文 |
| 单篇文献正式入库受治理文献库（registry登记+向量索引） | `literature-registry-intake` | 文献入库, registry登记, PDF归位, 向量索引重建 |
| 知识库"猜想-核实-迭代"主张生命周期管理 | `claim-lifecycle` | 主张抽取, 主张核实, epistemic门控, 库检定, 验证工单 |
| 打分/筛选型流水线的召回外扩（含抑制项审计） | `recall-widening-gate-audit` | 召回外扩, 锚点共现门, 抑制项审计, 限流可靠性 |

## 路由规则

1. **先分"找"还是"读"**：用户意图是**获得文献**（搜/下载/发现）→ 按下方检索与下载规则路由；意图是**理解已有/可获得的文献**（读懂、审阅、综合）→ 直接进 `literature-deep-reading`，由其六阶段流水线编排下游（精读→`nature-reader`、审阅→`critical-paper-appraisal`、取证→`chart-digitizer` 等、固化→`claim-lifecycle`），不要在本路由器内逐段派发阅读子任务
2. **搜索**：多源检索 → `nature-academic-search`；综述写作 → `literature-review`；自动流水线 → `nature-literature-pipeline`
3. **下载**：统一走 `nature-downloader`（机构访问 + 学校预设 + CAS/CARSI/EZproxy/WebVPN 多路由识别）；需要多源降级时叠加 `scansci-pdf-agent`
4. **检索+下载**：`nature-academic-search` 找到文献后，交给 `nature-downloader` 下载
5. **两套"自动文献发现"的分工**（勿混用）：
   - 项目**没有**文献 registry、目标是"关键词→每天推送到 IM"→ `nature-literature-pipeline`
   - 项目**已有**受治理的候选库/状态机、目标是"每周增量周报交人工裁决"→ `governed-literature-discovery`
   - 判据：看仓库里是否存在候选文献 registry 与校验脚本。两者是互补的交付形态，不是新旧版本，不要互相替代。

> 历史说明：`ustc-literature-downloader` / `zju-literature-downloader` 已于 2026-09-17 归并进 `nature-downloader`。
> USTC 与 ZJU 预设位于 `nature-downloader/data/schools.yaml`；两者原为**同一模板的两份实例**（仅 141 行校名/URL 差异），
> 其独有的「出版社 DOI 直取模式」「搜索系统字段说明」「实测用例」三节已完整迁入 `nature-downloader/SKILL.md`。
