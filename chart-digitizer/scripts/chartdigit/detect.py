"""图表结构检测：图像加载、面板分割、轴框定位、刻度线识别与置信度评估。

设计取向为「自动检测 + 报告确认」：本模块给出几何检测结果与逐项置信度，
但**绝不臆造坐标轴数值**——数值必须由调用方（智能体的视觉判读或人工）提供，
或在报告中被明确标注为未知。这避免了静默产生错误标定这一最危险的失效模式。

精度要点：轴框线宽通常 1~3 像素，若用整数行/列号定位会引入最多 ±1 像素的
系统性偏移并直接污染标定。因此长直线经初检后会用灰度暗度加权精化到亚像素
（见 :func:`refine_line_positions`），实测可把面板边界误差压到 1 像素以内。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image

# ------------------------------------------------------------------ 数据结构


@dataclass
class ImageData:
    """已加载的图像及其派生表示。"""

    path: Path
    rgb: np.ndarray  # (H, W, 3) uint8
    _gray: np.ndarray | None = field(default=None, repr=False)

    @property
    def width(self) -> int:
        return int(self.rgb.shape[1])

    @property
    def height(self) -> int:
        return int(self.rgb.shape[0])

    @property
    def gray(self) -> np.ndarray:
        if self._gray is None:
            self._gray = (
                0.299 * self.rgb[:, :, 0] + 0.587 * self.rgb[:, :, 1] + 0.114 * self.rgb[:, :, 2]
            )
        return self._gray

    @property
    def background_rgb(self) -> tuple[int, int, int]:
        """以灰度众数所在的灰度级估计背景色（对白底/深底图均适用）。"""
        g = np.rint(self.gray).astype(np.int32)
        counts = np.bincount(g.ravel(), minlength=256)
        level = int(counts.argmax())
        sel = np.abs(self.gray - level) < 2.0
        if not sel.any():
            return (255, 255, 255)
        med = np.median(self.rgb[sel], axis=0)
        return tuple(int(v) for v in med)  # type: ignore[return-value]


@dataclass
class LineHit:
    """一条被检出的长直线。

    ``pos`` 为浮点：初检得到整数行/列号后，会再用灰度暗度加权精化到亚像素，
    这是把标定误差压到 1 像素以内的关键（否则轴框两侧各差半像素会系统性偏置）。
    """

    pos: float        # 水平线为行号 y；垂直线为列号 x（亚像素）
    start: int        # 线段起点（水平线为 x，垂直线为 y）
    end: int          # 线段终点（开区间）
    thickness: int = 1

    @property
    def length(self) -> int:
        return self.end - self.start

    def covers(self, a: float, b: float, tol: int = 6) -> bool:
        return self.start - tol <= a and self.end + tol >= b


@dataclass
class Panel:
    """一个绘图面板（子图）：数据区的像素矩形，边界为亚像素坐标。"""

    left: float
    top: float
    right: float
    bottom: float
    closed: bool = False          # 四边是否都被检出
    edges_found: list[str] = field(default_factory=list)
    content_ratio: float = 0.0    # 面板内前景像素占比
    label: str = ""

    @property
    def box(self) -> tuple[float, float, float, float]:
        return self.left, self.top, self.right, self.bottom

    @property
    def int_box(self) -> tuple[int, int, int, int]:
        """取整后的矩形，供像素索引使用。"""
        return (
            int(round(self.left)), int(round(self.top)),
            int(round(self.right)), int(round(self.bottom)),
        )

    @property
    def size(self) -> tuple[float, float]:
        return self.right - self.left, self.bottom - self.top


@dataclass
class DetectReport:
    """检测结果报告。"""

    image: str
    size: tuple[int, int]
    background_rgb: tuple[int, int, int]
    panels: list[Panel] = field(default_factory=list)
    horizontal_lines: list[LineHit] = field(default_factory=list)
    vertical_lines: list[LineHit] = field(default_factory=list)
    tick_x: list[int] = field(default_factory=list)
    tick_y: list[int] = field(default_factory=list)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def main_panel(self) -> Panel | None:
        if not self.panels:
            return None
        return max(self.panels, key=lambda p: p.size[0] * p.size[1])

    def to_dict(self) -> dict:
        return {
            "image": self.image,
            "size": list(self.size),
            "background_rgb": list(self.background_rgb),
            "confidence": round(self.confidence, 3),
            "warnings": self.warnings,
            "notes": self.notes,
            "panels": [
                {
                    "label": p.label,
                    "box": [round(v, 3) for v in p.box],
                    "size": [round(v, 3) for v in p.size],
                    "closed": p.closed,
                    "edges": p.edges_found,
                    "content_ratio": round(p.content_ratio, 4),
                }
                for p in self.panels
            ],
            "horizontal_lines": [
                {"y": round(l.pos, 3), "x0": l.start, "x1": l.end, "length": l.length}
                for l in self.horizontal_lines
            ],
            "vertical_lines": [
                {"x": round(l.pos, 3), "y0": l.start, "y1": l.end, "length": l.length}
                for l in self.vertical_lines
            ],
            "tick_x": self.tick_x,
            "tick_y": self.tick_y,
        }


# ------------------------------------------------------------------ 图像加载


def load_image(path: str | Path) -> ImageData:
    """加载任意 Pillow 支持的图像（png/jpg/jpeg/bmp/tif/tiff/gif/webp/pcx...）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"图像不存在: {p}")
    with Image.open(p) as im:
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        rgb = im.convert("RGB")
        arr = np.asarray(rgb, dtype=np.uint8)
    return ImageData(path=p, rgb=arr)


