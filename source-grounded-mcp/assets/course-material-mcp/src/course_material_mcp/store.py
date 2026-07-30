from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path

from .models import Chunk


class SourceStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    source_id TEXT NOT NULL,
                    source_title TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    locator TEXT NOT NULL,
                    text TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM chunks")

    def add_chunks(self, chunks: Iterable[Chunk]) -> None:
        rows = [
            (
                c.source_id,
                c.source_title,
                c.source_type,
                c.priority,
                c.locator,
                c.text,
            )
            for c in chunks
        ]
        with self._connect() as connection:
            connection.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)", rows)

    def iter_chunks(self, source_type: str | None = None) -> Iterator[Chunk]:
        query = "SELECT source_id, source_title, source_type, priority, locator, text FROM chunks"
        parameters: tuple[str, ...] = ()
        if source_type is not None:
            query += " WHERE source_type = ?"
            parameters = (source_type,)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        yield from (Chunk(*row) for row in rows)

    def read_segment(self, source_id: str, locator: str) -> list[Chunk]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id, source_title, source_type, priority, locator, text
                FROM chunks WHERE source_id = ? AND locator = ?
                """,
                (source_id, locator),
            ).fetchall()
        return [Chunk(*row) for row in rows]
