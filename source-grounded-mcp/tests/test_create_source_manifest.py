"""Regression tests for the portable source-manifest generator."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "create_source_manifest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("create_source_manifest", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_manifest_recurses_and_keeps_only_supported_files(tmp_path: Path) -> None:
    source_root = tmp_path / "materials"
    (source_root / "nested").mkdir(parents=True)
    (source_root / ".cache").mkdir()
    (source_root / "lecture notes.pdf").write_bytes(b"%PDF")
    (source_root / "nested" / "kinetics.txt").write_text("k = A exp(-Ea/RT)")
    (source_root / ".cache" / "dependency.txt").write_text("must not be a source")
    (source_root / "figure.png").write_bytes(b"PNG")

    module = _load_module()

    manifest = module.build_manifest(source_root)

    assert manifest["source_root"] == str(source_root.resolve())
    assert [item["path"] for item in manifest["sources"]] == [
        "lecture notes.pdf",
        "nested/kinetics.txt",
    ]
    assert [item["format"] for item in manifest["sources"]] == ["pdf", "txt"]
    assert all("priority" not in item for item in manifest["sources"])
    assert all("source_type" not in item for item in manifest["sources"])


def test_build_manifest_uses_stable_unique_ids_for_non_ascii_names(tmp_path: Path) -> None:
    source_root = tmp_path / "materials"
    source_root.mkdir()
    (source_root / "教材.pdf").write_bytes(b"%PDF")
    (source_root / "课件.pdf").write_bytes(b"%PDF")

    module = _load_module()

    manifest = module.build_manifest(source_root)

    assert [item["source_id"] for item in manifest["sources"]] == ["source", "source-2"]
