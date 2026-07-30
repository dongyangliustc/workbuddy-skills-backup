from pathlib import Path

from course_material_mcp.models import Chunk
from course_material_mcp.retrieval import search_sources
from course_material_mcp.store import SourceStore


def test_equal_matches_prefer_higher_priority_source(tmp_path: Path) -> None:
    store = SourceStore(tmp_path / "index.sqlite")
    store.add_chunks(
        [
            Chunk("book", "Book", "textbook", 2, "PDF p. 10", "RRKM model"),
            Chunk("slides", "Slides", "teacher_slides", 3, "PDF p. 2", "RRKM model"),
        ]
    )

    response = search_sources(store, "RRKM")

    assert response.results[0].source_id == "slides"
    assert response.results[0].priority == 3
    assert response.results[0].locator == "PDF p. 2"


def test_no_match_returns_empty_evidence_not_fabricated_text(tmp_path: Path) -> None:
    store = SourceStore(tmp_path / "index.sqlite")
    store.add_chunks([Chunk("book", "Book", "textbook", 2, "PDF p. 1", "Arrhenius")])

    response = search_sources(store, "RRKM")

    assert response.results == []
