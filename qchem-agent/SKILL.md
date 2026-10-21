---
name: qchem-agent
version: 1.0.0
description: "Q-Chem 计算智能体：基于**版本精确**的本地知识库（本机安装版自带手册 + 939 个官方样例）生成、校验并提交 Q-Chem 输入，防止关键字/方法/取值幻觉。当需要写 Q-Chem 输入文件、选择计算方法与模块（单点/优化/频率/CCSD(T)/EOM-CCSD 激发能/TDDFT/溶剂化/EDA 等）、核查某个 $rem 关键字是否存在及其允许取值、或排查 Q-Chem 输入报错时使用。内置三重防幻觉闸门：关键字白名单、取值类型校验、手册页码溯源。"
agent_created: true
source: |
  作者：DY 课题组本地开发
  建立：2026-10-14
  知识底座：
    - 本机版手册 $QC/doc/manual.pdf（Q-Chem 6.1 User's Manual，1766 页）—— 判定基准
    - 官方样例 $QC/samples/（939 个 .in，46 类）—— 经验证据
    - 用户离线 PDF（Q-Chem 6.2，1816 页）—— 人工阅读
    - 线上手册 https://manual.q-chem.com/latest/（7.0）—— 交叉核对
license: MIT
requirements: |
  - Python 3.10+；pymupdf（仅重建知识库时需要，运行查询零第三方依赖）
  - 计算执行依赖 remote-sync 技能的 gtransfer（HPCCube profile）
---

# Q-Chem 计算智能体

## 1. 这个技能解决什么问题

Q-Chem 有 1500+ 个 `$rem` 关键字，且**不同大版本的关键字集不同**。模型凭记忆写输入时，
最典型的失败是"编出一个看起来很像但不存在的关键字"（例如 `METHOD TDDFT`、`EOM_NROOTS`、
`BASIS_SET`），提交后 Q-Chem 直接报错或更糟——静默忽略。

本技能把"写输入"从**记忆驱动**改成**检索 + 校验驱动**：
每个关键字都必须能在**本机版本**的手册或官方样例中溯源，否则被闸门拦下。

## 2. 两份手册的绑定关系（务必先读）

| 源 | 版本 | 用途 | 权威性 |
|---|---|---|---|
| 本机 `$QC/doc/manual.pdf` + `$QC/samples/` | **6.1.1** | **判定基准** | ★★★ 本机能不能跑以它为准 |
| 用户离线 `qchem_manual.pdf`（1816 页） | 6.2 | 阅读、查更详细推导 | ★★ 关键字可能本机没有 |
| 线上 `manual.q-chem.com/latest/` | **7.0** | 交叉核对、查新功能 | ★ 版本高于本机，**必须回查 6.1.1** |

> **核心纪律**：线上/离线手册可用于理解，但**任何要写进输入的关键字，最终必须在 6.1.1 知识库中可溯源**。
> 版本漂移是这类幻觉的头号来源。

## 3. 知识库构成（`references/qchem-6.1.1/`）

```
manual-6.1.1.pdf        本机版官方手册（4.7 MB, 1766 页）
samples/                939 个官方样例输入（46 类，含 ccman/ tddft/ adc/ freq/ ...）
kb/
  keywords.json         1522 个 $rem 关键字（含 type/default/options/手册页码/来源标记）
  values_observed.json  768 个关键字在官方样例中的**实测取值**（含次数与示例文件）
  sections.json         合法输入段（$molecule/$rem/$pcm/...），来自手册 + 样例实测
  samples.json          每个样例用了哪些段与关键字
  chunks.jsonl          1775 个手册分块（供检索）
  meta.json             构建元信息（版本、页数、各类计数）
```

`keywords.json` 中每个条目的 `source` 字段标明可信来源：
- `manual` —— 手册有定义块（含 TYPE/DEFAULT/OPTIONS）
- `samples` —— 手册无定义块，但**官方样例确实在用**（经验证有效）
- 两者都有 = 可信度最高

## 4. 三重防幻觉闸门（`scripts/qc_kb.py`）

