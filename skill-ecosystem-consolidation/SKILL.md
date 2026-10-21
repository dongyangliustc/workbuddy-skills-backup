---
name: skill-ecosystem-consolidation
description: 审计并归并 WorkBuddy 技能生态（~/.workbuddy/skills/）——盘点命名与职责重叠、识别同源重复与碎片化、验证路由层可达性、安全归并（先迁资产再删源）并跑回归校验。当用户说"skill 太乱了""技能重叠""归并梳理技能""skill 生态审计""清理技能"，或 agent 反映技能命名/职责重叠需要整理时使用。触发词：skill 归并、技能梳理、技能生态、skills 去重、技能审计、skill consolidation。
agent_created: true
---

# Skill 生态审计与归并

对 `~/.workbuddy/skills/` 做结构化审计并安全归并。**核心纪律：先证实重复，再动手；先迁资产，再删源。**

## 一、四个必须验证的机制事实

动手前先确认这四条，它们决定判断是否成立：

### 1. 注册名 = frontmatter `name`，不是目录名

```bash
awk '/^---/{c++;next} c==1 && /^name:/{print $2; exit}' <skill>/SKILL.md
```

**验证依据**（程序安装目录 `resources/app.asar.unpacked/cli/dist/codebuddy.js`）：

```js
resolveSkillOverrideState(skill, overrides) {
  if (isPluginSkill(skill)) return "on";
  let state = overrides?.[skill.name];        // ← 按 name 查表
  return isValidSkillOverrideState(state) ? state : "on";
}
```

**后果**：目录名 ≠ `name` 时，任何按目录名写目标的 router / 文档都会失配。
**建议**：让目录名与 `name` 严格一致；中文目录名改 ASCII。

### 2. `user-invocable-only` **不是**死链

```js
case "user-invocable-only":
  return { listedToModel:"hidden", userInvocable:true, modelInvocable:true };
```

返回 `modelInvocable: true` —— 模型**仍可显式调用**，只是不出现在模型的技能清单里（降低主动发现概率，但 `Skill(name)` 显式调用可行）。

**别把 `hidden` 误判为"不可达"。** 真死链只有一种：router/文档里写了**不存在的注册名**。

### 3. 判断"是否被模型加载"看两处

| 问题 | 依据 |
|---|---|
| 注册名是什么 | SKILL.md 的 frontmatter `name` |
| 是否仅用户可调用 | `~/.workbuddy/settings.json` 的 `skillOverrides` |
| 是否插件（不走 override） | `isPluginSkill()` —— 插件技能的 override 恒为 `on` |

### 4. marketplace 技能目录带 `__<market>` 后缀

`diagram-generator__skillhub` 之类后缀是**安装器命名约定**，用于区分同名技能来源。**不要重命名**——可能导致市场更新时重复拉取。注册名已正确，不影响调用。

---

## 二、审计流程

### Step 1：盘点

```bash
cd ~/.workbuddy/skills
ls -d */ | wc -l                      # 技能数
du -sh .                              # 总体积
```

逐个提取 frontmatter（`name` / `description` / `agent_created`）与行数。

### Step 2：判定重复的三级证据（从强到弱）

| 级别 | 判据 | 示例 |
|---|---|---|
| **强** | `diff` 行差占比小 + 差异是字符串替换 | 两份 600 行文件仅 141 行差异且全是校名/URL |
| **强** | `_user_meta.json` 的 `basedOn` 字段 | `"basedOn": "zju-literature-downloader"` |
| **中** | 目录名/name 前缀相同 + description 触发词重叠 | `docx-*` 四件套 |
| **中** | 一方是另一方脚本超集（函数已抽取重构） | 新版把工具函数移到 `lib/`，旧版内联 |
| **弱** | 仅主题相近 | 审计类技能共享方法论但内容不重复 |

**只有强/中证据支撑的才归并。弱证据的应建共享层（`_shared/`）而非合并。**

### Step 2.5：合并前的第零道闸门 —— 第三方归属（先判归属，再谈重复）

用 frontmatter 判定归属：`author` / `license` / `version` / `metadata.*`（如 `hermes`）
字段的有无，以及目录是否带 `__<market>` 后缀。

