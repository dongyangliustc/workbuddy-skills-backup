---
name: skill-router-general-tools
description: >-
  通用工具路由器。当用户提到浏览器自动化、联网搜索、网页抓取、文件摘要、
  代码审查、换肤等关键词时，路由到对应技能。
agent_created: true
---

# 通用工具路由器

将通用工具类请求路由到对应技能。

## 路由表

| 用户意图 | 目标技能 | 触发关键词 |
|----------|----------|------------|
| 浏览器自动化/联网/网页抓取/登录操作 | `web-access` | 搜索, 网页, 抓取, 浏览器, 登录, 小红书 |
| URL/文件/YouTube摘要 | `summarize` | 摘要, summarize, 总结URL |
| 系统化代码审查 | `code-review` | 代码审查, code review, PR审查 |
| WorkBuddy桌面端换肤 | `workbuddy-skin` | 换肤, 皮肤, 主题, skin |

## 路由规则

1. **任何联网操作**（搜索/抓取/登录） → `web-access`
2. **快速摘要**（URL/文件/YouTube） → `summarize`
3. **代码质量审查** → `code-review`
4. **界面主题** → `workbuddy-skin`
