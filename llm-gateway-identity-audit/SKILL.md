---
name: llm-gateway-identity-audit
description: 审计 OpenAI 兼容 / LLM 网关（LiteLLM、One-API、New-API 等）是否存在"请求 X 模型、实际由 Y 模型作答"。当用户怀疑自定义模型被静默替换、想确认实际作答模型、排查网关别名错配或 fallback 降级时使用。触发词：模型身份核对、静默降级、别名错配、实际用的哪个模型、换模型、fallback 审计、custom model 核验。
agent_created: true
---

# LLM 网关模型身份审计

## 何时用

- 用户怀疑"我配置的是 A 模型，但回答像是 B 模型"
- 排查 400 / 异常输出是否与网关层换模型有关
- 新增自定义模型后做上线前核验
- 定期巡检 `~/.workbuddy/models.json` 里的自定义模型

## 核心原理（关键，不要靠猜）

**JSON body 的 `model` 字段只回显请求别名，永远不可信。** 权威鉴别只能靠响应头。

LiteLLM 型网关会暴露：

| 响应头 | 含义 |
|---|---|
| `x-litellm-model-name` | **实际提供服务的上游模型**（权威） |
| `x-litellm-model-api-base` | 实际上游端点（常为 K8s 服务名，可反推厂商） |
| `x-litellm-attempted-fallbacks` | 本次触发几次 fallback，>0 即已换模型 |
| `x-litellm-attempted-retries` | 重试次数 |
| `x-litellm-key-rpm-limit` | 当前 key 的每分钟限额 |

其它网关（One-API / New-API）头名不同，先发一次请求把全部响应头打出来再找等价字段。

## 操作步骤

1. **取全量别名**：`GET {base}/v1/models`，用 `models.json` 里的 key。
2. **跑检测器**（已内置限速与分级）：
   ```bash
   python scripts/check_who_answers.py             # 审计 models.json 全部自定义模型
   python scripts/check_who_answers.py --all       # 审计网关全部别名
   python scripts/check_who_answers.py <别名> ...   # 指定别名
   ```
   退出码：`0` 无发现 / `1` 有发现 / `2` 执行出错。
3. **判定分级**：
   - `FALLBACK_FIRED` P0 —— 触发 fallback，已由备用模型作答
   - `ALIAS_MISMATCH` P0 —— 跨厂商/跨系列替换
   - `VARIANT_COLLAPSE` P1 —— 同系列但语义变体被抹平（如 `-reasoner` 与 `-chat` 同部署）
   - `VERSION_DRIFT` P2 —— 版本漂移
   - `NO_HEADER` P2 —— 无 LiteLLM 头（可能经本地代理），无法鉴别
4. **验证 fallback 是否会导致静默降级**（可选但推荐）：
   只有当「主模型拒绝某参数 **且** 备用模型接受该参数」时才会返回 200。做法：对主模型跑参数矩阵（
   `response_format` / `logprobs` / `seed` / `stop` / `n` / `penalty` / `temperature` / `max_tokens`
   / `tools` / `tool_choice` / `parallel_tool_calls` / `reasoning_effort` / `thinking` / `verbosity`
   / `service_tier` / `prompt_cache_key` / `stream_options` / `modalities`），把被拒参数再拿去打备用模型。

## 必踩的坑

- **限速**：网关常见 20 rpm/key。批量探测必须 3.2–3.5 s/请求并做 429 退避，否则 429/超时会伪装成"模型不可用"的假结论。首轮若已打爆限额，等 1 分钟或换 key 重测。
- **不要信模型自报身份**：实测 DeepSeek 系自报 "GPT-5.1"/"GPT-5.2"。自报只能作旁证。
- **组内负载均衡 ≠ 换模型**：同别名可能落到 `qwen38-27b` 与 `qwen38-27b-pd`（PD 分离部署），权重相同，只是延迟/行为有非确定性，不要误报为 P0。
- **本地代理会吃掉响应头**：若 url 指向 `127.0.0.1:*`，先确认代理是否透传上游头，否则得到 `NO_HEADER`。
- **分轮落盘**：审计脚本跑到一半崩溃很常见（超时），逐条 append 写文件，不要最后一次性 dump。

## 产物建议

- 报告：别名 → 真实上游完整映射表 + 分级结论 + 方法学局限（哪些路径未能复现要诚实标注）
- `alias_upstream_map.json`：结构化映射，便于下次 diff
- 把检测器留在工作区定期跑

## 相关技能

- `llm-gateway-400-debug`：自定义模型报 400 / 间歇性错误的根因诊断
  （本地捕获代理 + 结构化分析 + 同字节重放验证）。两者常配合使用：
  先用本技能确认"谁在作答"，再用 400-debug 定位"为什么报错"。
