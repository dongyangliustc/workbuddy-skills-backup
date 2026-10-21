#!/usr/bin/env python3
"""Validate a claims registry for the claim-lifecycle skill (portable).

Checks required fields, controlled-vocabulary values, epistemic rank vs
claim_kind coherence, evidence/anchor presence, and id uniqueness.

Usage:
    python validate_registry.py registry/claims.json [--fail-on-error]
    python validate_registry.py registry/claims.yaml
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

RANK = {"refuted": -2, "superseded": -2, "stale": -2, "conflicting": -1,
        "unverified": 0, "consistent": 1, "literature_supported": 2,
        "benchmark_verified": 3}
KINDS = {"fact", "derivation", "hypothesis", "workflow"}
PROV = {"literature", "own_publication", "synth_summary", "ideation"}
ID_RE = re.compile(r"^clm-\d{4}$")


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SystemExit(f"error: registry not found: {path}")
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("claims", [])
    # minimal flat-yaml loader (shared with extract/anchor)
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
            if key in {"project_module", "evidence_chain"}:
                current[key] = [v.strip().strip('"').strip("'")
                                for v in value[1:-1].split(",") if v.strip()] \
                    if value.startswith("[") else []
            else:
                current[key] = value.strip('"').strip("'") or None
    if current is not None:
        claims.append(current)
    return claims


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a claims registry")
    parser.add_argument("registry", type=Path)
    parser.add_argument("--fail-on-error", action="store_true",
                        help="exit non-zero on any error (for CI)")
    args = parser.parse_args()

    claims = _load(args.registry)
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for i, c in enumerate(claims, 1):
        cid = c.get("id", f"<row {i}>")
        if not re.fullmatch(ID_RE, str(cid)):
            errors.append(f"{cid}: id 必须符合 clm-XXXX")
        if cid in seen:
            errors.append(f"{cid}: id 重复")
        seen.add(cid)
        for field in ("statement", "source_context", "last_verified", "reviewer"):
            if not c.get(field):
                errors.append(f"{cid}: 缺必填字段 {field}")
        kind = c.get("claim_kind")
        if kind not in KINDS:
            errors.append(f"{cid}: 非法 claim_kind {kind!r}")
        prov = c.get("provenance")
        if prov not in PROV:
            errors.append(f"{cid}: 非法 provenance {prov!r}")
        st = c.get("epistemic_status")
        if st not in RANK:
            errors.append(f"{cid}: 非法 epistemic_status {st!r}")
            continue
        # coherence: hypothesis/workflow must not reach injectable rank
        if kind in {"hypothesis", "workflow"} and RANK[st] >= 2:
            errors.append(f"{cid}: {kind} 类被标记为 {st}（rank≥2），违反分轨规则")
        # injectable claims must carry evidence + anchor
        if RANK[st] >= 2:
            if not c.get("evidence_chain"):
                warnings.append(f"{cid}: rank≥2 但证据链为空，建议补齐")
            if not c.get("anchor_locator"):
                warnings.append(f"{cid}: rank≥2 但锚点缺失，建议补齐")
        else:
            if c.get("provenance") not in {"ideation", "synth_summary"}:
                warnings.append(f"{cid}: 低可信主张但来源为 {prov}，请复核来源标注")

    print(f"registry: {args.registry} ｜ claims: {len(claims)}")
    print(f"errors: {len(errors)} ｜ warnings: {len(warnings)}")
    for e in errors:
        print(f"  [ERROR] {e}")
    for w in warnings:
        print(f"  [WARN ] {w}")
    if errors:
        print("\nfailed validation")
        return 1 if args.fail_on_error else 0
    print("\nvalidation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
