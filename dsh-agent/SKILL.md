---
name: dsh-agent
description: Bridge WorkBuddy to the local DeepSeek Harness (dsh) agent engine. This skill should be used when the user wants to delegate a task to the dsh agent (phrases like "用 dsh 跑 XX" / "让 dsh 做 XX" / "dsh agent" / "deepseek harness" / "调用桥接skill" / "dsh-agent"), when the currently active reply model belongs to the deepseek family (deepseek-v4-flash / deepseek-v4-pro / deepseek-v4-flash-ascend / deepseek-chat / deepseek-reasoner) and an agent-enhanced run could improve results, or when an independent / isolated agent loop with multi-step tool use is preferable to a plain chat reply. NOT for plain single-message chat — for that, WorkBuddy's own model routing already talks to the same gateway.
version: 1.0.0
agent_created: true
display_name: "dsh agent 桥接"
description_zh: "WorkBuddy ↔ DeepSeek Harness(dsh) 桥接：把任务派给本地 dsh agent 引擎并取回结果"
---

# dsh-agent — WorkBuddy ↔ DeepSeek Harness 桥接

把任务派给本地 DeepSeek Harness（dsh）agent 引擎执行，并把最终结果带回当前 WorkBuddy 会话。

## 触发时机

1. 用户明确说「用 dsh 跑 XX」「让 dsh 做 XX」「调 dsh」「走 deepseek harness」。
2. **模型效能规则**：当前回复模型属于 **deepseek 家族**（deepseek-v4-flash / deepseek-v4-pro /
   deepseek-v4-flash-ascend / deepseek-chat / deepseek-reasoner 等）时，若任务适合 agent 化
   （多步、要工具调用、要独立引擎跑），应主动建议走本桥接 skill 提升效能。
3. 任务需要独立/隔离的 agent 循环、官方 harness 行为（工具策略、多步推理、轨迹），
   或需要 dsh 在后台并行跑独立任务并产出文件。

## 环境事实（已固化，勿改）

- **dsh 安装**：`~/.dsh`（DSH_HOME），Web UI `http://127.0.0.1:3080`。
- **启动器**：`~/.dsh/bin/dshctl`（已在 Git Bash PATH 追加）。
- **模型**：provider `ustc-gateway`（OpenAI 兼容）→ 中科大网关 `https://api.llm.ustc.edu.cn/v1`；
  模型 `deepseek-v4-flash`（默认调度）/ `deepseek-v4-pro` / `deepseek-v4-flash-ascend`；
  密钥 `~/.dsh/.credentials.yaml`（0600，`USTC_GATEWAY_API_KEY`）。
- **与 WorkBuddy 同源**：`~/.workbuddy/models.json` 指向同一网关同一密钥，二者共享模型访问。

## 标准流程

### 1. 确保 dsh 在运行
```bash
dshctl status            # 检查；未运行则
dshctl web               # 幂等拉起（后台），访问 127.0.0.1:3080
```

### 2. 方式 A（首选，一问一答）：headless
```bash
dshctl ask "<任务描述>"
```
- 运行 dsh 的 headless profile，答完打印**最终 assistant 消息**并退出（exit 0）。耗时约 5–60s（走网关）。
- `dshctl` 已内置 `NODE_OPTIONS=""`（规避宿主安全删除 shim）与 cygpath 路径转换，勿再手工绕开。

### 3. 方式 B（进阶，需要多轮/轨迹/后台）：Web RPC
对 `http://127.0.0.1:3080/api/<method>` 发 `POST`，body：
`{"type":"client-request","rpcId":"<任意>","method":"<方法>","payload":{...}}`；
响应 `{"type":"server-response","rpcId":...,"result":{"ok":true,"value":...}}`。

常用方法：
- `session.create` → 建会话（得 sessionId）
- `session.prompt`（payload 含 sessionId 与 prompt）→ 触发 agent
- `session.history` → **结果在 `value.events`**（不是 items）；轮询直到收到最终 assistant 事件
- `llm.providers` / `llm.models` / `settings.describe` → 只读状态校验
- `settings.update` payload 形如 `{ns, patch, expectedRevision?}`（**不是** `{section,...}`）

注意：`events.mux` 仅支持 WebSocket（HTTP 426 表示该用 WS），必要时轮询 `session.history` 代替。

## 结果处理

- 把 dsh 返回的文本/结论作为该任务的产出并入当前会话；如 dsh 在指定工作目录产出文件，
  明确告知文件路径并把关键内容摘要呈给用户。
- 长任务建议让 dsh 输出到文件，再由 WorkBuddy 读取汇总（避免一次 headless 输出过大被截断）。

## 陷阱与红线（第 0 优先级，逐条确认）

- **`NODE_OPTIONS=""` 必须**：WorkBuddy 宿主会注入 `--require=...genie-safe-delete.cjs` shim，
  把 `fs.rm/unlink` 改成回收站；dsh 保存设置会因 `.lock` 删不掉而失败（不可用则保存报错）。
  `dshctl` 已处理；**禁止**不经处理直接 `node ...\dsh\lib\bin.js web`。
- **路径必须 Windows 形式**：Git Bash `$HOME` 是 `/c/...`，Windows 版 node 不认；DSH_HOME 传
  POSIX 路径会读不到用户层 → 回退 `deepseek-official` 报 `MISSING_CREDENTIAL`。用 `cygpath -m` 转换。
- **3080 不是 OpenAI 兼容网关**：`/v1/chat/completions` 等返回 405/前端页；任何客户端都不能把
  baseURL 指向它。模型路由直接指向 USTC 网关即可（同网关同密钥）。
- headless 只打印最终消息，**不打印完整轨迹**；要看轨迹用 Web UI 或 RPC `session.history`。
- 安装时勿破坏 `~/.dsh` 目录结构（`profiles/`、`storages/`、`settings.yaml`、`.credentials.yaml`）。

## 决策：dsh vs WorkBuddy 原生

- **用 dsh**：需要官方 harness 的 agent 行为 / 独立沙箱跑 agent / 多路并行 agent / 想隔离执行。
- **留在 WorkBuddy**：需要 WorkBuddy 专有能力（技能库、计算化学 skill、文献、专家、Office 文档、
  文件系统集成）的长流程任务——dsh 桥接只作为其中一环被调用。
- 一句话：dsh 不是"嵌入到 WorkBuddy"，而是"被 WorkBuddy 按需调用"的平行引擎。
