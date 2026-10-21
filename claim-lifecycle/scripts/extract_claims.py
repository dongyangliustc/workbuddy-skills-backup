#!/usr/bin/env python3
"""Extract atomic claim cards from a self-generated knowledge document.

Portable, stdlib-only version of the claim capture step. Works against any
conversation/note document and emits a claims registry (JSON, with optional
YAML export compatible with the original project's claims.yaml).

Part of the claim-lifecycle skill. Machine screens provenance/kind only;
human review decides upgrades at the weekly gate.

Usage:
    python extract_claims.py --file notes/对话知识.md \
        --out registry/claims.json [--out-yaml registry/claims.yaml] \
        [--source-context "2026-08-27 对话"] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Lightweight classification heuristics (override after capture)
# ---------------------------------------------------------------------------

_HARD_NO_RE = re.compile(r"(^#|^>|^```|^---|^目标|^content|^来源|^建立日期|^用途)")
_HYPO_RE = re.compile(r"(是否正确|是否成立|是否能够|是否.*正确|假设|待验证|未验证|猜想|需要验证)")
_DERIV_RE = re.compile(r"(推导|等于|等价|递推|积分|展开|演算|参照|依据|由.*得到|可化为|约化为|近似为)")
_WORKFLOW_RE = re.compile(r"(应当|必须|禁止|规则|规范|流程|策略|方案|建议|门控|放置|采用|落点|存档于|继承)")


def _is_prose_candidate(line: str, is_bullet: bool) -> bool:
    """Keep only declarative bullet-style statements."""
    if not line or _HARD_NO_RE.search(line):
        return False
    if not is_bullet:
        return False
    statement = line.strip().rstrip("。；;.")
    return len(statement) >= 6 and not statement.endswith(":")


def _classify(text: str) -> str:
    if _HYPO_RE.search(text):
        return "hypothesis"
    if _DERIV_RE.search(text):
        return "derivation"
    if _WORKFLOW_RE.search(text):
        return "workflow"
    return "fact"


def extract_candidates(path: Path) -> list[str]:
    """Pull declarative bullet-style statements from a conversation doc.
    Accepts markdown bullets (-, *, +) and numbered lists."""
    candidates: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        bullet = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", raw)
        is_bullet = bool(bullet)
        line = bullet.group(1).strip() if bullet else stripped
        if _is_prose_candidate(line, is_bullet):
            candidates.append(line.rstrip("。；;."))
    return candidates


def strip_duplicates(statements: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in statements:
        key = re.sub(r"\s+", " ", s).strip()
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def build_cards(
    statements: list[str],
    source_context: str,
    reviewer: str,
    start_id: int = 1,
    prefix: str = "clm",
    provenance: str = "ideation",
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for i, statement in enumerate(statements):
        cards.append(
            {
                "id": f"{prefix}-{start_id + i:04d}",
                "statement": statement,
                "source_context": source_context,
                "claim_kind": _classify(statement),
                "provenance": provenance,
                "epistemic_status": "unverified",
                "project_module": [],
                "evidence_chain": [],
                "anchor_locator": None,
                "last_verified": date.today().isoformat(),
                "reviewer": reviewer,
            }
        )
    return cards


def next_start_id(existing: list[dict[str, Any]], prefix: str = "clm") -> int:
    numbers = []
    for c in existing:
        m = re.fullmatch(rf"{prefix}-(\d+)", str(c.get("id", "")))
        if m:
            numbers.append(int(m.group(1)))
    return (max(numbers) + 1) if numbers else 1


def load_registry(path: Path) -> list[dict[str, Any]]:
    """Load claims from .json or .yaml (project-style flat list)."""
    if not path.is_file():
        return []
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("claims", [])
    # Minimal YAML reader for project-style claims.yaml (flat `- id:` list)
    claims: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("claims:"):
            continue
        if line.startswith("  - id:"):
            if current is not None:
                claims.append(current)
            current = {"id": line.split(":", 1)[1].strip().strip('"')}
        elif current is not None and line.startswith("    "):
            key, _, value = line.strip().partition(":")
            value = value.strip()
            if key in {"project_module", "evidence_chain"} and value.startswith("["):
                current[key] = [
                    v.strip().strip('"').strip("'")
                    for v in value[1:-1].split(",")
                    if v.strip()
                ]
            elif key in {"project_module", "evidence_chain"}:
                current[key] = []
            else:
                current[key] = value.strip('"').strip("'") or None
    if current is not None:
        claims.append(current)
    return claims


def emit_yaml(claims: list[dict[str, Any]], header: list[str]) -> str:
    lines = [f"# {h}" for h in header]
    lines.append("claims:")
    for c in claims:
        lines.append(f"  - id: \"{c.get('id', '')}\"")
        lines.append(f"    statement: \"{c.get('statement', '')}\"")
        lines.append(f"    source_context: \"{c.get('source_context', '')}\"")
        lines.append(f"    claim_kind: \"{c.get('claim_kind', 'unclassified')}\"")
        lines.append(f"    provenance: \"{c.get('provenance', 'ideation')}\"")
        lines.append(f"    epistemic_status: \"{c.get('epistemic_status', 'unverified')}\"")
        mods = c.get("project_module") or []
        lines.append("    project_module: [" + ", ".join(f'"{m}"' for m in mods) + "]")
        chain = c.get("evidence_chain") or []
        lines.append("    evidence_chain: [" + ", ".join(f'"{e}"' for e in chain) + "]")
        lines.append(f"    anchor_locator: {c.get('anchor_locator') or 'null'}")
        lines.append(f"    last_verified: \"{c.get('last_verified', '')}\"")
        lines.append(f"    reviewer: \"{c.get('reviewer', '')}\"")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract atomic claim cards from a knowledge doc")
    parser.add_argument("--file", required=True, type=Path, help="source dialog/note document")
    parser.add_argument("--out", required=True, type=Path, help="output registry (.json)")
    parser.add_argument("--out-yaml", type=Path, help="optional project-style YAML export")
    parser.add_argument("--source-context", default="<auto>")
    parser.add_argument("--reviewer", default="DY")
    parser.add_argument("--provenance", default="ideation",
                        choices=["ideation", "synth_summary", "literature", "own_publication"])
    parser.add_argument("--dry-run", action="store_true", help="print without writing")
    args = parser.parse_args()

    if args.source_context == "<auto>":
        args.source_context = f"{date.today().isoformat()} 自产对话知识"

    if not args.file.is_file():
        print(f"error: source file not found: {args.file}", file=sys.stderr)
        return 2

    statements = strip_duplicates(extract_candidates(args.file))
    if not statements:
        print("warning: no bullet-style candidate claims found", file=sys.stderr)

    existing = load_registry(args.out)
    start_id = next_start_id(existing)
    cards = build_cards(statements, args.source_context, args.reviewer,
                        start_id=start_id, provenance=args.provenance)
    merged = existing + cards

    print(f"extracted {len(cards)} candidate claim(s) from {args.file.name} "
          f"(id range {cards[0]['id']}..{cards[-1]['id']})" if cards
          else "extracted 0 candidate claims")
    for c in cards:
        print(f"  [{c['claim_kind']:<12}] {c['id']} :: {c['statement'][:56]}")

    if args.dry_run:
        print("\n[DRY-RUN JSON]")
        print(json.dumps(merged, ensure_ascii=False, indent=2))
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(cards)} new claim(s) -> {args.out} (total {len(merged)})")

    if args.out_yaml:
        header = [
            f"Claim Registry (claim-lifecycle skill, extracted {date.today().isoformat()})",
            "门控：仅 literature_supported / benchmark_verified 可注入下游。",
        ]
        args.out_yaml.parent.mkdir(parents=True, exist_ok=True)
        args.out_yaml.write_text(emit_yaml(merged, header), encoding="utf-8")
        print(f"yaml export -> {args.out_yaml}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