**第三方技能（有外部作者署名或市场来源）一律不得被吸收合并**，即使触发词高度重叠。
理由：删除即丢失署名与许可；且市场更新会重新拉取，造成两份并存或覆盖。

此时正确动作是**边界分离**而非合并：

1. 在**自有**技能里写「边界表」，给出可判定的分工判据（不要写成"看情况"）；
2. 在 router 里为两者各加一行，并写明选择依据；
3. **不改动第三方技能的正文** —— 你的修改会在下次市场更新时被覆盖。

> 反例（2026-09-23 实测）：`nature-literature-pipeline`（`author: 十五 (JL Lab)`、
> `license: MIT`、hermes metadata，属 `nature-*` 家族，被 router 与 4 处文档引用）与一个自建新技能
> 触发词高度重叠，但形态互补（无 registry 的每日 IM 推送 vs 有 registry 的每周人工审阅）。
> 若按"主题相近"硬合并，会同时破坏 4 处引用、丢失署名，并让 `nature-*` 家族齐平性检查失衡。
> 判为「弱证据 + 第三方」⇒ 只做边界分离：新增 router 行 + 两处边界表，归并后
> `final_verify` 的"有效目标"计数 +1、零新增异常。

### Step 3：体积与合规异常

```bash
# 大体积定位
python -c "import os;rows=[(sum(os.path.getsize(os.path.join(r,f)) for r,_,fs in os.walk(d) for f in fs if os.path.exists(os.path.join(r,f))),d) for d in os.listdir('.') if os.path.isdir(d)];print(*sorted(rows,reverse=True)[:10],sep='\n')"

# frontmatter 合规（BOM / 缺失 / name 不一致）
# 用 §五 的校验脚本
```

常见异常：`node_modules` / `dist` / `.git` 残留在技能目录内；BOM 开头的 frontmatter；无 frontmatter 的手册型文件。

---

## 三、归并执行纪律

### 铁律

1. **备份先行**。`node_modules` / `.git` 可排除（可重建）：
   ```python
   import shutil,os
   skip={'node_modules','.git'}
   for r,dirs,fs in os.walk(src):
       dirs[:]=[d for d in dirs if d not in skip]   # 就地过滤
       ...
   ```
2. **先迁资产，再删源**。归并前必须确认目标**已覆盖**源的全部内容，包括：
   - 实现脚本（`diff` 同名文件）
   - 操作手册章节（`grep -nE "^#{1,3} "` 对比章节结构）
   - **独有实测资产**（DOI 模式、字段说明、实测用例）—— 这类最容易漏
3. **迁移后逐项列出新增章节**，确认落地再删源。
4. **归并形态选择**：
   - 同源多实例（如多校下载器）→ 收敛为**1 个实现 + 配置驱动**（预设放 `data/*.yaml`）
   - 小体量碎片（各 <150 行、无内容重复）→ **1 技能 + N 个 references**，用 `manifest.yaml` 的 `on_demand` 按需加载
   - 仅主题相近 → 建 `_shared/` 共享层，各技能保留自己的诊断逻辑
5. **credential / 集成类技能删除前**：检查 `settings.json` 的 `skillOverrides` 是否残留，删除后同步清理，否则留下失效条目。

### 合并技能的目录结构

```
merged-skill/
├── SKILL.md              # 小而精：边界 + 场景路由表 + 通用铁律
├── manifest.yaml         # always_load + references.on_demand
├── references/
│   ├── scenario-a.md     # 原技能正文（去 frontmatter）
│   └── scenario-b.md
└── scripts/              # 原技能的实现脚本
```

`SKILL.md` 里必须写清**边界表**——哪些场景属于本技能、哪些应走插件/其他技能。否则归并后触发词冲突反而更糟。

---

## 四、回归校验（归并后必跑）

### 4.1 正则校验（`final_verify.py`，五项检查）

