---
name: governed-literature-discovery
description: 为一个已有的「受治理文献知识库」建立每周自动论文发现与增量周报智能体。当用户要求"派一个定期的论文发现/文献监控智能体""每周文献周报""自动扫描新文献供我审阅"，且其项目已存在文献 registry（候选库/核心库/状态机/打分规则）时使用。也适用于诊断"发现环节自动化后信噪比差""同一篇论文每周重复出现""arXiv 抓取总是失败"这类问题。不适用于纯检索问答（用 nature-academic-search）或综述写作（用 literature-review）。
agent_created: true
---

# 受治理文献库的发现层部署

## 何时用

用户已有一套**治理层**（候选状态机、打分规则、registry schema、校验脚本），但没有自动化
的**发现层**。典型征兆：`tools/` 里全是校验/生成脚本却没有检索脚本；周报产物停留在数周前；
某份 `automation_prompt.md` 写着 "future automation" 却从未部署。

若连治理层都不存在，先判断是否值得自建：交给人工审阅的文献库若只做「关键词→推送」，
用现成方案（zotero-arxiv-daily 家族的 GitHub Actions + SMTP）更快；只有当需要
**状态机 + 门控 + 可审计的候选库**时才自建。

## 与相邻技能的边界（先判形态，再选技能）

| 场景 | 该用谁 | 判据 |
|---|---|---|
| 要一次检索、要引文核对 | `nature-academic-search` | 一次性检索，无周期性 |
| 要把检索结果写成综述 | `literature-review` | 产出是文章，不是持续监测 |
| **没有** registry，要「关键词 → 每天推送到 IM」 | `nature-literature-pipeline`（第三方技能） | 仓库里没有候选库/状态机 |
| **已有** registry，要「每周增量周报交人工裁决」 | 本技能 | 仓库里有候选库 + 校验脚本 |
| 只要下载 PDF | `nature-downloader` / `scansci-pdf-agent` | 不涉及筛选与治理 |

上表第 3、4 行是**互补的交付形态，不是新旧版本**。不要用本技能去替换
`nature-literature-pipeline`（它是第三方技能，带作者署名，且属于 `nature-*` 家族，
被 router 与多处文档引用）；反过来也不要拿它来替代受治理的候选库。

## 第 0 步：动手前必须探测网络可达性（最容易翻车的一步）

**永远不要先写检索代码再发现某个 API 不通。** 逐源做**双通道**探测：

| 通道 | 探测方式 |
|---|---|
| 脚本进程 | `python -c "import urllib.request; urllib.request.urlopen(url, timeout=30)"` |
| agent 工具 | 用网页抓取工具直接请求同一 URL |

实测结论（2026-09，Windows 沙箱走 `http_proxy`）：Crossref 两通道均通；
**arXiv `export.arxiv.org` 脚本通道被隧道阻断 502 而 agent 通道正常**；
OpenAlex / Semantic Scholar 脚本通道 429。这类不对称是常态，不要假设一致。

后果与对策：

- 脚本与 agent 必须分工：**脚本抓通得了的源，agent 抓通不了的源**，两边都产出同一种
  harvest JSON，再交给同一个离线摄入函数。
- **绝不要对不可达主机写重试循环**：3 次重试 × 10 个查询会超过工具调用超时，整轮被杀且
  管道缓冲导致**输出全丢**（表现为「空输出 + SIGTERM」，极难排查）。默认只抓可达源，
  不通的源做成显式开关。

## 第 1 步：两段式架构

```
fetch   -- 联网，产出 harvest JSON（脚本抓可达源；agent 抓其余后写成同构 JSON）
ingest  -- 完全离线、确定性：归一化 → 去重 → 打分 → 追加 registry → 校验/回滚
```

`ingest` 必须可对同一份 harvest 反复运行而不产生副作用（除 seen-list 外），
否则无法安全重放。

## 第 2 步：去重键（必须内含跨周持久层）

按顺序比对，命中即止：

1. `doi:` — 小写，剥掉 `https://doi.org/`、`doi:` 前缀
2. `arxiv:` — 小写，剥掉 `abs`/`pdf` URL 前缀与 `vN` 后缀
3. `title:` — NFKD 折叠后非字母数字折叠为单空格

比对对象顺序：现有 registry（候选库+核心库）→ **当前批次内已接受记录** → 跨周 seen-list。

两个容易漏的点：

- **批内去重**：同一篇论文常被两个不同查询各返回一次。若只在「写入成功」时登记批次键，
  被拒记录会在拒绝列表里重复出现。批次键应在**进入打分段之前**登记。