# ------------------------------------------------------------------ 前景掩码


def foreground_mask(
    img: ImageData,
    dark_threshold: int = 110,
    saturation_threshold: int = 40,
) -> np.ndarray:
    """提取前景掩码：显著暗于背景的像素，或明显带色的像素。

    ``dark_threshold`` 针对黑色轴框与曲线；``saturation_threshold`` 用于捕捉
    中等亮度但高饱和的彩色曲线（如橙、黄、青）。深色底图会自动反转判据。
    """
    gray = img.gray
    bg = img.background_rgb
    bg_gray = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]

    if bg_gray < 128:
        dark = gray > max(160, 255 - dark_threshold)
    else:
        dark = gray < dark_threshold

    hi = img.rgb.max(axis=2).astype(np.int16)
    lo = img.rgb.min(axis=2).astype(np.int16)
    colorful = (hi - lo) > saturation_threshold
    return dark | colorful


def grid_mask(img: ImageData, lo: int = 140, hi: int = 225) -> np.ndarray:
    """捕捉浅灰网格线（介于背景与主线之间的亮度带）。"""
    gray = img.gray
    bg = img.background_rgb
    bg_gray = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    if bg_gray < 128:
        return (gray > bg_gray + 25) & (gray < 250)
    return (gray > lo) & (gray < hi)


# ------------------------------------------------------------------ 直线检测


def _runs(flags: np.ndarray, max_gap: int = 0) -> list[tuple[int, int]]:
    """返回连续 True 段的 ``(start, end)``（end 为开区间），允许 gap 个缺口。"""
    idx = np.flatnonzero(flags)
    if idx.size == 0:
        return []
    if max_gap > 0:
        breaks = np.flatnonzero(np.diff(idx) > max_gap + 1)
    else:
        breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks + 1, [idx.size]))
    return [(int(idx[s]), int(idx[e - 1]) + 1) for s, e in zip(starts, ends)]


def _merge_adjacent(hits: list[LineHit], max_thickness: int = 4) -> list[LineHit]:
    """合并相邻行/列上重叠的线段，得到带厚度的单条直线。"""
    if not hits:
        return []
    hits = sorted(hits, key=lambda h: h.pos)
    out: list[LineHit] = []
    cur = [hits[0]]
    for h in hits[1:]:
        ref = cur[-1]
        overlap = min(ref.end, h.end) - max(ref.start, h.start)
        span = min(ref.length, h.length)
        if h.pos - ref.pos <= max_thickness and span > 0 and overlap > 0.5 * span:
            cur.append(h)
        else:
            out.append(_collapse(cur))
            cur = [h]
    out.append(_collapse(cur))
    return out


