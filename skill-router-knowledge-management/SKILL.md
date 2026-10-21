---
name: skill-router-knowledge-management
description: >-
  知识管理与知识库路由器。当用户提到IMA笔记、知识库上传、构建MCP知识库、
  系统化学习、拆书、知识追溯等关键词时，自动路由到对应子技能。
  覆盖笔记管理、知识库构建、方法论蒸馏全链路。
agent_created: true
---

# 知识管理与知识库路由器

将知识管理相关请求路由到对应子技能。

## 路由表

| 用户意图 | 目标技能 | 触发关键词 |
|----------|----------|------------|
| IMA笔记/知识库管理（读取/写入/检索/上传） | `ima-skills` | IMA, 笔记, 知识库, 上传到知识库 |
| 本地资料→只读MCP知识库（证据可追溯问答） | `source-grounded-mcp` | 构建知识库, MCP, 课程材料, 文档问答 |
| 风暴知识工坊（STORM多视角+10步闭环学习） | `storm-knowledge-crafter` | 系统化学习, 深度研究, 知识库构建, 10步学习 |
| 拆书（书→可执行skills方法论蒸馏） | `cangjie-skill` | 拆书, 蒸馏一本书, book2skill |

## 路由规则

1. **IMA操作**（笔记/知识库读写） → `ima-skills`
2. **从本地资料构建MCP知识库** → `source-grounded-mcp`
3. **系统化学习一个领域** → `storm-knowledge-crafter`（内含 source-grounded-mcp）
4. **把书蒸馏成技能** → `cangjie-skill`