1. **注册名与目录一致性** —— 允许例外：`__skillhub` 后缀、`_shared`
2. **router 目标可达性** —— 目标必须在注册名集合内（插件名如 `tencent-docx` 属预期例外）
3. **frontmatter 合规** —— 无 BOM、有 `---` 块、有 `name`
4. **manifest 覆盖率** —— 同一系列（如 `nature-*`）应齐平
5. **体积分布** —— 确认无失控项

**校验输出中的"异常"必须逐条归因**，区分"预期保留"与"真缺陷"。不要为了让输出干净而掩盖例外。

### 4.2 严格 YAML 解析（`yaml_verify.py`，不可省略）

> **教训（2026-09-17 实测）**：正则校验**不能**证明 YAML 合法。它只能证明
> "字段存在"，不能证明"文档可解析"。

本次归并中，正则校验报告 frontmatter 67/70 全合规；换成 PyYAML `safe_load`
后立刻炸出一条真实缺陷：

```yaml
# scansci-pdf-agent/SKILL.md — 非法 YAML
description: ... Triggers: "下载文献", "download paper", ...
                              ^ 值内含 ": " 且未加引号
                              → mapping values are not allowed here
```

**判据**：未加引号的标量中出现 `: `（冒号+空格）即非法。修复 = 整行加双引号
并转义内部引号，然后**回读比对语义值逐字符一致**（长度、触发词、专有名词）。

因此归并后必须**两条路径都跑**：

| 脚本 | 覆盖 | 能发现 |
|---|---|---|
| `final_verify.py` | 注册名 / 路由 / manifest / 体积 | 结构性不一致 |
| `yaml_verify.py` | PyYAML 严格解析 frontmatter + manifest | 语法级非法标量 |

`yaml_verify.py` 需 `pyyaml`；缺则
`python.exe -m pip install pyyaml`（装进受管 venv，勿污染系统环境）。

---

## 四点五、独立审核（归并完成后另起一轮）

**执行阶段自己查过的，审核阶段不能照抄结论**——要用独立证据路径重新取证，
否则发现不了迁移过程中的静默丢失。

审核必做四件事：

1. **备份逆向核对**：备份目录 vs 当前目录做集合差，逐个已删技能核验存在性，
   统计备份独有文件数与体积。给出"可恢复性"结论。
2. **迁移保真度**：对迁入 `references/` 的正文做 `difflib.SequenceMatcher`
   相似度比对，目标 **1.0000**；同时用关键词探针核验语义块未丢。
   哈希/相似度比"人工抽查"可靠。
3. **死链全库扫描**：搜所有对已删技能的引用（含 README、manifest、router 表）。
   注意区分**真路由引用**与**历史说明文本**（后者在 `> 历史说明：` 块内属正常）。
4. **两条校验路径复跑**：`final_verify.py` + `yaml_verify.py` 都要重跑。

> **迁移最易丢失的三类内容**（本次实测全部命中）：
> - **溯源/时效警示**（"本清单最初在 X 路由上验证，换 Y 路由需先确认"）
> - **单个备选分支**（如 Shibboleth 认证那一行）
> - **旧技能名残留**（README 里的目录名、正文里的交叉引用）
>
> 迁移时只搬"主体步骤"、丢掉"限定条件"，是最隐蔽的损失——因为文档读起来
> 完全通顺，只有对照源文件才发现少了约束。

---

## 五、校验脚本

`references/` 下两个脚本：

```bash
python final_verify.py            # 5 项结构检查，默认审计 ~/.workbuddy/skills
python yaml_verify.py             # PyYAML 严格解析 frontmatter + manifest + 名称一致性
```

两者互补，归并后**都要跑**，输出可直接作为归并前后对比基线。
缺少 `pyyaml` 时：`python.exe -m pip install pyyaml`。

---

## 六、已固化的判断结论（2026-09-17 实测）

- `user-invocable-only` → `modelInvocable: true`，**不是**死链
- `skillOverrides` 按 frontmatter `name` 查表
- 插件技能不受 `skillOverrides` 影响（`isPluginSkill` 短路返回 `on`）
- marketplace 技能目录的 `__<market>` 后缀不要改
- `pes-sketch/.git` 等 `.git` 目录会触发沙箱 safe-delete 硬保护（`trash-failed`），无法删除；影响小则记录为遗留项
