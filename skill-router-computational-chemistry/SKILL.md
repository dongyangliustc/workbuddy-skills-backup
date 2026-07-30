---
name: skill-router-computational-chemistry
description: >-
  计算化学工具链路由器。当用户提到 Gaussian、gjf、优化、单点能、频率计算、dpdata、
  势能面、过渡态、泛函/基组选型、Multiwfn、波函数分析、REST 高精度计算等关键词时，
  自动路由到对应计算化学子技能。覆盖从输入文件生成、计算执行、数据标注到高精度衔接的全流程。
agent_created: true
---

# 计算化学工具链路由器

将计算化学相关请求路由到对应子技能。

## 路由表

| 用户意图 | 目标技能 | 触发关键词 |
|----------|----------|------------|
| Gaussian .gjf 输入文件组装/提取/模板化 | `gjf-flux` | gjf, 输入文件, route section, Link1, 组装gjf |
| Gaussian 计算执行（本地/HPC/Slurm） | `run-gauss` | 跑Gaussian, 执行计算, 提交作业, g16, scratch |
| dpdata 能量/力/维里标注 | `dpdata-driver` | dpdata, driver, 标注, LabeledSystem, predict |
| 综合查询（关键字用法/泛函基组/错误排查/波函数分析） | `gaussian-agent` | Gaussian关键字, 泛函选择, 基组, Multiwfn, Sobko, 思想家公社 |
| Gaussian优化→REST R-xDH7高精度单点能 | `run-rest` | REST, R-xDH7, def2-QZVPP, 单点能, 高精度 |

## 路由规则

1. 若用户请求涉及 **Gaussian 输入文件编写** → 路由到 `gjf-flux`
2. 若用户请求涉及 **执行 Gaussian 计算** → 路由到 `run-gauss`
3. 若用户请求涉及 **dpdata 数据标注** → 路由到 `dpdata-driver`
4. 若用户请求涉及 **REST 高精度单点能** → 路由到 `run-rest`
5. 若用户请求涉及 **知识查询/泛函基组选型/错误排查/波函数分析** → 路由到 `gaussian-agent`
6. 若涉及多步骤全流程 → `gaussian-agent` 作为总调度，按需调用其他子技能
