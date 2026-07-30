# Evidence and configuration contract

## Final source configuration

Codex writes this file after the user confirms the source table. The user does not edit it manually.

```yaml
source_root: "/absolute/path/to/course-materials"
sources:
  - source_id: "course-slides"
    title: "课程教师课件"
    path: "slides/course-slides.pdf"
    format: "pdf"
    source_type: "teacher_slides"
    priority: 3
  - source_id: "required-textbook"
    title: "课程指定教材"
    path: "books/required-textbook.pdf"
    format: "pdf"
    source_type: "textbook"
    priority: 2
  - source_id: "supplementary-notes"
    title: "补充讲义"
    path: "notes/supplementary.md"
    format: "markdown"
    source_type: "supplementary_textbook"
    priority: 1
```

Required invariants:

- `source_root` is the approved absolute materials directory.
- `path` is relative to `source_root` and cannot contain `..`.
- `source_id` is stable, unique, and non-empty.
- `format` is `pdf`, `txt`, or `markdown`.
- `source_type` is a non-empty human-approved label.
- `priority` is a positive integer; a larger number is more authoritative.
- Every configured file must exist and yield extractable text.

## Evidence interface

The bundled MCP exposes exactly:

| Tool | Purpose |
| --- | --- |
| `search_sources(query, source_type?, top_k?)` | Return ranked, citable excerpts. |
| `read_source_segment(source_id, locator)` | Re-read the exact indexed segment used for a citation. |
| `list_source_policy()` | Return reviewed source titles, types, paths, and priorities. |

Every search result includes `source_id`, `source_title`, `source_type`, `priority`, `locator`, `excerpt`, and `score`.

## Host answer contract

Use retrieved material in this shape:

```markdown
## 基于指定资料
- [资料事实] ……（《来源标题》，PDF p. N / 文件行号）

## 推理或教学解释
- [模型推理] ……；这是基于资料的解释或推导，不是资料原文结论。

## 资料不足或冲突（如适用）
- [资料不足] 当前指定资料未直接支持该结论。
```

Rules:

- Retrieve before presenting a claim as source-backed.
- Cite the returned title and exact locator; never invent either.
- Label synthesis, derivation, analogy, calculation, or teaching explanation not stated in the excerpt as `[模型推理]`.
- If retrieval returns no direct evidence, use `[资料不足]` rather than general model knowledge disguised as a source fact.
- If approved sources conflict, cite both and state the priority policy; do not silently merge them.
