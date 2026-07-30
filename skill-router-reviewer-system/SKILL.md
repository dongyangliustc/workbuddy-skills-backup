---
name: skill-router-reviewer-system
description: >-
  审稿智能体系统路由器。当用户提到构建期刊审稿智能体、子审稿agent、
  文献数据库构建、ISSN爬取、IMA知识库部署等关键词时，路由到对应技能。
agent_created: true
---

# 审稿智能体系统路由器

将审稿智能体构建相关请求路由到对应技能。

## 路由表

| 用户意图 | 目标技能 | 触发关键词 |
|----------|----------|------------|
| 构建期刊子审稿智能体（ISSN爬取→文献库→PDF→IMA部署） | `chemistry-reviewer-child-agent-builder` | 子审稿agent, 期刊审稿智能体, ISSN爬取, 文献数据库 |

## 路由规则

1. **创建新期刊审稿子智能体** → `chemistry-reviewer-child-agent-builder`
2. 该技能内部会调用 `nature-academic-search`（文献检索）、`scansci-pdf-agent`/`nature-downloader`（PDF下载）、`ima-skills`（知识库部署）等子技能
