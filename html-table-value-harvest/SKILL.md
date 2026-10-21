---
name: html-table-value-harvest
description: 从网页数据表中程序化提取指定数值并附可审计来源（数据库参考值 / 标度因子 / 常数 / 实验量）。当需要"从 CCCBDB、JPL、CDMS 或任意学术数据库网页取一个精确值并记录来源+检索日期"，或抓取 HTML 表格时发现表头与数据行列数不一致、担心静默取错列时使用。含双锚点列对齐、结构自洽校验、独立佐证、失败即放弃（宁缺勿错）、证书校验保留等已验证做法。
agent_created: true
---

# HTML 表格取值与溯源（Table Value Harvest）

## 何时使用
- 需要从**网页数据表**取一个精确数值，且该值必须可审计（禁止凭记忆填写，例：G1 参考值溯源规则）。
- 表格含 `rowspan` / `colspan` / 前导占位单元格 → 表头列索引与数据行列索引**不必然一致**。
- 任何"抓表取数"场景：标度因子、EA/μ 参考文献值、分子常数、基准数据。

## 核心陷阱（必读）
**不要用「表头里找到标签」的索引直接去索引数据行。**

实测 CCCBDB `vibscalejustx.asp`：表为三区结构——第 0 列分区标签（`rowspan`）、第 1 列方法名、第 2 列起为基组值；**表头行前两列为空占位**（27 列），数据行 26 列。若按表头索引 19 直接取数据行，得到 **0.969**；正确值（数据列 18）为 **0.968**。此类错位**不报错、不异常**，纯静默污染。

## 已验证工作流

### 1. 取页面（保留证书校验）
- **优先 `curl`**：使用系统 CA 库，多数沙箱/企业 MITM 代理场景下可正常完成校验（`-w "%{http_code} %{ssl_verify_result}"`，`ssl_verify_result == 0` 即通过）。
- Python `urllib` 默认走内置 `certifi`，在 MITM 代理下常报 `CERTIFICATE_VERIFY_FAILED`。
- 降级顺序：`curl` → `urllib(校验)` → `urllib(不校验，并在输出中标注 ssl_verified=false)`。
- 加 `--retry 4 --retry-delay 3 --retry-all-errors`（代理偶发 502）。
- 落地缓存原始 HTML，供复现审计。

### 2. 解析（仅标准库即可）
用 `html.parser.HTMLParser` 收集 `<table>/<tr>/<td|th>`，单元格做归一化：`html.unescape` → 去 NBSP → 压缩空白 → strip。
**注意保留空单元格**（不可 drop），否则破坏列对齐。

### 3. 列对齐（关键）
设 `h` = 目标标签在表头中的索引，`h0` = 表头**首个非空**标签索引，`n_basis = len(header) - h0`：

```
leading = len(row) - n_basis          # 数据行前导列数，须 ∈ {1,2}
idx     = leading + (h - h0)          # 引导锚点
tail    = h - (len(header) - len(row))# 尾部锚点
要求 leading ∈ {1,2} 且 idx == tail 且 0 <= idx < len(row)
且方法名恰位于 index = leading-1
```

任一不满足 → **放弃该行**（宁缺勿错），绝不猜测。

### 4. 独立佐证
- **同表另一行**取同一列（例：`B3LYP` 与 `B3LYPultrafine` 应一致）。
- **原始 HTML 单元格结构**核对（数 `<td>`、看 `rowspan`/`colspan`）。
- **已知文献值旁证**（例：B3LYP/6-31G* = 0.960）。

### 5. 溯源落盘
```json
{"method": "...", "basis": "...", "lambda": 0.968,
 "source": "<URL> —— 《页面标题》 行 X，列 Y",
 "retrieved": "YYYY-MM-DD", "retrieved_iso": "...",
 "note": "...",
 "fetch": {"transport": "curl", "http_code": 200, "ssl_verify_result": 0, "ssl_verified": true},
 "cross_check": {"<另一行取值>": 0.968, "agree": true},
 "evidence": {"header_index": 19, "first_label_index": 2, "n_basis": 25,
              "col_index": 18, "lead_anchor": 18, "tail_anchor": 18,
              "row_mapping": {"aug-cc-pVTZ": "0.968", "...": "..."},
              "raw_header": ["..."], "raw_row": ["..."]}}
```

### 6. 失败即上报
解析未命中 → 写 `null` + `"source": "UNKNOWN"` 并以非零退出码结束，**绝不回退到记忆值**。

## 测试要点
- **证伪式回归**：断言"按错误算法（表头索引直取）会得到 0.969，且 ≠ 0.968"——锁死错位缺陷。
- 结构不自洽（长度/方法名位置不匹配）→ 断言返回 `None`。
- 非空表头标签列（无法确定边界）→ 断言返回 `None`（fail-safe）。
- 带 `rowspan` 分区标签的行（`leading == 2`）也要覆盖。

## 反面清单
- ❌ 用 `pandas.read_html` 后按位置取列而不校验语义（rowspan 会改变列数）。
- ❌ 只校验"表头里有这个标签"就取值。
- ❌ 解析失败时用记忆值兜底。
- ❌ 丢弃空单元格。
