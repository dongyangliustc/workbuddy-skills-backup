---
name: source-grounded-mcp
description: Use when a user wants Codex to create, install, audit, or update a local MCP for course learning or document Q&A from an approved folder, especially when answers need exact citations, source priorities, and explicit separation of source facts from model reasoning.
---

# Source-Grounded MCP

Create a real, local, read-only course-material MCP from the bundled tested starter. Complete the workflow through Codex registration; do not stop at a design document or an inventory.

## Inputs

Obtain or infer:

- absolute materials directory;
- empty project directory;
- unique Codex MCP name;
- human authority policy for each source.

Use `teacher_slides` with priority `3`, `textbook` with `2`, and `supplementary_textbook` with `1` only as a proposed default. Authority is a human decision, not a filename inference.

## Workflow

1. Run `scripts/create_source_manifest.py MATERIALS --output INVENTORY.json`. It inventories selectable-text PDF, TXT, and Markdown files and contains no authority fields.

2. If the user has not already classified every source, show a confirmation table with: file, displayed title, proposed source type, proposed priority, and extraction concern. Ask in plain language. **Do not ask the user to edit JSON or YAML.** If the user already supplied an unambiguous policy, state the proposed table as a commentary update and continue.

3. Run `scripts/scaffold_course_mcp.py PROJECT`. Refuse to overwrite a non-empty directory.

4. Read `references/evidence-contract.md`. Write `PROJECT/config/sources.yaml` yourself using the confirmed table. Use absolute `source_root`, inventory paths/formats, stable unique IDs, confirmed titles/types, and integer priorities. Do not leave placeholders.

5. From `PROJECT`, run:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -e ".[dev]"
   .venv/bin/python -m pytest -v
   .venv/bin/course-material-mcp ingest
   .venv/bin/course-material-mcp search "REPRESENTATIVE_QUERY"
   ```

   Stop and report a source that produces no extractable text; it may require OCR or conversion. Do not silently omit it.

6. Register the verified server:

   ```bash
   codex mcp add MCP_NAME -- ABSOLUTE_PROJECT/.venv/bin/course-material-mcp serve --config ABSOLUTE_PROJECT/config/sources.yaml --index ABSOLUTE_PROJECT/data/index/materials.sqlite
   codex mcp get MCP_NAME
   ```

   If the name already exists, request permission before replacing it.

7. Tell the user to restart Codex or open a new task. Give one natural-language test prompt naming the MCP and requiring `[资料事实]`, exact locators, `[模型推理]`, and `[资料不足]` when evidence is absent.

## Acceptance criteria

- Expose only `search_sources`, `read_source_segment`, and `list_source_policy`.
- Return title, type, priority, exact locator, bounded excerpt, and retrieval score.
- Prefer higher-priority sources among matching evidence.
- Never expose arbitrary filesystem, shell, write, or model-key tools.
- Keep this workflow local to Codex `stdio`; do not add web deployment instructions.
