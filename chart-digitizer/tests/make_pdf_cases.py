"""生成 chart-digitizer 的**自包含 PDF 测试用件**（编程构造，无外部素材）。

为什么必须自造而不是拿真实文献当断言基准：真实文献可能被移动/替换，且其排版是
偶然的。本模块把 SPEC §4.2 表格里列出的**每一个已知陷阱**都显式布置进 PDF，使
"图注消歧""栏切分""收紧""三通道回退"这些机制性行为可以被确定性断言。

产出两份文件
------------

``caseP1_paper.pdf`` —— **单页**（SPEC §4.2 明确"内含一页"），一页内布置全部陷阱：

===============================  ==================================================
元素                              考察点
===============================  ==================================================
正文段（10 pt，9 行）              正文字号基准；也是"图注上方正文段"的纵向上界来源
块内非首行的 ``Figure 3 for a …``   **图注消歧 G1**（P2 实测同形陷阱）
行中的 ``as shown in Figure 3 …``   正文引用不应被行首正则匹配
独立 1 行图注（9 pt，``|`` 分隔）   通道 A 命中；G2 的 size 分支与 separator 分支
``Fig. 5`` **出现两次**             歧义检测（``--figure 5`` 必须报歧义）
``Fig. 4`` 与 ``Fig. 3`` 几何相近   同页第二张图，两栏各一
轴标题 9.5 pt                      图例字号基准（S2）
图例文字 6.6 pt，同左 x，行距 6.7 pt 图例几何签名（S2）
图例色块 ``#7f7fff/#7fe57f/#ff7f7f`` 色块关联与精确取色（S2）
嵌套色块（外 ``#7fe57f`` 内 ``#3fd83f``） 芯色优先规则（S2）
近黑 5×0.5 pt 刻度短线              不得被误认为图例色块（S2）
图内面板标签 ``m/z 70``             文本 bbox 排除掩码（S2）
===============================  ==================================================

``caseP1_fallback.pdf`` —— 两页，分别单独考察通道 B 与通道 C：

* 第 1 页：一张占满整页的位图（必须被面积过滤器剔除）、一张图尺寸位图（必须命中）、
  一张 5×5 pt 小位图（必须被尺寸过滤器剔除）、**无任何文字** → 只可能走通道 B。
* 第 2 页：**只有矢量绘图**，无文字、无位图 → 只可能走通道 C。

通道 B/C 的判定被门控在"该页通道 A 无候选"之下，因此**不可能**与陷阱页同页存在；
这是把回退通道单独成页的原因。

图例色块取 8 bit 派生浮点（``0x7f/255`` 等）而非 ``0.5``：前者的 hex 与 P3 实测值
逐位一致（``#7f7fff`` / ``#7fe57f``），使"舍入边界"这件事由 ``pdf.hex_color`` 的
单元测试在**函数层**钉死，而夹具层保持"与真实文献同色"这一更强的性质。

用法::

    python make_pdf_cases.py            # 生成全部用件 + manifest
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

HERE = Path(__file__).resolve().parent
FIG = HERE / "figures"

PAPER_PDF = FIG / "caseP1_paper.pdf"
FALLBACK_PDF = FIG / "caseP1_fallback.pdf"
MANIFEST_JSON = FIG / "caseP1_manifest.json"

PAGE_W, PAGE_H = 595.276, 841.89
BODY = 10.0
CAPTION = 9.0
AXIS_TITLE = 9.5
LEGEND = 6.6
LEGEND_BASELINE_STEP = 6.7

LEFT_X0, LEFT_X1 = 50.0, 285.0
RIGHT_X0, RIGHT_X1 = 305.0, 540.0
FONT = "helv"

#: 版面几何（pt）。栏宽、图文间距都不是随手取的，各自有明确的考察目的：
#:
#: * 左栏正文最长行 231.8 pt（x1≈281.8）、右栏正文最短起点 305 → 栏间空带
#:   281.8–305（23.2 pt，中心 293.4），足以让 ``find_column_gutter`` 判出双栏。
#: * 正文段底 164.9 与 Fig. 3 图形顶 195 相差 30.1 pt，**大于** ``GAP_PT``=18，
#:   否则收紧时的内容带会把正文段桥接进图里（开发期实测踩到过）。
#: * 图例文字 x 相对栏左缘内缩 26 pt，面板标签 20 pt：都在
#:   ``PARAGRAPH_INSET_FRAC``=10% 之外，不会被误当成"图注上方的正文段"。
COL_L = (LEFT_X0, LEFT_X1)
COL_R = (RIGHT_X0, RIGHT_X1)

FIG3_BOX = (75.0, 195.0, 280.0, 315.0)
FIG4_BOX = (330.0, 195.0, 510.0, 315.0)
FIG5A_BOX = (75.0, 385.0, 280.0, 455.0)
FIG5B_BOX = (75.0, 530.0, 280.0, 600.0)

#: 图例色：8 bit 派生浮点，hex 与 P3 实测一致。
C_BLUE = (127 / 255, 127 / 255, 1.0)          # #7f7fff
C_GREEN = (127 / 255, 229 / 255, 127 / 255)   # #7fe57f（嵌套外层）
C_GREEN_CORE = (63 / 255, 216 / 255, 63 / 255)  # #3fd83f（嵌套芯色）
C_RED = (1.0, 127 / 255, 127 / 255)           # #ff7f7f
NESTED_OUTER, NESTED_CORE = "#7fe57f", "#3fd83f"

BOX_W, BOX_H = 5.0, 2.5     # 图例色块尺寸（pt），对齐 SPEC §3.2.2
TICK_W, TICK_H = 5.0, 0.5   # 刻度短线尺寸（pt），对齐 SPEC §4.2


# ------------------------------------------------------------------ 小工具


def text_width(text: str, size: float, font: str = FONT) -> float:
    """精确文本宽度（pt）。用于让段落/图注的 x 范围落在预期位置。"""
    return float(pymupdf.get_text_length(text, fontname=font, fontsize=size))


def line_box(baseline_y: float, size: float) -> tuple[float, float]:
    """``insert_text`` 用基线定位；本函数给出该行的近似 bbox 上下边。

    实测（PyMuPDF 1.28.2 / helv）：字号 10、基线 60 → 行 bbox ``y 49.2 … 63.0``，
    即上伸 1.08×size、下伸 0.30×size。S2 的"色块 y 区间与文字行重叠"判定依赖它。
    """
    return (baseline_y - 1.08 * size, baseline_y + 0.30 * size)


def _png_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _pattern_raster(w: int, h: int, *, base: int = 255, block: int = 24) -> np.ndarray:
    """一张确定性的彩色图案位图（避免随机数，便于复现）。"""
    arr = np.full((h, w, 3), base, dtype=np.uint8)
    for y in range(0, h, block * 2):
        arr[y : y + block, :, 0] = 200
        arr[y : y + block, :, 1] = 60
    for x in range(0, w, block * 3):
        arr[:, x : x + block, 2] = 180
    return arr


# ------------------------------------------------------- 单幅"图形"的绘制


def draw_figure(
    page: pymupdf.Page,
    *,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    legend_entries: list[tuple[str, tuple[float, float, float]]],
    nested_index: int | None = None,
    tick_values: tuple[float, ...] = (0.2, 0.4, 0.6, 0.8),
) -> dict:
    """在给定矩形内画一幅"论文插图"：左+下脊轴、刻度短线、曲线、图例、面板标签。

    返回该图的几何摘要（写进 manifest，供测试断言"夹具确实含这些元素"）。
    """
    spine = 0.8
    # 脊轴：左 + 下（开放框，与 P3 实测拓扑一致）
    page.draw_line(pymupdf.Point(x0, y0), pymupdf.Point(x0, y1), color=(0, 0, 0), width=spine)
    page.draw_line(pymupdf.Point(x0, y1), pymupdf.Point(x1, y1), color=(0, 0, 0), width=spine)

    # y 轴刻度短线：近黑、5×0.5 pt（易被误认为图例色块——SPEC §4.2 指定陷阱）
    tick_rects = []
    for v in tick_values:
        ty = y1 - v * (y1 - y0)
        r = pymupdf.Rect(x0 - TICK_W, ty - TICK_H / 2, x0, ty + TICK_H / 2)
        page.draw_rect(r, color=None, fill=(0, 0, 0))
        tick_rects.append([round(v_, 2) for v_ in (r.x0, r.y0, r.x1, r.y1)])

    # 曲线颜色**必须取自图例条目自己声明的颜色**（而不是固定的 [蓝,绿,红] 前缀）。
    #
    # 原实现用 `palette[:len(legend_entries)]`，于是 Fig.4（图例声明 蓝+红）画出的是
    # 蓝+绿——图例说红、图里没有红。这让 S2 的 R1 校验在这张图上**必然失败**，
    # 与本节文档声明的意图（"颜色与图例色块一致——S2 的 R1 校验据此可过"）相矛盾，
    # 且使 S2 拿不到"夹具上校验通过"的确定性正例。改为按图例声明取色后：
    #
    # * ``Fig. 3``（三条，其中绿为嵌套）→ 曲线画**外层** ``#7fe57f``，而图例取的是
    #   **芯色** ``#3fd83f`` → R1 如实失败。这是刻意保留的**负例**，正是 SPEC §3.2.3
    #   要防的"图例色块与图内色不符"。
    # * ``Fig. 4``（两条，无嵌套）→ 曲线与图例同色 → R1 通过，作为确定性**正例**。
    palette = [col for _, col in legend_entries]
    curves = []
    xs = np.linspace(x0 + 6, x1 - 4, 40)
    for i, col in enumerate(palette):
        span = (y1 - y0) * 0.5
        ys = y1 - span - 0.28 * span * np.sin(np.linspace(0, 2.5 * np.pi, xs.size) + i)
        pts = [pymupdf.Point(float(a), float(b)) for a, b in zip(xs, ys)]
        page.draw_polyline(pts, color=col, width=1.1)
        curves.append({"color": f"#{int(round(col[0]*255)):02x}{int(round(col[1]*255)):02x}"
                                f"{int(round(col[2]*255)):02x}"})

    # 图例：文字 6.6 pt，同左 x，行距 6.7 pt；色块紧贴文字左侧
    legend_x_text = x0 + 26.0
    legend_x_box = legend_x_text - 8.0
    legend_top = y0 + (y1 - y0) * 0.32
    entries = []
    for i, (name, col) in enumerate(legend_entries):
        base = legend_top + i * LEGEND_BASELINE_STEP
        page.insert_text((legend_x_text, base), name, fontsize=LEGEND, fontname=FONT,
                         color=(0, 0, 0))
        ly0, ly1 = line_box(base, LEGEND)
        by = 0.5 * (ly0 + ly1)
        box = pymupdf.Rect(legend_x_box, by - BOX_H / 2, legend_x_box + BOX_W, by + BOX_H / 2)
        page.draw_rect(box, color=None, fill=col)
        if nested_index is not None and i == nested_index:
            # 同一位置再叠一层更饱和的 fill → S2 必须取芯色
            page.draw_rect(box, color=None, fill=C_GREEN_CORE)
        entries.append(
            {
                "name": name,
                "hex": f"#{int(round(col[0]*255)):02x}{int(round(col[1]*255)):02x}"
                       f"{int(round(col[2]*255)):02x}",
                "baseline_y": round(base, 2),
                "text_x0": round(legend_x_text, 2),
                "line_bbox_y": [round(ly0, 2), round(ly1, 2)],
                "box_rect": [round(v, 2) for v in (box.x0, box.y0, box.x1, box.y1)],
                "nested_core": (
                    NESTED_CORE if (nested_index is not None and i == nested_index) else None
                ),
            }
        )

    # 图内面板标签：y 上刻意与图例文字分开（避免同块混排干扰 S2 的分组测试），
    # x 相对栏左缘内缩 20 pt。
    panel_label = "m/z 70" if x0 < 200 else "m/z 115"
    page.insert_text((x0 + 20.0, y0 + (y1 - y0) * 0.76), panel_label, fontsize=AXIS_TITLE,
                     fontname=FONT, color=(0, 0, 0))

    # 轴标题（字号基准，必须大于图例字号）
    title = "Photon Energy (eV)" if x0 < 200 else "Intensity (arb. units)"
    page.insert_text((x0 + 16.0, y1 + 22.0), title, fontsize=AXIS_TITLE, fontname=FONT,
                     color=(0, 0, 0))

    return {
        "graphics_rect": [round(v, 2) for v in (x0, y0, x1, y1)],
        "legend": entries,
        "legend_text_x": round(legend_x_text, 2),
        "legend_baseline_step": LEGEND_BASELINE_STEP,
        "legend_size": LEGEND,
        "axis_title": {"text": title, "size": AXIS_TITLE, "baseline_y": round(y1 + 22.0, 2)},
        "panel_label": {"text": panel_label, "size": AXIS_TITLE,
                        "baseline_y": round(y0 + (y1 - y0) * 0.76, 2)},
        "tick_rects": tick_rects,
        "tick_size_pt": [TICK_W, TICK_H],
        "curves": curves,
    }


# --------------------------------------------------------- caseP1_paper.pdf


BODY_PARA_LEFT = [
    "We measured the photon-energy dependence of the",
    "parent ion yield over the 8.0-11.5 eV range at a",
    "Figure 3 for a particular set of fit parameters.",   # ← 陷阱：块内非首行
    "The analysis of the remaining channels is reported",
    "as shown in Figure 3 for a particular set of fit",   # ← 行中引用（行首正则不应匹配）
    "channels and is not repeated here. A small error",
    "remains in the calibration, which we propagated",
    "through the whole reduction chain. The residuals",
    "were inspected visually and no outlier removal",
]

BODY_PARA_RIGHT = [
    "The right column carries the second figure of",
    "this synthetic page, which is deliberately",
    "similar in geometry to the first one so that a",
    "naive region estimator cannot tell the two apart.",
]

#: 陷阱行在左栏正文块中的行号（0 起）——与 P2 实测的"13 行块的第 8 行"同形。
TRAP_LINE_INDEX = 2
#: 行中引用所在行号（0 起）：行首正则**不应**匹配它。
MIDLINE_REF_INDEX = 4
#: ``insert_text`` 的行距系数（PyMuPDF 实测：字号 ×1.375）。
LINE_ADVANCE = 1.375


def _block_bottom(baseline_y0: float, size: float, n_lines: int) -> float:
    """多行 ``insert_text`` 块的底边（实测：块顶 = 基线 − 1.08×字号）。"""
    return (baseline_y0 - 1.08 * size) + n_lines * LINE_ADVANCE * size


def _assert_layout(man: dict) -> None:
    """自检夹具几何：生成器自己先守住"栏内不越界、图文间距够大"这两条约束。

    二者越界都是**静默**的：

    * 左栏正文伸进右栏 → x 投影上的栏间空带消失 → 双栏页被判成单栏 → 两张图都被
      裁成整页宽（开发期实测踩过）；
    * 正文段与图形距离 < ``GAP_PT`` → 收紧时内容带把正文桥接进图里（同样实测踩过）。
    """
    left_max = max(man["elements"]["body_left"]["line_widths"])
    right_max = max(man["elements"]["body_right"]["line_widths"])
    assert LEFT_X0 + left_max < RIGHT_X0 - 8.0, (
        f"左栏正文 x1={LEFT_X0 + left_max:.1f} 距右栏 {RIGHT_X0} 不足 8 pt，"
        "栏间空带会消失"
    )
    assert RIGHT_X0 + right_max < RIGHT_X1 + 12.0, "右栏正文越出栏宽"
    for key in ("fig3", "fig4"):
        x0, y0, x1, y1 = man["elements"][key]["graphics_rect"]
        col = COL_L if x0 < 200 else COL_R
        assert x0 >= col[0] and x1 <= col[1] + 1.0, f"{key} 图形越出栏范围"
    body_bottom = _block_bottom(52.0, BODY, len(BODY_PARA_LEFT))
    assert FIG3_BOX[1] - body_bottom > 18.0, (
        f"正文段底 {body_bottom:.1f} 与 Fig. 3 顶 {FIG3_BOX[1]} 的间距不足 GAP_PT=18"
    )


def make_paper(path: Path = PAPER_PDF) -> dict:
    """生成陷阱页 PDF（单页）。返回可供测试断言的几何 manifest。"""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    man: dict = {"pdf": str(path), "page_size": [PAGE_W, PAGE_H], "elements": {}}

    # 页眉（单行、不跨幅 → 纵向上界将走 page-margin 兜底）
    page.insert_text((LEFT_X0, 34.0), "Case P1 synthetic paper for chart-digitizer",
                     fontsize=CAPTION, fontname=FONT, color=(0, 0, 0))

    # 左栏正文段（含两处 Figure 3 干扰）
    body_left = "\n".join(BODY_PARA_LEFT)
    page.insert_text((LEFT_X0, 52.0), body_left, fontsize=BODY, fontname=FONT,
                     color=(0, 0, 0))
    man["elements"]["body_left"] = {
        "x0": LEFT_X0, "baseline_y0": 52.0, "n_lines": len(BODY_PARA_LEFT),
        "size": BODY,
        "line_widths": [round(text_width(t, BODY), 1) for t in BODY_PARA_LEFT],
        "trap_block_line_index": TRAP_LINE_INDEX,
        "trap_line_text": BODY_PARA_LEFT[TRAP_LINE_INDEX],
        "trap_block_n_lines": len(BODY_PARA_LEFT),
        "midline_reference_index": MIDLINE_REF_INDEX,
        "midline_reference_text": BODY_PARA_LEFT[MIDLINE_REF_INDEX],
    }
    body_right = "\n".join(BODY_PARA_RIGHT)
    page.insert_text((RIGHT_X0, 52.0), body_right, fontsize=BODY, fontname=FONT,
                     color=(0, 0, 0))
    man["elements"]["body_right"] = {
        "x0": RIGHT_X0, "baseline_y0": 52.0, "n_lines": len(BODY_PARA_RIGHT),
        "size": BODY,
        "line_widths": [round(text_width(t, BODY), 1) for t in BODY_PARA_RIGHT],
    }

    # --- Fig. 3（左栏）
    fig3 = draw_figure(
        page, x0=FIG3_BOX[0], y0=FIG3_BOX[1], x1=FIG3_BOX[2], y1=FIG3_BOX[3],
        legend_entries=[("TD 415 K", C_BLUE), ("TD 493 K", C_GREEN), ("RH", C_RED)],
        nested_index=1,
    )
    cap3 = "Fig. 3 | Test figure for digitizer."
    page.insert_text((LEFT_X0, 357.0), cap3, fontsize=CAPTION, fontname=FONT,
                     color=(0, 0, 0))
    man["elements"]["fig3"] = {
        **fig3, "label": "3", "page_no": 0, "column": "left",
        "caption": {"text": cap3, "baseline_y": 357.0, "size": CAPTION,
                    "separator": "|", "n_lines": 1},
    }

    # --- Fig. 5 ×2（同编号 → 歧义）
    fig5a = draw_figure(page, x0=FIG5A_BOX[0], y0=FIG5A_BOX[1], x1=FIG5A_BOX[2],
                        y1=FIG5A_BOX[3], legend_entries=[("RH", C_RED)],
                        tick_values=(0.5,))
    cap5a = "Fig. 5 | First duplicate label for ambiguity."
    page.insert_text((LEFT_X0, 497.0), cap5a, fontsize=CAPTION, fontname=FONT,
                     color=(0, 0, 0))
    fig5b = draw_figure(page, x0=FIG5B_BOX[0], y0=FIG5B_BOX[1], x1=FIG5B_BOX[2],
                        y1=FIG5B_BOX[3], legend_entries=[("RH", C_RED)],
                        tick_values=(0.5,))
    cap5b = "Fig. 5 | Second duplicate label for ambiguity."
    page.insert_text((LEFT_X0, 642.0), cap5b, fontsize=CAPTION, fontname=FONT,
                     color=(0, 0, 0))
    man["elements"]["fig5a"] = {**fig5a, "label": "5", "page_no": 0, "column": "left",
                                "caption": {"text": cap5a, "baseline_y": 497.0,
                                            "size": CAPTION, "n_lines": 1}}
    man["elements"]["fig5b"] = {**fig5b, "label": "5", "page_no": 0, "column": "left",
                                "caption": {"text": cap5b, "baseline_y": 642.0,
                                            "size": CAPTION, "n_lines": 1}}

    # --- Fig. 4（右栏，与 Fig. 3 几何相近）
    fig4 = draw_figure(
        page, x0=FIG4_BOX[0], y0=FIG4_BOX[1], x1=FIG4_BOX[2], y1=FIG4_BOX[3],
        legend_entries=[("TD 415 K", C_BLUE), ("RH", C_RED)],
    )
    cap4 = "Fig. 4 | Geometrically similar second figure."
    page.insert_text((RIGHT_X0, 357.0), cap4, fontsize=CAPTION, fontname=FONT,
                     color=(0, 0, 0))
    man["elements"]["fig4"] = {
        **fig4, "label": "4", "page_no": 0, "column": "right",
        "caption": {"text": cap4, "baseline_y": 357.0, "size": CAPTION,
                    "separator": "|", "n_lines": 1},
    }

    man["table_captions"] = []
    _assert_layout(man)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    man["ambiguous_labels"] = ["5"]
    return man


# ------------------------------------------------------ caseP1_fallback.pdf


def make_fallback(path: Path = FALLBACK_PDF) -> dict:
    """生成通道 B / 通道 C 的专用页（各一页，均无文字）。"""
    doc = pymupdf.open()
    man: dict = {"pdf": str(path), "pages": {}}

    # --- 第 1 页：通道 B（位图）
    p0 = doc.new_page(width=PAGE_W, height=PAGE_H)
    full = _pattern_raster(300, 420)
    p0.insert_image(pymupdf.Rect(0, 0, PAGE_W, PAGE_H), stream=_png_bytes(full))
    fig_rect = pymupdf.Rect(60.0, 80.0, 460.0, 380.0)
    inner = np.full((240, 320, 3), 255, dtype=np.uint8)
    inner[30:210, 40:280] = (30, 30, 200)
    inner[100:130, 20:300] = (200, 30, 30)
    p0.insert_image(fig_rect, stream=_png_bytes(inner))
    tiny = pymupdf.Rect(500.0, 500.0, 505.0, 505.0)
    p0.insert_image(tiny, stream=_png_bytes(np.full((16, 16, 3), 90, dtype=np.uint8)))
    man["pages"]["raster"] = {
        "page_no": 0,
        "page_covering_raster": [0.0, 0.0, PAGE_W, PAGE_H],
        "figure_raster": [60.0, 80.0, 460.0, 380.0],
        "tiny_raster": [500.0, 500.0, 505.0, 505.0],
        "expected_source": "raster",
        "expected_count": 1,
    }

    # --- 第 2 页：通道 C（纯矢量，无文字无位图）
    p1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    gx0, gy0, gx1, gy1 = 80.0, 120.0, 440.0, 360.0
    p1.draw_line(pymupdf.Point(gx0, gy0), pymupdf.Point(gx0, gy1), color=(0, 0, 0), width=1.0)
    p1.draw_line(pymupdf.Point(gx0, gy1), pymupdf.Point(gx1, gy1), color=(0, 0, 0), width=1.0)
    xs = np.linspace(gx0 + 8, gx1 - 6, 60)
    ys = gy1 - 120 - 80 * np.sin(np.linspace(0, 2.2 * np.pi, xs.size))
    p1.draw_polyline([pymupdf.Point(float(a), float(b)) for a, b in zip(xs, ys)],
                     color=C_BLUE, width=1.4)
    for k in range(1, 5):
        ty = gy1 - k * (gy1 - gy0) / 5.0
        p1.draw_line(pymupdf.Point(gx0 - 5, ty), pymupdf.Point(gx0, ty),
                     color=(0, 0, 0), width=0.6)
    man["pages"]["vector"] = {
        "page_no": 1,
        "graphics_rect": [gx0, gy0, gx1, gy1],
        "expected_source": "vector-cluster",
        "expected_count": 1,
        "max_confidence": 0.4,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return man


# ------------------------------------------------------------------- main


def build_all(*, verbose: bool = True) -> dict:
    """生成全部 PDF 用件与 manifest，返回 manifest 字典。"""
    FIG.mkdir(parents=True, exist_ok=True)
    man = {
        "generator": "make_pdf_cases.py",
        "pymupdf": pymupdf.version[0],
        "paper": make_paper(),
        "fallback": make_fallback(),
    }
    MANIFEST_JSON.write_text(json.dumps(man, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    if verbose:
        for p in (PAPER_PDF, FALLBACK_PDF):
            d = pymupdf.open(str(p))
            sizes = ", ".join(f"{pg.rect.width:.0f}x{pg.rect.height:.0f}" for pg in d)
            print(f"{p.name:24s} {d.page_count} 页  {sizes}")
            d.close()
        print(f"{MANIFEST_JSON.name:24s} 几何 manifest 已写出")
    return man


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    build_all(verbose=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
