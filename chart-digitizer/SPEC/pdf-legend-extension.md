# SPEC：chart-digitizer 扩展 —— 文献 PDF → 指定图 → 裁图 + 数值 + 图例

**状态**：待执行（2026-09-17 由自动化分期实施）
**版本**：1.0（2026-09-16 定稿）
**目标技能**：`C:\Users\Administrator.DESKTOP-7RU274I\.workbuddy\skills\chart-digitizer\`
**依据**：`D:\WORK\workbuddy\2026-09-16-19-05-47\chart-digitizer_文献图例扩展_可行性分析.md`（含全部实测数据）

---

## 0. 一页速览（执行者先读这一页）

### 要交付什么

给 `chart-digitizer` 增加一条**前置链路**：吃文献 PDF，用户说"读 Figure 3"，输出

1. **裁图** —— 该图在 600 dpi 下的裁剪 PNG（供查看/存档/复核）
2. **数值** —— 复用现有 native 引擎提取并交付 CSV/XLSX/JSON
3. **图例信息** —— 该图的序列名称 + 精确 RGB + 图元类型提示，用于自动命名与颜色校验

### 硬性边界（违反即失败）

| 边界 | 说明 |
|------|------|
| **零新增依赖** | `pymupdf 1.28.2`、`pdfplumber 0.11.10` 本机已装。**不得**引入 tesseract / OCR / pdf2image 等 |
| **零 OCR** | 图例文字取自 PDF **文本层**。不做任何像素级文字识别 |
| **不改动已验证核心** | `calibration.py`、`extract.py` 的颜色聚类/去重算法、`audit.py` 的校验逻辑**一律不动**。唯一允许的侵入是在 `extract_series` 增加一个**可选** `exclude` 参数（见 §3.3） |
| **绝不猜** | 图定位有歧义时报错并列出候选，不选"最像的"。图例颜色校验失败时**回退**，不硬用 |
| **图例是可选增强** | 任何环节失败都必须静默回退到现有行为，绝不让整条链路失败 |
| **不改现有测试的期望值** | 现有 6 阶段全部检查必须继续通过。新增测试只能**追加**阶段 |

### 分期与验收门

| 阶段 | 内容 | 验收门（必须全绿才算过） |
|------|------|------------------------|
| **S1** | `pdf.py`：PDF → 定位图 → 600 dpi 裁图 + 溯源 JSON + `from-pdf` CLI | `run_all.py` 7 阶段全绿；`from-pdf --list` 能在真实 PDF 上列出图 |
| **S2** | `legend.py`：图例文字+色块+类型提示；颜色一致性校验；文本 bbox 排除掩码 | `run_all.py` 8 阶段全绿；颜色校验失败路径有测试覆盖 |
| **S3**（**本 SPEC 不实现**） | 读数值标注做标定交叉校验 | 仅登记为后续项，只告警不填值 |

---

## 1. 环境与路径（照抄，勿自行探测）

```bash
PY="C:/Users/Administrator.DESKTOP-7RU274I/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
SKILL="C:/Users/Administrator.DESKTOP-7RU274I/.workbuddy/skills/chart-digitizer"
# 回归测试
cd "$SKILL/tests" && "$PY" run_all.py
```

已确认可用（2026-09-16 实测，勿重复验证）：

| 项 | 实测值 |
|----|--------|
| `pymupdf` | **1.28.2**（`get_text` / `get_drawings` / `get_pixmap` / `Document.new_page` / `insert_text` / `draw_rect` 全可用） |
| `pdfplumber` | 0.11.10（备用通道，**默认不用**） |
| `tesseract` | **未安装**（因此 OCR 路线不可行——不要尝试安装） |
| PDF 文本层 | 3/3 实测文献均有 |
| 600 dpi `clip=` 裁图 | 单栏 143–220 ms，1858×2639 px，14.7 MB |
| 等效提取精度 | **图宽 0.043%**（对数轴跨 5 数量级 ≈ 0.002 数量级，可忽略） |

实测用真实文献（**可能被移走，测试必须能跳过**）：

| 代号 | 路径 |
|------|------|
| P1 | `D:/WORK/Coulomb/Fitting wave/Obara-1986-Efficient recursive computation of.pdf` |
| P2 | `D:/WORK/KHP_C5/respond/J. Phys. Chem. A 2007, 111, 10998-11005.pdf` |
| P3 | `D:/WORK/KHP_C5/respond/Communications Chemistry, 4(1), 72.pdf` |

---

## 2. 实测事实（决定了下面所有设计，勿重新论证）

1. **现代期刊插图是矢量图**。P3 单页有 9728 个矢量绘图对象、仅 11 个位图。→ **必须渲染页面再裁剪**，
   `page.get_images()` 不够用。
2. **图注锚定可行但非万能**。P2 找到 5 行、P3 找到 15 行、**P1 找到 0 行**。
3. **图注正则会误匹配正文引用**。P2 中存在 `"Figure 3 for a particular set of fit parameters."`，
   这是正文，不是图注。→ 必须消歧（见 §3.1）。
4. **图例文字在文本层里、零 OCR 可读**。P3 实测：`TD 415 K` / `TD 493 K` / `RH`，
   字号 **6.6 pt**（轴标题为 9.5 pt），同一左边界 x=87.3，行距 6.7 pt。
5. **图例色块是矢量对象、RGB 精确无抗锯齿**。P3 实测：`#7f7fff` / `#7fe57f` / `#ff7f7f`，
   `type="f"`，尺寸约 5×2.5 pt，位置 x≈78.8–83.9（紧邻文字左侧 x=87.3）。
