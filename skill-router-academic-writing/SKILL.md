---
name: skill-router-academic-writing
description: >-
  学术写作 Nature 系列路由器。当用户提到写论文、润色、引用、配图、统计、数据声明、
  审稿回复、模拟审稿、参考文献验证、论文精读、实验日志、论文做PPT等关键词时，
  自动路由到对应 nature-* 子技能。覆盖论文写作到投稿的全生命周期。
agent_created: true
---

# 学术写作 Nature 系列路由器

将学术写作相关请求路由到对应 nature-* 子技能。

## 路由表

| 用户意图 | 目标技能 | 触发关键词 |
|----------|----------|------------|
| 论文起草（摘要/引言/方法/讨论/结论） | `nature-writing` | 写论文, 起草, 搭框架, 写引言, 写摘要 |
| 学术英语润色/改写/翻译/LaTeX排版 | `nature-polishing` | 润色, 改写, 翻译, 排版, SCI写作, 语言编辑 |
| 自动添加 Nature/CNS 系列引用 | `nature-citation` | 加引用, 补文献, 找引用, 分段引用, CNS引用 |
| 论文配图（Python/R/AI示意图） | `nature-figure` | 配图, 作图, 画图, 科研绘图, 图形摘要 |
| 统计报告审查与修正 | `nature-statistics` | 统计, p值, 样本量, 置信区间, 多重比较 |
| 数据可用性声明 | `nature-data` | 数据声明, 数据可用性, 代码可用性, FAIR |
| 审稿意见回复/Rebuttal | `nature-response` | 审稿回复, rebuttal, 修回信, 逐点回复 |
| 模拟审稿（投稿前自审） | `nature-reviewer` | 模拟审稿, 预审, 找论文问题, 审稿人视角 |
| Proposal-first 写作pipeline | `nature-proposal-writer` | proposal, 写作pipeline, 科研写作状态机 |
| 参考文献多源交叉验证 | `nature-ref-verifier` | 校验文献, 核对参考文献, ref check |
| 论文精读/中英对照/全文翻译 | `nature-reader` | 读论文, 精读, 论文翻译, 中英对照 |
| 实验日志标准化 | `nature-experiment-log` | 实验日志, 记录实验, Obsidian |
| 论文→中文PPT（组会/文献汇报） | `nature-paper2ppt` | 论文做PPT, 组会PPT, 文献汇报, 读书报告 |

## 路由规则

1. **写作阶段**：起草 → `nature-writing`；润色 → `nature-polishing`
2. **引用阶段**：加引用 → `nature-citation`；验证引用 → `nature-ref-verifier`
3. **投稿准备**：配图 → `nature-figure`；统计 → `nature-statistics`；数据声明 → `nature-data`
4. **审稿阶段**：模拟审稿 → `nature-reviewer`；回复审稿 → `nature-response`
5. **阅读阶段**：精读论文 → `nature-reader`；做PPT → `nature-paper2ppt`
6. **全流程写作**：`nature-proposal-writer` 作为状态机总调度
7. **日常记录**：实验日志 → `nature-experiment-log`
