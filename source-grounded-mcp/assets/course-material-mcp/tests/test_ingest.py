from pathlib import Path

from course_material_mcp.ingest import extract_source
from course_material_mcp.sources import SourceConfig


class FakePage:
    def __init__(self, text: str):
        self.text = text

    def extract_text(self) -> str:
        return self.text


class FakeReader:
    def __init__(self, _: str):
        self.pages = [FakePage("RRKM page one"), FakePage("second page")]


def test_pdf_chunks_have_exact_one_based_page_locators(tmp_path: Path) -> None:
    source = SourceConfig("book", "Book", Path("book.pdf"), "pdf", "textbook", 2)

    chunks = list(extract_source(source, tmp_path, reader_factory=FakeReader))

    assert [chunk.locator for chunk in chunks] == ["PDF p. 1", "PDF p. 2"]


def test_text_chunks_have_line_range_locators(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("first\nsecond\nthird\n", encoding="utf-8")
    source = SourceConfig("notes", "Notes", Path("notes.md"), "markdown", "notes", 1)

    chunks = list(extract_source(source, tmp_path, text_block_lines=2))

    assert [chunk.locator for chunk in chunks] == ["notes.md · lines 1-2", "notes.md · lines 3-3"]