6. **色块色与曲线色目视一致**（P3 裁图已确认蓝/绿/红对应）——但**必须程序化校验**，见 §3.2 R1。
7. **面板拓扑可能不匹配现有 `find_panels`**。P3 该图是"三条堆叠、共用 x 轴、仅左+下开放脊"，
   不是 caseD 的 2×2 闭合框。→ S1/S2 **不负责**面板切分，交给用户 `--region`/`--axes` 介入。
8. **PDF 文本 bbox 可当排除掩码**，能确定性修掉两条长期限制（见 §3.3）。

---

## 3. 详细设计

### 3.1 S1 · `chartdigit/pdf.py` —— 图定位与裁剪

#### 3.1.1 数据模型

```python
@dataclass
class FigureCandidate:
    page_no: int                    # 0 起
    label: str                      # "3" / "3a" / "?"（无编号时）
    rect: pymupdf.Rect              # PDF 点坐标，y 自顶向下
    source: str                     # "caption" | "raster" | "vector-cluster"
    caption: str                    # 图注首行文本（可为空）
    confidence: float               # 0..1
    ambiguous_with: int             # 同 label 的候选个数（>1 表示有歧义）

@dataclass
class FigureCrop:
    pdf: str
    page_no: int
    rect_pt: tuple[float, float, float, float]
    dpi: int
    px_size: tuple[int, int]
    image_path: str
    candidate: FigureCandidate
```

#### 3.1.2 定位：三通道，按优先级合并

**通道 A —— 图注锚定（主通道）**

1. `page.get_text("dict")` 取 blocks；**同时对 `type==1`（image）的 block 也保留其 bbox**——
   pymupdf 在同一坐标系里同时给出文字与图像的 bbox，无需另调 `get_image_info()`。
2. 逐行匹配正则（**按实测格式**）：
   ```
   ^\s*(?:Fig(?:ure)?\.?|表|TABLE|Table)\s*(\d+)\s*([a-zA-Z])?\s*[.:|]?
   ```
   同时记录**行级 bbox** 与该行所在 block 的 bbox。
3. **消歧（必须有，P2 已实测踩中）**：对每个匹配行，判定其是否为**真图注**：
   - 该行的 bbox 所在 block **不含正文续行**（即 block 内行数少，或该行是 block 首行且 block 宽度
     ≈ 单栏宽）；**且**
   - 字号 ≤ 正文字号（取该页出现最多的字号作为正文字号）；**且**
   - 匹配后紧跟分隔符（`.`/`|`/`:`/制表/两个以上空格）**或**匹配串位于行首 4 字符内。
   不满足者丢弃。**若丢弃后某编号出现 >1 个候选，标记 `ambiguous_with` 并要求用户明确**。
4. **图形区域 = 该图注同栏、图注上方**：
   - 栏范围：横坐标取**图注行 bbox 的 x0/x1**；若图注行明显窄于栏宽，用该 block 的 x0/x1 扩展；
     若图注横跨两栏（x0 接近左边距且 x1 接近右边距），则按整页宽处理。
   - 纵向上界：向上找**同栏内**最近的一个非图注文字 block，取其 bbox 底边；若不存在则取栏顶/页边距。
   - 纵向下界：图注行 bbox 顶边（**不含图注本身**）。

