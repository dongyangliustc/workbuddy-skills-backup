---
name: skill-router-literature
description: >-
  文献检索与下载路由器。当用户提到搜文献、查文献、下载PDF、批量下载、图书馆访问、
  WebVPN、DOI下载、文献综述检索等关键词时，自动路由到对应子技能。
  覆盖多源检索、机构下载、综述写作全链路。
agent_created: true
---

# 文献检索与下载路由器

将文献检索与下载相关请求路由到对应子技能。

## 路由表

| 用户意图 | 目标技能 | 触发关键词 |
|----------|----------|------------|
| 多源文献检索+引文审计 | `nature-academic-search` | 搜文献, 查文献, 文献检索, 引文核对, 他引判定 |
| 自动化文献发现流水线（搜索→评分→精读→交付） | `nature-literature-pipeline` | 文献pipeline, 每日文献, 自动文献发现 |
| 通用机构访问下载（Chrome登录态） | `nature-downloader` | 下载文献, 下载PDF, 图书馆, 机构访问 |
| 多源PDF下载agent（scansci-pdf CLI） | `scansci-pdf-agent` | 下载文献, download paper, 批量下载, fetch DOI |
| 浙大图书馆/WebVPN下载 | `zju-literature-downloader` | 浙大图书馆, 浙大WebVPN, 求是学术 |
| 中科大图书馆/WebVPN下载 | `ustc-literature-downloader` | 中科大图书馆, 中科大WebVPN, USTC |
| 文献综述写作辅助 | `literature-review` | 文献综述, literature review, 找论文 |

## 路由规则

1. **检索优先**：先确定用户要"搜"还是"下载"
2. **搜索**：多源检索 → `nature-academic-search`；综述写作 → `literature-review`；自动流水线 → `nature-literature-pipeline`
3. **下载**：按机构路由 — 浙大 → `zju-literature-downloader`；中科大 → `ustc-literature-downloader`；通用 → `nature-downloader`；多源降级 → `scansci-pdf-agent`
4. **检索+下载**：`nature-academic-search` 找到文献后，按机构路由到对应下载器
