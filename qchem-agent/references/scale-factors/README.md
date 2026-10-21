# 频率校正因子 / ZPE 校正因子 —— 知识挂载

> 挂载日期：2026-10-20
> 挂载位置：`~/.workbuddy/skills/qchem-agent/references/scale-factors/`
> 服务对象：所有远程量子化学作业（Q-Chem / Gaussian）的**振动频率与热化学**后处理
> 原始数据源：https://github.com/liyuanhe211/Collection_of_Frequency_Scale_Factors

---

## 1. 为什么需要这份挂载

谐波频率系统性偏离实验值（基组不完备 + 谐振近似 + 电子相关近似）。工程上的标准做法是
用经验校正因子缩放。**但不同物理量有不同的因子，混用会引入 1% 量级的系统误差**：

| 物理量 | 用哪一列 | 本项目取值 | 影响下游 |
|---|---|---|---|
| 谐波频率（峰位、IR/Raman 峰横坐标） | `Fundamentals (F)` | **0.9540** | 模拟光谱峰位 |
| 零点能 ZPE（标量） | `ZPE` | **0.9760** | ZPE、ADE、Te |
| 热焓修正 H(T)−H(0) | `H(T)-H(0)` | 本项目暂不用 | 反应焓、ΔG |
| 熵 S(T) | `S(T)` | 本项目暂不用 | −TΔS |

---

## 2. 表结构（原始 9 列）

```
|Method|Basis set|ZPE|H(T)-H(0)|S(T)|Fundamentals (F)|Fund <1000|Fund >1000|Source|
```

| 列 | 含义 |
|---|---|
| `Method` / `Basis set` | 方法与基组，**必须逐字匹配自己的计算口径** |
| `ZPE` | 零点能校正因子（scaled ZPE = factor × 未缩放 ZPE） |
| `H(T)-H(0)` | 热焓修正的校正因子 |
| `S(T)` | 熵的校正因子 |
| `Fundamentals (F)` | **基频**（fundamentals）校正因子 —— 缩放谱峰位置用这个 |
| `Fund <1000` / `Fund >1000` | 按波数分段的两套基频因子（低频/高频分开拟合） |
| `Source` | 出处：`Truhlar` / `CCCBDB` / `NIST` / DOI 文献 |

数据源三类：
- `Truhlar` → http://comp.chem.umn.edu/freqscale/version3b2.htm
- `CCCBDB` → http://cccbdb.nist.gov/vibscalejust.asp
- 其余为文献，直接给 DOI

**关键认知**：`ZPE` 与 `Fundamentals (F)` 是**两列独立拟合的量**。前者以 ZPE 实验值为目标函数
最小二乘，后者以基频谱线为目标函数。二者标定目标不同，因此**数值本就不相等，也不应互相替代**。
（另：ZPE = Σ½hν 是全体频率的加权和，误差传播方式与单条谱线不同，这也决定了两类因子不可互换。）

---

## 3. 本项目裁决（用户确认，2026-10-20）

> **频率校正因子 = 0.9540；ZPE 校正因子 = 0.9760。两者分别独立施加，不得混为一谈。**

| 项 | 值 | 施加对象 | 施加方式 |
|---|---|---|---|
| `FREQ_SCALE` | **0.9540** | 每个谐波频率（cm⁻¹） | ν̃_scaled = 0.9540 × ν̃_harmonic |
| `ZPE_SCALE` | **0.9760** | ZPE 标量 | ZPE_scaled = 0.9760 × ZPE_harmonic |

---

## 4. 来源核查（逐行查表结果，如实记录）

**频率因子 0.9540 —— 精确命中本项目口径**：

```
|CAM-B3LYP|aug-cc-pVTZ||||0.9540 |||10.1021/acs.jpca.6b12147|
```
- 方法/基组 = CAM-B3LYP/aug-cc-pVTZ ✓（与本项目 cam-b3lyp/aug-cc-pVTZ 完全一致）
- 落在 `Fundamentals (F)` 列 ✓（正是"频率因子"该用的列）
- 该行 **ZPE / H / S 列全部为空**