**通道 B —— 位图 bbox（P1 唯一有效通道）**

从 `get_text("dict")` 的 `type==1` block 取 bbox，过滤掉过小者（宽或高 < 40 pt，排除公式/图标）。
若某页的位图 bbox 数 ≥1 且通道 A 在该页无候选，则按"阅读顺序"编号为图 1、2…，`source="raster"`。

**通道 C —— 矢量簇（兜底，仅 A、B 均空时启用）**

`page.get_drawings()` 取全部 rect，按空白间隔聚类（同栏内、间隔 > 20 pt 即断开），取面积最大的簇。
`source="vector-cluster"`，`confidence` 最高只给 0.4。

**收紧（三种通道都要做）** —— 先粗定再收紧，避免把正文/留白裁进去：

1. 按上述粗定 rect 渲染（**低 dpi，如 100**，成本可忽略）；
2. 在渲染图上找**非背景行/列**的范围（背景色 = 该区域众数色；256 级灰度差 < 8 视为背景）；
3. 把该范围换算回 PDF 点坐标，作为**收紧后** rect；
4. 收紧后若长宽比异常（如高/宽 < 0.15 或 > 8）或面积 < 全页 1%，**丢弃该候选**并降低 confidence。

#### 3.1.3 裁剪

```python
pm = page.get_pixmap(dpi=dpi, clip=fig_rect, colorspace=pymupdf.csRGB)
pm.save(out_path)
```

- **必须用 `clip=` 局部渲染**。整页 600 dpi 是 97–104 MB，裁剪后仅 6.5–14.7 MB。
- 默认 `dpi=600`。允许 `--dpi` 覆盖。**下限 300**（低于此等效精度开始显著劣化）。
- 输出 PNG（无损）。**不要**输出 JPEG。

#### 3.1.4 溯源 sidecar

与现有风格一致，输出 `{"pdf","page_no","rect_pt","dpi","px_size","source","caption","confidence","generated_at"}`。
这是"报告必须可独立复核"这一既有约定的延续。

#### 3.1.5 CLI

```bash
# 列出 PDF 内检出的所有图（含页码、bbox、来源、置信度、图注首 60 字）
"$PY" "$SKILL/chartdigit.py" from-pdf paper.pdf --list

# 裁出 Figure 3（并打印下一步该跑的 extract 命令）
"$PY" "$SKILL/chartdigit.py" from-pdf paper.pdf --figure 3 -o fig3 --dpi 600

# 有歧义/定位失败时：用户指定页码 + 手工矩形
"$PY" "$SKILL/chartdigit.py" from-pdf paper.pdf --page 5 --rect 43.6,130,266.5,372 -o fig3
```

**行为约定（重要）**

- **`--list` 是无副作用的**：可随时调用、不写文件、永远退出码 0（除非 PDF 打不开）。
  这是人机消歧的主入口。
- `--figure N` 若遇 `ambiguous_with > 1` → **退出码 1**，stderr 打印全部候选（页码 + bbox + 图注首行），
  并提示"请用 `--page` + `--rect` 指定"。
- 定位失败 → **退出码 1**，打印 `--list` 的结果与手工框选指引，**不得**猜。
- 裁剪成功后 **stdout 直接给出下一步命令**（含 `--axes` 提示），使链路可串起来：
  ```
  下一步：
    "$PY" "$SKILL/chartdigit.py" extract <crop.png> --axes L,T,R,B --xlim ... --ylim ... --format csv xlsx json
  ```

新增退出码：沿用现有约定（0 成功 / 1 用法与定位问题 / 2 提取失败 / 3 无 GetData / 4 待人工审核）。

---

### 3.2 S2 · `chartdigit/legend.py` —— 图例提取与颜色校验

#### 3.2.1 图例文字的几何签名（实测确定）

在 `fig_rect` 内取全部文字行，按以下条件筛出"图例条目"候选：

| 条件 | 阈值 | P3 实测依据 |
|------|------|-------------|
| 字号显著小于轴标题 | `size < 0.80 × 该区域内最大字号` | 6.6 vs 9.5 pt（比值 0.69）✓ |
| 同组左边界对齐 | 左 x 的极差 ≤ 3 pt | 三条均为 x=87.3 ✓ |
| 行距紧凑 | 行距 < 1.6 × 该组行高 | 6.7 pt 行距 vs 6.6 pt 字号 ✓ |
| 组内条目 ≥ 2 | — | 单条无法与轴标签区分，须有 ≥2 条共线 |

