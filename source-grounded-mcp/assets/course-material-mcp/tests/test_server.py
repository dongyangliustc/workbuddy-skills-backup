from pathlib import Path

from course_material_mcp.server import create_server


def test_server_exposes_only_read_only_evidence_tools(tmp_path: Path) -> None:
    config = tmp_path / "sources.yaml"
    config.write_text(
        """
source_root: /tmp/materials
sources:
  - source_id: book
    title: Book
    path: book.pdf
    format: pdf
    source_type: textbook
    priority: 2
""".strip()
        + "\n",
        encoding="utf-8",
    )

    server = create_server(index_path=tmp_path / "index.sqlite", config_path=config)

    assert set(server._tool_manager._tools) == {
        "search_sources",
        "read_source_segment",
        "list_source_policy",
    }
    assert "[资料事实]" in server.instructions
    assert "[模型推理]" in server.instructions
    assert "[资料不足]" in server.instructions