def _collapse(group: list[LineHit]) -> LineHit:
    pos = float(sum(g.pos for g in group) / len(group))
    start = min(g.start for g in group)
    end = max(g.end for g in group)
    return LineHit(pos=pos, start=start, end=end, thickness=len(group))


def refine_line_positions(
    gray: np.ndarray,
    lines: Sequence[LineHit],
    orientation: str = "h",
    span: int = 2,
) -> list[LineHit]:
    """用暗度加权把整数行/列号精化到亚像素。

    以 ``(255 - 灰度)`` 为权重在 ``pos ± span`` 的窄带内求质心。轴框线宽 1~3 像素时
    可把定位精度从 ±1 像素提升到约 0.1 像素，从而消除标定的系统性偏移。
    """
    H, W = gray.shape
    out: list[LineHit] = []
    for h in lines:
        base = int(round(h.pos))
        lo = max(0, base - span)
        if orientation == "h":
            hi = min(H, base + span + 1)
            if hi - lo < 2 or h.end <= h.start:
                out.append(h)
                continue
            prof = (255.0 - gray[lo:hi, h.start : h.end]).mean(axis=1)
        else:
            hi = min(W, base + span + 1)
            if hi - lo < 2 or h.end <= h.start:
                out.append(h)
                continue
            prof = (255.0 - gray[h.start : h.end, lo:hi]).mean(axis=0)

        tot = float(prof.sum())
        if tot <= 1e-9:
            out.append(h)
            continue
        w = prof / tot
        center = lo + float((w * np.arange(hi - lo)).sum())
        out.append(LineHit(pos=center, start=h.start, end=h.end, thickness=h.thickness))
    return out


def find_lines(
    mask: np.ndarray,
    min_ratio: float = 0.55,
    max_gap: int = 2,
    orientation: str = "h",
) -> list[LineHit]:
    """检测贯穿图像的长直线。``orientation`` 取 ``h`` 或 ``v``。

    关键点：同一行/列上的**多个**不连续长段必须全部保留。2x2 子图布局中，
    左右两个面板的顶边位于同一行号却是两段独立的线段；若只保留最长的一段，
    多面板布局将无法被正确切分。
    """
    mat = mask if orientation == "h" else mask.T
    span = mat.shape[1]
    need = max(20, int(span * min_ratio))
    raw: list[LineHit] = []
    for pos in range(mat.shape[0]):
        line = mat[pos]
        if int(line.sum()) < need:
            continue
        for s0, s1 in _runs(line, max_gap):
            if s1 - s0 >= need:
                raw.append(LineHit(pos=float(pos), start=s0, end=s1))
    return _merge_adjacent(raw)


# ------------------------------------------------------------------ 面板检测


