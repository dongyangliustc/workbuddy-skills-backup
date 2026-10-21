#!/usr/bin/env python3
"""Anchor claims against an evidence index (conservative, portable).

Generalized version of the epistemic anchor step. Machine only *screens*
(never upgrades): it proposes a status for every claim based on external
(non-ideation, non-self) evidence hits.

Anchoring rules (conservative three-level):
  - skip ideation-provenance chunks and the claim's own source document
    (prevents self-anchoring distortion);
  - literature_supported  <- a T0/T0* chunk literally contains the claim (score >= threshold)
  - consistent            <- some non-ideation anchor exists but match is weak
  - unverified            <- no external anchor found

The index backend is pluggable via an adapter function. The bundled implemen-
tation calls the course_material_mcp retrieval stack if importable (spherical
wave project), otherwise falls back to a token-overlap scorer over a local
text corpus directory.

Usage (MCP index):
    python anchor_claims.py --registry claims.json --index /path/materials.sqlite

Usage (portable corpus):
    python anchor_claims.py --registry claims.json --corpus ./papers_txt \
        [--score-threshold 100] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

ScoreThreshold = 100.0


# ---------------------------------------------------------------------------
# Registry helpers (JSON canonical; YAML loader reused from extract_claims)
# ---------------------------------------------------------------------------

def load_registry(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("claims", [])
    return _load_flatyaml(path)


def _load_flatyaml(path: Path) -> list[dict[str, Any]]:
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


def save_registry(path: Path, claims: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Evidence adapter: one hit = dict(source_id, source_title, provenance,
#                                  epistemic_status, locator, score)
# ---------------------------------------------------------------------------

def _mcp_backend(index_path: Path) -> Optional[Callable[[str], list[dict[str, Any]]]]:
    """Return a search callable backed by course_material_mcp, or None."""
    try:
        from course_material_mcp.retrieval import search_sources  # type: ignore
        from course_material_mcp.store import SourceStore  # type: ignore
    except Exception:
        return None

    store = SourceStore(index_path)

    def search(statement: str) -> list[dict[str, Any]]:
        resp = search_sources(store, statement, top_k=5)
        return [
            {
                "source_id": r.source_id,
                "source_title": r.source_title,
                "provenance": r.provenance,
                "epistemic_status": r.epistemic_status,
                "locator": r.locator,
                "score": float(getattr(r, "score", 0) or 0),
            }
            for r in resp.results
        ]

    return search


def _corpus_backend(corpus_dir: Path) -> Callable[[str], list[dict[str, Any]]]:
    """Token-overlap scorer over a flat text corpus (portable fallback).

    Each *.txt / *.md file is treated as a document; score = summed IDF-ish
    overlap of query tokens present in the document. Deterministic and
    dependency-free; intended for corpora of extracted primary-source text.
    """
    docs: list[dict[str, Any]] = []
    for p in sorted(corpus_dir.glob("*.txt")) + sorted(corpus_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        docs.append({"source_id": p.stem, "locator": str(p), "text": text})

    token_re = re.compile(r"[\w\u4e00-\u9fff]+")
    doc_tokens = [{t for t in token_re.findall(d["text"])} for d in docs]

    def search(statement: str) -> list[dict[str, Any]]:
        query = {t for t in token_re.findall(statement) if len(t) > 1}
        if not query:
            return []
        results = []
        for d, tokens in zip(docs, doc_tokens):
            overlap = query & tokens
            if not overlap:
                continue
            score = float(len(overlap)) * 100.0 / max(1.0, len(query) ** 0.5)
            results.append(
                {
                    "source_id": d["source_id"],
                    "source_title": d["source_id"],
                    "provenance": "literature",
                    "epistemic_status": "literature_supported",
                    "locator": d["locator"],
                    "score": score,
                }
            )
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:5]

    return search


def build_backend(args: argparse.Namespace) -> Callable[[str], list[dict[str, Any]]]:
    if args.index is not None:
        backend = _mcp_backend(args.index)
        if backend is None:
            print("warning: course_material_mcp not importable; "
                  "falling back to --corpus token scorer (none given)",
                  file=sys.stderr)
            if args.corpus is None:
                raise SystemExit("error: no usable backend (need --index with MCP venv "
                                 "or --corpus dir)")
        else:
            return backend
    if args.corpus is not None:
        return _corpus_backend(args.corpus)
    raise SystemExit("error: specify --index or --corpus")


def anchor_one(search: Callable, claim: dict[str, Any],
               own_source: str, threshold: float) -> tuple[str, list[dict[str, Any]]]:
    """Return (proposed_status, evidence). Machine only screens: never returns
    a status above literature_supported and hinges on external hits only."""
    hits = search(claim.get("statement", ""))
    external = [
        h for h in hits
        if h.get("provenance") != "ideation"
        and h.get("source_id") != own_source
    ]
    if not external:
        return "unverified", []
    best = external[0]
    if best["provenance"] in {"literature", "own_publication"} and best["score"] >= threshold:
        return "literature_supported", external
    if best["score"] > 0:
        return "consistent", external
    return "unverified", external


def main() -> int:
    parser = argparse.ArgumentParser(description="Anchor claims against evidence (conservative)")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--index", type=Path, help="MCP sqlite index (uses course_material_mcp)")
    parser.add_argument("--corpus", type=Path, help="portable text corpus directory")
    parser.add_argument("--own-source", default=None,
                        help="source_id to exclude (the claims' origin doc)")
    parser.add_argument("--score-threshold", type=float, default=ScoreThreshold)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-epistemic", default=None,
                        choices=["unverified", "consistent", "literature_supported",
                                 "benchmark_verified"])
    args = parser.parse_args()

    claims = load_registry(args.registry)
    if not claims:
        print("no claims in registry")
        return 0
    search = build_backend(args)

    rank = {"refuted": -2, "superseded": -2, "stale": -2, "conflicting": -1,
            "unverified": 0, "consistent": 1, "literature_supported": 2,
            "benchmark_verified": 3}

    changed = 0
    for c in claims:
        if args.min_epistemic and rank.get(c.get("epistemic_status"), 0) < rank.get(args.min_epistemic, 0):
            continue  # gate: only inspect claims below the floor
        proposed, evidence = anchor_one(search, c, args.own_source or c.get("source_id", ""),
                                        args.score_threshold)
        c["_proposed"] = proposed
        c["_evidence"] = evidence
        if proposed != c.get("epistemic_status"):
            changed += 1

    for c in claims:
        if "_proposed" not in c:
            continue
        ev = c["_evidence"]
        print(f"[{c.get('epistemic_status','?')} -> {c['_proposed']:<20}] "
              f"{c.get('id')}: {c.get('statement','')[:50]}")
        top = ev[:2]
        for e in top:
            print(f"    anchor: {e['source_id']} ({e['provenance']}, score={e['score']:.0f})")

    if args.dry_run:
        return 0

    for c in claims:
        if "_proposed" in c:
            c["epistemic_status"] = c.pop("_proposed")  # machine-screened write-back
            if c.get("_evidence"):
                c["evidence_chain"] = [e["source_id"] for e in c["_evidence"]][:8]
                c["anchor_locator"] = c["_evidence"][0]["locator"]
            c.pop("_evidence", None)

    save_registry(args.registry, claims)
    print(f"\nanchored {len(claims)} claim(s); {changed} status change(s) proposed -> {args.registry}")
    print("NOTE: machine ONLY screens. Human review at the weekly gate may upgrade "
          "or downgrade before any downstream injection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
