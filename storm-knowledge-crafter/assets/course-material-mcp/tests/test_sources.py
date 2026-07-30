from pathlib import Path

import pytest

from course_material_mcp.sources import load_manifest


def test_load_manifest_preserves_reviewed_source_policy(tmp_path: Path) -> None:
    config = tmp_path / "sources.yaml"
    config.write_text(
        """
source_root: /tmp/materials
sources:
  - source_id: lecture
    title: Course lecture
    path: lecture.pdf
    format: pdf
    source_type: teacher_slides
    priority: 3
""".strip()
        + "\n",
        encoding="utf-8",
    )

    manifest = load_manifest(config)

    assert manifest.source_root == Path("/tmp/materials")
    assert manifest.sources[0].source_type == "teacher_slides"
    assert manifest.sources[0].priority == 3


def test_load_manifest_rejects_empty_source_list(tmp_path: Path) -> None:
    config = tmp_path / "sources.yaml"
    config.write_text("source_root: /tmp/materials\nsources: []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="at least one reviewed source"):
        load_manifest(config)