def find_panels(
    img: ImageData,
    mask: np.ndarray,
    h_lines: Sequence[LineHit],
    v_lines: Sequence[LineHit],
    min_size: int = 40,
) -> list[Panel]:
    """由长直线网格推断绘图面板。

    先尝试闭合矩形（四边齐全），再退化为「左轴 + 下轴」的开放面板。
    """
    hs = sorted(h_lines, key=lambda l: l.pos)
    vs = sorted(v_lines, key=lambda l: l.pos)
    panels: list[Panel] = []
    used: set[tuple[float, float, float, float]] = set()

    # --- 闭合矩形：四条边都能找到支撑直线
    for i, top_l in enumerate(hs):
        for bot_l in hs[i + 1 :]:
            if bot_l.pos - top_l.pos < min_size:
                continue
            x_lo = max(top_l.start, bot_l.start)
            x_hi = min(top_l.end, bot_l.end)
            if x_hi - x_lo < min_size:
                continue
            left_c = [v for v in vs if v.pos <= x_lo + 8 and v.covers(top_l.pos, bot_l.pos)]
            right_c = [v for v in vs if v.pos >= x_hi - 8 and v.covers(top_l.pos, bot_l.pos)]
            if not left_c or not right_c:
                continue
            lft = min(left_c, key=lambda v: abs(v.pos - x_lo))
            rgt = min(right_c, key=lambda v: abs(v.pos - x_hi))
            if rgt.pos - lft.pos < min_size:
                continue
            key = (lft.pos, top_l.pos, rgt.pos, bot_l.pos)
            if key in used:
                continue
            used.add(key)
            panels.append(
                Panel(
                    left=lft.pos, top=top_l.pos, right=rgt.pos, bottom=bot_l.pos,
                    closed=True, edges_found=["top", "bottom", "left", "right"],
                )
            )

    if panels:
        panels = _dedupe_nested(panels)

    # --- 退化情形：仅「下轴 + 左轴」
    if not panels:
        bottom = max(hs, key=lambda l: l.length, default=None)
        left = max(vs, key=lambda l: l.length, default=None)
        if bottom is not None and left is not None:
            panels.append(
                Panel(
                    left=left.pos, top=float(max(left.start, 0)),
                    right=float(bottom.end), bottom=bottom.pos,
                    closed=False, edges_found=["bottom", "left"],
                )
            )
            panels[0].label = "open-axes"

    for idx, p in enumerate(panels):
        if not p.label:
            p.label = f"panel{idx + 1}"
        p.content_ratio = _content_ratio(mask, p)
    return panels


def _content_ratio(mask: np.ndarray, p: Panel) -> float:
    left, top, right, bottom = p.int_box
    sub = mask[top:bottom, left:right]
    if sub.size == 0:
        return 0.0
    return float(sub.mean())


_ROW_TOLERANCE = 6.0


def _dedupe_nested(panels: list[Panel]) -> list[Panel]:
    """剔除被更大面板完全包含的重复项（保留最外层），并按阅读序排列。

    排序必须**行优先 + 容差聚类**：亚像素精化会让同一行的左右面板得到
    14.4999 与 14.5001 这类微小差异，若直接用 ``(top, left)`` 严格排序，
    左右顺序会被颠倒，连带破坏调用方按 ``panel1/2/3...`` 定位面板的约定。
    """
    out: list[Panel] = []
    for p in sorted(panels, key=lambda q: -q.size[0] * q.size[1]):
        if any(
            q.left <= p.left and q.right >= p.right and q.top <= p.top and q.bottom >= p.bottom
            for q in out
        ):
            continue
        out.append(p)
    return sorted(out, key=lambda q: (round(q.top / _ROW_TOLERANCE), q.left))


# ------------------------------------------------------------------ 刻度检测


def _offset_hits(
    mask: np.ndarray,
    axis_pos: int,
    lo: int,
    hi: int,
    orientation: str,
    offset: int,
) -> int:
    """在"轴线向外第 ``offset`` 像素"这一条 1 像素宽的窄带上数命中数。"""
    H, W = mask.shape
    if orientation == "x":
        y = axis_pos + offset
        if not (0 <= y < H):
            return 0
        return int(mask[y, max(0, lo): min(W, hi)].sum())
    x = axis_pos - offset
    if not (0 <= x < W):
        return 0
    return int(mask[max(0, lo): min(H, hi), x].sum())


