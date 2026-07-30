from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Chunk
from .store import SourceStore


_ASCII = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_CJK = re.compile(r"[\u4e00-\u9fff]")


@dataclass(frozen=True)
class SearchResult:
    source_id: str
    source_title: str
    source_type: str
    priority: int
    locator: str
    excerpt: str
    score: float


@dataclass(frozen=True)
class SearchResponse:
    query: str
    results: list[SearchResult]


def search_sources(
    store: SourceStore,
    query: str,
    *,
    source_type: str | None = None,
    top_k: int = 5,
) -> SearchResponse:
    clean_query = " ".join(query.split())
    if not clean_query:
        raise ValueError("query must not be empty")
    limit = min(max(top_k, 1), 10)
    ranked = [
        (score, chunk)
        for chunk in store.iter_chunks(source_type)
        if (score := _score(clean_query, chunk.text)) > 0
    ]
    ranked.sort(key=lambda item: (item[1].priority, item[0]), reverse=True)
    return SearchResponse(
        clean_query,
        [_result(chunk, score, clean_query) for score, chunk in ranked[:limit]],
    )


def _score(query: str, text: str) -> float:
    query_folded = query.casefold()
    text_folded = text.casefold()
    score = 100.0 if query_folded in text_folded else 0.0
    for token in _ASCII.findall(query_folded):
        if token in text_folded:
            score += 10.0
    cjk = "".join(_CJK.findall(query))
    for start in range(max(len(cjk) - 1, 0)):
        if cjk[start : start + 2] in text:
            score += 1.0
    return score


def _result(chunk: Chunk, score: float, query: str) -> SearchResult:
    return SearchResult(
        chunk.source_id,
        chunk.source_title,
        chunk.source_type,
        chunk.priority,
        chunk.locator,
        _excerpt(chunk.text, query, 900),
        score,
    )


def _excerpt(text: str, query: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    index = text.casefold().find(query.casefold())
    if index < 0:
        for token in _ASCII.findall(query.casefold()):
            index = text.casefold().find(token)
            if index >= 0:
                break
    if index < 0:
        return text[: limit - 3] + "..."
    start = max(index - limit // 3, 0)
    end = min(start + limit - 3, len(text))
    start = max(end - (limit - 3), 0)
    return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")
