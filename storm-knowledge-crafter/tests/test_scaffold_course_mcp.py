"""Tests for deterministic course-MCP project scaffolding."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "scaffold_course_mcp.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("scaffold_course_mcp", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scaffold_creates_a_runnable_project_layout(tmp_path: Path) -> None:
    module = _load_module()
    destination = tmp_path / "course-mcp"

    module.scaffold_project(destination)

    assert (destination / "README.md").is_file()
    assert (destination / "pyproject.toml").is_file()
    assert (destination / "config" / "sources.yaml").is_file()
    assert (destination / "src" / "course_material_mcp" / "server.py").is_file()
    assert (destination / "tests" / "test_server.py").is_file()


def test_scaffold_refuses_to_overwrite_a_nonempty_directory(tmp_path: Path) -> None:
    module = _load_module()
    destination = tmp_path / "course-mcp"
    destination.mkdir()
    (destination / "user-file.txt").write_text("keep me", encoding="utf-8")

    with pytest.raises(FileExistsError, match="not empty"):
        module.scaffold_project(destination)

    assert (destination / "user-file.txt").read_text(encoding="utf-8") == "keep me"