**必须同时满足**才判为图例组。找不到时 `legend_found=False`，**静默回退**（用户已明确接受此行为）。

#### 3.2.2 色块关联（矢量对象，RGB 精确）

对每条图例文字行（y 区间 `[y0, y1]`、左边界 `x0`）：

1. 在 `page.get_drawings()` 中找满足 **`drawing.rect.y0 > y0-3`、`drawing.rect.y1 < y1+3`、
   `drawing.rect.x1 <= x0 + 0.5`、`drawing.rect.width > 3`** 的对象。实测此处会混入
   **y 轴刻度短线**（P3 实测到 `#000000`、5×0.5 pt 的横向短划）→ 用下面第 2 步排除。
2. **取色优先级**：
   - 优先 `type == "s"`（描边）的 `d["color"]`——线段样本的真实颜色；
   - 否则用 `type == "f"`（填充）的 `d["fill"]`，但**同一 y 区间出现多个嵌套 fill 时取饱和度最高者**
     （P3 实测同一色块有 `#7fe57f` 与 `#3fd83f` 两层，内层才是芯色）；
   - 跳过近灰/近黑（`max(rgb) - min(rgb) < 25`）与纯黑——那是刻度线或文字。
3. **图元类型提示**：色块长宽比 `w/h > 3` → `line`；`1/3 < w/h < 3` → `marker`（散点）；
   色块末端有垂直短划 → `errorbar`。写入 `kind_hint`，仅供 `extract --kind` 参考，**不作强制**。

#### 3.2.3 R1 颜色一致性校验（**强制，不可省**）

这是本方案**唯一会静默出错**的风险点：若期刊对图例色块做了淡化/换色/加边框，按图例颜色去提取会
**一条曲线都提不到**，或提到错误对象。

```python
def verify_legend_colors(rgb: np.ndarray, region, legend_colors, tol=64.0,
                         min_pixels: int = 30) -> LegendCheck
```

对每个图例颜色，在**图形区域内**统计与该颜色距离 < `tol` 的像素数（复用现有 `color_mask`
或 `dominant_colors` 的距离函数，**不要另写一套**）：

- 全部命中 → `ok=True`
- 任一命中数为 0 或 < `min_pixels` → `ok=False`，并在报告中**明确列出**哪个图例颜色在图内找不到
- 命中数与图例条目数不匹配（如 4 条图例但只有 3 个颜色能在图内找到）→ `ok=False`

**`ok=False` 时的行为**：调用方**必须回退**到 `dominant_colors` 自动路径，并在报告中写明
"图例颜色校验未通过，已回退自动配色"，同时打印图例给出的颜色与图内实际主色，便于人工判断。
**绝不硬用未通过校验的颜色。**

#### 3.2.4 命名规则（R5 缓解）

命名必须**始终与颜色 hex 并存**：`TD 415 K #7f7fff`。

理由：`c1_d93637` 一看便知是占位符；错命名为 `TD 415 K` 则**看起来可信**。
保留 hex 使错误命名可被发现、可被核对。

#### 3.2.5 交付物中的图例区块

沿用标定审核的做法（写在数据之前 / 固定区块）：

- CSV 头部、XLSX `Metadata` 表各增加「图例」区块：条目名、hex、`kind_hint`、来源
  （PDF 文本层 / 矢量对象）、颜色校验结果、校验失败时的回退说明。
- JSON 增加 `legend` 字段：
  ```json
  {"found": true, "source": "pdf-text+vector", "entries": [
     {"name": "TD 415 K", "color": "#7f7fff", "kind_hint": "marker", "matched_pixels": 12883}],
   "color_check": {"ok": true, "checked": 3, "matched": 3}}
  ```

---

### 3.3 S2 · 排除掩码（本方案的非预期高价值收益）

PDF 文本层给出**所有文字行的 bbox**（轴标题、面板标签、图例文字）。把这些矩形从提取区域剔除，
可**确定性**修掉两条长期限制：

- SKILL.md 已知局限第 6 条："图例若与曲线同色，可能被当作数据一并提取"
- troubleshooting **B7**："误检出文字标注为序列"