def auto_tick_band(
    mask: np.ndarray,
    axis_pos: float,
    lo: float,
    hi: float,
    orientation: str = "x",
    max_out: int = 20,
    line_ratio: float = 0.5,
) -> tuple[int, int] | None:
    """自动确定"从轴线向外多远仍属于刻度"的扫描带。

    实测动机：固定 ``band=(2,12)`` 会把**刻度数字标签**一起扫进来。matplotlib 默认布局下
    刻度本体只占轴线外 1..4 px，而标签要到 10 px 开外（实测 caseA：刻度在 B+1..B+4，
    标签起于 B+10）。把标签笔画当成刻度会凭空多出若干"刻度"，足以把等距的线性轴误判成
    非等距、进而触发错误的"该轴不是线性刻度"警告。

    规则（自轴线向外逐像素推进）：

    1. 命中数 ≥ ``line_ratio`` × 跨度 → 判定仍在**轴线本体**上，跳过（轴线有宽度，
       且与 tick 相邻，实测对数轴 y 方向第 1 个偏移正是轴线本身）；
    2. 命中数为 0 → 撞上刻度与标签之间的空白，**终止**；
    3. 其余情况计入扫描带。

    返回 ``(first_offset, last_offset)``（含两端）；一个偏移都未纳入时返回 ``None``。
    """
    ap = int(round(axis_pos))
    l = int(round(lo)) - 1          # pad=1：边界上的刻度也要参与判断
    h = int(round(hi)) + 1
    depth = mask.shape[0] if orientation == "y" else mask.shape[1]
    span = max(1, min(depth, h) - max(0, l))

    first: int | None = None
    last: int | None = None
    for off in range(1, max_out + 1):
        n = _offset_hits(mask, ap, l, h, orientation, off)
        if n >= line_ratio * span:
            continue
        if n == 0:
            break
        if first is None:
            first = off
        last = off
    if first is None or last is None:
        return None
    return first, last


def find_ticks(
    mask: np.ndarray,
    axis_pos: float,
    lo: float,
    hi: float,
    orientation: str = "x",
    band: tuple[int, int] = (2, 12),
    min_len: int = 3,
    pad: int = 0,
    mode: str = "sum",
) -> list[int]:
    """沿某条轴向外侧扫描短线段，返回刻度位置的像素坐标列表。

    ``orientation='x'``：轴为水平线（位于 ``axis_pos`` 行），刻度向下延伸。
    ``orientation='y'``：轴为垂直线（位于 ``axis_pos`` 列），刻度向左延伸。

    ``lo``/``hi`` 为**闭区间**端点。默认 ``pad=0`` 时内部按半开区间切片，端点行/列会被
    排除；当调用方要求"边界上的刻度也必须检出"（例如标定审核要数清刻度总数）时应传
    ``pad=1``。实测：matplotlib 对数轴的**首个与末个主刻度恰好压在轴框边界上**，``pad=0``
    会稳定漏掉它们，使刻度数少 2 个、并让后续"每格数值"推算失真。

    ``mode`` 决定带内命中判据：

    * ``"sum"``：带内命中像素总数 ≥ ``min_len``（原行为，适合同长的粗刻度）；
    * ``"any"``：带内**任一**偏移命中即算（配合 :func:`auto_tick_band`）。对数轴的主刻度
      长约 3 px、次刻度仅 1 px，同行不同长；``"sum"`` 只会捞出主刻度，``"any"`` 才能把
      两类刻度都计入——而**次刻度的存在与否正是"非等距 ⇒ 该轴不是线性刻度"的判定依据**。
    """
    axis_pos = int(round(axis_pos))
    lo = int(round(lo)) - int(pad)
    hi = int(round(hi)) + int(pad)
    H, W = mask.shape

    if orientation == "x":
        y0 = min(axis_pos + band[0], H - 1)
        y1 = min(axis_pos + band[1] + 1, H)
        if y1 <= y0:
            return []
        strip = mask[y0:y1, max(0, lo) : min(W, hi)]
        if mode == "any":
            hits = [int(i) + max(0, lo) for i in np.flatnonzero(strip.any(axis=0))]
        else:
            hits = [int(i) + max(0, lo)
                    for i in np.flatnonzero(strip.sum(axis=0) >= min_len)]
    else:
        x0 = max(axis_pos - band[1], 0)
        x1 = max(axis_pos - band[0] + 1, 1)
        if x1 <= x0:
            return []
        strip = mask[max(0, lo) : min(H, hi), x0:x1]
        if mode == "any":
            hits = [int(i) + max(0, lo) for i in np.flatnonzero(strip.any(axis=1))]
        else:
            hits = [int(i) + max(0, lo)
                    for i in np.flatnonzero(strip.sum(axis=1) >= min_len)]

    if not hits:
        return []
    # 相邻像素聚为一簇（同一根刻度线有宽度）
    groups: list[list[int]] = [[hits[0]]]
    for h in hits[1:]:
        if h - groups[-1][-1] <= 2:
            groups[-1].append(h)
        else:
            groups.append([h])
    return [int(round(sum(g) / len(g))) for g in groups]