| 闸门 | 命令 | 拦什么 |
|---|---|---|
| ① 关键字白名单 | `validate` | 不在 1522 个注册表中的关键字 → **报错**并给出最接近候选 |
| ② 取值类型校验 | `validate` | LOGICAL 型给了非逻辑值、INTEGER 型给了非整数 → 警告 |
| ③ 手册页码溯源 | `validate` / `keyword` | 每个通过的关键字都回显手册页码或官方样例来源 |

另加两条辅助闸门：
- **段名白名单**：`$xxx` 段名必须在 `sections.json` 中（防止 `$rem` 拼错成 `$REM`/`$scan`）
- **取值提示**：字符串型关键字的取值若未见于文档枚举/官方样例，给 `ℹ` 提示（**不计警告**，
  因为文档枚举通常不完备）——这是为避免假警报而刻意设计的分级

## 5. 使用流程

```bash
SK=~/.workbuddy/skills/qchem-agent
PY="<python>"   # 任意 3.10+ 解释器

# ① 查任务该用什么模块（先看 references/modules.md 的决策表）
python $SK/scripts/qc_kb.py search "EOM-CCSD excitation energy open shell" --top 5

# ② 核实关键字是否存在、类型与取值
python $SK/scripts/qc_kb.py keyword EE_STATES
python $SK/scripts/qc_kb.py keyword MEM_TOTAL
python $SK/scripts/qc_kb.py values METHOD        # 官方样例实测取值

# ③ 取模板（7 个已校验模板，见 references/templates/）
cp $SK/references/templates/05_eom_ccsd_excitation_openshell.in ./job.in
#    改电荷/多重度/坐标/基组/MEM_TOTAL

# ④ 过闸门（**未知关键字即报错，退出码 2**）
python $SK/scripts/qc_kb.py validate ./job.in

# ⑤ 提交（HPCCube：核数由内存反推，MEM_TOTAL <= --mem）
python ~/.workbuddy/skills/remote-sync/scripts/gtransfer.py submit \
  --server hpccube --local ./job.in --remote "~/C2nCN/<task>" \
  --mem 100G --cpus-per-task 26 --walltime 7-00:00:00 \
  --download-glob "*.out *.fchk" --out-dir ./results/<task> --timeout 604800
```

## 6. 已固化模板（`references/templates/`，全部通过闸门）

| 文件 | 内容 | 依据 |
|---|---|---|
| `01_sp_energy.in` | 单点能 | 手册 + 样例通用最小集 |
| `02_opt_geometry.in` | 几何优化 | `[S:ccman/EEEOMCCSD_opt.in]` |
| `03_freq_vibrational.in` | 频率/振动 | `[S:freq/]` |
| `04_ccsdt_single_point.in` | CCSD(T) 高精度单点 | `[S:ccman/CCSD_opt.in]`、本组既有 C12H- 口径 |
| `05_eom_ccsd_excitation_openshell.in` | **开壳层 EOM-EE-CCSD 激发能（本项目主用）** | `[S:ccman/EEEOMCCSD_openshell.in]` |
| `06_tddft_excitation.in` | TDDFT 激发能 | `[M:p.452 Example 7.21]` |
| `07_sftddft_excitation.in` | 自旋翻转 TDDFT | `[M:p.454 Example 7.23]` |
| `08_ir_spectrum.in` | **红外光谱：OPT+FREQ 两段式**（IR 强度默认输出 km/mol） | `[M:p.972-974 Example 10.28]` |

## 7. 硬性约束（写输入时必须一起满足）

1. **`MEM_TOTAL` 必填**，且 **≤ SLURM `--mem`**；CC 类方法再给 `CC_MEMORY`。
   Q-Chem 默认按整节点内存申请堆，超 cgroup 会被 OOM 杀（实测 exit 137）。
2. **`--cpus-per-task` 由内存反推**（sbatch 硬校验 `内存/CPU ≤ 3944MB`），
   不是按并行需求——本 Q-Chem 是串行版。
