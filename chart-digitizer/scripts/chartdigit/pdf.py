"""文献 PDF → 图定位 → 局部渲染裁图（S1）。

本模块是 chart-digitizer 的**前置输入适配层**：吃一份文献 PDF，回答"第 N 张图在
哪一页的哪个矩形里"，并按 600 dpi 局部渲染出 PNG，同时写出可独立复核的溯源 JSON。
它**不**做图例提取（``legend.py``，S2）、**不**做数值标注 OCR，也**不**改动任何
已验证的提取/标定核心。

三通道定位（实测依据见 ``references/pdf-legend.md``）
--------------------------------------------------

===========  ================================================  ==================
通道          原理                                              实测命中
===========  ================================================  ==================
A 图注锚定    文本层行级 bbox 匹配 ``Fig./Figure/Table N``，     P2 ✓ P3 ✓ P1 ✗
             图形区域 = 该图注**同栏上方**
B 位图 bbox   ``get_text("dict")`` 中 ``type==1`` 的 block bbox  P1 ✓（扫描页）P3 ✗
C 矢量簇      ``get_drawings()`` 按纵向空隙聚类取最大簇          兜底
===========  ================================================  ==================

优先级为 A → B → C，且**逐页**判定：某页通道 A 有候选就不再启用 B/C。

两条必须遵守的设计承诺
----------------------

1. **绝不猜**。通道 A 的正则会误匹配正文引用（P2 实测
   ``"Figure 3 for a particular set of fit parameters."``），因此有消歧门；同编号出现
   多个候选时由调用方报错并列出全部候选，而不是"选一个最像的"。
2. **先粗定后收紧**。粗定矩形必然含留白甚至邻图，故一律在低 dpi（默认 100）下渲染
   一次，用"非背景行/列"把矩形收到实际内容边界，再按 600 dpi 局部渲染裁剪。
   收紧同时充当**校验**：长宽比异常或面积过小的候选直接丢弃。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np
import pymupdf

__all__ = [
    "CAPTION_RE",
    "SEPARATOR_CHARS",
    "DEFAULT_DPI",
    "MIN_DPI",
    "CaptionHit",
    "FigureCandidate",
    "FigureCrop",
    "hex_color",
    "pt_to_px",
    "px_to_pt",
    "box_pt_to_px",
    "caption_match",
    "has_separator",
    "page_body_size",
    "caption_decision",
    "find_caption_lines",
    "column_bounds",
    "find_column_gutter",
    "top_bound",
    "coarse_rect_for_caption",
    "find_raster_rects",
    "find_vector_cluster",
    "content_bounds",
    "tighten_rect",
    "mark_ambiguity",
    "locate_figures",
    "crop_figure",
]


# --------------------------------------------------------------------- 常量

#: 图注/表注标签正则。要点（均来自 P2/P3 实测）：
#:
#: * ``(\d{1,3})`` 之后**不再允许空白**再接字母——否则 ``Fig. 2 TPES`` 会把 ``T``
#:   吃成面板字母（实测 ``Fig. 2 TPES and TPEPICO…`` 会误判为 ``2T``）。
#: * 末尾用零宽前瞻锁定标签词边界，使 ``Table 1); (iii) …``（P3 实测正文误匹配）
#:   仍能被**识别出来**，从而交由消歧门拒绝，而不是悄悄漏过。
CAPTION_RE = re.compile(
    r"^\s*(?P<kw>Fig(?:ure)?\.?|TABLE|Table|表)\s*(?P<num>\d{1,3})(?P<panel>[a-zA-Z])?"
    r"(?=[\s.:|;；,，、)\t]|$)"
)
#: 编号与面板字母一律走**具名**分组读取（``num`` / ``panel``）——用 ``group(1)`` 这类
#: 位置索引会在正则增删分组时静默错位（开发期实测：加一个 ``kw`` 分组后编号被读成了
#: 关键词本身，标签变成 ``"Figure1"``，且因各标签互不相同而**不报错**）。

#: 表注关键词（与图注共用一条正则，但走不同分支——见 ``caption_kind``）。
TABLE_KEYWORDS = frozenset({"TABLE", "Table", "表"})

#: 标签与说明之间的显式分隔符。``./|/:/制表`` 为 SPEC 明列，其余为中文刊常见写法。
SEPARATOR_CHARS = frozenset(".:|;；，,、\t")

DEFAULT_DPI = 600
MIN_DPI = 300

#: 「字号显著小于正文」的余量。P3 实测 8.0 vs 9.5、P2 实测 8.5 vs 9.5，余量取 0.25
#: 既能容纳 0.1 pt 的舍入噪声，又不会把正文字号本身判成图注。
SIZE_EPS = 0.25
#: 靠「显式分隔符」接受时，所在 block 允许的最大行数。
SEP_MAX_BLOCK_LINES = 4
#: 单行独立 block 的最大字符数（见 ``caption_decision`` 的分支 c）。
SINGLE_LINE_MAX_CHARS = 200

#: 通道 B 的最小边长（pt）。SPEC 指定 40 pt，用于滤掉公式/图标级小图元。
MIN_RASTER_PT = 40.0
#: 通道 B 排除「整页位图」：老刊扫描件每页就是一张大位图（P1 实测
#: 每页都有 0,0,576,810 的 2400×3375 图），它不是插图。
MAX_PAGE_RASTER_FRAC = 0.9

#: 收紧时用的低 dpi。SPEC 指定"如 100"，成本可忽略。
TIGHTEN_DPI = 100
#: 灰度差小于该值即视为背景（SPEC：256 级灰度差 < 8）。
BG_TOL = 8.0
#: 内容带之间的桥接空隙（pt）。P3 第 5 页 Fig. 2 实测**图内**最大空隙 12.9 pt
#: （三条堆叠面板之间），取 18 pt 留约 40% 余量；而两张不同图之间通常 ≥ 20–30 pt。
GAP_PT = 18.0
#: 收紧后允许的最小面积（占整页比例）与长宽比窗口（SPEC 明列）。
MIN_AREA_FRAC = 0.01
MIN_ASPECT = 0.15
MAX_ASPECT = 8.0

#: 通道 C 的纵向聚类断开阈值（SPEC 明列 20 pt）。
VECTOR_GAP_PT = 20.0

#: 各通道的置信度基线。
CONF_CAPTION = 0.90
CONF_CAPTION_WEAK = 0.72
CONF_CAPTION_SEP_BONUS = 0.05
CONF_RASTER = 0.60
CONF_VECTOR = 0.40

#: 正文段落判定：左边界相对栏左界的容差（pt + 栏宽比例），右边界须覆盖的栏宽比例。
PARAGRAPH_EDGE_TOL = 6.0
PARAGRAPH_INSET_FRAC = 0.10
PARAGRAPH_RAGGED_FRAC = 0.15
#: 页眉判定：block 宽度占正文宽度的比例下限，且须位于页面上部该比例以内。
HEADER_WIDTH_FRAC = 0.75
HEADER_TOP_FRAC = 0.15


# --------------------------------------------------------------------- 数据模型


@dataclass
class FigureCandidate:
    """一张被定位到的图。坐标为 PDF 点，y 自顶向下。"""

    page_no: int
    label: str
    rect: pymupdf.Rect
    source: str
    caption: str = ""
    confidence: float = 0.5
    ambiguous_with: int = 1

    @property
    def ambiguous(self) -> bool:
        """同编号候选是否多于 1 个。"""
        return self.ambiguous_with > 1

    @property
    def bbox_pt(self) -> tuple[float, float, float, float]:
        return (self.rect.x0, self.rect.y0, self.rect.x1, self.rect.y1)

    @property
    def size_pt(self) -> tuple[float, float]:
        return (self.rect.width, self.rect.height)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "label": self.label,
            "rect_pt": [round(v, 2) for v in self.bbox_pt],
            "source": self.source,
            "caption": self.caption,
            "confidence": round(float(self.confidence), 3),
            "ambiguous_with": self.ambiguous_with,
        }


@dataclass
class FigureCrop:
    """一次成功的裁剪。``image_path`` 指向 PNG，旁边还有一个溯源 JSON。"""

    pdf: str
    page_no: int
    rect_pt: tuple[float, float, float, float]
    dpi: int
    px_size: tuple[int, int]
    image_path: str
    candidate: FigureCandidate

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf": self.pdf,
            "page_no": self.page_no,
            "rect_pt": [round(v, 2) for v in self.rect_pt],
            "dpi": self.dpi,
            "px_size": list(self.px_size),
            "source": self.candidate.source,
            "caption": self.candidate.caption,
            "confidence": round(float(self.candidate.confidence), 3),
            "label": self.candidate.label,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }


@dataclass
class CaptionHit:
    """一条**通过正则**的候选行。``reason == "ok"`` 才是真图注/表注。"""

    page_no: int
    label: str
    line_text: str
    line_bbox: tuple[float, float, float, float]
    block_bbox: tuple[float, float, float, float]
    size: float
    block_lines: int
    is_first_line: bool
    separator: bool
    reason: str
    branch: str = ""
    kind: str = "figure"

    @property
    def ok(self) -> bool:
        return self.reason == "ok"

    @property
    def strong(self) -> bool:
        """是否由「字号更小」这一最强信号判定（而非靠格式/孤立块兜底）。"""
        return self.branch == "size"

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "label": self.label,
            "kind": self.kind,
            "text": self.line_text[:80],
            "line_bbox": [round(v, 2) for v in self.line_bbox],
            "size": round(float(self.size), 2),
            "block_lines": self.block_lines,
            "first_line": self.is_first_line,
            "separator": self.separator,
            "reason": self.reason,
            "branch": self.branch,
        }


# ------------------------------------------------------------------ 基础换算


def hex_color(rgb: Sequence[float]) -> str:
    """RGB 浮点（0..1）→ ``#rrggbb``。

    量化规则为**四舍五入到最近整数**（``int(v*255 + 0.5)``，即 .5 进位），
    越界值先钳到 ``[0, 1]``。这条规则把舍入边界钉死在函数里而不是散落在各处：

    * ``0.5 → 127.5 → 128 → '80'``（.5 进位）
    * ``127/255 = 0.49804 → 127.49 → 127 → '7f'``

    实测对应：P3 图例色块矢量对象给出的正是 ``#7f7fff``——源图的 8 bit 值
    ``0x7f`` 存进 PDF 后是 0.498，故取 ``7f`` 而非 ``80``。两种写法都能被本函数
    无歧义复现。
    """
    vals = list(rgb)
    if len(vals) != 3:
        raise ValueError(f"颜色需要 3 个分量，收到 {len(vals)} 个")
    out: list[int] = []
    for v in vals:
        try:
            f = float(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"颜色分量不是数值: {v!r}") from exc
        if f != f:  # NaN
            raise ValueError("颜色分量是 NaN")
        f = min(1.0, max(0.0, f))
        out.append(int(f * 255.0 + 0.5))
    return "#%02x%02x%02x" % tuple(out)


def pt_to_px(
    pt: Sequence[float], rect_pt: Sequence[float], dpi: int
) -> tuple[float, float]:
    """PDF 点坐标 → 裁剪图**像素**坐标（原点在 ``rect_pt`` 左上角）。"""
    s = dpi / 72.0
    return ((pt[0] - rect_pt[0]) * s, (pt[1] - rect_pt[1]) * s)


def px_to_pt(
    px: Sequence[float], rect_pt: Sequence[float], dpi: int
) -> tuple[float, float]:
    """裁剪图像素坐标 → PDF 点坐标（``pt_to_px`` 的严格逆运算）。"""
    s = 72.0 / dpi
    return (rect_pt[0] + px[0] * s, rect_pt[1] + px[1] * s)


def box_pt_to_px(
    box_pt: Sequence[float], rect_pt: Sequence[float], dpi: int
) -> tuple[float, float, float, float]:
    """矩形：PDF 点 → 裁剪图像素。S2 的排除掩码即用此换算（本阶段先行固化并单测）。"""
    x0, y0 = pt_to_px((box_pt[0], box_pt[1]), rect_pt, dpi)
    x1, y1 = pt_to_px((box_pt[2], box_pt[3]), rect_pt, dpi)
    return (x0, y0, x1, y1)


# ------------------------------------------------------------------ 文本层解析


def _iter_text_blocks(page: pymupdf.Page) -> Iterator[dict[str, Any]]:
    for b in page.get_text("dict")["blocks"]:
        if b.get("type", 0) == 0 and b.get("lines"):
            yield b


def _line_text(line: dict[str, Any]) -> str:
    return "".join(s.get("text", "") for s in line.get("spans", ()))


def _line_size(line: dict[str, Any]) -> float:
    spans = line.get("spans") or ()
    if not spans:
        return 0.0
    # 取字符数加权平均，避免上下标 span 把整行字号带偏
    num = sum(float(s.get("size", 0.0)) * max(1, len(s.get("text", ""))) for s in spans)
    den = sum(max(1, len(s.get("text", ""))) for s in spans)
    return num / den if den else float(spans[0].get("size", 0.0))


def _block_bbox(block: dict[str, Any]) -> tuple[float, float, float, float]:
    b = block["bbox"]
    return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))


def page_body_size(page: pymupdf.Page) -> float:
    """正文基准字号：按**字符数加权**取该页出现最多的字号。

    实测依据：P2 正文 9.5 pt / 图注 8.5 pt；P3 正文 9.5 pt / 图注 8.0 pt /
    表注 9.0 pt。用字符数加权（而非行数）使长正文段主导统计。
    无文本时返回 ``0.0``。
    """
    counts: dict[float, int] = {}
    for b in _iter_text_blocks(page):
        for ln in b["lines"]:
            for s in ln.get("spans", ()):
                size = round(float(s.get("size", 0.0)), 1)
                if size < 4.0:  # 页眉页码等装饰性小字不计入基准
                    continue
                counts[size] = counts.get(size, 0) + max(1, len(s.get("text", "")))
    if not counts:
        return 0.0
    # 字符数优先，平手时取较大字号
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def caption_match(text: str) -> re.Match[str] | None:
    """在图注上下文中做标签匹配，返回匹配对象。

    ``group("num")`` 编号、``group("panel")`` 面板字母、``group("kw")`` 关键词
    （用于区分图/表）。
    """
    return CAPTION_RE.match(text)


def caption_kind(m: re.Match[str]) -> str:
    """由匹配对象判定 ``"figure"`` 或 ``"table"``。"""
    return "table" if m.group("kw") in TABLE_KEYWORDS else "figure"


def has_separator(text: str, end: int) -> bool:
    """标签之后是否紧跟显式分隔符（或两个以上空格）。

    ``end`` 为 ``caption_match`` 的 ``.end()``（零宽前瞻不消耗字符）。
    实测对照：

    ========================================  ==============
    行文本                                     判定
    ========================================  ==============
    ``Fig. 3 | Test figure …``                 True（``|``）
    ``Table 1 Summary of …``                   False
    ``Figure 6(a) and (b) shows …``            False
    ``Figure 3 for a particular set of …``     False
    ========================================  ==============
    """
    rest = text[end:]
    stripped = rest.lstrip(" ")
    if len(rest) - len(stripped) >= 2:
        return True
    return stripped[:1] in SEPARATOR_CHARS


def caption_decision(
    *,
    line_text: str,
    size: float,
    body_size: float,
    block_lines: int,
    is_first_line: bool,
    separator: bool,
) -> tuple[str, str]:
    """消歧门。返回 ``(结论, 生效分支)``：结论为 ``"ok"`` 或拒绝原因；
    分支为 ``"size"`` / ``"separator"`` / ``"single-line"``（拒绝时为空串）。

    规则与实测依据：

    **G1 段落结构** —— 真图注自成一段，故匹配行必须是所在 block 的**首行**；
    仅当 block 本身就只有 ≤ 2 行时放宽（孤立短块）。

    实测：P2 第 2 页 ``Figure 3 for a particular set of fit parameters. The analysis of``
    是 13 行块的第 8 行 → 直接被 G1 拦下。

    **另一类干扰走的是不同关卡**（实测复核过，勿混为一谈）：P3 第 6 页正文
    ``Figure 6(a) and (b) shows the results of continuum multiple…`` 是 17 行块的**首行**，
    但它**过不了正则**——标签后紧跟 ``(`` 不在前瞻字符集内，``caption_match`` 直接返回
    ``None``，连消歧门都进不去。即"首行 + 同字号"的正文必须由**正则的前瞻**挡，
    而"续行 + 同字号"的正文由 **G1** 挡，两道关卡各有职守。

    **G2 字号或显式格式** —— 满足任一条即接受：

    * ``size``：``size < body_size - SIZE_EPS``。期刊图注惯例小于正文（实测 P3
      8.0 vs 9.5、P2 8.5 vs 9.5）。最强信号，但不是唯一信号。
    * ``separator``：标签后紧跟显式分隔符 **且** block 行数 ≤ 4：覆盖
      ``TABLE 1: Valence Band Energies…``（P2 第 3 页；该页表格正文 8.5 pt 反而
      比 9.0 pt 的表注小，单靠字号会漏）。
    * ``single-line``：该行是**孤立单行 block** 且长度 ≤ ``SINGLE_LINE_MAX_CHARS``：
      覆盖"图注与正文同字号同排版、且无分隔符"的期刊（如 ``Fig. 3 Comparison of
      the measured yields``）。本分支放宽召回、可能引入噪声，但噪声只会变成
      ``--list`` 里多一条候选（用户可见、可消歧），而漏检会让通道 A 静默失去
      整张图——按"绝不猜"的边界宁多勿漏，多出的候选一律由歧义报告兜住。
    """
    if not (is_first_line or block_lines <= 2):
        return "not_first_line", ""
    if size <= 0.0 or body_size <= 0.0:
        return "no_font_metrics", ""
    if size < body_size - SIZE_EPS:
        return "ok", "size"
    if separator and block_lines <= SEP_MAX_BLOCK_LINES:
        return "ok", "separator"
    if block_lines == 1 and len(line_text) <= SINGLE_LINE_MAX_CHARS:
        return "ok", "single-line"
    return "font_not_caption", ""


def find_caption_lines(
    page: pymupdf.Page, *, page_no: int = 0
) -> list[CaptionHit]:
    """找出该页**全部**正则命中的行，并逐条给出消歧结论。

    返回列表包含被拒绝的命中（``reason != "ok"``），便于测试与诊断逐条断言；
    调用方应只使用 ``[h for h in hits if h.ok]``。

    ``label`` 命名空间（**本实现对 SPEC §3.1.1 示例的一处必要收窄**）：

    * 图注 → ``"3"`` / ``"3a"``（数字，可直接喂 ``--figure``）；
    * 表注 → ``"T1"`` / ``"T2"``。

    分开命名空间是因为 P2 实测到真实碰撞：``Figure 1`` 与 ``TABLE 1`` 同号、
    ``Figure 2`` 与 ``TABLE 2`` 同号，若都用裸数字，``--figure 1`` 会把一张图与
    一张表报成"歧义"，而二者本无歧义。
    """
    body = page_body_size(page)
    hits: list[CaptionHit] = []
    for b in _iter_text_blocks(page):
        bb = _block_bbox(b)
        nlines = len(b["lines"])
        for idx, ln in enumerate(b["lines"]):
            text = _line_text(ln)
            m = caption_match(text)
            if not m:
                continue
            kind = caption_kind(m)
            num = m.group("num") + (m.group("panel") or "")
            label = f"T{num}" if kind == "table" else num
            lb = ln["bbox"]
            line_bbox = (float(lb[0]), float(lb[1]), float(lb[2]), float(lb[3]))
            size = _line_size(ln)
            sep = has_separator(text, m.end())
            reason, branch = caption_decision(
                line_text=text,
                size=size,
                body_size=body,
                block_lines=nlines,
                is_first_line=(idx == 0),
                separator=sep,
            )
            hits.append(
                CaptionHit(
                    page_no=page_no,
                    label=label,
                    line_text=text,
                    line_bbox=line_bbox,
                    block_bbox=bb,
                    size=size,
                    block_lines=nlines,
                    is_first_line=(idx == 0),
                    separator=sep,
                    reason=reason,
                    branch=branch,
                    kind=kind,
                )
            )
    return hits


# --------------------------------------------------------------- 图形区域粗定


def _text_extent(page: pymupdf.Page) -> tuple[float, float]:
    """页面正文的 x 范围（所有文字 block 的并集）。"""
    xs0: list[float] = []
    xs1: list[float] = []
    for b in _iter_text_blocks(page):
        bb = _block_bbox(b)
        xs0.append(bb[0])
        xs1.append(bb[2])
    if not xs0:
        return (0.0, 0.0)
    return (min(xs0), max(xs1))


def find_column_gutter(
    page: pymupdf.Page, *, min_gap: float = 8.0
) -> float | None:
    """检测双栏页的**栏间空带**中心 x，单栏页返回 ``None``。

    做法：把所有文字 block 的 x 区间投影到 x 轴（区间并集），找最宽的连续空白段；
    若该段宽度 ≥ ``min_gap`` 且中心落在正文宽度的中间 50%，判为双栏。

    **跨幅 block 必须先剔除**：页眉/页脚（P3 实测 ``43.6–551.7``）横跨整幅正文宽，
    留在 profile 里会把整条 x 轴填满、空带消失，双栏页被判成单栏——开发期实测到
    这个失效后，改为只用宽度 < ``full_frac × 正文宽`` 的 block 建 profile。

    实测：P3 第 5 页左栏 43.6–291.7、右栏 303.6–551.7 → 空带 291.7–303.6
    （11.9 pt）；P2 第 2 页空带 294.6–318.0（23.4 pt）。二者都被正确识别为双栏。
    """
    tx0, tx1 = _text_extent(page)
    width = tx1 - tx0
    if width < 40.0:
        return None
    full_frac = 0.80
    n = int(width) + 2
    prof = np.zeros(n, dtype=bool)
    used = 0
    for b in _iter_text_blocks(page):
        bb = _block_bbox(b)
        if (bb[2] - bb[0]) >= full_frac * width:
            continue  # 页眉/页脚等跨幅 block
        i0 = max(0, int(bb[0] - tx0))
        i1 = min(n, int(np.ceil(bb[2] - tx0)))
        if i1 > i0:
            prof[i0:i1] = True
            used += 1
    if used == 0:
        return None
    gaps = _bands(~prof, 0.0)
    if not gaps:
        return None
    best = max(gaps, key=lambda s: s[1] - s[0])
    gap_width = best[1] - best[0] + 1
    if gap_width < min_gap:
        return None
    center = tx0 + 0.5 * (best[0] + best[1] + 1)
    lo, hi = tx0 + 0.25 * width, tx0 + 0.75 * width
    if not (lo <= center <= hi):
        return None
    return center


def column_bounds(page: pymupdf.Page, hit: CaptionHit) -> tuple[float, float]:
    """由图注所在 block 推断**栏**的左右边界。

    不能只用图注 block 的 x 范围：单行图注的文字宽度往往远窄于栏宽（实测 P3
    ``Fig. 7 Type I conformer (C+D) bfþ1g`` 的 block 只有 43.6–168.1，而该栏实为
    43.6–291.7）。早期版本用"窄就退回整幅正文宽"兜底，结果同一页的 Fig. 7 与
    Fig. 8 都被判成全页宽、区域互相覆盖——本函数因此改为**按栏切分**：

    1. 取正文 x 范围 ``(tx0, tx1)``；
    2. 用 :func:`find_column_gutter` 检测栏间空带中心 ``g``；
    3. 图注 block 横向已接近整幅正文宽（``x1 ≥ tx1 − 0.15·W``）→ 整幅宽；
    4. 否则按图注中心落在 ``g`` 左右决定取左栏 ``(tx0, g)`` 或右栏 ``(g, tx1)``；
    5. 单栏页（无空带）→ 整幅宽。
    """
    b = hit.block_bbox
    tx0, tx1 = _text_extent(page)
    width = tx1 - tx0
    if width < 40.0:
        return (b[0], b[2])
    # 跨幅图注：**左右两边都要贴正文边距**。只看右边会把右栏图注误判成整幅宽
    # （P3 第 6 页 Fig. 10 与第 5 页 Fig. 3 的图注 block 右边界恰为 551.7 = 正文右缘，
    # 开发期实测因此把两张右栏图裁成了整页宽）。
    if b[0] <= tx0 + 0.15 * width and b[2] >= tx1 - 0.15 * width:
        return (tx0, tx1)
    gutter = find_column_gutter(page)
    if gutter is None:
        return (tx0, tx1)
    cx = 0.5 * (b[0] + b[2])
    if cx <= gutter:
        return (tx0, gutter)
    return (gutter, tx1)


def _is_paragraph(block: dict[str, Any], col: tuple[float, float]) -> bool:
    """block 是否为**正文段落**（贴栏左缘、覆盖该栏大部分宽度、至少两行）。

    关键作用：把"图内文字标注"排除在"图注上方的正文段"之外。P3 第 5 页
    Fig. 2 图注上方最近的文字 block 是刻度标签 ``11``（y1=368.5）与面板标签
    ``TPES``/``m/z 70``/``m/z 115``——若不加此判据，纵向上界会收到 368.5，
    裁出的将是一条 8 pt 的窄缝。

    两条边界都用**比例**而不是等值比较：

    * 右边：正文左对齐右不齐，块宽天然小于栏宽，所以只要求覆盖到栏宽的
      ``1 - PARAGRAPH_RAGGED_FRAC``。
    * 左边：栏左界来自**栏间空带中心**（由 :func:`find_column_gutter` 给出），
      它必然落在栏间空隙里、而不是正文左缘，所以容差同样按栏宽取比例
      （``PARAGRAPH_INSET_FRAC``）。实测 P3 右栏文字起点 303.6 与空带中心 297.65
      差 5.95 pt；若用固定 6 pt 容差就只剩 0.05 pt 余量，换个版式立刻失效。

    真正的把关点仍是**左边界**：图内标注一律相对栏左缘大幅内缩
    （P3 的 ``TPES`` x0=76.1、``11`` x0=93.8，栏左缘 43.6，内缩 32–50 pt，
    远超 10% 栏宽）。
    """
    b = _block_bbox(block)
    if len(block["lines"]) < 2:
        return False
    col_width = col[1] - col[0]
    if col_width <= 0:
        return False
    if b[0] < col[0] - PARAGRAPH_EDGE_TOL:
        return False
    if b[0] - col[0] > PARAGRAPH_INSET_FRAC * col_width:
        return False
    return b[2] >= col[1] - PARAGRAPH_RAGGED_FRAC * col_width


def top_bound(
    page: pymupdf.Page, col: tuple[float, float], caption_top: float
) -> tuple[float, float]:
    """纵向上界。返回 ``(y, 依据)``。

    1. 同栏内、图注上方**最近的一个正文段落**的底边；
    2. 否则取页面顶部跨幅页眉的底边；
    3. 否则退到页面顶端留白（``max(18 pt, 3% 页高)``）。
    """
    best: float | None = None
    for b in _iter_text_blocks(page):
        bb = _block_bbox(b)
        if bb[3] > caption_top - 0.5:
            continue
        if bb[2] <= col[0] or bb[0] >= col[1]:  # 与栏无横向重叠
            continue
        if not _is_paragraph(b, col):
            continue
        if best is None or bb[3] > best:
            best = bb[3]
    if best is not None:
        return best, "paragraph-above"

    tx0, tx1 = _text_extent(page)
    text_width = tx1 - tx0
    page_h = float(page.rect.height)
    headers = []
    for b in _iter_text_blocks(page):
        bb = _block_bbox(b)
        if text_width > 0 and (bb[2] - bb[0]) >= HEADER_WIDTH_FRAC * text_width:
            if bb[1] < page_h * HEADER_TOP_FRAC and bb[3] <= caption_top - 0.5:
                headers.append(bb[3])
    if headers:
        return min(headers), "page-header"

    return float(page.rect.y0) + max(18.0, 0.03 * page_h), "page-margin"


def coarse_rect_for_caption(
    page: pymupdf.Page, hit: CaptionHit
) -> tuple[pymupdf.Rect, str]:
    """图的**粗定**矩形：同栏宽 × [上界, 图注行顶边)。

    下界取图注**行**顶边而非 block 顶边，且不含图注自身——但**保留**图注上方的
    轴标题与刻度标签（它们属于图）。
    """
    col = column_bounds(page, hit)
    y_top, basis = top_bound(page, col, hit.line_bbox[1])
    rect = pymupdf.Rect(col[0], y_top, col[1], hit.line_bbox[1])
    if rect.y1 - rect.y0 < 24.0:  # 粗定区太小，必然定位失败
        rect = pymupdf.Rect(col[0], y_top, col[1], min(page.rect.y1, hit.line_bbox[1] + 24.0))
    return rect, basis


# ------------------------------------------------------------- 通道 B / 通道 C


def find_raster_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """通道 B：位图 bbox，按阅读顺序（上→下、左→右）返回。

    过滤三类非插图：边长 < ``MIN_RASTER_PT`` 的（公式/图标）、面积占整页 ≥
    ``MAX_PAGE_RASTER_FRAC`` 的（老刊扫描页整页位图，P1 实测每页都有）。
    """
    page_area = float(page.rect.width) * float(page.rect.height)
    out: list[pymupdf.Rect] = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type", 0) != 1:
            continue
        r = pymupdf.Rect(b["bbox"])
        if r.width < MIN_RASTER_PT or r.height < MIN_RASTER_PT:
            continue
        if page_area > 0 and (r.width * r.height) >= MAX_PAGE_RASTER_FRAC * page_area:
            continue
        out.append(r)
    out.sort(key=lambda r: (round(r.y0, 1), round(r.x0, 1)))
    return out


def find_vector_cluster(page: pymupdf.Page) -> pymupdf.Rect | None:
    """通道 C：把 ``get_drawings()`` 的 rect 按纵向空白聚类，取面积最大的簇。

    断开阈值为 ``VECTOR_GAP_PT``（20 pt）。返回簇的 bbox（未收紧），无对象时
    ``None``。现代期刊单页可有上万个矢量对象（P3 实测 2883–9728），故用
    numpy 排序后贪心合并，避免 Python 层逐对比。
    """
    rects: list[tuple[float, float, float, float]] = []
    for d in page.get_drawings():
        r = d.get("rect")
        if r is None:
            continue
        if r.width <= 0 and r.height <= 0:
            continue
        rects.append((float(r.x0), float(r.y0), float(r.x1), float(r.y1)))
    if not rects:
        return None
    arr = np.asarray(rects, dtype=float)
    order = np.argsort(arr[:, 1], kind="stable")
    arr = arr[order]

    clusters: list[list[int]] = [[0]]
    cur_y1 = arr[0, 3]
    for i in range(1, arr.shape[0]):
        if arr[i, 1] - cur_y1 > VECTOR_GAP_PT:
            clusters.append([i])
            cur_y1 = arr[i, 3]
        else:
            clusters[-1].append(i)
            cur_y1 = max(cur_y1, arr[i, 3])

    best: pymupdf.Rect | None = None
    best_area = -1.0
    for idxs in clusters:
        sub = arr[idxs]
        x0, y0 = float(sub[:, 0].min()), float(sub[:, 1].min())
        x1, y1 = float(sub[:, 2].max()), float(sub[:, 3].max())
        area = (x1 - x0) * (y1 - y0)
        if area > best_area:
            best_area = area
            best = pymupdf.Rect(x0, y0, x1, y1)
    return best


# ------------------------------------------------------------------- 收紧


def _bands(flags: np.ndarray, min_gap_px: float) -> list[tuple[int, int]]:
    """把布尔行/列标记切成连续段，并把空隙小于 ``min_gap_px`` 的段合并。"""
    idx = np.flatnonzero(flags)
    if idx.size == 0:
        return []
    segs: list[tuple[int, int]] = []
    start = prev = int(idx[0])
    for k in idx[1:]:
        k = int(k)
        if k - prev - 1 <= min_gap_px:
            prev = k
        else:
            segs.append((start, prev))
            start = prev = k
    segs.append((start, prev))
    return segs


def content_bounds(
    rgb: np.ndarray,
    *,
    bg_tol: float = BG_TOL,
    gap_px: float = 0.0,
) -> tuple[int, int, int, int] | None:
    """在渲染图上找非背景内容的像素范围 ``(x0, y0, x1, y1)``（含端点）。

    背景色取该区域的**众数色**（逐通道众数）；灰度差 < ``bg_tol`` 视为背景。
    ``gap_px`` 用于桥接内容带之间的空隙：

    * 行方向从**最靠近图注的底部内容带**起向上走，只吞并空隙 < ``gap_px`` 的带。
      这样同栏上方若有另一张图或一段正文（空隙通常 ≥ 20–30 pt），不会被卷进来。
    * 列方向**只在选中的那条行带内**统计，再以最宽的列带为锚、吞并近邻带
      （P3 Fig. 2 的旋转 y 轴标题与主绘图区相距 11.4 pt，属于同一张图）。

    **列方向必须先按行带裁剪**：否则粗定区内任何别处的文字（例如同一栏里上一张图
    的图注）都会把 x 范围撑开——开发期实测，夹具里 Fig. 5a 的 x0 被上一张图的图注
    从 70 拉到 50.7。

    无内容时返回 ``None``。
    """
    if rgb.dtype != np.uint8:
        rgb = rgb.astype(np.uint8)
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError("content_bounds 需要 HxWx3 的 RGB 数组")
    sub = rgb[:, :, :3]
    bg = np.array(
        [np.bincount(sub[:, :, c].ravel()).argmax() for c in range(3)], dtype=float
    )
    gray = sub.astype(np.float64).mean(axis=2)
    content = np.abs(gray - float(bg.mean())) > bg_tol
    if not content.any():
        return None

    row_segs = _bands(content.any(axis=1), gap_px)
    if not row_segs:
        return None
    y0, y1 = row_segs[-1]  # 最靠近底部（图注侧）的那条带
    col_segs = _bands(content[y0 : y1 + 1, :].any(axis=0), gap_px)
    if not col_segs:
        return None
    x0, x1 = max(col_segs, key=lambda s: s[1] - s[0])
    return (x0, y0, x1, y1)


def tighten_rect(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    *,
    dpi: int = TIGHTEN_DPI,
    bg_tol: float = BG_TOL,
    gap_pt: float = GAP_PT,
    validate: bool = True,
) -> tuple[pymupdf.Rect | None, dict[str, Any]]:
    """先粗定后收紧：低 dpi 渲染 → 找非背景范围 → 换算回 PDF 点。

    返回 ``(rect 或 None, 诊断字典)``。当 ``validate=True`` 时，收紧结果若长宽比
    落在 ``[MIN_ASPECT, MAX_ASPECT]`` 之外、或面积小于整页 ``MIN_AREA_FRAC``，
    判定为定位失败（返回 ``None``）——宁可报"定位失败 + 手工框选指引"，也不猜。
    """
    info: dict[str, Any] = {
        "coarse_rect": [round(v, 2) for v in (rect.x0, rect.y0, rect.x1, rect.y1)],
        "tighten_dpi": dpi,
        "tightened": False,
        "reject": None,
    }
    if rect.width <= 1.0 or rect.height <= 1.0:
        info["reject"] = "coarse-too-small"
        return None, info

    pm = page.get_pixmap(dpi=dpi, clip=rect, colorspace=pymupdf.csRGB)
    if pm.width <= 0 or pm.height <= 0:
        info["reject"] = "empty-render"
        return None, info
    rgb = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, 3)

    scale = dpi / 72.0
    bounds = content_bounds(rgb, bg_tol=bg_tol, gap_px=max(0.0, gap_pt * scale))
    if bounds is None:
        info["reject"] = "no-content"
        return None, info

    x0, y0, x1, y1 = bounds
    out = pymupdf.Rect(
        rect.x0 + x0 / scale,
        rect.y0 + y0 / scale,
        rect.x0 + (x1 + 1) / scale,
        rect.y0 + (y1 + 1) / scale,
    )
    info["tightened"] = True
    info["content_rect"] = [round(v, 2) for v in (out.x0, out.y0, out.x1, out.y1)]

    if validate:
        w, h = out.width, out.height
        if w <= 0 or h <= 0:
            info["reject"] = "degenerate"
            return None, info
        aspect = h / w
        page_area = float(page.rect.width) * float(page.rect.height)
        frac = (w * h) / page_area if page_area else 0.0
        info["aspect"] = round(aspect, 3)
        info["area_frac"] = round(frac, 5)
        if aspect < MIN_ASPECT or aspect > MAX_ASPECT:
            info["reject"] = "aspect-out-of-range"
            return None, info
        if frac < MIN_AREA_FRAC:
            info["reject"] = "area-too-small"
            return None, info
    return out, info


# --------------------------------------------------------------- 通道合并


def mark_ambiguity(cands: Sequence[FigureCandidate]) -> None:
    """就地写入 ``ambiguous_with``：同 ``label`` 的候选个数（≥1）。

    非编号候选（通道 B/C 的 ``"?"``）不参与计数——它们没有编号，
    "同编号多个候选"对它们没有意义，强行计成 4 只会制造噪声。
    """
    counts: dict[str, int] = {}
    for c in cands:
        if c.label != "?":
            counts[c.label] = counts.get(c.label, 0) + 1
    for c in cands:
        c.ambiguous_with = counts.get(c.label, 1) if c.label != "?" else 1


def locate_figures(
    doc: pymupdf.Document,
    *,
    tighten: bool = True,
    pages: Iterable[int] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> list[FigureCandidate]:
    """三通道图定位（逐页判定，A → B → C）。

    ``pages`` 为 0 起的页码序列；缺省遍历全文档。``diagnostics`` 非空时逐条追加
    定位过程记录（供 ``--list`` 与测试使用）。

    表注（``label`` 以 ``T`` 开头）**被识别但不产出候选**：本阶段的图形区域算法
    是 SPEC §3.1.2 的"图注**上方**同栏"，而表注在三类实测文献里都是"标题在上、
    表格在下"，且 P2 第 4 页的表格是**横向旋转页**（正文块横跨 x 36.5–744，
    图注块却只有 x 36.5–322.6），按图注 block 推栏宽会截掉大半张表。与其给出
    一张看似可信的错表，不如明确不裁——表注一律经 ``diagnostics`` 的
    ``channel == "table-caption"`` 回报给调用方。
    """
    wanted = list(range(doc.page_count)) if pages is None else list(pages)
    out: list[FigureCandidate] = []

    for pno in wanted:
        if not (0 <= pno < doc.page_count):
            continue
        page = doc[pno]
        page_cands: list[FigureCandidate] = []

        # --- 通道 A
        for hit in find_caption_lines(page, page_no=pno):
            if not hit.ok:
                if diagnostics is not None:
                    diagnostics.append({"channel": "caption-reject", **hit.to_dict()})
                continue
            if hit.kind == "table":
                if diagnostics is not None:
                    diagnostics.append({"channel": "table-caption", **hit.to_dict()})
                continue
            coarse, basis = coarse_rect_for_caption(page, hit)
            rect: pymupdf.Rect | None = coarse
            info: dict[str, Any] = {}
            if tighten:
                rect, info = tighten_rect(page, coarse)
            if rect is None:
                if diagnostics is not None:
                    diagnostics.append(
                        {
                            "channel": "caption-tighten-reject",
                            "page_no": pno,
                            "label": hit.label,
                            "coarse_basis": basis,
                            **info,
                        }
                    )
                continue
            conf = CONF_CAPTION if hit.strong else CONF_CAPTION_WEAK
            if hit.separator:
                conf = min(0.95, conf + CONF_CAPTION_SEP_BONUS)
            page_cands.append(
                FigureCandidate(
                    page_no=pno,
                    label=hit.label,
                    rect=rect,
                    source="caption",
                    caption=hit.line_text.strip()[:200],
                    confidence=round(conf, 3),
                )
            )
            if diagnostics is not None:
                diagnostics.append(
                    {
                        "channel": "caption",
                        "page_no": pno,
                        "label": hit.label,
                        "coarse_basis": basis,
                        **info,
                    }
                )

        # --- 通道 B（仅当本页 A 无候选）
        if not page_cands:
            rasters = find_raster_rects(page)
            for i, r in enumerate(rasters, start=1):
                rect = r
                info = {}
                if tighten:
                    rect, info = tighten_rect(page, r)
                if rect is None:
                    if diagnostics is not None:
                        diagnostics.append(
                            {"channel": "raster-tighten-reject", "page_no": pno, **info}
                        )
                    continue
                page_cands.append(
                    FigureCandidate(
                        page_no=pno,
                        label="?",
                        rect=rect,
                        source="raster",
                        caption="",
                        confidence=CONF_RASTER,
                    )
                )
                if diagnostics is not None:
                    diagnostics.append(
                        {"channel": "raster", "page_no": pno, "index": i, **info}
                    )
            # --- 通道 C（仅当 A、B 均空）
            if not page_cands:
                cluster = find_vector_cluster(page)
                if cluster is not None:
                    rect, info = (
                        tighten_rect(page, cluster) if tighten else (cluster, {})
                    )
                    if rect is not None:
                        page_cands.append(
                            FigureCandidate(
                                page_no=pno,
                                label="?",
                                rect=rect,
                                source="vector-cluster",
                                caption="",
                                confidence=CONF_VECTOR,
                            )
                        )
                    if diagnostics is not None:
                        diagnostics.append(
                            {"channel": "vector-cluster", "page_no": pno, **info}
                        )

        out.extend(page_cands)

    mark_ambiguity(out)
    out.sort(key=lambda c: (c.page_no, c.rect.y0, c.rect.x0))
    return out


# ------------------------------------------------------------------ 裁剪


def crop_figure(
    pdf_path: str | Path,
    candidate: FigureCandidate,
    out_path: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
    doc: pymupdf.Document | None = None,
) -> FigureCrop:
    """按 ``candidate.rect`` 局部渲染裁剪，写 PNG + 溯源 JSON。

    **必须**用 ``clip=``：整页 600 dpi 约 97–104 MB，裁剪后仅 6.5–14.7 MB
    （SPEC §3.1.3）。输出固定 PNG（无损），不输出 JPEG。
    """
    if dpi < MIN_DPI:
        raise ValueError(f"dpi 下限为 {MIN_DPI}（收到 {dpi}）；低于此等效精度显著劣化")
    own = doc is None
    doc = doc or pymupdf.open(str(pdf_path))
    try:
        page = doc[candidate.page_no]
        rect = pymupdf.Rect(candidate.rect)
        pm = page.get_pixmap(dpi=dpi, clip=rect, colorspace=pymupdf.csRGB)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pm.save(str(out_path))
        crop = FigureCrop(
            pdf=str(pdf_path),
            page_no=candidate.page_no,
            rect_pt=candidate.bbox_pt,
            dpi=dpi,
            px_size=(pm.width, pm.height),
            image_path=str(out_path),
            candidate=candidate,
        )
        sidecar = out_path.with_suffix(out_path.suffix + ".json")
        sidecar.write_text(
            json.dumps(crop.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return crop
    finally:
        if own:
            doc.close()


def render_page_png(
    pdf_path: str | Path,
    page_no: int,
    out_path: str | Path,
    *,
    dpi: int = 110,
) -> str:
    """整页缩略图，供人工框选时目视核对。"""
    doc = pymupdf.open(str(pdf_path))
    try:
        pm = doc[page_no].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pm.save(str(out_path))
        return str(out_path)
    finally:
        doc.close()
