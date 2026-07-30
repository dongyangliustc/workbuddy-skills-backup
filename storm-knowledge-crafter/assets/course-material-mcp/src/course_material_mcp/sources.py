from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


SUPPORTED_FORMATS = {"pdf", "txt", "markdown"}


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    title: str
    path: Path
    format: str
    source_type: str
    priority: int


@dataclass(frozen=True)
class SourceManifest:
    source_root: Path
    sources: list[SourceConfig]

    def public_policy(self) -> list[dict[str, Any]]:
        return [{**asdict(source), "path": source.path.as_posix()} for source in self.sources]


def load_manifest(config_path: Path) -> SourceManifest:
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("source configuration must be a mapping")
    root_value = payload.get("source_root")
    if not isinstance(root_value, str) or not root_value.strip():
        raise ValueError("source_root must be a non-empty path")
    source_root = Path(root_value).expanduser()
    if not source_root.is_absolute():
        source_root = config_path.parent / source_root
    items = payload.get("sources")
    if not isinstance(items, list) or not items:
        raise ValueError("sources must contain at least one reviewed source")

    sources: list[SourceConfig] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each source must be a mapping")
        source_id = _string(item, "source_id")
        if source_id in seen:
            raise ValueError(f"duplicate source_id: {source_id}")
        seen.add(source_id)
        relative_path = Path(_string(item, "path"))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"source path must stay inside source_root: {relative_path}")
        source_format = _string(item, "format")
        if source_format not in SUPPORTED_FORMATS:
            raise ValueError(f"unsupported source format: {source_format}")
        priority = item.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
            raise ValueError("priority must be a positive integer")
        sources.append(
            SourceConfig(
                source_id=source_id,
                title=_string(item, "title"),
                path=relative_path,
                format=source_format,
                source_type=_string(item, "source_type"),
                priority=priority,
            )
        )
    return SourceManifest(source_root, sources)


def _string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()