**实现**：`extract_series` 增加**可选**参数

```python
def extract_series(rgb, region, *, ..., exclude: Sequence[tuple[float,float,float,float]] | None = None)
```

在生成颜色掩码**之后、连通域分析之前**，把 `exclude` 中的矩形区域置为背景。
默认 `None` 时行为完全不变（**保证现有 6 阶段测试不受影响**）。

CLI：`extract --exclude-from-pdf paper.pdf --page 5 --rect ...`（或直接 `--exclude L,T,R,B` 多次）。

> **注意**：文字行的 bbox 是 **PDF 点坐标**，需按 `dpi/72` + `rect_pt` 的偏移换算到裁剪图的像素坐标。
> 换算必须写成独立小函数并**单独测**（这是最容易静默错位的一步）。

---

## 4. 测试计划

### 4.1 原则（沿用本技能已确立的约定）

1. **机制类缺陷用确定性单元测试锁定，不依赖真实文件的偶然排版**。
2. **真实 PDF 只做"能力探测"**：断言"能检出 ≥1 张图""能找到图例"，**不断言具体数值**
   （文献可能被移动/替换）。且**文件不存在时必须优雅跳过**，不能让测试变红。
3. **测试脚本必须自己返回退出码**（`main() -> int` + `sys.exit(main())`）。这是本轮已踩过的坑：
   曾有一个验证脚本返回 `None`，失败仍报"通过"。

### 4.2 新增第 7 阶段：`tests/make_pdf_cases.py` + `tests/test_pdf_units.py`

**自包含测试 PDF（用 pymupdf 编程生成，已确认 API 可用）**

在 `make_testcases.py` 中增加 `make_pdf_cases()`，生成 `tests/figures/caseP1_paper.pdf`，内含**一页**，
刻意布置以下全部已知陷阱：

| 元素 | 布置方式 | 考察点 |
|------|---------|--------|
| 正文段落 | `insert_text`，字号 10 pt | 正文字号基准 |
| **正文引用干扰项** | 正文中故意写 `"as shown in Figure 3 for a particular set..."` | **图注消歧**（P2 实测陷阱） |
| 图注 | 独立行 `"Fig. 3 \| Test figure for digitizer."`，字号 9 pt | 通道 A 命中 |
| 图形区域 | `draw_line` 画左+下脊轴、`draw_rect` 画图例色块 | 矢量图（非位图） |
| 轴标题 | `insert_text` 9.5 pt，如 `"Photon Energy (eV)"` | 字号基准（排除图例） |
| 图例文字 | `insert_text` **6.6 pt**，三条同左 x、行距 6.7 pt | **几何签名** |
| 图例色块 | `draw_rect(fill=...)`，**精确 RGB**：`(0.5,0.5,1.0)` / `(0.5,0.9,0.5)` / `(1.0,0.5,0.5)` | 色块关联 + 取色 |
| 嵌套色块 | 同一位置再叠一层更饱和的 fill | **芯色优先**规则 |
| y 轴刻度短线 | `draw_rect` 近黑、5×0.5 pt | **刻度线不得被误认为色块** |
| 面板标签 | 图内 `insert_text`，如 `"m/z 70"` | 排除掩码 |
| 第二张图 | 同页再放一个 `Fig. 4`，几何相近 | **歧义检测** |

**`test_pdf_units.py` 必测项（目标 35–45 项）**

- 图注正则命中 `Fig. 3`，且**不**把正文里的 `Figure 3 for a particular...` 判为图注
- 消歧规则逐条断言（block 续行 / 字号 / 分隔符）
- 图形区域落在图注上方、同栏内、**不含图注本身**
- 收紧函数：给定"图 + 四周留白"的合成图，输出恰为图的边界
- `--rect` 手工路径与 `--page` 组合可用
- 颜色换算：RGB 浮点 → `#rrggbb` 正确（含 0.5→`7f`/`80` 的舍入边界）
- 图例几何签名：满足全部条件才成组；**缺任一条即不成组**（逐条负例）
- 嵌套色块取芯色：`#7fe57f` + `#3fd83f` → 取 `#3fd83f`
- 刻度短线（近黑 + 近方形小尺寸）**不**被取为色块
- `verify_legend_colors` 三态：全中 / 有一个为 0 命中 / 命中数与条目数不匹配
- 点→像素换算函数：`dpi=600`、`rect_pt` 偏移下的往返一致
- **歧义时返回退出码 1**（子进程方式调 CLI）

