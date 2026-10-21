# WorkBuddy Skills Backup

> **最近同步**: 2026-10-21
> **GitHub 仓库**: dongyangliustc/workbuddy-skills-backup (public, 已脱敏)
> **技能规模**: 82 个技能目录 + `_shared` 公共资源 + 路由器体系

## 脱敏说明（重要）

本仓库为**公开**备份。同步时已将所有真实 IP 地址（课题组服务器、内网示例等）替换为占位符 **`__SERVER_IP__`**。
恢复使用时请全局搜索 `__SERVER_IP__` 并替换为实际地址（对照本地环境 `~/.workbuddy/skills/`）。

## 分类总览

| 分类 | 代表技能 | 路由器 |
|------|----------|--------|
| 计算化学工具链 | gaussian-agent, qchem-agent, dpdata-driver, gjf-flux, run-gauss, run-rest, pes-sketch, remote-sync, sobko-kb-bridge | skill-router-computational-chemistry |
| 学术写作 Nature 系列 | nature-writing, nature-polishing, nature-figure, nature-citation, nature-response, nature-reviewer, nature-paper2ppt 等 16 个 | skill-router-academic-writing |
| 文献检索与深度阅读 | nature-academic-search, nature-downloader, nature-literature-pipeline, literature-deep-reading, literature-registry-intake, scansci-pdf-agent, critical-paper-appraisal | skill-router-literature |
| 知识管理 | ima-skills, cangjie-skill, storm-knowledge-crafter, claim-lifecycle, source-grounded-mcp | skill-router-knowledge-management |
| 数值审计与证伪 | quadrature-validity-audit, solver-error-budget-ablation, numerical-conditioning-falsification, cross-paper-curve-comparison, optimization-accounting-audit | skill-router-numerical-audit |
| 专利 | patent-disclosure-skill, patent-agent-engagement, cnipa-patent-agent-distribution, nature-paper-to-patent | skill-router-patent |
| 系统运维与网关诊断 | llm-gateway-400-debug, llm-gateway-identity-audit, dsh-agent, wb-monitor-webui, workbuddy-automation-watchdog, win-longtask-liveness | skill-router-system-ops |
| 通用工具 | code-review, summarize, web-access, diagram-generator, ppt-workflow, docx-toolkit, workbuddy-skin | skill-router-general-tools |

完整清单以仓库内各 `SKILL.md` 为准。

## 排除说明（体积控制）

以下内容**不在备份内**：

- `qchem-agent/references/qchem-6.1.1/`（Q-Chem 6.1.1 手册与样例，约 39MB，第三方可再获取）→ 恢复方式见该目录留下的 README.md 标记
- `nature-figure/assets/`（绘图图集资源，约 34MB，可由脚本重新生成）→ 恢复方式见该目录留下的 README.md 标记
- skill 目录内嵌的 `.workbuddy/` 本地状态目录（automation memory 等）

## 恢复方式

将此仓库克隆到 `~/.workbuddy/skills/` 目录即可恢复全部技能：
```bash
git clone https://github.com/dongyangliustc/workbuddy-skills-backup.git ~/.workbuddy/skills
```
克隆后按上述排除说明补齐两个大资源目录，并完成 `__SERVER_IP__` 占位符替换。
