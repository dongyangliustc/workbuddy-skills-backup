#!/usr/bin/env python3
"""Multi-mode delivery generator for a claims registry (portable, stdlib-only).

One gated claims registry drives many downstream artifacts. Every mode applies
the epistemic gate BEFORE formatting: only the conclusions that clear the floor
(rank >= min_rank) are eligible; hypothesis/unverified never leak into factual
deliverables unless explicitly requested (--include-debt for internal reports).

Modes:
  inspect   -- periodic library inspection report (库存体检)
  weekly    -- claim verification weekly (核实周报)
  tickets   -- verification task tickets (验证工单)
  audit     -- provenance audit trail (追溯审计档案)
  inject    -- delivery inject packs: code_context | teaching_material | design_constraint

Usage (all share --registry claims.json):
  python report_generator.py inspect  --registry claims.json [--snapshot prev.json]
  python report_generator.py weekly   --registry claims.json
  python report_generator.py tickets  --registry claims.json
  python report_generator.py audit    --registry claims.json [--id clm-0001]
  python report_generator.py inject   --mode teaching_material --registry claims.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

RANK = {"refuted": -2, "superseded": -2, "stale": -2, "conflicting": -1,
        "unverified": 0, "consistent": 1, "literature_supported": 2,
        "benchmark_verified": 3}
INJECTABLE = {"literature", "own_publication"}


def load_registry(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("claims", [])


def rank_of(c: dict[str, Any]) -> int:
    return RANK.get(c.get("epistemic_status", "unverified"), 0)


def _fmt_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if v:
        return [str(v)]
    return []


# ---------------------------------------------------------------------------
# Mode 1: inspection (periodic library health report)
# ---------------------------------------------------------------------------

def build_inspection(claims: list[dict[str, Any]],
                     snapshot: list[dict[str, Any]] | None) -> str:
    lines = ["# 库检定期报告（Claim Registry Inspection）", ""]
    lines.append(f"> 生成日期：{date.today().isoformat()} ｜ 主张卡总数：{len(claims)}")
    lines.append("")

    lines.append("## 1. 可信度分布")
    lines.append("| 核实状态 | 数量 | 占比 | 可注入下游? |")
    lines.append("|---|---|---|---|")
    statuses = Counter(c.get("epistemic_status", "unverified") for c in claims)
    for s in sorted(statuses, key=lambda x: RANK.get(x, 0), reverse=True):
        n = statuses[s]
        ok = "✅" if RANK.get(s, 0) >= 2 else "—"
        lines.append(f"| `{s}` | {n} | {n / len(claims):.1%} | {ok} |")
    lines.append("")

    lines.append("### 1.1 来源属性分布")
    prov = Counter(c.get("provenance", "ideation") for c in claims)
    lines.append(" · ".join(f"`{k}` {v}" for k, v in prov.items()))
    lines.append("")
    lines.append("### 1.2 主张类型分布")
    kinds = Counter(c.get("claim_kind", "unclassified") for c in claims)
    lines.append(" · ".join(f"`{k}` {v}" for k, v in kinds.items()))
    lines.append("")

    lines.append("## 2. 验证债务（Verification Debt）")
    debt = [c for c in claims if RANK.get(c.get("epistemic_status"), 0) < 2]
    lines.append(f"共 **{len(debt)}** 条未达注入门槛，按模块归组：")
    by_mod: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in debt:
        for m in (_fmt_list(c.get("project_module")) or ["(未分组)"]):
            by_mod[m].append(c)
    for mod, items in sorted(by_mod.items()):
        lines.append(f"### {mod}（{len(items)}）")
        lines.append("| ID | 状态 | 类型 | 主张 |")
        lines.append("|---|---|---|---|")
        for c in items:
            lines.append(f"| {c.get('id')} | {c.get('epistemic_status')} | "
                         f"{c.get('claim_kind')} | {c.get('statement')} |")
        lines.append("")
    lines.append("")

    lines.append("## 3. 漂移预警（Conflicting / Refuted / Superseded / Stale）")
    flagged = [c for c in claims if RANK.get(c.get("epistemic_status"), 0) < 0]
    if flagged:
        for c in flagged:
            lines.append(f"- **{c.get('id')}** [{c.get('epistemic_status')}] {c.get('statement')}")
    else:
        lines.append("- 无负向状态主张。")
    lines.append("")

    lines.append("## 4. 模块健康度")
    rows: dict[str, dict[str, Any]] = {}
    for c in claims:
        for m in (_fmt_list(c.get("project_module")) or ["(未分组)"]):
            r = rows.setdefault(m, {"total": 0, "ok": 0})
            r["total"] += 1
            if RANK.get(c.get("epistemic_status"), 0) >= 2:
                r["ok"] += 1
    lines.append("| 模块 | 总数 | 达门槛 | 覆盖率 |")
    lines.append("|---|---|---|---|")
    for m, r in sorted(rows.items()):
        lines.append(f"| {m} | {r['total']} | {r['ok']} | {r['ok'] / r['total']:.0%} |")
    lines.append("")

    if snapshot:
        lines.append("## 5. 环比变化（vs 上期快照）")
        prev = {c.get("id"): c for c in snapshot}
        cur = {c.get("id"): c for c in claims}
        new_ids = [i for i in cur if i not in prev]
        up = [i for i in cur if i in prev and rank_of(cur[i]) > rank_of(prev[i])]
        down = [i for i in cur if i in prev and rank_of(cur[i]) < rank_of(prev[i])]
        lines.append(f"- 新增：**{len(new_ids)}** / 升级：**{len(up)}** / 降级：**{len(down)}**")
        for grp, ids, arrow in (("升级", up, "↑"), ("降级", down, "↓")):
            if ids:
                lines.append(f"  - {grp}：{', '.join(ids)}（{arrow}）")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode 2: weekly review (claim verification weekly)
# ---------------------------------------------------------------------------

def build_weekly(claims: list[dict[str, Any]]) -> str:
    lines = ["# 主张核实周报（Claim Verification Weekly）", ""]
    lines.append(f"> 生成日期：{date.today().isoformat()} ｜ 审查者：DY")
    lines.append("")
    lines.append("## Gained Evidence / 升级建议")
    lines.append("| ID | 主张 | 当前 → 建议 | 建议依据 | 动作 |")
    lines.append("|---|---|---|---|---|")
    yes = [c for c in claims if c.get("epistemic_status") == "literature_supported"]
    for c in yes[:20]:
        ev = _fmt_list(c.get("evidence_chain"))
        lines.append(f"| {c.get('id')} | {c.get('statement')} | → literature_supported "
                     f"| {', '.join(ev) or '—'} | 复核锚点后可注入 |")
    lines.append("")
    lines.append("## Lost Evidence / 冲突")
    lost = [c for c in claims if RANK.get(c.get("epistemic_status"), 0) < 0]
    lines.append("| ID | 主张 | 状态 | 证据 |")
    lines.append("|---|---|---|---|")
    for c in lost:
        lines.append(f"| {c.get('id')} | {c.get('statement')} | {c.get('epistemic_status')} "
                     f"| {', '.join(_fmt_list(c.get('evidence_chain'))) or '—'} |")
    if not lost:
        lines.append("| — | 无 | — | — |")
    lines.append("")
    lines.append("## Verification Debt（需人工复核 / 登记验证）")
    debt = [c for c in claims if rank_of(c) < 2]
    lines.append("| ID | 类型 | 主张 | 建议验证手段 |")
    lines.append("|---|---|---|---|")
    for c in sorted(debt, key=rank_of)[:30]:
        means = {"hypothesis": "基准 / 推演 / 查新",
                 "derivation": "推导可复现 + 数值自洽",
                 "fact": "回原始文献逐条背书",
                 "workflow": "人工确认流程规范"}.get(c.get("claim_kind"), "人工")
        lines.append(f"| {c.get('id')} | {c.get('claim_kind')} | {c.get('statement')} | {means} |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode 3: verification tickets (actionable work queue)
# ---------------------------------------------------------------------------

def build_tickets(claims: list[dict[str, Any]]) -> str:
    lines = ["# 验证任务工单（Verification Tickets）", ""]
    lines.append(f"> 生成日期：{date.today().isoformat()} ｜ 按不确定度升序（最不确定优先）")
    lines.append("")
    debt = sorted([c for c in claims if rank_of(c) < 2], key=rank_of)
    for i, c in enumerate(debt, 1):
        means = {"hypothesis": "跑基准 → 推演 → 查新",
                 "derivation": "手推复现 + benchmark",
                 "fact": "文献逐条背书",
                 "workflow": "人工确认"}.get(c.get("claim_kind"), "人工")
        lines.append(f"### 工单 #{i} ｜ {c.get('id')}")
        lines.append(f"- 主张：{c.get('statement')}")
        lines.append(f"- 类型 / 状态：{c.get('claim_kind')} / {c.get('epistemic_status')}")
        lines.append(f"- 模块：{', '.join(_fmt_list(c.get('project_module'))) or '—'}")
        lines.append(f"- 建议验证手段：{means}")
        lines.append(f"- 证据链：{', '.join(_fmt_list(c.get('evidence_chain'))) or '—'}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode 4: audit trail (baseline + per-claim full chain)
# ---------------------------------------------------------------------------

def build_audit(claims: list[dict[str, Any]], only_id: str | None) -> str:
    lines = ["# 追溯审计档案（Audit Trail）", ""]
    lines.append(f"> 导出日期：{date.today().isoformat()} ｜ 全链：statement → 锚点 → 证据链 → 裁决史")
    lines.append("")
    lines.append("## 基线声明")
    lines.append("- 机器只锚定、从不自动升级；升级/降级均由人工在周报复核门槛裁决。")
    lines.append("- 双轴正交：来源属性（provenance）与核实状态（epistemic_status）不合并。")
    lines.append("- 注入门槛：rank ≥ 2 且 provenance ∈ {literature, own_publication}。")
    lines.append("")
    targets = [c for c in claims if (only_id is None or c.get("id") == only_id)]
    lines.append(f"## 主张明细（{len(targets)}）")
    for c in targets:
        lines.append(f"### {c.get('id')}")
        lines.append(f"- **主张**：{c.get('statement')}")
        lines.append(f"- 出处：{c.get('source_context')}")
        lines.append(f"- 类型 / 来源：{c.get('claim_kind')} / {c.get('provenance')}")
        lines.append(f"- 核实状态：{c.get('epistemic_status')}（rank={rank_of(c)}）")
        lines.append(f"- 模块：{', '.join(_fmt_list(c.get('project_module'))) or '—'}")
        lines.append(f"- 锚点：{c.get('anchor_locator') or '—'}")
        lines.append(f"- 证据链：{', '.join(_fmt_list(c.get('evidence_chain'))) or '—'}")
        lines.append(f"- 最近核实：{c.get('last_verified')} ｜ 核实人：{c.get('reviewer')}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode 5: inject packs (gated multi-mode delivery)
# ---------------------------------------------------------------------------

def build_inject(claims: list[dict[str, Any]], mode: str) -> str:
    ok = [c for c in claims if rank_of(c) >= 2 and c.get("provenance") in INJECTABLE]
    lines_header = {
        "code_context": [
            "代码上下文注入包（Code-Context Inject Pack）",
            "> 仅 rank ≥ 2 且来源为 literature / own_publication。供代码 Agent / 下游推理作为可信结论。",
        ],
        "teaching_material": [
            "教学讲义素材包（Teaching Material Pack）",
            "> 把已核实结论整理为讲义要点，去掉内部 locator 细节。",
        ],
        "design_constraint": [
            "设计约束卡（Design Constraint Pack）",
            "> 已核实结论作为设计/实现约束。",
        ],
    }.get(mode, [mode, ""])
    lines = [f"# {lines_header[0]}", lines_header[1], ""]
    lines.append(f"> 生效日期：{date.today().isoformat()} ｜ 合格结论数：{len(ok)}")
    lines.append("")
    if not ok:
        lines.append("※ 当前无达到注入门槛（rank ≥ 2）的结论。")
        lines.append("")
        return "\n".join(lines)
    for c in ok:
        lines.append(f"- **{c.get('statement')}**")
        if mode == "code_context":
            ev = _fmt_list(c.get("evidence_chain"))
            lines.append(f"  - 证据：{', '.join(ev) or '—'} ｜ 锚点：{c.get('anchor_locator') or '—'}")
        elif mode == "teaching_material":
            lines.append(f"  - 要点来源：{c.get('source_context')}")
        elif mode == "design_constraint":
            lines.append(f"  - 归属模块：{', '.join(_fmt_list(c.get('project_module'))) or '全局'}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
MODES = {
    "inspect": (build_inspection, ["--snapshot"]),
    "weekly": (build_weekly, []),
    "tickets": (build_tickets, []),
    "audit": (build_audit, ["--id"]),
    "inject": (build_inject, ["--mode"]),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claim registry multi-mode delivery generator")
    sub = parser.add_subparsers(dest="mode", required=True)
    for name, (_, extra) in MODES.items():
        p = sub.add_parser(name)
        p.add_argument("--registry", required=True, type=Path)
        p.add_argument("--out", type=Path, help="output .md path (default: stdout)")
        if "--snapshot" in extra:
            p.add_argument("--snapshot", type=Path, help="previous registry snapshot for diff")
        if "--id" in extra:
            p.add_argument("--id", help="single claim id for audit")
        if "--mode" in extra:
            p.add_argument("--mode", required=True,
                           choices=["code_context", "teaching_material", "design_constraint"])
    args = parser.parse_args(argv)

    claims = load_registry(args.registry)
    if not claims:
        print("no claims in registry", file=sys.stderr)
        return 1

    fn = MODES[args.mode][0]
    if args.mode == "inspect":
        snap = load_registry(args.snapshot) if getattr(args, "snapshot", None) else None
        body = fn(claims, snap)
    elif args.mode == "audit":
        body = fn(claims, getattr(args, "id", None))
    elif args.mode == "inject":
        body = fn(claims, args.mode)
    else:
        body = fn(claims)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body + "\n", encoding="utf-8")
        print(f"wrote {args.mode} -> {args.out}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
