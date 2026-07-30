from pathlib import Path

import pytest

from course_material_mcp.cli import build_index


def test_build_index_rejects_a_source_with_no_extractable_text(tmp_path: Path) -> None:
    materials = tmp_path / "materials"
    materials.mkdir()
    (materials / "empty.md").write_text("", encoding="utf-8")
    config = tmp_path / "sources.yaml"
    config.write_text(
        f"""
source_root: {materials}
sources:
  - source_id: empty
    title: Empty source
    path: empty.md
    format: markdown
    source_type: notes
    priority: 1
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no extractable text"):
        build_index(config, tmp_path / "index.sqlite")