### 4.3 新增第 8 阶段：`tests/test_pdf_smoke.py`（真实文献，可跳过）

```python
REAL_PDFS = [P1, P2, P3]   # 见 §1
```

- 文件不存在 → 打印 `（跳过：文献不存在）` 并计为**通过**（不计入检查数）
- 存在时断言：
  - `from-pdf --list` 退出码 0，且**至少能列出一张图**（P1 走通道 B、P3 走通道 A，两条都要覆盖）
  - P3 上 `--figure 2` 能裁出图，且输出 PNG 宽 > 1000 px（验证 600 dpi clip 生效）
  - P3 的图例提取能在**该图区域内**检出 ≥2 条同左 x 的小字号文字
  - 端到端：`from-pdf` → `extract` 在裁图上能跑通并产出 CSV（序列数 ≥1）
  - **回归红线**：对裁出的图跑 `ticks_for_box`，确认线性轴仍判 `uniform`（见 §6）

### 4.4 `run_all.py` 扩展

```python
STAGES = [
    ("生成测试用例", "make_testcases.py"),          # 增加 make_pdf_cases()
    ("单元测试（缺陷锁定）", "test_extract_units.py"),
    ("标定审核单元测试", "test_audit_units.py"),
    ("PDF 单元测试", "test_pdf_units.py"),          # 新增
    ("API 精度验证", "verify_accuracy.py"),
    ("对数坐标验证", "verify_log_axes.py"),
    ("CLI 冒烟测试", "test_cli_smoke.py"),
    ("PDF 真实文献冒烟", "test_pdf_smoke.py"),      # 新增
]
```

### 4.5 CLI 冒烟新增（追加到 `test_cli_smoke.py`，目标 +12–18 项）

`--list` 无副作用（不写文件）；`--figure` 成功/歧义/失败三态退出码；`--page`+`--rect` 手工路径；
`--dpi` 下限校验；裁图 PNG 存在且尺寸符合 `dpi/72` 换算；图例区块出现在 CSV/XLSX/JSON 中；
颜色校验失败时报告含回退说明。

---

## 5. 文档更新（与代码同批完成）

| 文件 | 更新内容 |
|------|---------|
| `SKILL.md` | 触发条件加入"文献 PDF / 读某张图 / Figure N"；新增「文献 PDF 工作流」一节（`from-pdf --list` → `--figure N` → `extract` → 可选 `--exclude-from-pdf`）；交付格式表增加「图例」区块；资源表加入 `pdf.py`/`legend.py`；测试表 6 → 8 阶段；验收清单加入"图例颜色校验通过或已回退"一条 |
| `references/pdf-legend.md`（新建） | PDF 定位三通道、图注消歧规则、图例几何签名与阈值、颜色校验、排除掩码的原理与实测依据 |
| `references/troubleshooting.md` | 新增 `## D-pdf. 文献 PDF` 节：图定位失败/歧义、图例不存在、颜色校验失败、面板拓扑为开放脊、文献被移动导致测试跳过 |
| `references/algorithms.md` | §2 增加 PDF 前置链路一节（指向 pdf-legend.md，避免重复） |

---

## 6. 回归红线（每次改动后必须核对）

1. **现有 6 阶段全部检查必须继续通过**（24 + 53 + 61 + 精度 + 对数轴）。
2. **线性轴必须仍判 `uniform`**。基线（`tests/figures/`）：

   | 图 | x 轴 | y 轴 |
   |----|------|------|
   | A 多曲线 | uniform | uniform |
   | B 散点 | uniform | uniform |
   | C 误差棒 | uniform | uniform |
   | E 半对数 | uniform | **nonuniform** |
   | F 双对数 | **nonuniform** | **nonuniform** |

   线性轴一旦出现 `nonuniform`，标定审核的硬警告就开始**误报**——比漏报更糟。
3. **`extract_series(exclude=None)` 时行为必须与改动前逐位一致**。
4. `verify_accuracy.py` 的三种口径（truth-box / auto-box / auto-color）**数值不得变化**。

---

## 7. 明确不做（防范围蔓延）

