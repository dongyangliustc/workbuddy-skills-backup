from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .retrieval import search_sources as retrieve
from .sources import load_manifest
from .store import SourceStore


INSTRUCTIONS = (
    "Call search_sources before presenting a claim as supported by the approved materials. "
    "Write retrieved claims under [资料事实] and cite source_title plus the exact locator. "
    "Put any derivation, synthesis, analogy, or teaching explanation not stated in the "
    "retrieved passage under [模型推理]. If the tools return no direct evidence, write "
    "[资料不足] and do not invent a source or locator. Show conflicts instead of silently "
    "merging them."
)


def create_server(*, index_path: Path, config_path: Path) -> FastMCP:
    store = SourceStore(index_path)
    manifest = load_manifest(config_path)
    mcp = FastMCP("Source-Grounded Course Materials", instructions=INSTRUCTIONS)

    @mcp.tool()
    def search_sources(
        query: str,
        source_type: str | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Search citable excerpts from the reviewed local source policy."""

        response = retrieve(store, query, source_type=source_type, top_k=top_k)
        return {"query": response.query, "results": [asdict(r) for r in response.results]}

    @mcp.tool()
    def read_source_segment(source_id: str, locator: str) -> dict[str, Any]:
        """Read the exact indexed segment identified by source and locator."""

        return {"results": [asdict(c) for c in store.read_segment(source_id, locator)]}

    @mcp.tool()
    def list_source_policy() -> dict[str, Any]:
        """List reviewed sources and priorities without exposing arbitrary filesystem access."""

        return {"sources": manifest.public_policy()}

    return mcp
