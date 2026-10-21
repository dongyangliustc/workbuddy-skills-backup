---
name: skill-router-computational-chemistry
description: >-
  计算化学工具链路由器。当用户提到 Gaussian、gjf、优化、单点能、频率计算、dpdata、
  势能面、过渡态、泛函/基组选型、Multiwfn、波函数分析、REST 高精度计算、服务器传输、
  远端执行、上传下载、集群作业等关键词时，自动路由到对应计算化学子技能。
  覆盖从输入文件生成、计算执行、数据传输、数据标注到高精度衔接的全流程。
agent_created: true
---

# 计算化学工具链路由器

将计算化学相关请求路由到对应子技能。

## 路由表

| 用户意图 | 目标技能 | 触发关键词 |
|----------|----------|------------|
| Gaussian .gjf 输入文件组装/提取/模板化 | `gjf-flux` | gjf, 输入文件, route section, Link1, 组装gjf |
| Gaussian 计算执行（本地/HPC/Slurm） | `run-gauss` | 跑Gaussian, 执行计算, 提交作业, g16, scratch |
| **本地↔服务器传输 + 远端执行桥接** | `remote-sync` | 上传, 下载, 传输, 服务器, 远端执行, ssh, scp, 回传结果, gtransfer |
| dpdata 能量/力/维里标注 | `dpdata-driver` | dpdata, driver, 标注, LabeledSystem, predict |
| 综合查询（关键字用法/泛函基组/错误排查/波函数分析） | `gaussian-agent` | Gaussian关键字, 泛函选择, 基组, Multiwfn, Sobko, 思想家公社 |
| Gaussian优化→REST R-xDH7高精度单点能 | `run-rest` | REST, R-xDH7, def2-QZVPP, 单点能, 高精度 |
| Sobko 知识库桥接（MCP 未挂载时的 stdio 兜底） | `sobko-kb-bridge` | Sobko知识库, 思想家公社检索, Multiwfn手册 |
| 反应势能面示意图（YAML→可编辑pptx，CLI/GUI调参） | `pes-sketch` | 势能面图, PES, 反应能态图, 能量分布图 |
| 阴离子光脱附截面 PDCS 计算（PYCS + even-tempered 弥散基组） | `pycs-photodetachment` | 光脱附, PDCS, 光电离截面, 弥散基组 |
| 从图表图片提取数值数据（多曲线/误差棒/对数坐标） | `chart-digitizer` | 图里取数, 提取数据点, 数字化曲线, 论文插图 |
| 从网页数据表提取指定数值（附可审计来源） | `html-table-value-harvest` | CCCBDB, JPL, CDMS, 网页表格取值, 数据库常数 |
| Python 数据→Origin 工程图（与 matplotlib 全一致） | `origin-opju-parity-build` | Origin, OPJU, originpro, 工程图 |

## 路由规则

1. 若用户请求涉及 **Gaussian 输入文件编写** → 路由到 `gjf-flux`
2. 若用户请求涉及 **执行 Gaussian 计算**（本地） → 路由到 `run-gauss`
3. 若用户请求涉及 **本地↔服务器文件传输 / 远端执行 / 结果回传** → 路由到 `remote-sync`
4. 若用户请求涉及 **dpdata 数据标注** → 路由到 `dpdata-driver`
5. 若用户请求涉及 **REST 高精度单点能** → 路由到 `run-rest`
6. 若用户请求涉及 **知识查询/泛函基组选型/错误排查/波函数分析** → 路由到 `gaussian-agent`
7. 若涉及多步骤全流程 → `gaussian-agent` 作为总调度，按需调用其他子技能
8. **数据获取前置**：论文插图→数据用 `chart-digitizer`；数据库网页取值用 `html-table-value-harvest`（两者产出可交给 `cross-paper-curve-comparison` 做定量比对）
9. **知识库兜底**：`gaussian-agent` 依赖的 Sobko MCP 未挂载时 → `sobko-kb-bridge`
10. **结果可视化**：常规科研图 → `nature-figure`（学术写作路由器）；需在 Origin 内继续编辑 → `origin-opju-parity-build`；反应能级示意 → `pes-sketch`

## 流水线说明

```
gjf-flux(组装) → remote-sync(上传/远端执行/回传) → dpdata-driver(标注)
       ↑                                        ↓
       └──────── gaussian-agent(总调度) ←── run-rest(高精度)
```

remote-sync 与 run-gauss 的分工：run-gauss 提供**本地**执行知识（环境/scratch/命令模板）；
remote-sync 负责**跨机**传输与远端执行桥接（SFTP 上传下载 + g16/direct/slurm + 状态轮询）。