| 不做 | 理由 |
|------|------|
| 图例文字 OCR | tesseract 未装；PDF 文本层已足够；图片输入走现有流程 |
| 安装 OCR / pdf2image 等新依赖 | 违反"零新增依赖"边界 |
| 自动判断"用户指的是哪张图" | 用户直说 `Figure 3` 成本极低且无歧义；自动语义匹配风险高收益低 |
| 读刻度数字自动填 `--xlim/--ylim` | 违背"绝不臆造坐标轴数值"红线。**S3 只做告警**，且本 SPEC 不实现 |
| 开放脊（脊柱式）面板自动切分 | P3 实测该拓扑存在，但属独立小任务；本次交用户在 `--region`/`--axes` 层介入 |
| 修改 `calibration.py` / 颜色聚类算法 / `audit.py` 校验逻辑 | 已验证核心，不动 |
| 在线文献下载 | 本机已有 `nature-downloader` / `scansci-pdf-agent`，需要时上游衔接，不在本 SPEC 内 |

---

## 8. 交付物清单

| # | 交付物 | 路径 |
|---|--------|------|
| 1 | PDF 定位与裁剪模块 | `<SKILL>/scripts/chartdigit/pdf.py` |
| 2 | 图例提取与校验模块 | `<SKILL>/scripts/chartdigit/legend.py` |
| 3 | CLI 子命令 `from-pdf` 与 `--exclude-from-pdf` | `<SKILL>/scripts/chartdigit/cli.py` |
| 4 | `extract_series` 的可选 `exclude` 参数 | `<SKILL>/scripts/chartdigit/extract.py` |
| 5 | 自包含测试 PDF 生成器 | `<SKILL>/tests/make_pdf_cases.py` |
| 6 | PDF 单元测试（35–45 项） | `<SKILL>/tests/test_pdf_units.py` |
| 7 | 真实文献冒烟（可跳过） | `<SKILL>/tests/test_pdf_smoke.py` |
| 8 | 8 阶段测试入口 | `<SKILL>/tests/run_all.py` |
| 9 | 参考文档 | `<SKILL>/references/pdf-legend.md` |
| 10 | 文档更新 | `SKILL.md` / `troubleshooting.md` / `algorithms.md` |
| 11 | 端到端演示产物（P3 的 Fig. 2 全链路） | 见 §9 |
| 12 | 交付报告 | 见 §9 |

---

## 9. 交付报告要求（自动化最后一棒必须产出）

写到 `D:\WORK\workbuddy\2026-09-16-19-05-47\pdf_legend_delivery_report.md`，必须包含：

1. **S1 / S2 各自的验收门是否通过**（贴 `run_all.py` 的汇总表原文）
2. **新增检查数**：单元 / CLI / 冒烟各多少项，总计
3. **真实文献实测结果表**：P1/P2/P3 各自走哪条通道、定位到几张图、图例是否检出、颜色校验结果
4. **端到端演示**：P3 的 Fig. 2 从 PDF → 裁图 → 提数 → 图例命名，产出实际文件路径，并附裁图与提取结果的对照说明
5. **回归红线核对**：线性轴仍判 `uniform` 的证据（列出 A/B/C 的结果）
6. **未完成项与原因**（如有），以及下一步建议
7. **风险台账**：R1–R7 中哪些已被测试覆盖、哪些仍靠人工把关

---

## 10. 执行顺序（自动化三棒）

| 棒次 | 时间（北京时间） | 内容 | 完成判据 |
|------|----------------|------|---------|
| 第 1 棒 | 09-17 06:00 | S1：`pdf.py` + `make_pdf_cases.py` + `test_pdf_units.py`（PDF 部分）+ `from-pdf` CLI + `run_all.py` 第 7 阶段 | `run_all.py` 7 阶段全绿；`from-pdf --list` 在 P3 上能列出图 |
| 第 2 棒 | 09-17 08:00 | S2：`legend.py` + `exclude` 参数 + 排除掩码 + CLI 冒烟新增 + `pdf-legend.md` | `run_all.py` 8 阶段全绿；颜色校验失败路径有测试 |
| 第 3 棒 | 09-17 09:30 | 收尾：文档更新、端到端演示、交付报告、回归红线核对 | 报告写入指定路径；未完成项已列明 |

**每一棒都必须遵守 §6 回归红线。** 后一棒发现前一棒未完成时，**先补齐前一棒再继续**——
后一棒的提示词已包含完整 SPEC 路径与上下文，可独立完成全部工作。
