---
name: literature-registry-intake
description: 把**单篇**文献正式入库到「受治理文献库」的完整流程——PDF 按规范归位、两处 registry 登记（候选库 + 向量索引 sources）、派生的人类可读索引视图同步、向量索引重建、三方一致性验证。当用户说「入库」「收录这篇」「加进文献库」「登记到 registry」「把它加入索引」，或手上已有一篇 PDF/arXiv 号要正式登记时使用。触发词：入库、收录、登记文献、加入文献库、单篇入库、intake、add to registry、single paper intake。
agent_created: true
---

# 单篇文献入库（受治理 registry + 向量索引）

适用：项目已有「受控 registry（YAML/yaml）+ 派生索引视图（md）+ 向量索引库」三层结构
（典型如 `index/registry/*.yaml` → `index/*.md` → `materials.sqlite`）。
不适用：批量发现入库（见 `governed-literature-discovery`）；纯下载不进库（见 `nature-downloader`）。

## 0. 顺序是硬约束：索引重建必须**最后**做

```text
① PDF 归位 → ② registry 登记 → ③ 派生视图同步 → ④ 一次性重建向量索引 → ⑤ 三方验证
```

**踩过的坑**：先做了 ④ 再回来改 ③ 的 md，脏 md 已经进过索引 → 必须再跑一次 ④。
索引重建耗时长（几十秒到分钟级），重复跑纯属浪费，而且"到底哪一版进了索引"会说不清。
**开工前先确认派生视图要不要改**（多数情况要改，因为它按篇数计数）。

## 1. 元数据核实：不可信文件名，也不可信交接摘要

- 下载后**读 PDF 正文首页**核对：标题、作者顺序、页数、是否有期刊版式（判断预印本 vs 正式版）。
- 同时核 arXiv/Crossref 的 abs 页：`v1` 日期、分类、有无 journal ref、DOI 形态。
- 沙箱里 `arxiv.org` 直连通常被阻断：走 PDF 下载 MCP（`smart_download` 传 arXiv ID / DOI）；
  `scansci_pdf_smart_download` 报 "arXiv PDF not available" 时，改用 `scansci_pdf_download`
  + `strategy: oa_first` 即可成功（同一次会话实测）。
- 文件名规范要与既有文件**批量比对**后确定（例：`Author_Year_keywords_Journal.pdf`，
  预印本用 `..._arXiv.pdf`；姓氏单数、禁空格）。用 `ls papers/*/ | grep -i arxiv` 先看惯例。

## 2. registry 登记：先读 schema，别凭记忆写

**两处都要写**，字段名各库不同，**先 `yaml.safe_load` 读一条既有同类条目**照抄字段集：

| 位置 | 作用 | 典型字段 |
|---|---|---|
| `index/registry/candidates.yaml` | 受控事实来源 | id / title / authors / year / source_type / identifiers{doi,arxiv,isbn} / paths{pdf,note} / topic_tags / project_modules / state / review_status / relevance / role / confidence / actionability |
| `<mcp>/config/sources.yaml` | 向量索引的数据源清单 | source_id / title / path / format / source_type / priority |

要点：
- **枚举字段先查校验器**（`tools/validate_registry.py` 的 `ENUMS`），别自造值：
  `state` / `review_status` / `relevance` / `role` / `confidence` / `actionability` 都是封闭集。
- **arXiv-only 条目惯用 `doi: null` + `arxiv: '<id>'`**（先看既有条目再定），不要塞 DataCite DOI。
- **作者写姓氏还是全名**要看该库的主流风格（本库 curated 条目用姓氏、pipeline 追加条目用全名）；
  与文件名规范保持一致。
- **登记"来源路径"要写清楚这是怎么进来的**：加 `metadata_note` 说明是窗口外命中 / 手工批准 /
  元数据补齐，并写 `provenance` / `discovered_on` / `discovery_source`。**别让它看起来像 pipeline 自动产出。**
- 分档（`relevance`、⭐ 星级）**必须做跨文件比对**再用现有 rubric 定，否则会与旧条目差档。
- 追加前**备份**：`cp X.yaml X.yaml.bak-intake-<date>-<hhmm>`。

## 3. 文件格式的坑（YAML + md 都吃这个）

