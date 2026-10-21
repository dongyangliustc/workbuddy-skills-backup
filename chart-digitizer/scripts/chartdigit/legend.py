"""文献插图图例提取与颜色一致性校验（S2）。

吃 :mod:`chartdigit.pdf` 定位到的图形矩形，产出三样东西：

1. **图例条目** —— 名称 + 精确 RGB + 图元类型提示。文字取自 PDF **文本层**
   （零 OCR；``tesseract`` 未安装，亦不引入任何新依赖），颜色取自同页**矢量对象**
   （RGB 精确、无抗锯齿）。
2. **R1 颜色一致性校验** —— 本方案**唯一会静默出错**的风险点，见
   :func:`verify_legend_colors`。校验不通过时调用方**必须回退**自动配色。
3. **排除掩码** —— 把图内全部文字行的 bbox 换算成裁剪图像素矩形，供
   ``extract_series(exclude=...)`` 确定性剔除面板标签 / 轴标题 / 图例文字。
   换算见 :func:`text_box_to_px`（全链路最容易静默错位的一步，独立成函数并单测）。

设计原则（与 SPEC §0 硬性边界一致）：

* **图例是可选增强**：任何环节失败都返回 ``found=False`` 或 ``ok=False``，
  由调用方静默回退，绝不让整条链路失败。
* **绝不猜**：颜色关联不到就如实置 ``None`` 并让校验失败，不拿邻近对象顶替。
* 阈值全部来自实测（P3 = ``Communications Chemistry, 4(1), 72.pdf``），
  见 ``references/pdf-legend.md`` §10。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pymupdf

from . import pdf as _pdf
from .extract import color_mask

# --------------------------------------------------------------- 阈值（SPEC §3.2.1）

#: 图例字号 / 区域内最大字号的上限比。P3 实测 6.6 / 9.5 = 0.69 < 0.80 ✓
LEGEND_SIZE_RATIO = 0.80
#: 组内左边界 x 的极差上限（pt）。P3 实测三条均为 x = 87.3（极差 0）✓
LEGEND_ALIGN_TOL_PT = 3.0
#: 行距 / 行高 的上限比。P3 实测行推进 6.7 pt、字号 6.6 pt → 比值 1.015 < 1.6 ✓
LEGEND_SPACING_RATIO = 1.6
#: 组内条目数下限。单条无法与轴标签区分，必须有 ≥2 条共线。
LEGEND_MIN_ITEMS = 2

# ------------------------------------------------- 阈值（SPEC §3.2.2 色块关联）

#: 色块必须落在文字行 bbox 的 y 区间内（上下各放宽 3 pt）
SWATCH_Y_SLACK_PT = 3.0
#: 色块右端必须不越过文字左边界（放宽 0.5 pt）
SWATCH_X_SLACK_PT = 0.5
#: 色块最小宽度（pt）：滤掉零宽线段与点状图元
SWATCH_MIN_WIDTH_PT = 3.0
#: 色块右端与文字左边界之间允许的最大空隙（pt）。
#:
#: **这是 SPEC §3.2.2 字面判据之外的加固**。SPEC 只要求「在文字左侧」，
#: 但真实期刊里曲线常被拆成大量小线段，某一小段完全可能同时满足
#: 「在文字左侧」「宽度 > 3」「y 落在文字行内」——而 R1 校验**抓不住**它
#: （该颜色在图内当然大量存在），于是会静默地把曲线段的颜色当成图例色。
#: P3 实测色块 x ∈ [78.8, 83.9]、文字 x = 87.3 → 空隙仅 3.4 pt；
#: 取 12 pt 留约 3.5 倍余量。
SWATCH_MAX_GAP_PT = 12.0
#: 「近灰 / 近黑」判据：``(max(rgb) - min(rgb)) * 255`` 低于此值即视为无彩色。
#: 刻度短线与文字都是近黑（P3 实测 ``#000000``），必须排除。
NEUTRAL_CHROMA = 25.0
#: 图例色块的最小面积（pt²），用于排除亚像素级碎片
SWATCH_MIN_AREA_PT2 = 1.0

# ------------------------------------------------- 阈值（SPEC §3.2.2 第 3 步）

#: 长宽比 > 此值 → ``line``
KIND_LINE_ASPECT = 3.0
#: 长宽比 < 此值 → ``marker``（散点）
KIND_MARKER_ASPECT = 1.0 / 3.0
#: 端点垂直短划的判定：高宽比 ≥ 此值
KIND_TICK_ASPECT = 2.0
#: 端点短划允许落在色块右端之内 / 之外的 pt 数
KIND_TICK_BACK_PT = 1.5
KIND_TICK_FWD_PT = 3.0

# --------------------------------------------------------------- 阈值（R1 校验）

#: 颜色匹配容差——与 ``extract.color_mask`` 的默认 tol 一致（SPEC 原话：
#: "复用现有 color_mask / dominant_colors 的距离函数，不要另写一套"）。
VERIFY_TOL = 64.0
#: 单个图例颜色在图形区域内至少要命中的像素数
VERIFY_MIN_PIXELS = 30
#: 排除掩码的外扩像素数。文字 bbox 是紧贴字形的，抗锯齿光晕会溢出 1–2 px；
#: 不外扩会让掩码边缘留下一条"文字色"的细线，在 scatter 路径下可能被当成标记点。
EXCLUDE_PAD_PX = 2


# --------------------------------------------------------------------- 数据模型


@dataclass
class TextRow:
    """图内的一条文字行（PDF 点坐标，y 自顶向下）。"""

    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    text: str

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 1e-6)


@dataclass
class Swatch:
    """一次成功的色块关联。"""

    rgb: tuple[float, float, float]
    hex: str
    rect_pt: tuple[float, float, float, float]
    drawing_type: str            # "s" 描边 / "f" 填充 / "fs"
    kind_hint: str
    n_geo: int                   # 落进几何候选集的对象数（含被排除的刻度短线）
    n_kept: int                  # 几何候选去掉近灰/近黑后的对象数

    @property
    def chroma(self) -> float:
        return (max(self.rgb) - min(self.rgb)) * 255.0


@dataclass
class LegendEntry:
    """一条图例条目。``color`` 为 ``None`` 表示未关联到色块（校验必然失败）。"""

    name: str
    color: str | None = None
    kind_hint: str = ""
    source: str = "pdf-text"     # pdf-text | pdf-text+vector
    matched_pixels: int = 0
    matched_pixels_plot: int | None = None
    row: TextRow | None = None
    swatch: Swatch | None = None

    @property
    def rgb255(self) -> tuple[int, int, int] | None:
        """图例色的 8 bit 三元组（供 ``extract_series(colors=...)`` 使用）。"""
        if not self.color:
            return None
        h = self.color.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def series_name(self) -> str:
        return legend_series_name(self)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "color": self.color,
            "kind_hint": self.kind_hint,
            "matched_pixels": self.matched_pixels,
        }
        if self.matched_pixels_plot is not None:
            d["matched_pixels_plot"] = self.matched_pixels_plot
        if self.row is not None:
            d["line_bbox_pt"] = [round(v, 2) for v in self.row.box]
            d["font_size_pt"] = round(self.row.size, 2)
        if self.swatch is not None:
            d["swatch_rect_pt"] = [round(v, 2) for v in self.swatch.rect_pt]
            d["swatch_type"] = self.swatch.drawing_type
            d["n_geo_candidates"] = self.swatch.n_geo
        d["source"] = self.source
        return d


@dataclass
class LegendCheck:
    """R1 颜色一致性校验结果。"""

    ok: bool = False
    checked: int = 0
    matched: int = 0
    tol: float = VERIFY_TOL
    min_pixels: int = VERIFY_MIN_PIXELS
    hits: list[int] = field(default_factory=list)
    plot_hits: list[int] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ok": self.ok,
            "checked": self.checked,
            "matched": self.matched,
            "tol": self.tol,
            "min_pixels": self.min_pixels,
            "hits": list(self.hits),
        }
        if self.plot_hits:
            d["plot_hits"] = list(self.plot_hits)
        if self.missing:
            d["missing"] = list(self.missing)
        if self.reason:
            d["reason"] = self.reason
        return d


@dataclass
class LegendInfo:
    """一次图例提取的全部结果（交付物中的「图例」区块即由此渲染）。"""

    found: bool
    source: str = "pdf-text"
    entries: list[LegendEntry] = field(default_factory=list)
    color_check: LegendCheck | None = None
    fallback: str = ""
    fallback_detail: dict[str, Any] = field(default_factory=dict)
    #: 供 ``extract_series(exclude=...)`` 使用的**像素**矩形（已按 dpi 与 rect_pt 换算）
    exclude_boxes_px: list[tuple[float, float, float, float]] = field(default_factory=list)
    #: 文字行的 PDF 点矩形（供报告与复核）
    text_boxes_pt: list[tuple[float, float, float, float]] = field(default_factory=list)
    #: 色块本身的 PDF 点矩形
    swatch_boxes_pt: list[tuple[float, float, float, float]] = field(default_factory=list)
    note: str = ""

    @property
    def usable(self) -> bool:
        """是否可直接采用图例配色（全部条目有颜色 **且** 校验通过）。"""
        return bool(
            self.found
            and self.entries
            and all(e.color for e in self.entries)
            and self.color_check is not None
            and self.color_check.ok
        )

    def colors(self) -> list[tuple[int, int, int]]:
        return [c for c in (e.rgb255 for e in self.entries) if c is not None]

    def names(self) -> list[str]:
        return [e.series_name() for e in self.entries]

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "found": bool(self.found),
            "source": self.source,
            "entries": [e.to_dict() for e in self.entries],
            "color_check": self.color_check.to_dict() if self.color_check else None,
            "fallback": self.fallback,
        }
        if self.fallback_detail:
            d["fallback_detail"] = self.fallback_detail
        if self.note:
            d["note"] = self.note
        d["usable"] = self.usable
        return d


# ------------------------------------------------------------------ 基础工具


def legend_series_name(entry: LegendEntry) -> str:
    """SPEC §3.2.4：名称**始终与颜色 hex 并存**（``TD 415 K #7f7fff``）。

    理由：``c1_d93637`` 一看便知是占位符；错命名为 ``TD 415 K`` 则**看起来可信**。
    保留 hex 使错误命名可被发现、可被核对。

    名称先做空白规整，避免 PDF 文本层的首尾空格在交付物里拼出双空格
    （``" X " + "#010203"`` → ``"X  #010203"``），使序列名无法稳定比对。
    """
    name = " ".join(entry.name.split())
    if entry.color:
        return f"{name} {entry.color}".strip()
    return name


def text_rows(page: pymupdf.Page, rect: pymupdf.Rect | Sequence[float]) -> list[TextRow]:
    """取出 ``rect`` 内全部文字行（按 y 升序）。

    只用 **PDF 文本层**——本方案零 OCR。
    """
    r = pymupdf.Rect(rect)
    out: list[TextRow] = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type", 0) != 0:
            continue
        for ln in b.get("lines", ()):
            bb = pymupdf.Rect(ln["bbox"])
            if not bb.intersects(r):
                continue
            txt = "".join(s.get("text", "") for s in ln.get("spans", ()))
            if not txt.strip():
                continue
            out.append(TextRow(float(bb.x0), float(bb.y0), float(bb.x1), float(bb.y1),
                               round(_pdf._line_size(ln), 3), txt.strip()))
    out.sort(key=lambda x: (x.y0, x.x0))
    return out


def text_box_to_px(
    box_pt: Sequence[float],
    rect_pt: Sequence[float],
    dpi: int,
    pad_px: int = EXCLUDE_PAD_PX,
) -> tuple[float, float, float, float]:
    """文字行 bbox（PDF **点**） → 裁剪 PNG 的**像素**矩形。

    换算 = ``(pt − rect_pt) × dpi/72``，再按 ``pad_px`` 外扩（抗锯齿光晕）。

    **本函数是排除掩码的唯一入口，单独成函数并单独测**——它是整条链路里最容易
    静默错位的一步：少减一次 ``rect_pt`` 偏移、或把 ``dpi/72`` 写成 ``72/dpi``，
    掩码就会糊到图的另一处，而**提取结果照常产出**、不会报错。
    """
    x0, y0 = _pdf.pt_to_px((box_pt[0], box_pt[1]), rect_pt, dpi)
    x1, y1 = _pdf.pt_to_px((box_pt[2], box_pt[3]), rect_pt, dpi)
    p = max(0, int(pad_px))
    return (x0 - p, y0 - p, x1 + p, y1 + p)


def exclusion_boxes_px(
    rows: Iterable[TextRow],
    rect_pt: Sequence[float],
    dpi: int,
    pad_px: int = EXCLUDE_PAD_PX,
) -> list[tuple[float, float, float, float]]:
    """一批文字行 → 裁剪图像素矩形（排除掩码的输入）。"""
    return [text_box_to_px(r.box, rect_pt, dpi, pad_px) for r in rows]


def resolve_dpi(
    image_width_px: int,
    rect_pt: Sequence[float],
    *,
    sidecar: str | Path | None = None,
    tol_pt: float = 0.5,
) -> tuple[int, str]:
    """确定裁剪 PNG 的渲染 dpi → ``(dpi, 告警说明)``。

    换算 ``点 → 像素`` 必须知道 PNG 是按哪个 dpi 渲染的。两个来源，按可靠性排序：

    1. **溯源 sidecar**（``from-pdf`` 在 PNG 旁写的同名 ``.json``）——权威值；
       但只在它的 ``rect_pt`` 与调用方给的 ``--rect`` 一致时才采信，否则说明
       用户拿的是**另一张图**的 PNG，此时任何换算都是错的。
    2. 由 ``image_width_px / rect 宽度 × 72`` **反推**——PNG 是 ``clip=`` 局部渲染，
       宽度严格等于 ``rect 宽 × dpi/72``，反推是精确的。

    两条路径都不成立时返回 ``(_pdf.DEFAULT_DPI, 告警)``：调用方应把告警转达给用户，
    而不是静默用一个可能错的 dpi 去糊掩码。
    """
    w_pt = abs(float(rect_pt[2]) - float(rect_pt[0]))
    warn = ""
    if sidecar is not None:
        p = Path(sidecar)
        if p.is_file():
            try:
                meta = json.loads(p.read_text(encoding="utf-8"))
                s_rect = [float(v) for v in meta.get("rect_pt", [])]
                s_dpi = int(meta.get("dpi") or 0)
                if s_dpi > 0 and len(s_rect) == 4 and all(
                    abs(a - b) <= tol_pt for a, b in zip(s_rect, rect_pt)
                ):
                    return s_dpi, ""
                if s_dpi > 0:
                    warn = (f"PNG 溯源 sidecar 记录的矩形 {s_rect} 与 --rect "
                            f"{[round(float(v), 2) for v in rect_pt]} 不一致，"
                            "已改用反推 dpi；排除掩码可能与图错位，请核对")
            except Exception:  # noqa: BLE001 —— sidecar 坏了不该阻断链路
                warn = "PNG 溯源 sidecar 无法解析，已改用反推 dpi"
    if w_pt > 0 and image_width_px > 0:
        return int(round(image_width_px / w_pt * 72.0)), warn
    return _pdf.DEFAULT_DPI, (warn + "；rect 宽度为 0，dpi 退回默认 "
                              f"{_pdf.DEFAULT_DPI}，排除掩码可能错位")


# ---------------------------------------------------------- SPEC §3.2.1 几何签名


def group_legend_lines(
    rows: Sequence[TextRow],
    *,
    size_ratio: float = LEGEND_SIZE_RATIO,
    align_tol: float = LEGEND_ALIGN_TOL_PT,
    spacing_ratio: float = LEGEND_SPACING_RATIO,
    min_items: int = LEGEND_MIN_ITEMS,
) -> list[list[TextRow]]:
    """按 SPEC §3.2.1 的几何签名把文字行切成图例组。

    四条**必须同时满足**才成组：

    ==========================  ============================================
    条件                         P3 实测依据
    ==========================  ============================================
    ``size < 0.80 × 区域内最大字号``  6.6 vs 轴标题 9.5 pt（比值 0.69）✓
    组内左边界 x 极差 ``≤ 3 pt``        三条均为 x = 87.3 ✓
    行距（top→top）``< 1.6 × 行高``     6.7 pt 行距 vs 6.6 pt 字号 ✓
    组内条目数 ``≥ 2``                  单条无法与轴标签区分
    ==========================  ============================================

    「行距」取 **top→top 推进量**（排版学上的行距含义）。为什么不取
    「bottom→top 间隙」：夹具里文字行 bbox 高 9.06 pt 而基线推进只有 6.7 pt
    （行 bbox 相互重叠），间隙式读法恒为负、恒满足，等于没有这条判据。

    逐行贪心而非全组合枚举：真实文献图例最多十余条，但同区域内文字行可达数百，
    枚举是 O(n²) 起步且没有判别力收益。
    """
    if not rows:
        return []
    max_size = max(r.size for r in rows)
    cands = sorted((r for r in rows if r.size < size_ratio * max_size),
                   key=lambda r: (r.y0, r.x0))
    if not cands:
        return []

    groups: list[list[TextRow]] = []
    cur: list[TextRow] = [cands[0]]
    for r in cands[1:]:
        merged = cur + [r]
        spread = max(x.x0 for x in merged) - min(x.x0 for x in merged)
        line_h = max(x.height for x in merged)
        step = r.y0 - cur[-1].y0
        if spread <= align_tol and step < spacing_ratio * line_h:
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    groups.append(cur)
    return [g for g in groups if len(g) >= min_items]


def pick_legend_group(groups: Sequence[Sequence[TextRow]]) -> list[TextRow] | None:
    """从多个候选组里选定一个：**条目最多者优先，并列时取最靠上者**。

    选「最多」而不是「最靠上」：图内孤立的双行小字（如两行刻度标签叠在一起）
    会先成组，按位置取会误选。选「最靠上」只是并列时的确定性 tie-break。
    """
    if not groups:
        return None
    best = max(groups, key=lambda g: (len(g), -g[0].y0))
    return list(best)


# ------------------------------------------------------- SPEC §3.2.2 色块关联


def _drawing_chroma(d: dict) -> float:
    """绘图对象颜色的彩度（0–255）；``type`` 含 ``s`` 者看描边色，否则看填充色。

    PyMuPDF 的 ``type`` 取 ``f`` / ``s`` / ``fs``；``fs``（同时描边与填充）按描边
    处理——SPEC「优先描边色，因为是线段样本的真实颜色」的语义对 ``fs`` 同样成立。
    """
    c = d.get("color") if "s" in str(d.get("type", "")) else d.get("fill")
    if not c:
        return 0.0
    return (max(c) - min(c)) * 255.0


def is_neutral(d: dict, drop_gray: float = NEUTRAL_CHROMA) -> bool:
    """近灰 / 近黑 —— 刻度短线与文字的共同时特征（SPec §3.2.2 第 2 步）。

    P3 实测混入几何候选集的是 ``#000000``、5×0.5 pt 的横向短划，彩度 0。
    """
    return _drawing_chroma(d) < drop_gray


def geo_candidates(
    drawings: Sequence[dict],
    *,
    y0: float,
    y1: float,
    x0: float,
    y_slack: float = SWATCH_Y_SLACK_PT,
    x_slack: float = SWATCH_X_SLACK_PT,
    min_width: float = SWATCH_MIN_WIDTH_PT,
    max_gap: float = SWATCH_MAX_GAP_PT,
    min_area: float = SWATCH_MIN_AREA_PT2,
) -> list[dict]:
    """SPEC §3.2.2 第 1 步：文字**左侧**、y 落在文字行内的绘图对象。

    ``max_gap`` 见 :data:`SWATCH_MAX_GAP_PT` 的说明（SPEC 字面判据之外的加固）。
    """
    out: list[dict] = []
    for d in drawings:
        r = pymupdf.Rect(d["rect"])
        if not (r.y0 > y0 - y_slack and r.y1 < y1 + y_slack):
            continue
        if r.x1 > x0 + x_slack:                      # 不在文字左侧
            continue
        if x0 - r.x1 > max_gap:                      # 离文字太远（多半是曲线残段）
            continue
        if r.width <= min_width:
            continue
        if r.width * r.height < min_area:
            continue
        out.append(d)
    return out


def _end_tick_present(rect, neighbours: Sequence[dict]) -> bool:
    """色块右端是否存在垂直短划（误差棒图元的特征）。"""
    for d in neighbours:
        r = pymupdf.Rect(d["rect"])
        if r == rect:
            continue
        if r.height <= 0 or r.width <= 0:
            continue
        if r.height / r.width < KIND_TICK_ASPECT:
            continue
        if r.x1 >= rect.x1 - KIND_TICK_BACK_PT and r.x0 <= rect.x1 + KIND_TICK_FWD_PT:
            return True
    return False


def kind_hint_from_swatch(rect, neighbours: Sequence[dict] = ()) -> str:
    """SPEC §3.2.2 第 3 步：色块几何 → 图元类型提示。

    仅写入交付物供 ``extract --kind`` **参考，不作强制**。

    * 右端带垂直短划 → ``errorbar``（横线段 + 端点竖线即误差棒的标准画法）
    * 长宽比 ``w/h > 3`` → ``line``
    * ``1/3 < w/h < 3`` → ``marker``（散点）
    * 其余（``w/h ≤ 1/3``，竖长条形）→ ``errorbar``

    SPEC 只给了前三条；末条是本实现补齐的空档——竖长条色块最合理的解释就是
    误差棒，而 ``kind_hint`` 不强制，猜错的代价仅是一个提示值。
    """
    r = pymupdf.Rect(rect)
    if r.height <= 0 or r.width <= 0:
        return ""
    if _end_tick_present(r, neighbours):
        return "errorbar"
    aspect = r.width / r.height
    if aspect > KIND_LINE_ASPECT:
        return "line"
    if KIND_MARKER_ASPECT < aspect < KIND_LINE_ASPECT:
        return "marker"
    return "errorbar"


def pick_swatch(
    drawings: Sequence[dict],
    *,
    y0: float,
    y1: float,
    x0: float,
    max_gap: float = SWATCH_MAX_GAP_PT,
    min_width: float = SWATCH_MIN_WIDTH_PT,
) -> Swatch | None:
    """SPEC §3.2.2 取色优先级 → :class:`Swatch`（找不到返回 ``None``）。

    优先级：``type`` 含 ``s``（描边）的颜色 → 否则取填充色中**彩度最高**者
    （同一 y 区间出现多个嵌套 fill 时内层才是芯色；P3 实测外层 ``#7fe57f``、
    内层 ``#3fd83f``，内层饱和度高）。近灰/近黑一律跳过。

    多重描边时取 **x1 最大者**（离文字最近），因为远处的残段更可能是曲线。

    ``max_gap`` / ``min_width`` 可下调以复现"距离加固被关掉"的场景——单元测试据此
    证明**近黑判据本身**足以挡住刻度短线，而不只是靠距离加固顺带挡住。
    """
    geo = geo_candidates(drawings, y0=y0, y1=y1, x0=x0, max_gap=max_gap,
                         min_width=min_width)
    kept = [d for d in geo if not is_neutral(d)]
    if not kept:
        return None

    strokes = [d for d in kept if "s" in str(d.get("type", "")) and d.get("color")]
    if strokes:
        best = max(strokes, key=lambda d: pymupdf.Rect(d["rect"]).x1)
        col = best["color"]
        dtype = str(best.get("type", "s"))
    else:
        fills = [d for d in kept if d.get("fill")]
        if not fills:
            return None
        best = max(fills, key=lambda d: (_drawing_chroma(d),
                                        pymupdf.Rect(d["rect"]).x1))
        col = best["fill"]
        dtype = str(best.get("type", "f"))

    r = pymupdf.Rect(best["rect"])
    rgb = (float(col[0]), float(col[1]), float(col[2]))
    band = [d for d in drawings
            if pymupdf.Rect(d["rect"]).y0 > y0 - SWATCH_Y_SLACK_PT
            and pymupdf.Rect(d["rect"]).y1 < y1 + SWATCH_Y_SLACK_PT]
    return Swatch(
        rgb=rgb,
        hex=_pdf.hex_color(rgb),
        rect_pt=(r.x0, r.y0, r.x1, r.y1),
        drawing_type=dtype,
        kind_hint=kind_hint_from_swatch(r, band),
        n_geo=len(geo),
        n_kept=len(kept),
    )


# ------------------------------------------------------- SPEC §3.2.3 R1 校验


def verify_legend_colors(
    rgb: np.ndarray,
    region: Sequence[int] | None,
    legend_colors: Sequence[Sequence[float] | str],
    *,
    tol: float = VERIFY_TOL,
    min_pixels: int = VERIFY_MIN_PIXELS,
    swatch_boxes: Sequence[Sequence[int]] | None = None,
    plot_min_pixels: int | None = None,
) -> LegendCheck:
    """**R1 颜色一致性校验**（强制，不可省）→ :class:`LegendCheck`。

    这是本方案**唯一会静默出错**的风险点：若期刊对图例色块做了淡化 / 换色 /
    加边框，按图例颜色去提取会**一条曲线都提不到**，或提到错误对象——而产物
    看上去完全正常。故必须程序化校验，失败即回退。

    对每个图例颜色，在**图形区域内**统计与它距离 < ``tol`` 的像素数（复用
    ``extract.color_mask`` 的距离函数，**不另写一套**）：

    * 全部命中且均 ≥ ``min_pixels`` → ``ok=True``
    * 任一命中数为 0 或 < ``min_pixels`` → ``ok=False``，``missing`` 列出该色
    * 命中数与图例条目数不匹配 → ``ok=False``

    ``swatch_boxes`` 给定时额外统计 **剔除图例色块后**的命中数（``plot_hits``）：
    色块本身就在图形区域内，实测夹具色块 5×2.5 pt 在 600 dpi 下约 880 px，
    远大于 ``min_pixels``=30——只用区域总命中数，校验会被色块自己「喂饱」而
    **形同虚设**。加上「剔除色块后仍须命中」这一条，校验才真正回答
    「这个颜色在**曲线区**里存在吗」。两个条件同时满足才 ``ok=True``。

    ``legend_colors`` 接受 ``#rrggbb`` 字符串或 0–255 / 0–1 数值序列。
    """
    arr = np.asarray(rgb)
    if arr.ndim == 3 and region is not None:
        l, t, r, b = (int(v) for v in region)
        h, w = arr.shape[:2]
        l = max(0, min(l, w - 1))
        r = max(l + 1, min(r, w))
        t = max(0, min(t, h - 1))
        b = max(t + 1, min(b, h))
        sub = arr[t:b, l:r]
        off = (l, t)
    else:
        sub = arr
        off = (0, 0)

    cols: list[tuple[int, int, int]] = []
    hexes: list[str] = []
    for c in legend_colors:
        if isinstance(c, str):
            h = c.lstrip("#")
            triple = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        else:
            vals = [float(v) for v in c]
            if max(vals) <= 1.0:               # PDF 口径的 0..1 浮点
                triple = tuple(round(v * 255.0) for v in vals)  # type: ignore[assignment]
            else:                              # 8 bit 口径
                triple = tuple(int(round(v)) for v in vals)      # type: ignore[assignment]
        cols.append(triple)                    # type: ignore[arg-type]
        hexes.append("#%02x%02x%02x" % triple)

    n = len(cols)
    hits = [int(color_mask(sub, c, tol=tol).sum()) for c in cols]

    plot_hits: list[int] = []
    if swatch_boxes and n:
        keep = np.ones(sub.shape[:2], dtype=bool)
        for bx in swatch_boxes:
            x0 = int(np.floor(min(bx[0], bx[2]))) - off[0]
            x1 = int(np.ceil(max(bx[0], bx[2]))) - off[0]
            y0 = int(np.floor(min(bx[1], bx[3]))) - off[1]
            y1 = int(np.ceil(max(bx[1], bx[3]))) - off[1]
            x0c, x1c = max(0, x0), min(sub.shape[1], x1)
            y0c, y1c = max(0, y0), min(sub.shape[0], y1)
            if x1c > x0c and y1c > y0c:
                keep[y0c:y1c, x0c:x1c] = False
        plot_hits = [int((color_mask(sub, c, tol=tol) & keep).sum()) for c in cols]

    pmin = min_pixels if plot_min_pixels is None else int(plot_min_pixels)
    missing = [hexes[i] for i in range(n) if hits[i] < min_pixels]
    plot_missing = ([hexes[i] for i in range(n) if plot_hits[i] < pmin]
                    if plot_hits else [])

    reasons: list[str] = []
    if n == 0:
        reasons.append("没有可校验的图例颜色")
    if missing:
        reasons.append("图内找不到（或命中不足）：" + "、".join(missing))
    if plot_missing:
        reasons.append("剔除图例色块后仍找不到（或命中不足）：" + "、".join(plot_missing))

    ok = bool(n) and not missing and not plot_missing
    # ``matched`` 必须**同时**计入两类失败。只用 ``missing`` 会漏掉「区域总命中够、
    # 但剔除图例色块后不足」这一整类（实测夹具 Fig.3 的 #3fd83f：hits=882 过阈值、
    # plot_hits=0 不过），于是报告出现 ``checked=3 matched=3 ok=False`` 这种自相矛盾的
    # 组合——而 ``matched`` 恰是人快速扫报告时唯一的数量信号，矛盾直接摧毁可发现性。
    bad = set(missing) | set(plot_missing)
    matched = n - len(bad)
    return LegendCheck(
        ok=ok, checked=n, matched=matched, tol=float(tol), min_pixels=int(min_pixels),
        hits=hits, plot_hits=plot_hits, missing=missing,
        reason="；".join(reasons),
    )


def verify_legend(
    info: LegendInfo,
    rgb: np.ndarray,
    region: Sequence[int] | None,
    *,
    tol: float = VERIFY_TOL,
    min_pixels: int = VERIFY_MIN_PIXELS,
    swatch_boxes_px: Sequence[Sequence[int]] | None = None,
) -> LegendCheck:
    """:func:`verify_legend_colors` 的 ``LegendInfo`` 包装（就地写入 ``color_check``）。"""
    if not info.found or not info.entries:
        chk = LegendCheck(ok=False, checked=0, matched=0, tol=float(tol),
                          min_pixels=int(min_pixels), reason="未检出图例")
        info.color_check = chk
        return chk
    nocolor = [e.name for e in info.entries if not e.color]
    cols = [e.color for e in info.entries if e.color]
    chk = verify_legend_colors(rgb, region, cols, tol=tol, min_pixels=min_pixels,
                              swatch_boxes=swatch_boxes_px)
    if nocolor:
        chk.ok = False
        chk.missing = list(chk.missing) + nocolor
        chk.reason = ("；".join(x for x in [chk.reason,
                                            "未关联到色块：" + "、".join(nocolor)] if x))
        chk.matched = max(0, chk.matched - len(nocolor))
    for e, h in zip([e for e in info.entries if e.color], chk.hits):
        e.matched_pixels = int(h)
    for e, h in zip([e for e in info.entries if e.color], chk.plot_hits):
        e.matched_pixels_plot = int(h)
    info.color_check = chk
    return chk


# ------------------------------------------------------------------ 主流程


def extract_legend(
    page: pymupdf.Page,
    rect: pymupdf.Rect | Sequence[float],
    *,
    dpi: int = _pdf.DEFAULT_DPI,
    keep_unverified: bool = False,
) -> LegendInfo:
    """从 ``rect`` 内提取图例（**不做**颜色校验，校验由调用方显式执行）。

    失败**永不抛异常**：``found=False`` 并附 ``note``，由调用方静默回退到现有行为。

    ``keep_unverified``：默认 ``False``（SPEC §0「图例是可选增强」）。置 ``True`` 时
    即使某条未关联到色块也保留条目并给出 ``color=None``，便于诊断；**交付物一律
    使用默认值**。
    """
    r = pymupdf.Rect(rect)
    rows = text_rows(page, r)
    info = LegendInfo(found=False)
    info.text_boxes_pt = [row.box for row in rows]
    info.exclude_boxes_px = exclusion_boxes_px(rows, (r.x0, r.y0, r.x1, r.y1), dpi)

    groups = group_legend_lines(rows)
    group = pick_legend_group(groups)
    if not group:
        info.note = ("未找到满足几何签名的图例组"
                     f"（区域内文字行 {len(rows)} 条、最大字号 "
                     f"{max((x.size for x in rows), default=0.0):.1f} pt）；"
                     "按「图例是可选增强」静默回退")
        return info

    drawings = page.get_drawings()
    entries: list[LegendEntry] = []
    for row in group:
        sw = pick_swatch(drawings, y0=row.y0, y1=row.y1, x0=row.x0)
        # 关联不到色块时如实记 None（绝不用"离得最近的对象"顶替）——R1 会因此失败，
        # 调用方随即回退自动配色。这正是"绝不猜"红线的落点。
        entries.append(LegendEntry(
            name=row.text,
            color=sw.hex if sw else None,
            kind_hint=sw.kind_hint if sw else "",
            source="pdf-text+vector" if sw else "pdf-text",
            row=row,
            swatch=sw,
        ))

    info.found = True
    info.entries = entries
    info.swatch_boxes_pt = [e.swatch.rect_pt for e in entries if e.swatch]
    n_have = sum(1 for e in entries if e.color)
    info.source = "pdf-text+vector" if n_have == len(entries) else "pdf-text"
    if n_have < len(entries):
        info.note = (f"{len(entries)} 条图例中仅 {n_have} 条关联到色块；"
                     "未关联者不会参与配色")
    return info


def swatch_boxes_px(info: LegendInfo, rect_pt: Sequence[float], dpi: int) -> list[tuple[float, float, float, float]]:
    """图例色块的裁剪图像素矩形（供 R1 的 ``swatch_boxes`` 与人工排除使用）。"""
    return [text_box_to_px(b, rect_pt, dpi, pad_px=0) for b in info.swatch_boxes_pt]


def fallback_message(
    info: LegendInfo,
    region_colors: Sequence[tuple[tuple[int, int, int], int]] = (),
) -> tuple[str, dict[str, Any]]:
    """颜色校验失败时的回退说明 + 结构化明细。

    返回 ``(人读说明, 明细)``。说明里同时给出**图例给出的颜色**与**图内实际主色**，
    便于人工判断是"期刊换色"还是"本工具关联错了色块"。

    ``region_colors`` 为 ``[((r, g, b), 像素数), ...]``，**8 bit 整数**口径
    （与 ``extract.dominant_colors`` 一致）。注意不要用 ``pdf.hex_color`` 渲染它——
    那个函数的入参是 **0..1 浮点**，喂 8 bit 整数会被钳到 1.0，六个颜色全变
    ``#ffffff``，报告随即失去全部诊断价值（开发期实测踩到过）。
    """
    chk = info.color_check
    legend_colors = [{"name": e.name, "color": e.color, "kind_hint": e.kind_hint}
                     for e in info.entries]
    actual = [{"color": "#%02x%02x%02x" % tuple(int(v) & 0xFF for v in c[:3]),
               "pixels": int(n)} for c, n in region_colors]
    detail: dict[str, Any] = {
        "reason": chk.reason if chk else "未检出图例",
        "legend_colors": legend_colors,
        "region_colors": actual,
        "color_check": chk.to_dict() if chk else None,
    }
    lines = ["图例颜色校验未通过，已回退自动配色。"]
    if chk and chk.reason:
        lines.append(f"  原因：{chk.reason}")
    if chk:
        lines.append(f"  命中像素（区域内）：{chk.hits}"
                     f"  阈值 min_pixels={chk.min_pixels}")
        if chk.plot_hits:
            lines.append(f"  命中像素（剔除图例色块后）：{chk.plot_hits}")
    if legend_colors:
        lines.append("  图例给出的颜色：" + "、".join(
            f"{d['name']}={d['color'] or '未关联'}" for d in legend_colors))
    if actual:
        lines.append("  图内实际主色：" + "、".join(
            f"{d['color']}({d['pixels']}px)" for d in actual))
    lines.append("  请人工核对：或期刊对图例色块做过淡化/换色，或本工具关联到了错误对象。")
    return "\n".join(lines), detail
