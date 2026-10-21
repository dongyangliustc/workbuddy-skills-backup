"""数据要素提取：多曲线分离、散点标记检测、误差棒分离。

三档提取策略，按图元形态自适应：

``line``
    逐列扫描，取该列前景像素的中位数作为曲线纵坐标；对同色多曲线可切换为
    连续性跟踪模式（``trace``），按「距上一列结果最近」选择分段。
``scatter``
    连通域标记 + 质心定位，按面积与圆度筛选标记点，可剔除文字与图例。
``errorbar``
    对细长竖向连通域，取包围盒上下端为误差范围、几何中心为数据点；
    若标记点与误差棒分为两色，则分别提取后配对。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

try:  # scipy 为可选依赖，仅散点/误差棒路径需要
    from scipy import ndimage as _ndi
except Exception:  # pragma: no cover
    _ndi = None

RGB = tuple[int, int, int]


# ------------------------------------------------------------------ 数据结构


@dataclass
class Series:
    """一条待交付的数据序列（内部以像素坐标存储）。"""

    name: str
    kind: str = "line"                      # line | scatter | errorbar
    color: RGB = (0, 0, 0)
    points: list[tuple[float, float]] = field(default_factory=list)
    yerr: list[tuple[float, float]] | None = None   # 每点 (下误差, 上误差)，像素单位
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.points)

    def sorted_points(self) -> list[tuple[float, float]]:
        return sorted(self.points, key=lambda p: (p[0], p[1]))


# ------------------------------------------------------------------ 颜色工具


def _as_rgb(c: Sequence[int]) -> np.ndarray:
    return np.asarray(c[:3], dtype=np.float64)


def color_mask(rgb: np.ndarray, color: Sequence[int], tol: float = 64.0) -> np.ndarray:
    """生成与目标颜色的距离在 ``tol`` 之内的掩码。"""
    diff = rgb.astype(np.float64) - _as_rgb(color)
    return (diff * diff).sum(axis=2) <= tol * tol


def apply_exclude_mask(
    mask: np.ndarray,
    boxes: Sequence[Sequence[float]],
    off_x: int = 0,
    off_y: int = 0,
) -> np.ndarray:
    """把 ``boxes`` 覆盖的区域从 ``mask`` 中剔除（置为背景）。

    ``boxes`` 为**整图像素**坐标 ``(left, top, right, bottom)``；``off_x``/``off_y``
    是 :func:`extract_series` 内部 ``_crop`` 得到的子图偏移，用于把整图坐标换算到
    子图坐标。越界自动裁剪，不影响掩码形状。

    用途（SPEC §3.3）：把 PDF 文本层的文字行 bbox 从提取区域剔除，确定性修掉两条
    长期限制——「图例与曲线同色时被当数据一并提取」与 troubleshooting B7
    「误检出文字标注为序列」。

    调用时机固定为**颜色掩码生成之后、连通域分析之前**：早于掩码没有对象可剔，
    晚于连通域分析则文字已经统计成连通域，剔了也回不去。

    ``mask`` 由 ``color_mask`` 每次新建，故就地修改是安全的（也避免了一次
    全区域拷贝）。``boxes`` 为空时**原样返回**，不触碰任何像素。
    """
    if not boxes:
        return mask
    h, w = mask.shape[:2]
    for b in boxes:
        x0 = int(math.floor(min(b[0], b[2]))) - off_x
        x1 = int(math.ceil(max(b[0], b[2]))) - off_x
        y0 = int(math.floor(min(b[1], b[3]))) - off_y
        y1 = int(math.ceil(max(b[1], b[3]))) - off_y
        x0 = max(0, min(x0, w))
        x1 = max(0, min(x1, w))
        y0 = max(0, min(y0, h))
        y1 = max(0, min(y1, h))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = False
    return mask


def dominant_colors(
    rgb: np.ndarray,
    background: Sequence[int] | None = None,
    top_k: int = 6,
    min_count: int = 30,
    quant: int = 16,
    merge_dist: float = 48.0,
    bg_margin: float = 60.0,
) -> list[tuple[RGB, int]]:
    """统计区域内的主导前景色。

    做法：颜色量化做直方图取峰，再按颜色距离合并邻近峰，最后按像素数排序。

    ``bg_margin`` 为背景剔除阈值：与背景色距离小于该值的候选色被丢弃。

    **该阈值必须与后续 ``color_mask`` 的匹配容差 ``tol`` 保持一致**。若剔除阈值
    小于匹配容差，会出现这样的缺陷：某个候选色与背景的距离刚好越过剔除阈值
    （实测 61.0 vs 阈值 60），得以进入候选项；但随后以 ``tol=64`` 生成掩码时，
    该掩码会把**整个浅色背景**判为前景（实测掩码像素数 72434 ≈ 全区域 73455），
    输出一条覆盖全图的伪序列。故 ``extract_series`` 显式传入 ``bg_margin=tol``。
    """
    flat = rgb.reshape(-1, 3).astype(np.int32)
    if flat.size == 0:
        return []
    q = flat // max(1, quant)
    key = (q[:, 0] << 16) | (q[:, 1] << 8) | q[:, 2]
    uniq, counts = np.unique(key, return_counts=True)
    order = np.argsort(-counts)
    peaks: list[tuple[np.ndarray, int]] = []
    for i in order:
        if counts[i] < min_count:
            break
        k = int(uniq[i])
        # 直接用属于该量化桶的原始像素求均值，避免量化偏差
        bucket = key == k
        mean_col = flat[bucket].mean(axis=0)
        peaks.append((mean_col, int(counts[i])))

    merged: list[list] = []
    for col, cnt in peaks:
        for grp in merged:
            if np.linalg.norm(col - grp[0]) <= merge_dist:
                grp[1] += cnt
                grp[2].append(col)
                break
        else:
            merged.append([col, cnt, [col]])
    merged.sort(key=lambda g: -g[1])
    out: list[tuple[RGB, int]] = []
    for grp in merged[: top_k * 2]:
        mean_col = np.mean(np.stack(grp[2]), axis=0)
        c = tuple(int(round(v)) for v in mean_col)
        if background is not None and np.linalg.norm(mean_col - _as_rgb(background)) < bg_margin:
            continue
        out.append((c, grp[1]))
        if len(out) >= top_k:
            break
    return out


# ------------------------------------------------------------------ 辅助


def _crop(rgb: np.ndarray, region: tuple[int, int, int, int]) -> tuple[np.ndarray, int, int]:
    left, top, right, bottom = region
    H, W = rgb.shape[:2]
    left = max(0, min(int(left), W - 1))
    right = max(left + 1, min(int(right), W))
    top = max(0, min(int(top), H - 1))
    bottom = max(top + 1, min(int(bottom), H))
    return rgb[top:bottom, left:right], left, top


def _column_trace(mask: np.ndarray, y0: int, mode: str = "column") -> list[tuple[float, float]]:
    """逐列提取曲线纵坐标。``mode`` 取 ``column`` 或 ``trace``。"""
    pts: list[tuple[float, float]] = []
    prev: float | None = None
    for x in range(mask.shape[1]):
        idx = np.flatnonzero(mask[:, x])
        if idx.size == 0:
            continue
        runs = _split_runs(idx)
        if len(runs) == 1 or mode == "column":
            if mode == "trace" and prev is not None and len(runs) > 1:
                pick = min(runs, key=lambda r: abs(float(np.median(r)) - prev))
            else:
                pick = max(runs, key=len) if len(runs) > 1 else runs[0]
        else:
            pick = min(runs, key=lambda r: abs(float(np.median(r)) - prev))
        y = float(np.median(pick))
        prev = y
        pts.append((float(x), y0 + y))
    return pts


def _split_runs(idx: np.ndarray, max_gap: int = 1) -> list[np.ndarray]:
    if idx.size == 0:
        return []
    brk = np.flatnonzero(np.diff(idx) > max_gap + 1)
    starts = np.concatenate(([0], brk + 1))
    ends = np.concatenate((brk + 1, [idx.size]))
    return [idx[s:e] for s, e in zip(starts, ends)]


# ------------------------------------------------------------------ 连通域


def _components(mask: np.ndarray, close: bool = True):
    """连通域分析，返回 ``(标签图, 组件属性列表)``。

    标记前先做一次 3x3 闭运算：空心标记（``facecolors='none'``）的描边在抗锯齿
    下常断成数段，直接标记会把同一个数据点拆成多个连通域而重复计数。质心、面积与
    包围盒仍在**原始**掩码上统计，因此定位精度不受形态学操作影响。
    """
    if _ndi is None:
        raise RuntimeError("scipy 未安装：散点/误差棒提取需要 scipy.ndimage")
    st = np.ones((3, 3), dtype=int)
    # 迭代两次闭运算：空心标记（matplotlib 的 facecolors='none' 圆环）描边在
    # 抗锯齿下可断裂 2~3 像素，单次 3x3 闭运算桥接不足，会把同一数据点重复计数。
    seed = _ndi.binary_closing(mask, structure=st, iterations=2) if close else mask
    lbl, _n = _ndi.label(seed, structure=st)
    props = []
    for i, sl in enumerate(_ndi.find_objects(lbl), start=1):
        if sl is None:
            continue
        region = lbl[sl] == i
        core = region & mask[sl]
        if not core.any():
            core = region
        ys, xs = np.nonzero(core)
        if ys.size == 0:
            continue
        area = int(ys.size)
        y0 = int(sl[0].start + ys.min())
        y1 = int(sl[0].start + ys.max()) + 1
        x0 = int(sl[1].start + xs.min())
        x1 = int(sl[1].start + xs.max()) + 1
        h = max(1, y1 - y0)
        w = max(1, x1 - x0)
        props.append(
            {
                "label": i,
                "area": area,
                "h": h,
                "w": w,
                "aspect": h / w,
                "fill": area / float(h * w),
                "cx": float(sl[1].start + xs.mean()),
                "cy": float(sl[0].start + ys.mean()),
                "y0": y0,
                "y1": y1,
                "x0": x0,
                "x1": x1,
            }
        )
    return lbl, props


# ------------------------------------------------------------------ 主流程


def drop_antialias_colors(
    colors: Sequence[RGB],
    background: Sequence[int] | None = None,
    tol: float = 14.0,
    min_t: float = 0.06,
    max_t: float = 0.94,
) -> list[RGB]:
    """剔除候选色中的**抗锯齿混合色**。

    原理：抗锯齿像素是「纯色芯」与「背景」的线性混合，因此在 RGB 空间中**严格位于
    连线上**。设纯色芯为 q、背景为 b，候选色 p 若满足

        p ≈ q + t·(b - q),   t ∈ (0, 1)

    则为混合色。判定用「投影参数 t 落在区间内」+「到线段的垂距 ≤ tol」两个条件。

    实测验证（半对数图，红曲线）：纯色芯 (217,55,56)、背景 (255,255,255)、
    光晕 (244,200,200) → t≈0.71、垂距≈1.9，判为混合色；而同期网格线灰
    (228,228,228) 的垂距极大（代入 t≈0.29 时 G 通道偏差 115），不误杀。

    **为什么要在生成掩码之前剔除，而不是事后靠 `dedupe_series` 补救**：
    光晕色是浅色，`color_mask(tol=64)` 会把图中**其它浅色元素**（尤其是浅灰网格线）
    一并匹配进来。实测该光晕掩码 4246 像素中有 4 条水平网格线各占 495 像素（区域宽
    500），掩码已被网格线主导，提取出的"序列"其实是网格线，与曲线不重合，于是
    `dedupe_series` 的重合率判据失效、无法补救。在源头剔除才能真正解决。

    按「与背景的显著度」降序遍历，保证纯色芯先入 `kept`、光晕随后被判为混合色，
    与候选色自身的像素计数无关（实测光晕像素数反而多于纯色芯）。
    """
    if background is None or len(colors) <= 1:
        return list(colors)
    bg = _as_rgb(background).astype(np.float64)
    ordered = sorted(colors, key=lambda c: -float(np.linalg.norm(_as_rgb(c) - bg)))
    kept: list[RGB] = []
    for c in ordered:
        p = _as_rgb(c).astype(np.float64)
        mixed = False
        for k in kept:
            q = _as_rgb(k).astype(np.float64)
            d = bg - q
            denom = float(d @ d)
            if denom <= 1e-9:
                continue
            t = float((p - q) @ d) / denom
            if not (min_t <= t <= max_t):
                continue
            if float(np.linalg.norm((p - q) - t * d)) <= tol:
                mixed = True
                break
        if not mixed:
            kept.append(c)
    return kept


def extract_series(
    rgb: np.ndarray,
    region: tuple[int, int, int, int],
    background: Sequence[int] | None = None,
    colors: Sequence[RGB] | None = None,
    names: Sequence[str] | None = None,
    kind: str = "auto",
    tol: float = 64.0,
    min_points: int = 8,
    max_components: int = 2000,
    errorbar_aspect: float = 2.2,
    marker_max_size: int | None = None,
    dedupe: bool = True,
    exclude: Sequence[Sequence[float]] | None = None,
) -> list[Series]:
    """从指定区域提取数据序列。

    ``kind`` 取 ``auto`` / ``line`` / ``scatter`` / ``errorbar``；``auto`` 依据连通域
    形态自动判别。``colors`` 为空时自动做颜色聚类并按像素量降序排列；未显式命名的
    序列会以「序号 + 十六进制色值」命名，便于在交付文件中辨识来源。

    ``dedupe`` 默认开启，剔除抗锯齿边缘色派生的重复序列（见 :func:`dedupe_series`）。

    ``exclude`` 是**可选**的排除矩形列表（整图像素坐标，见 :func:`apply_exclude_mask`），
    在颜色掩码生成后、连通域分析前把文字行 bbox 等区域置为背景。默认 ``None`` 时
    本函数的每一步与引入该参数之前**逐位一致**——这是 SPEC §6 的回归红线，不得破坏。
    """
    sub, off_x, off_y = _crop(rgb, region)

    if colors is None:
        found = dominant_colors(sub, background=background, top_k=6, bg_margin=tol)
        # 先剔除抗锯齿混合色，再生成掩码——否则浅色光晕会吞掉网格线等浅色元素
        colors = drop_antialias_colors([c for c, _ in found], background)
    if not colors:
        return []

    series: list[Series] = []
    for i, col in enumerate(colors):
        mask = color_mask(sub, col, tol=tol)
        if exclude:
            mask = apply_exclude_mask(mask, exclude, off_x, off_y)
        if int(mask.sum()) < min_points:
            continue
        nm = names[i] if names and i < len(names) else _color_name(col, i)
        s = _extract_one(
            mask, col, nm, kind,
            off_x=off_x, off_y=off_y,
            errorbar_aspect=errorbar_aspect,
            marker_max_size=marker_max_size,
            max_components=max_components,
        )
        if s is not None and len(s) >= min_points:
            series.append(s)
    return dedupe_series(series, background=background) if dedupe else series


def _color_name(color: Sequence[int], index: int) -> str:
    """以「序号 + 十六进制色值」命名未显式命名的序列。"""
    r, g, b = (int(v) & 0xFF for v in color[:3])
    return f"c{index + 1}_{r:02x}{g:02x}{b:02x}"



def _extract_one(
    mask: np.ndarray,
    color: RGB,
    name: str,
    kind: str,
    off_x: int,
    off_y: int,
    errorbar_aspect: float,
    marker_max_size: int | None,
    max_components: int,
) -> Series | None:
    k = kind
    if k == "auto":
        k = _infer_kind(mask, errorbar_aspect)

    if k == "line":
        pts = _column_trace(mask, off_y, mode="column")
        pts = [(x + off_x, y) for x, y in pts]
        return Series(name=name, kind="line", color=color, points=pts,
                      meta={"pixels": int(mask.sum())})

    _, props = _components(mask)
    if not props:
        return None

    if len(props) > max_components:
        props.sort(key=lambda p: -p["area"])
        props = props[:max_components]

    if k == "scatter":
        pts, sizes = [], []
        for p in props:
            if _looks_like_text(p):
                continue
            pts.append((p["cx"] + off_x, p["cy"] + off_y))
            sizes.append(p["area"])
        if not pts:
            return None
        med = float(np.median(sizes)) if sizes else 0.0
        return Series(name=name, kind="scatter", color=color, points=pts,
                      meta={"marker_area_median": round(med, 2),
                            "n_components": len(pts)})

    # ---- errorbar：分离细长竖向分量
    pts: list[tuple[float, float]] = []
    errs: list[tuple[float, float]] = []
    for p in props:
        if _looks_like_text(p):
            continue
        if p["aspect"] >= errorbar_aspect:
            cy = 0.5 * (p["y0"] + p["y1"])
            pts.append((p["cx"] + off_x, cy + off_y))
            errs.append((float(p["y1"] - cy), float(cy - p["y0"])))
        elif pts and abs(p["cy"] + off_y - pts[-1][1]) < 6 and abs(p["cx"] + off_x - pts[-1][0]) < 6:
            continue  # 标记点已由误差棒分量代表
    if not pts:
        # 退化为散点：误差棒与标记点同色但未被分离
        pts = [(p["cx"] + off_x, p["cy"] + off_y) for p in props if not _looks_like_text(p)]
        return Series(name=name, kind="scatter", color=color, points=pts,
                      meta={"note": "未检出误差棒分量，已按散点输出"})
    order = np.argsort([p[0] for p in pts])
    pts = [pts[i] for i in order]
    errs = [errs[i] for i in order]
    return Series(name=name, kind="errorbar", color=color, points=pts, yerr=errs,
                  meta={"n_components": len(pts)})


def _infer_kind(mask: np.ndarray, errorbar_aspect: float) -> str:
    """依据连通域形态自动判别图元类型。"""
    try:
        _, props = _components(mask)
    except RuntimeError:
        return "line"
    if not props:
        return "line"
    props = [p for p in props if not _looks_like_text(p)]
    if not props:
        return "line"
    tall = sum(1 for p in props if p["aspect"] >= errorbar_aspect)
    if tall >= max(3, 0.25 * len(props)):
        return "errorbar"
    big = [p for p in props if p["area"] > 400 or p["w"] > 0.7 * mask.shape[1]]
    if len(props) >= 8 and not big:
        return "scatter"
    if big:
        return "line"
    return "scatter" if len(props) > 3 else "line"


def _looks_like_text(p: dict) -> bool:
    """粗筛文字/图例：极小且填充率高的连通域，或面积过小的噪点。"""
    if p["area"] <= 6:
        return True
    if p["h"] <= 8 and p["w"] <= 8 and p["fill"] > 0.65:
        return True
    return False


def estimate_marker_size(rgb: np.ndarray, region: tuple[int, int, int, int],
                         color: Sequence[int], tol: float = 64.0) -> float:
    """估计标记点直径（像素），用于误差棒与标记点分离。"""
    sub, _, _ = _crop(rgb, region)
    mask = color_mask(sub, color, tol=tol)
    try:
        _, props = _components(mask)
    except RuntimeError:
        return 0.0
    if not props:
        return 0.0
    small = [p for p in props if p["aspect"] < 2.0 and p["area"] > 6]
    if not small:
        return 0.0
    return float(np.median([max(p["h"], p["w"]) for p in small]))


def resample_polyline(
    points: Sequence[tuple[float, float]],
    n: int | None = None,
    step_x: float | None = None,
) -> list[tuple[float, float]]:
    """对逐列提取的折线做等距重采样，便于后续分析或压缩交付体积。

    以 x 为自变量按线性插值取 ``n`` 个点，或按 ``step_x`` 像素等距取样。
    """
    pts = sorted(points, key=lambda p: p[0])
    if len(pts) < 2:
        return list(pts)
    xs = np.asarray([p[0] for p in pts], dtype=float)
    ys = np.asarray([p[1] for p in pts], dtype=float)
    uniq, idx = np.unique(xs, return_index=True)
    if uniq.size < 2:
        return list(pts)
    ys = ys[idx]
    if step_x:
        grid = np.arange(uniq[0], uniq[-1] + 1e-9, step_x)
    elif n and n > 1:
        grid = np.linspace(uniq[0], uniq[-1], int(n))
    else:
        return list(pts)
    return [(float(g), float(np.interp(g, uniq, ys))) for g in grid]


# ------------------------------------------------------------------ 去重


def _nearest_distances(
    a: Sequence[tuple[float, float]],
    b: Sequence[tuple[float, float]],
) -> np.ndarray | None:
    """a 中每个点到 b 的最近距离（像素）。"""
    if not a or not b:
        return None
    A = np.asarray(a, dtype=float)
    B = np.asarray(b, dtype=float)
    try:
        from scipy.spatial import cKDTree

        d, _ = cKDTree(B).query(A, k=1)
        return np.asarray(d, dtype=float)
    except Exception:  # pragma: no cover - scipy 缺失时按分块暴力计算
        best = np.full(A.shape[0], np.inf)
        for i in range(0, B.shape[0], 256):
            chunk = B[i : i + 256]
            d2 = ((A[:, None, :] - chunk[None, :, :]) ** 2).sum(axis=2)
            best = np.minimum(best, d2.min(axis=1))
        return np.sqrt(best)


def _mean_nearest_distance(
    a: Sequence[tuple[float, float]],
    b: Sequence[tuple[float, float]],
) -> float | None:
    """a 中每个点到 b 的最近距离的平均值（像素），供诊断使用。"""
    d = _nearest_distances(a, b)
    return None if d is None else float(np.mean(d))


def _halo_tolerance(s: Series, base: float) -> float:
    """散点序列的抗锯齿光晕容差。

    折线的抗锯齿边缘紧贴曲线芯，点对点距离判据即可捕获；但**散点标记的光晕位于
    标记圆周上**，它与标记中心的距离约等于标记半径，远大于 ``base``（默认 4px），
    因此按中心距离比较会漏判，浅色光晕会被当成独立序列输出。

    该函数按连通域面积反推标记半径（``r = sqrt(area/π)``）并叠加到基础容差上。
    非散点序列或缺少面积元数据时返回 ``base``。
    """
    if s.kind != "scatter":
        return base
    area = s.meta.get("marker_area_median")
    if not area:
        return base
    return base + float(np.sqrt(max(0.0, float(area)) / math.pi))


def dedupe_series(
    series: Sequence[Series],
    tol_px: float = 4.0,
    min_overlap: float = 0.7,
    background: Sequence[int] | None = None,
) -> list[Series]:
    """剔除由抗锯齿边缘色派生的重复序列。

    彩色曲线在抗锯齿下会派生多条近乎平行的高饱和色带（原色 + 过渡色），颜色聚类
    会把它们当成不同"曲线"。但它们的点集在空间上高度重合，因此按**显著性降序**
    保留，凡「候选序列中有 ``min_overlap`` 以上比例的点落在已保留序列 ``tol_px``
    内」者判为重复。

    三个关键设计：

    1. 用**重合率**而非平均距离判定：派生的色带常只覆盖曲线的一部分（陡峭段明显、
       平缓段被背景淹没），平均距离会被少数未覆盖点拉高而漏判。
    2. 用**颜色相对背景的显著性**而非点数排序：抗锯齿色是原色与背景的混合，其与
       背景的距离必然更小，而它在某些图上像素数反而更多（色带更宽）。若按点数排序
       会保留被冲淡的边缘色，导致曲线范围被截断（实测 y 范围从 [-1,1] 缩成
       [-0.885,1.006]）。未提供背景色时退回按点数排序。
    3. **散点标记的光晕需按标记半径放宽容差**（见 :func:`_halo_tolerance`）：光晕
       位于标记圆周而非圆心，否则漏判（实测蓝色散点因此多出一条 28 点的浅蓝色
       "伪折线"）。

    注意：若两条真实曲线在绝大部分区间严格重叠，会被误合并，此时应使用
    ``--no-dedupe``。
    """
    if len(series) <= 1:
        return list(series)

    def rank_key(s: Series) -> float:
        if background is None:
            return -float(len(s))
        r, g, b = (int(v) for v in s.color[:3])
        br, bg_, bb = (int(v) for v in background[:3])
        return -math.sqrt((r - br) ** 2 + (g - bg_) ** 2 + (b - bb) ** 2)

    kept: list[Series] = []
    for s in sorted(series, key=rank_key):
        for k in kept:
            d = _nearest_distances(s.points, k.points)
            if d is None:
                continue
            tol = max(_halo_tolerance(k, tol_px), _halo_tolerance(s, tol_px))
            if float((d < tol).mean()) >= min_overlap:
                break
        else:
            kept.append(s)
    return kept


