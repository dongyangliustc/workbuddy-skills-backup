"""Static contract tests for the human and agent-facing workflow."""

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_workflow_has_no_manual_placeholders_or_web_deployment() -> None:
    text = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ["SKILL.md", "README.md", "references/evidence-contract.md"]
    )

    for forbidden in ["__SET_ME__", "Streamable HTTP", "网页版部署", "web product"]:
        assert forbidden not in text


def test_readme_documents_only_real_bundled_commands() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "scripts/scaffold_course_mcp.py" in readme
    assert "scripts/create_source_manifest.py" in readme
    assert "codex mcp add" in readme
    assert "用户无需手工编辑 JSON 或 YAML" in readme
    assert readme.index("## 中文说明") < readme.index("## English Guide")


def test_skill_requires_table_confirmation_not_config_editing() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "Do not ask the user to edit JSON or YAML" in skill
    assert "confirmation table" in skill
    assert "scaffold_course_mcp.py" in skill


def test_readme_uses_formal_project_documentation_tone() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for colloquial_phrase in [
        "同学",
        "告诉 Codex",
        "如果同学不知道",
        "实际发生了什么",
        "这个 Skill",
        "自带真实",
        "这张表只是",
    ]:
        assert colloquial_phrase not in readme
    assert "用户职责" in readme
    assert "自动化流程" in readme