3. **scratch 每轮清空 + 唯一命名**（被 kill 的作业会留下损坏 `qarchive.h5`）。
4. 判读结果以 `.out` 是否出现能量与 `Thank you very much` 为准，**不要只看退出码**
   （实测 `qchem` 会返回 rc=1 却完整正常收尾；gtransfer 已做容错）。

## 8. 重建 / 升级知识库

换版本（如升级到 Q-Chem 6.2/7.0）时，只需对新版手册与样例目录重跑：

```bash
python $SK/scripts/build_kb.py \
  --manual <新版本 $QC/doc/manual.pdf> \
  --samples <新版本 $QC/samples> \
  --out $SK/references/qchem-<版本>/kb \
  --label <版本>
python $SK/scripts/qc_kb.py --kb $SK/references/qchem-<版本>/kb stats
```

构建器会报告三项交叉核对指标，用于评估解析质量：
- `keywords_manual`（手册定义数）
- `keywords_samples_only`（手册未定义但样例在用 —— 说明手册不完整，属正常）
- `keywords_unrecognized`（样例用过却未入册 —— **应为 0**，非 0 说明解析漏检）

## 9. 已知边界（诚实声明）

- 关键字**是否存在**判定可靠（双源 + 1766 页手册）；但**取值枚举不完备**
  （如 `METHOD` 的文档 OPTIONS 只有寥寥几项），故字符串型取值只给提示不报错。
- 手册是 LaTeX 生成的 PDF，文本层含连字（ﬁ/ﬂ），构建器已归一；
  但少数表格化定义（如 `CCMAN2` 的 options）仍可能串行，属已知瑕疵。
- 本技能**不**判断物理方案的优劣（该用什么方法/基组属于科学判断），
  只保证"你写的关键字确实是 Q-Chem 的、且本机版本支持"。

## 10. 频率 / ZPE 校正因子（振动与热化学后处理）

**Q-Chem 6.1.1 没有谐波频率的校正因子关键字**（`FREQ_SCALE_FACTOR` 属 `$vibronic`，
只用于振动分辨电子光谱，见手册 p.991-992）⇒ 校正一律在**后处理**做。

校正因子查表与口径见 **`references/scale-factors/README.md`**（含 1405 行官方汇总表快照、
CAM-B3LYP/B3LYP 抽取表、来源核查记录）。核心纪律：

1. **频率因子 ≠ ZPE 因子，分别独立施加，不得互换。**
   频率因子作用于每条谱线位置；ZPE 因子作用于 ZPE 标量。
2. **查不到精确组合时的外推优先序**：精确命中 → （精确行但目标列为空时）同方法
   **最相近基组** → 同方法整体无数据则**拒绝**，不得跨方法外推。
   「最相近」由 `scripts/scale_lookup.py` 的固定距离度量给出（家族 5 / ζ 3 / 弥散 2 /
   极化 1），前两名差值 <0.5 时必须并列报告、由人裁决。
3. 弥散级别不一致要显式告警（阴离子与低频模式敏感）；同方法仅一条候选时须注明
   "全表唯一"。
4. 因子只施加于**谐波**结果；非谐（VPT2/VCI）不再乘校正因子。
5. 交付物须写明：因子值 + 来源列 + 来源行 + 表快照日期。
6. 表内有 `CpV` 拼写错误行且数值与规范行**不同**（`aug-cc-CpVTZ=0.9550` vs
   `aug-cc-pVTZ=0.9540`）⇒ 必须用规范行。

```bash
SK=~/.workbuddy/skills/qchem-agent
python $SK/scripts/scale_lookup.py --method CAM-B3LYP --basis aug-cc-pVTZ --col both
python $SK/scripts/scale_lookup.py --method CAM-B3LYP --basis aug-cc-pVTZ --col zpe \
    --reference-other-methods          # 跨方法值仅作旁证，不得作主值
grep -iE "^\|(CAM-)?B3LYP\|aug-cc-pVTZ" \
    $SK/references/scale-factors/raw/liyuanhe211_README_2026-10-20.md
```

