# Course Material MCP

这是由 `source-grounded-mcp` Skill 生成的本地只读课程资料 MCP。来源策略保存在 `config/sources.yaml`；使用者不需要手工编辑该文件，应让 Codex 根据已确认的资料表维护它。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -v
.venv/bin/course-material-mcp ingest
.venv/bin/course-material-mcp search "测试关键词"
```

注册到 Codex：

```bash
codex mcp add MCP_NAME -- \
  /ABSOLUTE/PROJECT/.venv/bin/course-material-mcp \
  serve \
  --config /ABSOLUTE/PROJECT/config/sources.yaml \
  --index /ABSOLUTE/PROJECT/data/index/materials.sqlite
```

The generated project is a local, read-only MCP. Ask Codex to maintain the reviewed source policy, rebuild the index after source changes, run the test suite, and verify a representative retrieval before registration.
