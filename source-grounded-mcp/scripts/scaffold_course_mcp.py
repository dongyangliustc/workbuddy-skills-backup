#!/usr/bin/env python3
"""Copy the bundled, tested local course-MCP starter into a new project."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


TEMPLATE_ROOT = Path(__file__).parents[1] / "assets" / "course-material-mcp"


def scaffold_project(destination: Path) -> Path:
    """Create a project without overwriting any existing user files."""

    target = destination.expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"destination is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        TEMPLATE_ROOT,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".DS_Store"),
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a local course-material MCP project.")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    target = scaffold_project(args.project_dir)
    print(f"Created course-material MCP project at {target}")


if __name__ == "__main__":
    main()