**ZPE 因子 0.9760 —— 来源行不是 aug-cc-pVTZ**：

表中 CAM-B3LYP 唯一带 ZPE 值的行是
```
|CAM-B3LYP|ma-TZVP|0.9760 |||0.9510 |||Truhlar|
```
- 该行 ZPE = 0.9760，但基组是 **ma-TZVP**（同泛函、heterogeneous 基组）
- **CAM-B3LYP/aug-cc-pVTZ 行的 ZPE 列空缺**，故 0.9760 属于"同泛函、不同基组"的取值

旁证（同基组、相近泛函）—— B3LYP/aug-cc-pVTZ 的 ZPE 因子：
| 来源 | ZPE |
|---|---|
| 10.1021/jp048233q | 0.9867 |
| 10.1021/jp073974n | 0.9884 |
| Truhlar | 0.9850 |

**结论与保留意见**：
1. 0.9760 在 Truhlar 体系的多个三-zeta 基组中是通用值（`M06-L/def2-TZVP`、`M06-L/def2-TZVPP`、
   `M08-HX/ma-TZVP`、`MPW3LYP/6-31G(d)` 均为 0.9760），因此作为 CAM-B3LYP 三-zeta 的
   ZPE 因子**可辩护**；
2. 但同基组 B3LYP 旁证给出 0.985–0.988，与 0.9760 相差约 0.010（即 ZPE 相差约 1%）。
   该差异属**基组口径混搭**带来的不确定度，非笔误；
3. **按用户裁决执行 0.9760**，此处仅留档以备审稿质疑或敏感性分析。

敏感性工具：`tools/ir_spectrum_extract.py --zpe-scale <值>` 可任取因子重算 ZPE 与 ΔZPE。

---

## 5. 使用规则（硬约束）

### 5.1 外推优先序（用户确认 2026-10-20）

当所用「方法 + 基组」组合在表中**没有数据**时，按以下顺序处理：

| 级 | 情形 | 做法 |
|---|---|---|
| ① | `Method` + `Basis set` **逐字命中**且目标列有值 | **直接用**，无需外推 |
| ② | 精确行存在，但目标列**为空** | 降到 ③，用同方法的相近基组 |
| ③ | 同方法（同泛函）下有**其它基组**的目标列数据 | **取基组最相近者**（见 §5.2），并在交付物标注实际来源行与基组差异 |
| ④ | 该**方法整体无数据** | **拒绝**。本规则不允许跨方法外推——不得拿 B3LYP 的值代替 CAM-B3LYP、不得拿 M06-2X 代替 M06-HF |

第 ④ 级是硬边界。跨泛函借用会引入远大于基组差异的误差，且无法用本表的标定逻辑辩护。

### 5.2 「最相近基组」的可操作定义

不能凭手感判断"相近"。本挂载固定一个**可复算的距离度量**（`scripts/scale_lookup.py`）：

```
d = 5 × [基组家族不同]
  + 3 × |ζ 基数级别差|
  + 2 × |弥散级别差|
  + 1 × |极化级别差|
  + 0.3 × (1 − 基组名相似度)        # 仅作同分时的稳定化
```

权重理由：

| 项 | 权重 | 为什么 |
|---|---|---|
| 基组家族（Pople / Dunning / TZV / def2 / pc / ma…） | 5 | 不同家族的构造逻辑不同，函数形状差异大于参数多少 |
| ζ 基数级别（DZ/TZ/QZ/5Z） | 3 | 决定径向柔度，是最主要的系统误差来源 |
| 弥散（none / 单+或 ma / aug 或 ++） | 2 | **阴离子与低频模式对此极敏感**，本项目尤其要盯 |
| 极化级别（(d,p) → (2d,2p) → (3df,3pd)） | 1 | 精细修正 |

