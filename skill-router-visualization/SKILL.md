---
name: skill-router-visualization
description: >-
  科研可视化路由器。当用户提到势能面、PES草图、反应能态图等关键词时，
  路由到对应技能。
agent_created: true
---

# 科研可视化路由器

将科研可视化相关请求路由到对应技能。

## 路由表

| 用户意图 | 目标技能 | 触发关键词 |
|----------|----------|------------|
| 反应势能面（PES）草图→可编辑PPTX | `pes-sketch` | 势能面, PES, 反应能态图, 能量分布图 |

## 路由规则

1. **PES草图** → `pes-sketch`
2. 注意：论文配图（matplotlib/ggplot/AI示意图）属于学术写作分类，路由到 `nature-figure`
