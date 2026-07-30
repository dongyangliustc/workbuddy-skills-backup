from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    source_id: str
    source_title: str
    source_type: str
    priority: int
    locator: str
    text: str
