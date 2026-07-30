#!/usr/bin/env python3
"""Create a review-first allowlist manifest for a local source directory."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SUPPORTED_SUFFIXES = {".md": "markdown", ".pdf": "pdf", ".txt": "txt"}


def build_manifest(source_root: Path) -> dict[str, Any]:
    """Inventory directly citable source formats without assigning authority."""

    root = source_root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"source directory does not exist: {root}")

    sources: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for path in sorted(root.rglob("*")):
        relative_path = path.relative_to(root)
        if (
            not path.is_file()
            or any(part.startswith(".") for part in relative_path.parts)
        ):
            continue
        source_format = SUPPORTED_SUFFIXES.get(path.suffix.casefold())
        if source_format is None:
            continue
        source_id = _unique_source_id(path.stem, used_ids)
        sources.append(
            {
                "source_id": source_id,
                "title": path.name,
                "path": relative_path.as_posix(),
                "format": source_format,
            }
        )
    return {"source_root": str(root), "sources": sources}


def _unique_source_id(stem: str, used_ids: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", stem.casefold()).strip("-") or "source"
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an editable, review-first manifest for local MCP sources."
    )
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = build_manifest(args.source_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(manifest['sources'])} unclassified sources to {args.output}")


if __name__ == "__main__":
    main()