def uniform_tick_spacing(ticks: Sequence[int], tol_ratio: float = 0.35) -> float:
    """评估刻度间距的规整度：返回 0~1，1 表示完全等距。"""
    if len(ticks) < 3:
        return 0.0
    d = np.diff(np.asarray(ticks, dtype=float))
    d = d[d > 0]
    if d.size < 2:
        return 0.0
    med = float(np.median(d))
    if med <= 0:
        return 0.0
    dev = np.abs(d - med) / med
    return float((dev <= tol_ratio).mean())


def ticks_for_box(
    img: "ImageData",
    box: Sequence[float],
    dark_threshold: int = 110,
    min_count: int = 3,
) -> tuple[list[int], list[int]]:
    """在**指定的**数据区矩形上检出两轴刻度位置。

    与 :func:`detect` 内建刻度检测的区别：后者固定使用 ``main_panel()``（面积最大的
    面板），在 2x2 等多面板图中会对**错误的面板**取刻度。本函数以调用方给定的矩形为
    准，因此可与 ``--axes`` / ``--panel`` 严格对齐——标定审核依赖这一点。

    先按"刻度朝外"扫描，检不出时退回"刻度朝内"。返回 ``(tick_x, tick_y)`` 的像素坐标
    列表，均为整数像素位置。

    刻度检出刻意用**自适应扫描带 + 带内任一像素命中**（见 :func:`auto_tick_band` 与
    :func:`find_ticks` 的 ``mode``），因为标定审核要的是**刻度总数与间距模式**：固定的
    宽扫描带会把刻度标签一并算作刻度（污染间距模式），而"带内求和 ≥3 px"又会漏掉对数轴
    上只有 1 px 长的次刻度（丢掉"非等距"这一唯一的硬证据）。

    ``pad=1``：轴框边界上的刻度（实测 matplotlib 对数轴的首/末主刻度即压在此处）必须
    计入，否则刻度总数偏少。反向刻度（朝内）作为兜底保留固定带宽。
    """
    left, top, right, bottom = (float(v) for v in box)
    mask = foreground_mask(img, dark_threshold=dark_threshold)

    band_x = auto_tick_band(mask, bottom, left, right, orientation="x")
    tick_x: list[int] = []
    if band_x is not None:
        tick_x = find_ticks(mask, bottom, left, right, orientation="x",
                            band=band_x, pad=1, mode="any")
    if len(tick_x) < min_count:
        tick_x = find_ticks(mask, bottom, left, right, orientation="x",
                            band=(2, 12), min_len=3, pad=1)

    band_y = auto_tick_band(mask, left, top, bottom, orientation="y")
    tick_y: list[int] = []
    if band_y is not None:
        tick_y = find_ticks(mask, left, top, bottom, orientation="y",
                            band=band_y, pad=1, mode="any")
    if len(tick_y) < min_count:
        tick_y = find_ticks(mask, left, top, bottom, orientation="y",
                            band=(2, 12), min_len=3, pad=1)

    return tick_x, tick_y


# ------------------------------------------------------------------ 综合入口


_MULTISCALE_RATIOS = (0.55, 0.42, 0.32, 0.24, 0.16)