- **跨周 seen-list 是现成方案的共同缺口**：几乎所有开源方案只靠「昨天/上周」的日期窗口
  增量，第 n 周出现过的论文第 n+1 周会被重新加入。把它作为独立状态文件
  （`{doi:, arxiv:, title:}` → 首次见到日期 + 处置结果），并把处置结果（added /
  in_registry / below_threshold）一并记下，这样拒绝本身就是可审计的。

「第一作者姓+年份已存在」只作**提示**，不作删除依据：仍写入，但强制
`state: needs_human_review` + `review_status: human_review_needed`。

## 第 3 步：给自动化划定论断权限（治理层的关键）

自动化**永远不得**声称：

- `relevance: core` —— 「直接支撑本项目公式/实现/基准门」是人判断
- `confidence` 高于 `abstract_only`（`benchmark_verified` 绝对禁止）
- 任何提升到 `indexed` / `injected_to_code_context` 的状态

允许的状态只有 `metadata_verified` / `abstract_screened` / `needs_human_review`。

## 第 4 步：写入门槛必须由真实分布校准，不得凭直觉

先跑一轮 `--dry-run`，把被拒项的**分数直方图**打出来再定阈值。经验数据（324 条抓取）：

| 分数 | 条数 | 性质 |
|---|---|---|
| 0 | 271 | 完全离题 |
| 1–4 | 21 | 单一弱命中或负向抵销 |
| 5–8 | 31 | 裸领域词命中的邻域噪声 |
| ≥11 | 1 | 真正相关 |

结论：低分阈值（≥5）会每周把约 31 条近失项推到审阅者面前并稀释 registry。
**默认门槛定在 medium，低分项完整保留在 discovery log 与周报「自动剔除」表中**，
既保精度又不丢审计。

## 第 5 步：写入纪律（可回滚）

1. 先备份 `candidates.yaml` → `*.bak-discovery-<ts>`
2. 按既有记录格式**追加**（严格照抄缩进与嵌套，别用 YAML 库重排整文件）
3. 在同一进程内跑校验函数
4. 任何校验错误 → 还原备份 → 非零退出

失败一轮必须让 registry 与之前**逐字节一致**。注意把 `*.bak-*` 加进 `.gitignore`，
否则每周自动同步会不断提交备份。

## 陷阱清单（都是实测踩过的）

1. **关键词子串误报**：`ethylene` 命中 `trichloroethylene`、`n2` 命中 `n2o`、
   `dft` 命中 `tddft`。单词型术语一律加词边界 `(?<![a-z0-9])…(?![a-z0-9])`；
   多词短语才用子串。
2. **期刊缩写匹配失效**：配 `journal of physics b` 匹配不上 `J. Phys. B: At. Mol. Opt. Phys.`。
   watch 列表必须同时收全称与缩写。
3. **对不可达主机重试** → 整轮超时且输出全丢（见第 0 步）。
4. **并行编辑同一文件会静默丢写入**：一次对同一文件发起多处编辑，相邻写入互相覆盖，
   直到运行报 `NameError` 才暴露。同文件编辑一律**串行**。
5. **打分维度必须硬性封顶**并重算总分，防止子任务打出「11/10」。
6. **无人值守时不得阻塞等待澄清**，也不得为了制造产出而放宽阈值；0 新增要如实报告。

## 交付清单

```
config/<profile>.json          查询集 + 关键词组与权重 + 作者/期刊 watch + 阈值（月度人工复核）
tools/discover_candidates.py   queries / fetch / ingest / status，纯标准库
state/discovery_seen.json      跨周 seen-list
outputs/harvests/              每轮原始 harvest（可 gitignore，体积大）
outputs/discovery_logs/        每轮决策日志（入库，审计留痕）
<周报生成器> --since            增量段：本周新增 / 被标记 / 自动剔除
docs/.../discovery_agent_spec.md  规格：环境实测、去重键、权限边界、故障处置、每周清单
自动化                          默认每周一次，与既有定时任务错峰
```

## 与 GitHub 现成方案的取舍（供论证用）

| 方案 | 强项 | 缺什么 |
|---|---|---|
| zotero-arxiv-daily 家族 | GitHub Actions 零成本定时投递；用 Zotero 库当兴趣画像；本地/云端 LLM 可切 | 无状态机、无跨周 seen-list、推荐算法仅嵌入相似度 |
| arxiv-sanity-lite | cron + 邮件范式 | 多年停更、非 LLM、无领域打分 |
| paper-qa (PaperQA2) | 本地语料 agentic RAG、带引用 | 不做发现，只做问答 |
| gpt-researcher | 多源深研 | 输出不可复现、依赖实时网页、按次计费 → 不适合周期性审阅流 |

若有治理层：补发现前端。若没有：优先用现成方案，不要为「每周推送」自建状态机。
