---
name: skill-router-numerical-audit
description: >-
  数值审计与证伪方法学路由器。当用户提到数值结果可疑、审计加速比、求积网格合法性、
  病态矩阵、误差预算、假覆盖、口径不一致、流水线解析缺陷、回归守卫、跨论文曲线比对、
  论文数据复现质疑等关键词时，自动路由到对应审计子技能。
  覆盖从数值条件数诊断到优化收益核算、从测试假覆盖到已发布结论抗反驳的全套审计方法。
agent_created: true
---

# 数值审计与证伪方法学路由器

将"结论可疑、需要审计/证伪/复算"类请求路由到对应子技能。

## 路由表

| 用户意图 | 目标技能 | 触发关键词 |
|----------|----------|------------|
| 参数轴被病态逆矩阵卡住，判定重整/换传播器能否解锁 | `numerical-conditioning-falsification` | 病态, 条件数, 重整, 列均衡, Riccati |
| 审计"优化开关的收益是否被正确核算"（加速比声明） | `optimization-accounting-audit` | 加速比, 优化收益, 屏函数, 免费区, 实测代价 |
| 审计数值求积网格是否合法（权重/精确度/守恒残差） | `quadrature-validity-audit` | 求积, 网格合法性, 权重正性, 网格收敛卖点 |
| 判定"精度不够"来自数值发散还是系统偏置（分层误差预算） | `solver-error-budget-ablation` | 不收敛, 精度不够, 误差预算, 消融, 系统偏置 |
| 审计"测试通过但用户路径失效"的假覆盖（口径不一致） | `interface-parity-verification` | 假覆盖, 口径不一致, CLI/HTTP多口径, 契约 |
| 流水线解析/抽取类缺陷的事后修复（真机数据假阳性） | `pipeline-parser-defect-postmortem` | 解析缺陷, 假阳性, 夹具修复, 受控重算 |
| 为已知正确性缺陷装安全回归守卫 | `decision-pinned-kernel-defect-guard` | 回归守卫, 缺陷回归, 防复发 |
| 自家计算量与文献图曲线的定量比对（含抗反驳四检） | `cross-paper-curve-comparison` | 曲线比对, 文献对比, 标定未知, 抗反驳 |

## 路由规则

1. **症状是"数值不对"**（不收敛/精度差/病态） → `solver-error-budget-ablation` 或 `numerical-conditioning-falsification`
2. **症状是"声明不可信"**（加速比/网格收敛/数据吻合） → `optimization-accounting-audit` / `quadrature-validity-audit` / `cross-paper-curve-comparison`
3. **症状是"代码层假象"**（测试过但线上坏/流水线解析错） → `interface-parity-verification` / `pipeline-parser-defect-postmortem` / `decision-pinned-kernel-defect-guard`
4. **串联**：`pipeline-parser-defect-postmortem` 修复后用 `decision-pinned-kernel-defect-guard` 守卫；`cross-paper-curve-comparison` 的数据提取阶段交给 `chart-digitizer`（计算化学路由器）。

## 边界表

- **chart-digitizer（提取）vs cross-paper-curve-comparison（比对）**：前者把图变成数，后者把数变成可辩护结论；提取完才比对，互不替代。
- **solver-error-budget-ablation vs numerical-conditioning-falsification**：前者分层归因误差来源，后者专判"病态逆矩阵是否卡死参数轴"；病态症状先走后者。
- 本路由器不处理**文献/引用审计**（走 `nature-ref-verifier`）与**代码风格审查**（走 `code-review`）。