def detect(
    path: str | Path,
    dark_threshold: int = 110,
    min_ratio: float | None = None,
    detect_ticks: bool = True,
) -> DetectReport:
    """对一张图执行结构检测，产出报告。

    ``min_ratio`` 为 ``None`` 时启用**多尺度搜索**：长度阈值从严格到宽松依次尝试，
    一旦检出至少一个面板即停。单幅插图（轴框横跨整图）与 2x2、3x3 子图面板
    （轴框仅占整图的一小部分）都能自适应，无需调用方预先知道布局。
    """
    img = load_image(path)
    mask = foreground_mask(img, dark_threshold=dark_threshold)

    ratios = (float(min_ratio),) if min_ratio is not None else _MULTISCALE_RATIOS
    h_lines: list[LineHit] = []
    v_lines: list[LineHit] = []
    panels: list[Panel] = []
    used_ratio = ratios[0]
    for r in ratios:
        h_try = find_lines(mask, min_ratio=r, orientation="h")
        v_try = find_lines(mask, min_ratio=r, orientation="v")
        p_try = find_panels(img, mask, h_try, v_try)
        if p_try:
            # 先由整数线索确定布局，再把线条位置精化到亚像素并重建面板，
            # 使标定锚点不受线宽/抗锯齿造成的半像素偏置影响。
            h_lines = refine_line_positions(img.gray, h_try, "h")
            v_lines = refine_line_positions(img.gray, v_try, "v")
            panels = find_panels(img, mask, h_lines, v_lines) or p_try
            used_ratio = r
            break
    else:
        used_ratio = ratios[-1]

    rep = DetectReport(
        image=str(path),
        size=(img.width, img.height),
        background_rgb=img.background_rgb,
        panels=panels,
        horizontal_lines=h_lines,
        vertical_lines=v_lines,
    )
    rep.notes.append(f"轴线长度阈值 min_ratio={used_ratio:.2f}")

    main = rep.main_panel()
    if detect_ticks and main is not None:
        rep.tick_x = find_ticks(mask, main.bottom, main.left, main.right, orientation="x")
        rep.tick_y = find_ticks(mask, main.left, main.top, main.bottom, orientation="y")

    # ------------------ 置信度
    score = 0.0
    if main is not None:
        score += 0.40 if main.closed else 0.15
        area_frac = (main.size[0] * main.size[1]) / max(1, img.width * img.height)
        score += 0.20 * min(1.0, area_frac / 0.45)
        score += 0.20 * uniform_tick_spacing(rep.tick_x)
        score += 0.20 * uniform_tick_spacing(rep.tick_y)
    else:
        rep.warnings.append("未检出任何面板/轴框，需人工指定数据区像素矩形")

    if main is not None and not main.closed:
        rep.warnings.append(
            "轴框未闭合（仅检出部分边），数据区上/右边界为推断值，建议人工确认"
        )
    if detect_ticks and len(rep.tick_x) < 3:
        rep.warnings.append(f"x 轴刻度检出过少（{len(rep.tick_x)} 个），数值标定需人工给定")
    if detect_ticks and len(rep.tick_y) < 3:
        rep.warnings.append(f"y 轴刻度检出过少（{len(rep.tick_y)} 个），数值标定需人工给定")

    rep.notes.append("刻度仅作辅助定位；轴数值必须由外部提供，本工具不做 OCR 猜测")
    rep.confidence = round(min(1.0, score), 3)
    return rep


def render_diagnostic(
    img: ImageData,
    report: DetectReport,
    out_path: str | Path,
    curve_points: dict[str, list[tuple[float, float]]] | None = None,
) -> Path:
    """输出检测诊断图（供质量把关，非默认交付物）。"""
    from PIL import ImageDraw

    canvas = Image.fromarray(img.rgb.copy())
    d = ImageDraw.Draw(canvas)

    for p in report.panels:
        d.rectangle(p.int_box, outline=(0, 160, 255), width=2)
        d.text((p.left + 4, max(0.0, p.top - 14)),
               f"{p.label} {'closed' if p.closed else 'open'}", fill=(0, 120, 220))

    base = report.main_panel()
    if base is not None:
        for t in report.tick_x:
            d.line([(t, base.bottom), (t, base.bottom + 10)], fill=(255, 140, 0), width=1)
        for t in report.tick_y:
            d.line([(base.left - 10, t), (base.left, t)], fill=(255, 140, 0), width=1)

    if curve_points:
        palette = [(255, 0, 0), (0, 180, 0), (0, 0, 255), (255, 0, 255), (0, 200, 200)]
        for i, (_name, pts) in enumerate(curve_points.items()):
            col = palette[i % len(palette)]
            step = max(1, len(pts) // 400)
            for x, y in pts[::step]:
                d.ellipse([x - 2, y - 2, x + 2, y + 2], outline=col)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out