| 坑 | 症状 | 做法 |
|---|---|---|
| **CRLF** | `Edit`/`old_string` 匹配失败，或写回后整文件行尾被翻转 | 先探测 `raw.count(b'\r\n')` 与裸 LF；用 `open(..., newline='')` 读、`split(CRLF)`/`CRLF.join` 写 |
| **既有条目是 flow style + 全引号** | 自己写的 block style 与邻居风格不一致，diff 巨大 | 照抄邻居风格（`topic_tags: ["a", "b"]`） |
| **表对齐空格** | 精确匹配原文的表格行失败 | 表格行用**容错正则** `\| T4\s+\| 名\s+\| 8\s+\|`；新行宽度从**表分隔行**反推 `len(dash)-2` 再 `ljust` |
| **插入锚点选在"块中间"** | 新条目插到了上一条的**续行之前**，上一条的说明行被挤到新条目后面 | 锚点要选在**整块的最后一行**；插完必须 `sed -n` 回看前后 5 行 |

**最狠的一条**：所有替换都要**断言命中恰好 1 次**（`assert joined.count(old)==1`）。
否则一次静默 0 命中 = 你以为改了、其实没改；一次多命中 = 改错了地方。
本次就是靠这个断言抓出"计数时点那行前面没有 3 个空格"。

## 4. 派生索引视图同步

改写前先跑**计数审计工具**（如 `tools/audit_index_counts.py`），它会直接告诉你
"磁盘 PDF 数 ≠ 视图行数，缺的是哪一篇"——**这是最省事的入手点**：
如果缺的恰好是你这篇，说明视图此前是同步的，补齐它是本次入库的**义务**，不是额外工作。

要同步的地方通常是：
1. 每个视图的**头部计数**（篇数、分组计数、分布统计、同步日期/计数时点）；
2. **明细行 / 条目块**（按该文件既有风格：README 用表格行、by_topic 用表格行、by_relevance 用编号条目块）；
3. 若插入编号块 → **后续编号要整体顺移**（降序替换避免连锁冲突），且先确认编号没被别处引用；
4. 目录树里的**子目录计数**（如 `【14】`→`【15】`）。

改完立刻重跑计数审计，直到输出 `AUDIT PASSED`。

## 5. 向量索引重建与验证链

```bash
cp <index>.sqlite <index>.sqlite.bak-intake-<date>-<hhmm>       # 先备份
<mcp>/.venv/Scripts/<server>.exe ingest --config config/sources.yaml --index <index>.sqlite
```

**四道门，缺一不可**：

```text
1. tools/validate_registry.py            → "Registry validation passed."
2. tools/check_three_way.py              → A == B == C（磁盘 PDF ≡ registry paths.pdf ≡ sources.yaml）
3. tools/audit_index_counts.py           → AUDIT PASSED（视图计数 ≡ 磁盘 ≡ registry）
4. 检索验证：search_sources 用该篇特征词查询 → 命中新 source_id，
   且打标正确（provenance / epistemic_status / claim_kind）
```

第 4 步**必须真查**：只看到 `indexed_chunks: N` 只说明 pipeline 跑完了，不说明这篇进去了。
用**论文独有的措辞**当 query（例如它特有的方法名），命中并能读到正确 locator（页码）才算过。

## 6. 收尾

- 删除一次性脚本与临时文件（`_tmp_*.yaml`、`_sync_*.py`）；**要留就把脚本模板化后放进技能的 `assets/`**，
  别把硬编码一次性脚本留在项目根目录。
- 备份按 `<name>.bak-<用途>-<日期>-<hhmm>` 命名，与既有备份同级存放。
- 记一条日常日志：入库物、两处 registry 的 id、四道门结果、以及**本轮踩到的格式坑**。

## 7. 诚实边界

- **视图计数一致 ≠ 内容正确**：审计只查篇数与行数，不查"这篇定价合不合理"。
  分档/分类的正确性靠**跨文件比对 + 既有 rubric**，没有工具能替你判断。
- **`state: fulltext_available` 只表示"有全文且已核实元数据"**，不表示已精读。
  没有精读就不要写笔记，也不要把 `actionability` 抬到 `implementation_guidance` 以上。
