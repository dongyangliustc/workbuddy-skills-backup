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
| docx 精细结构化操作（缺字形/公式OMML/批注审计/图表编号核对/附图重绘） | `docx-toolkit` | 豆腐块, 缺字形, 公式图片, OMML, 批注提取, 定位原文, 图表编号引用, 附图重绘 |
| 金融数据查询/资产配置/回测/现金流分析 | `yingmi-skill` | 基金, 资产配置, 回测, 现金流, 财经图表 |
| 主题→PPT全流程（文献搜索→美化→交付） | `ppt-workflow` | 做PPT, 主题PPT, 美化优化, 标准化工作流 |
| Draw.io/Mermaid/Excalidraw 图表生成与编辑 | `diagram-generator` | draw.io, mermaid, excalidraw, 拓扑图, UML |

## 路由规则

1. **任何联网操作**（搜索/抓取/登录） → `web-access`
2. **快速摘要**（URL/文件/YouTube） → `summarize`
3. **代码质量审查** → `code-review`
4. **界面主题** → `workbuddy-skin`
5. **docx 结构性缺陷修复 / 精细定位审计** → `docx-toolkit`；常规 docx 读写与排版仍走 `tencent-docx` 插件
6. **金融/投资场景** → `yingmi-skill`（涨跌配色遵循中国惯例：涨红跌绿）
7. **主题式 PPT 制作** → `ppt-workflow`；已有论文转 PPT 走 `nature-paper2ppt`（学术写作路由器）
8. **结构化图表文件**（draw.io/mermaid） → `diagram-generator`；对话内联示意走内置 Visualizer，不走本技能