使用纪律：

1. 优先取距离**最小**者；若前两名距离相差 **< 0.5**，必须**并列报告**，由人裁决，
   不自动取第一条。
2. **弥散级别不一致时必须显式提示**（脚本会自动打印警告）。对阴离子体系，
   无弥散基组借给有弥散基组是高风险操作，宁可另找来源。
3. 同方法**只有一条**候选数据时，无法做候选间比较，必须在交付物中写明
   "全表仅此一条，属唯一可用值"。

### 5.3 其余硬约束

1. **禁止把 Fundamentals 因子当 ZPE 因子用，反之亦然**（本项目已明令区分，见 §3）。
2. 分段因子（`Fund <1000` / `Fund >1000`）只在明确需要时才用；本项目统一用单一
   `Fundamentals (F)`，避免与展宽后谱线的连续性冲突。
3. 因子只施加于**谐波**结果。非谐（VPT2/VCI）结果不应再乘校正因子。
4. 每次施加因子，都要在同一交付物里写明：因子值 + 来源列 + 来源行 + 数据库快照日期。
5. **拼写陷阱**：表中 4 行存在 `CpV` 大小写录入错误，且数值与规范行**不同**：
   `aug-cc-CpVTZ = 0.9550` ≠ `aug-cc-pVTZ = 0.9540`；`cc-CpVTZ = 0.9550` ≠ `cc-pVTZ = 0.9540`。
   必须使用**规范拼写行**。查表工具已做严格/宽松两级匹配，宽松命中会显式告警。

---

## 6. 目录内容

```
references/scale-factors/
├── README.md                          本文件（口径 + 裁决 + 外推规则 + 来源核查）
├── qchem_cam_b3lyp_rows.tsv          已抽取的 CAM-B3LYP / B3LYP 全表（145 行，便于检索）
└── raw/
    └── liyuanhe211_README_2026-10-20.md   原始表格快照（1405 数据行，75 KB，可溯源）

scripts/scale_lookup.py                查表 + 可审计的就近基组外推（本技能级）
```

## 7. 检索与查表

```bash
PY="C:/Users/Administrator.DESKTOP-7RU274I/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
SK="C:/Users/Administrator.DESKTOP-7RU274I/.workbuddy/skills/qchem-agent"

# ① 首选：查表工具（自动区分精确 / 空列 / 就近外推 / 拒绝）
$PY "$SK/scripts/scale_lookup.py" --method CAM-B3LYP --basis aug-cc-pVTZ --col both

# ② 需要旁证时（跨方法的值只作参考，不得作主值）
$PY "$SK/scripts/scale_lookup.py" --method CAM-B3LYP --basis aug-cc-pVTZ \
    --col zpe --reference-other-methods

# ③ 直接翻原文（确认原始行与出处）
grep -iE "^\|(CAM-)?B3LYP\|aug-cc-pVTZ" \
  "$SK/references/scale-factors/raw/liyuanhe211_README_2026-10-20.md"

# ④ 只看 ZPE 列非空的行
awk -F'|' '$3!="" && $2!="" {print $2,$3,$4,$5,$6,$7,$8,$9,$10}' \
  "$SK/references/scale-factors/raw/liyuanhe211_README_2026-10-20.md" | grep -i b3lyp
```

## 8. 版本与时效

- 快照取自 GitHub `main` 分支，抓取日期 2026-10-20（经 jsDelivr CDN 镜像获取；
  `raw.githubusercontent.com` 与 `github.com` 直连在本机返回空内容）。
- 表格含少量录入瑕疵（见 §5.3 第 5 条），`aug-cc-pVTZ` 规范行不受影响，
  但引用其他基组时需核对拼写。
- 更新方式：重新下载 README 覆盖 `raw/` 快照，并在本文件 §8 追加日期；
  若本项目裁决值变化，必须同步更新 §3 与项目 `workflows/ir_spectrum/WORKFLOW.md`。
