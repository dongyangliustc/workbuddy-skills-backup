from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .ingest import extract_source
from .retrieval import search_sources
from .server import create_server
from .sources import load_manifest
from .store import SourceStore


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "sources.yaml"
DEFAULT_INDEX = ROOT / "data" / "index" / "materials.sqlite"


def build_index(config_path: Path, index_path: Path) -> int:
    manifest = load_manifest(config_path)
    chunks = []
    for source in manifest.sources:
        path = manifest.source_root / source.path
        if not path.is_file():
            raise FileNotFoundError(f"reviewed source does not exist: {path}")
        source_chunks = list(extract_source(source, manifest.source_root))
        if not source_chunks:
            raise ValueError(
                f"source has no extractable text; OCR or conversion may be required: {path}"
            )
        chunks.extend(source_chunks)
    store = SourceStore(index_path)
    store.clear()
    store.add_chunks(chunks)
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only course-material MCP")
    subcommands = parser.add_subparsers(dest="command", required=True)

    ingest = subcommands.add_parser("ingest", help="rebuild the local evidence index")
    ingest.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ingest.add_argument("--index", type=Path, default=DEFAULT_INDEX)

    search = subcommands.add_parser("search", help="search indexed evidence")
    search.add_argument("query")
    search.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    search.add_argument("--source-type")
    search.add_argument("--top-k", type=int, default=5)

    serve = subcommands.add_parser("serve", help="serve MCP over local stdio")
    serve.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    serve.add_argument("--index", type=Path, default=DEFAULT_INDEX)

    args = parser.parse_args()
    if args.command == "ingest":
        count = build_index(args.config, args.index)
        print(json.dumps({"indexed_chunks": count, "index": str(args.index)}))
    elif args.command == "search":
        response = search_sources(
            SourceStore(args.index),
            args.query,
            source_type=args.source_type,
            top_k=args.top_k,
        )
        print(json.dumps(asdict(response), ensure_ascii=False, indent=2))
    else:
        create_server(index_path=args.index, config_path=args.config).run(transport="stdio")


if __name__ == "__main__":
    main()
