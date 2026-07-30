from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader

from .models import Chunk
from .sources import SourceConfig


class _Page(Protocol):
    def extract_text(self) -> str: ...


class _Reader(Protocol):
    pages: list[_Page]


def normalise_text(text: str) -> str:
    printable = "".join(c for c in text if c.isprintable() or c in "\n\t")
    return re.sub(r"\s+", " ", printable).strip()


def extract_source(
    source: SourceConfig,
    source_root: Path,
    *,
    reader_factory: Callable[[str], _Reader] = PdfReader,
    text_block_lines: int = 80,
) -> Iterator[Chunk]:
    path = (source_root / source.path).resolve()
    if source_root.resolve() not in path.parents:
        raise ValueError(f"source escapes source_root: {source.path}")
    if source.format == "pdf":
        reader = reader_factory(str(path))
        for page_number, page in enumerate(reader.pages, start=1):
            text = normalise_text(page.extract_text() or "")
            if text:
                yield _chunk(source, f"PDF p. {page_number}", text)
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    for start in range(0, len(lines), text_block_lines):
        block = normalise_text("\n".join(lines[start : start + text_block_lines]))
        if block:
            first = start + 1
            last = min(start + text_block_lines, len(lines))
            yield _chunk(source, f"{source.path.as_posix()} · lines {first}-{last}", block)


def _chunk(source: SourceConfig, locator: str, text: str) -> Chunk:
    return Chunk(
        source.source_id,
        source.title,
        source.source_type,
        source.priority,
        locator,
        text,
    )
