"""黑白线画（零彩色）分离引擎：按「笔画宽度 × 划线模式」分离同色多曲线。

为什么必须另开一条分离轴
------------------------
native 引擎的分离轴是**颜色聚类**（``dominant_colors`` → ``color_mask`` → 一色一序列）。
当图中彩色像素为 0（纯黑白线画，2000 年前印刷期刊常见）时**不存在**可用的颜色差异：
四条曲线即使靠「线宽 × 划线模式」区分（如 IC 粗/SC 细 × LG 实线/VG 虚线），
在 RGB 上也完全同色，颜色引擎只能给出**并集**——那不是任何一条曲线。

本模块提供第二条分离轴：**几何线型**。全部算法只用 Pillow + numpy + scipy，
不引入任何新依赖（无 cv2、无 skimage）。

两条核心原理
------------
**① 宽度估计必须与斜率无关。**

天真的做法是「按列内前景连续段的长度估宽」。该值在水平段 ≈ 笔画宽，
在陡峭段 ≈ 笔画宽 / |sin θ| —— 斜率一变，宽度就假性放大，同一条曲线在
平缓段被判"细"、在陡峭段被判"粗"，分离轴自己先崩了。

本模块改用 **EDT（欧氏距离变换）剖面**：``W = 2 · max(EDT[该段])``。
EDT 是「到最近背景像素的欧氏距离」，各向同性——水平笔画与垂直笔画的纵剖面
最大值都等于 ``w/2``，与走向**无关**。因此宽度估计天然免疫斜率。

**② 缺口不插值。**

虚线间隙、图例遮挡、曲线越出面板——都表现为"该列无墨"。本引擎只输出
**实际观测到**的点，缺口另记为 ``meta["gaps"]`` / ``meta["segments"]``，
绝不把"未观测"当作"已观测"补齐（那会把缺口伪装成数据）。

三层流水线
----------
1. **采样**（:func:`column_samples`）—— 逐列取墨段，**意图**是按 EDT 剖面的显著谷
   把交叉处合并成一段的两条笔画拆回各自的中位数。**但自审实测该意图并未实现**
   （见下「自审实测边界」第 1 条：10 404 次调用中触发 0 次，且对细线数学不可达）。
   每段产出一个 ``(x, y, W)`` 样本。
2. **分型**（:func:`width_classes`）—— 对全部样本的 ``W`` 做直方图取峰，
   得到本图的**线型宽度字典**。交叉区的虚高样本是少数，不构成峰。
3. **成轨**（:func:`track_samples` + :func:`merge_tracks`）—— 逐列连续性跟踪
   （宽度类必须匹配 + 切向外推 + 间隙容忍），再把被大缺口切开的同型轨道合并。
   最后由 :func:`classify_dash` 按「单位长度内的段数」判定实线/虚线。

分段判据为什么用"段数密度"
--------------------------
「虚线」与「被图例遮挡的实线」都表现为多段 + 缺口，靠缺口长度区分不了
（遮挡缺口可能比虚线间隙还短）。但两者的**段数密度**差一个数量级：
虚线每约 14 px 一段（≈7 段/100px），遮挡实线全长只有 2 段（≈0.35 段/100px）。
故以 ``段数/百像素`` 为主判据，并要求段数 ≥ :data:`MIN_DASH_SEGMENTS`。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

try:
    from scipy import ndimage as _ndi
except Exception:  # pragma: no cover
    _ndi = None

from .extract import Series, _split_runs

RGB = tuple[int, int, int]

#: 判定"彩色像素"的通道极差阈值（与 ``pdf-legend.md`` §能力边界 同一判据）
COLOR_CHANNEL_SPREAD = 25

#: 虚线的段数下限——低于此只可能是遮挡/噪声造成的偶发断裂
MIN_DASH_SEGMENTS = 4

#: 虚线判据：每 100 像素长度内的段数
MIN_DASH_DENSITY = 1.8

#: 判别"是不是墨"时，与背景色的距离阈值（RGB 欧氏距离）
INK_TOL = 60.0


# ------------------------------------------------------------------ 数据结构


@dataclass
class Sample:
    """一个列内墨段的观测量。"""

    x: int
    y: float          # 段内纵坐标（像素，区域局部坐标）
    w: float          # 笔画宽度估计（像素）= 2·max(EDT)，已做沿 x 的局部取大
    n: int            # 段内墨像素数
    cls: int = -1     # 归属的宽度类下标（-1 表示未分型）
    w_raw: float = 0.0      # 本列自身测得的宽度（未做局部取大）
    censored: bool = False  # 本列宽度偏低、靠邻列抬起来（笔画末端列）

    def as_tuple(self) -> tuple[int, float, float]:
        return (self.x, self.y, self.w)


@dataclass
class Track:
    """一条由若干采样点缝起来的曲线。"""

    samples: list[Sample] = field(default_factory=list)
    cls: int = -1
    dash: str = "unknown"
    gaps: list[tuple[float, float]] = field(default_factory=list)
    segments: list[tuple[int, int]] = field(default_factory=list)
    shared_columns: int = 0
    crossing_columns: int = 0    # 经过"墨团"（宽度显著大于类中心）的列数
    joined_by: str = "trace"     # trace | extrapolation | isolated-pair

    @property
    def n(self) -> int:
        return len(self.samples)

    @property
    def x_first(self) -> float:
        return self.samples[0].x if self.samples else math.nan

    @property
    def x_last(self) -> float:
        return self.samples[-1].x if self.samples else math.nan

    @property
    def w_median(self) -> float:
        if not self.samples:
            return 0.0
        return float(np.median([s.w for s in self.samples]))

    def tail_slope(self, k: int = 9) -> float:
        """末端切向斜率（像素 y / 像素 x）。"""
        s = self.samples[-min(k, len(self.samples)):]
        if len(s) < 2:
            return 0.0
        xs = np.array([p.x for p in s], dtype=float)
        ys = np.array([p.y for p in s], dtype=float)
        if np.ptp(xs) < 1e-9:
            return 0.0
        return float(np.polyfit(xs, ys, 1)[0])

    def head_slope(self, k: int = 9) -> float:
        s = self.samples[:min(k, len(self.samples))]
        if len(s) < 2:
            return 0.0
        xs = np.array([p.x for p in s], dtype=float)
        ys = np.array([p.y for p in s], dtype=float)
        if np.ptp(xs) < 1e-9:
            return 0.0
        return float(np.polyfit(xs, ys, 1)[0])


# ------------------------------------------------------------------ 步骤 0：彩色检测


def color_pixel_count(rgb: np.ndarray) -> int:
    """统计图中的彩色像素数（``max(R,G,B) − min(R,G,B) > 25``）。

    这是「黑白线画能力边界」的**判据本身**（``pdf-legend.md`` §能力边界）：
    为 0 表示零彩色，颜色引擎无轴可用，必须转由本引擎处理；非 0 表示颜色轴
    存在，应交还颜色引擎。
    """
    a = np.asarray(rgb)
    if a.ndim == 2:
        return 0
    mx = a.max(axis=2).astype(np.int16)
    mn = a.min(axis=2).astype(np.int16)
    return int(((mx - mn) > COLOR_CHANNEL_SPREAD).sum())


def has_color(rgb: np.ndarray, min_pixels: int = 50) -> bool:
    """图中是否含足以支撑颜色分离的彩色像素。"""
    return color_pixel_count(rgb) >= min_pixels


# ------------------------------------------------------------------ 步骤 1：二值化与 EDT


def binarize(
    rgb: np.ndarray,
    background: Sequence[int] | None = None,
    ink_tol: float = INK_TOL,
) -> np.ndarray:
    """把区域转成"墨/背景"布尔掩码。

    ``background`` 缺省时取整幅图的**逐通道中位数**——对以白底为主的手稿插图，
    该估计等于白，比"取左上角像素"稳（角上可能是轴框墨）。
    """
    a = np.asarray(rgb, dtype=np.float64)
    if a.ndim == 2:
        a = np.stack([a] * 3, axis=2)
    if background is None:
        bg = np.median(a.reshape(-1, 3), axis=0)
    else:
        bg = np.asarray(background[:3], dtype=np.float64)
    diff = a - bg
    return np.sqrt((diff * diff).sum(axis=2)) > ink_tol


def edt_width_map(ink: np.ndarray) -> np.ndarray:
    """返回 ``2 × EDT``：每个墨像素处的**局部笔画宽度**（单位已是像素）。

    对宽度 w 的笔画，其脊线上像素到最近背景的欧氏距离 ≈ w/2，故乘以 2 还原。
    该量与走向无关（各向同性），这是本引擎宽度估计免疫斜率的根本原因。

    **本函数返回的数值就是宽度本身，调用方不得再乘 2。** 该处曾出过一次
    真实的 ×2 重复计数（``2·EDT`` 又被乘一次 2 → 测得宽度恒为真值的两倍，
    宽度分型因此全部错位）。故此处显式写明契约，并由
    ``tests/test_bwline_units.py::test_width_scale_contract`` 锁死。
    """
    if _ndi is None:
        raise RuntimeError("scipy 未安装：黑白线画分离需要 scipy.ndimage")
    edt = _ndi.distance_transform_edt(np.asarray(ink, dtype=bool))
    return (2.0 * edt).astype(np.float64)


def trim_frame(ink: np.ndarray, margin: int) -> np.ndarray:
    """去掉区域四边的轴框墨。

    数据区矩形是 ``ax.get_window_extent()``，轴脊恰好压在边界上，其**内侧一半**
    落在区域内。默认线宽 1.2 pt ≈ 1.67 px，加上抗锯齿约 2 px，故 ``margin=3``
    足以清掉；代价是丢掉边界 3 px 内的数据，须在交付说明中写明。
    """
    if margin <= 0:
        return ink
    out = ink.copy()
    m = int(margin)
    out[:m, :] = False
    out[-m:, :] = False
    out[:, :m] = False
    out[:, -m:] = False
    return out


# ------------------------------------------------------------------ 步骤 2：逐列采样


def _split_run_at_valleys(
    run: np.ndarray,
    edt_col: np.ndarray,
    w_hint: float,
    valley_depth: float = 0.62,
    peak_prominence: float = 0.45,
) -> list[np.ndarray]:
    """把一个列内连续墨段按 EDT 剖面的**显著谷**切成若干子段。

    交叉处两条笔画合并成一段，此时「整段中位数」落在两笔之间，**谁都不对**。
    但两笔各自的脊线在 EDT 剖面上是峰、峰间有谷；沿谷切开后，每段的中位数
    才是各自曲线的真实纵坐标。

    两个门槛（自审已实测其**实际口径与可满足性**，见下）：

    * ``valley_depth`` —— 谷底必须**跌到** ``0.62 × w_hint/2 = 0.31 · w_hint`` 以下，
      否则只是噪声起伏，不足以说明"这里其实是两条笔画"；
    * ``peak_prominence`` —— 谷两侧的峰要比谷底高出 ``0.45 × w_hint/2``，
      否则（例如一条陡曲线在自身拐点附近）会把单笔画误切成两段。

    **⚠ 单位口径与可满足性（2026-09-17 自审实测，改动本函数前必读）**

    传入的 ``edt_col`` 是 :func:`edt_width_map` 的输出 ``2·EDT``，即**宽度刻度**
    （单笔画的脊线值 ≈ ``w_hint``），**不是**原始 EDT。故上式的真实含义是
    "谷底跌破脊线的 **31%**"，而不是字面读出的 62% —— 两者相差 2 倍。
    该错位是修 `2·EDT` 被再乘 2 那处缺陷时**只改了取值处、未同步门槛处**的遗留。

    更硬的约束：``2·EDT`` 有**绝对下限 2.0**（任何墨像素的 EDT ≥ 1），
    故判据 ``0.31·w_hint > 2.0`` 只在 ``w_hint > 6.45 px`` 时才可能被满足。
    **对常见的 1–3 px 细线，本判据数学上不可满足。**

    实测后果：五个黑白用例共 **10 404 次调用触发 0 次**（``w_hint`` 全在 2.0–4.5 px）。
    因此交叉区的正确性**并非**来自本函数，而是来自「宽度类必须匹配 + 共享认领 +
    切向外推」。真正的失效场景是**浅角交叉**（长合并段），该场景至今无覆盖
    （见 ``separate_bw`` docstring 的「自审实测边界」第 3 条）。

    单元测试 ``test_valley_split`` 直接喂入**原始 EDT 刻度**的一维剖面
    （峰高 3.0 配 ``w_hint=6.0``），与运行时刻度差一倍 —— 其绿灯**不覆盖**真实路径。
    要真正启用本机制，须按宽度刻度重定标并补齐"引擎刻度"下的断言与精度回归，
    属"改动既有判定逻辑"，应单独立项。

    **判据方向必须写对。** ``valley_depth`` 是谷底值的**上界**（越低越像真谷），
    故判定是 ``v > floor → 跳过``。曾写成 ``v < floor → 跳过``，方向恰好反了：
    越是深谷越被拒绝，等价于**整个谷切分功能全程失效**（
    ``test_valley_split`` 的双峰用例暴露：峰高 3.0、谷底 0 的明显双峰被判为 1 段）。
    该缺陷不会让任何图形用例报错——只是交叉处两条笔画回收不到各自的中心，
    误差从"交叉区几列"扩散成"交叉区整段"，属于最难发现的一类。

    返回按原顺序排列的子段索引数组；无显著谷时返回 ``[run]`` 原样。
    """
    n = run.size
    if n < 6 or w_hint <= 0:
        return [run]
    vals = edt_col[run]
    hw = w_hint / 2.0
    floor = valley_depth * hw
    cands: list[tuple[int, float]] = []
    for i in range(1, n - 1):
        v = vals[i]
        if v > floor:
            continue                      # 谷底不够深（相对期望半宽）
        if not (v <= vals[i - 1] and v <= vals[i + 1]):
            continue                      # 只认局部极小值
        left = float(vals[:i].max())
        right = float(vals[i + 1:].max())
        if min(left, right) - v >= peak_prominence * hw:
            cands.append((i, v))
    if not cands:
        return [run]

    # 把**连续**的候选点归成一个簇，每簇只出一刀（取簇内最深者）。
    # 为什么必须这么做：谷底常常是一条**平台**而不是单点——平滑剖面在两条笔画
    # 之间会连续若干像素都是最小值，于是"每个平台像素都是局部极小值"，
    # 都会成为候选。若逐个切分，一个宽谷会被切成十几段碎片。
    # 反例（``test_valley_split`` 实测）：峰高 3.0、谷底为 0 的双峰被切成 **13 段**。
    clusters: list[list[tuple[int, float]]] = []
    for i, v in cands:
        if clusters and i - clusters[-1][-1][0] <= 1:
            clusters[-1].append((i, v))
        else:
            clusters.append([(i, v)])
    reps = [min(c, key=lambda q: (q[1], q[0]))[0] for c in clusters]

    # 代表点之间再要求一个最小间隔（约半个期望笔宽）：两个相邻笔画的间隙
    # 不该比笔宽本身还窄，否则切出来的"子段"不足一个笔画，没有物理意义。
    min_sep = max(3, int(round(hw)))
    cuts: list[int] = []
    for r in reps:
        if not cuts or r - cuts[-1] >= min_sep:
            cuts.append(r)
    if not cuts:
        return [run]
    out: list[np.ndarray] = []
    prev = 0
    for c in cuts:
        if c - prev >= 2:                 # 与上一刀至少隔 2 像素，避免碎片
            out.append(run[prev:c])
            prev = c
    if n - prev >= 2:
        out.append(run[prev:])
    return out or [run]


def column_samples(
    ink: np.ndarray,
    wmap: np.ndarray,
    w_hint: float = 3.0,
    min_pixels: int = 2,
    valley_depth: float = 0.62,
    w_win: int = 2,
) -> list[list[Sample]]:
    """逐列抽取墨段，返回 ``samples[x]``。

    每个墨段产出一个 :class:`Sample`：``y`` 取段内**中位数**（与既有
    ``_column_trace`` 同语义），``w`` 取段内 ``wmap`` 的**最大值**。

    ``wmap`` 必须是由 :func:`edt_width_map` 产出的**宽度图**（已是像素宽度），
    不是原始 EDT——本函数不再做任何 ×2。

    **为什么要沿 x 做 ``±w_win`` 的宽度局部取大**（这是本函数最关键的一步）：

    笔画**末端列**的宽度必然被低估。理由：列内墨段长度为 L 时，段内 EDT 的上界
    是 ``⌈(L+1)/2⌉``——末端列只切到笔画的一小截，EDT 由"段有多长"决定，而不是
    由"笔画有多宽"决定。虚线的每一段有两个末端列，于是**粗虚线的末端列会被测成
    "细线"宽度**。实测后果非常隐蔽：BW-B 的粗虚线（真宽 4.5 px）在每个虚线段的
    首末列给出 2.0 px，被分到**细线类**，于是沿着粗虚线长出了一条 39 点的
    "幽灵细线"轨道（BW-D 同样长出 69 点的幽灵）。它颜色同黑、位置同粗线，
    不与任何真值对应，却会被当作一条独立曲线交付出去。

    修法：笔画宽度是**沿曲线缓变**的量，故取样本在 x 邻域内的最大宽度即可恢复
    末端列的真宽。邻列匹配用 **y 区间重叠**（容差 ``w_hint``），使斜笔画跨列位移
    仍能对上，同时不会误取到远处另一条线。``censored`` 标记"本列宽度被邻列抬起"，
    供后续拒绝"全由末端列拼成的幽灵轨道"。

    ``w_hint`` 只用于谷切分与邻列匹配的相对门槛，给一个中间的估计值即可
    （默认 3.0 px）；它不会成为输出的宽度值——宽度始终由数据本身测出。
    """
    H, W = ink.shape[:2]
    y_tol = max(3, int(round(w_hint)))

    # ---- 第一遍：逐列取墨段，记录 (y0, y1, y_median, w, n)
    per: list[list[tuple[int, int, float, float, int]]] = []
    for x in range(W):
        idx = np.flatnonzero(ink[:, x])
        runs: list[tuple[int, int, float, float, int]] = []
        if idx.size:
            wcol = wmap[:, x]
            for run in _split_runs(idx):
                for sub in _split_run_at_valleys(run, wcol, w_hint,
                                                 valley_depth=valley_depth):
                    if sub.size < min_pixels:
                        continue
                    runs.append((int(sub[0]), int(sub[-1]),
                                 float(np.median(sub)),
                                 float(wcol[sub].max()), int(sub.size)))
        per.append(runs)

    # ---- 第二遍：宽度沿 x 局部取大，修掉笔画末端列的宽度低估
    out: list[list[Sample]] = []
    for x in range(W):
        samples: list[Sample] = []
        for (y0, y1, ym, w, n) in per[x]:
            best = w
            for xa in range(max(0, x - w_win), min(W, x + w_win + 1)):
                if xa == x:
                    continue
                for (b0, b1, _bm, bw, _bn) in per[xa]:
                    if b0 <= y1 + y_tol and y0 <= b1 + y_tol and bw > best:
                        best = bw
            samples.append(Sample(x=int(x), y=ym, w=float(best), n=n,
                                  w_raw=float(w), censored=best > w + 1e-9))
        out.append(samples)
    return out


# ------------------------------------------------------------------ 步骤 3：宽度分型


def width_classes(
    ws: Sequence[float],
    bin_w: float = 0.4,
    min_frac: float = 0.055,
    merge_rel: float = 0.35,
    smooth: Sequence[float] = (1.0, 2.0, 3.0, 2.0, 1.0),
) -> list[float]:
    """对全部样本宽度做直方图取峰，得到本图的**线型宽度字典**。

    为什么用"直方图取峰"而不是"1-D 聚类"：交叉区会把若干样本的宽度抬到两类之间，
    形成一条从细到粗的连续尾巴。排序聚类会被这条尾巴**串联**（细类的上界一路
    接上粗类的下界，两类并成一类），而取峰只认"哪里样本最多"，尾巴再长也不构成峰。

    三个门槛：

    * ``min_frac`` —— 峰高须达最高峰的 5.5%，滤掉噪声小丘；
    * ``merge_rel`` —— 相距不到 35% 的两峰合并（抗锯齿造成的宽度抖动）；
    * 峰位取平滑前直方图在峰邻域内的**加权重心**，避免被 0.4 px 的桶宽量化；
    * 直方图范围比最大值**多出两个桶**，且首末桶也允许成为峰（理由见下）。

    直方图范围这一点不是洁癖：宽度值恰好落在最后一个桶时，会被
    ``range(1, len(h) - 1)`` 的峰判据排除，**主峰当场消失**。反例
    （``test_end_to_end_minimal`` 实测）：真宽 9 px 的笔画被量到 10.0 px，
    恰好落在末桶；主峰因此漏检，类中心被陪衬峰拖到 7.2，而 10.0 相对 7.2
    偏 39%（超过 ``w_tol=0.35``）→ 该类**全部样本被判为"不属于任何类"** →
    整条粗实线消失（4 条只出 3 条）。图形测试报"少一条线"，看不出是漏检峰。
    """
    arr = np.asarray([w for w in ws if w > 0], dtype=float)
    if arr.size == 0:
        return []
    hi = float(arr.max()) + 2.0 * bin_w
    edges = np.arange(0.0, hi + bin_w, bin_w)
    if edges.size < 4:
        return [float(np.median(arr))]
    hist, edges = np.histogram(arr, bins=edges)
    centers = 0.5 * (edges[:-1] + edges[1:])
    k = np.asarray(smooth, dtype=float)
    k = k / k.sum()
    h = np.convolve(hist.astype(float), k, mode="same")
    if h.max() <= 0:
        return [float(np.median(arr))]

    peaks: list[tuple[float, float]] = []      # (峰位, 峰高)
    limit = min_frac * h.max()
    for i in range(len(h)):
        left = h[i - 1] if i > 0 else -np.inf
        right = h[i + 1] if i < len(h) - 1 else -np.inf
        if not (h[i] >= left and h[i] > right and h[i] >= limit):
            continue
        lo = max(0, i - 1)
        hi_i = min(len(hist), i + 2)
        wsum = hist[lo:hi_i].sum()
        if wsum <= 0:
            continue
        pos = float((hist[lo:hi_i] * centers[lo:hi_i]).sum() / wsum)
        peaks.append((pos, float(h[i])))
    if not peaks:
        return [float(np.median(arr))]

    peaks.sort(key=lambda p: p[0])
    merged: list[list[tuple[float, float]]] = [[peaks[0]]]
    for p in peaks[1:]:
        if p[0] <= merged[-1][-1][0] * (1.0 + merge_rel):
            merged[-1].append(p)
        else:
            merged.append([p])
    # 合并后的类中心必须按**峰高加权**，不能取峰位算术平均。
    # 反例（``test_end_to_end_minimal`` 实测）：无抗锯齿的二值图里 EDT 只有整数档，
    # 真宽 9 px 的笔画产生峰 10（780 个样本）、6（70 个）、8（70 个），三者因
    # 相距 < 35% 被并成一组。算术平均给出 8.0，而 10.0 相对 8.0 偏 25%（在
    # ``w_tol=0.35`` 内勉强过关）；若峰位更分散，算术平均会把主峰带偏到
    # ``w_tol`` 之外，导致该宽度类的**全部样本被判为不属于任何类**——
    # 实测后果是整条粗实线消失（4 条只出 3 条）。按峰高加权得 9.54，主峰不被
    # 陪衬峰拖走。
    out_centers: list[float] = []
    for grp in merged:
        tot = sum(w for _p, w in grp)
        out_centers.append(
            float(sum(p * w for p, w in grp) / tot) if tot > 0 else float(grp[0][0])
        )
    return out_centers


def assign_class(w: float, centers: Sequence[float], w_tol: float = 0.35) -> int:
    """把宽度 ``w`` 归到最近的宽度类；超出 ``w_tol`` 相对容差则返回 -1。

    返回 -1 是**有意为之**：交叉区被抬高的样本不该被硬塞进某一类，
    否则它会把该类的宽度估计带偏。
    """
    if not centers:
        return -1
    best_i, best_d = -1, math.inf
    for i, c in enumerate(centers):
        d = abs(w - c) / max(1e-9, c)
        if d < best_d:
            best_d, best_i = d, i
    return best_i if best_d <= w_tol else -1


# ------------------------------------------------------------------ 步骤 4：成轨


def track_samples(
    samples: list[list[Sample]],
    centers: Sequence[float],
    w_tol: float = 0.35,
    gap_patience: int = 16,
    fit_k: int = 9,
    slope_gain: float = 1.6,
    base_tol: float = 3.0,
    min_points: int = 6,
    allow_shared: bool = True,
    share_relax: float = 1.35,
) -> list[Track]:
    """逐列连续性跟踪，把采样点缝成轨道。

    四个机制，各自对应一类失败：

    1. **宽度类必须匹配**（``w_tol``）—— 主分离轴。交叉处合并段的宽度被抬高，
       因此不会被细线轨道误领，细线在交叉区如实留下缺口（而不是被引到粗线上）。
    2. **切向外推**而非水平外推 —— 陡峭段水平外推会直接串到邻线。匹配容差随
       末端斜率线性放宽（``slope_gain``），陡处放宽、平处收紧。
    3. **间隙容忍**（``gap_patience`` 列）—— 虚线间隙不得当作曲线终止；期间
       轨道保持存活并继续外推。
    4. **共享认领**（``allow_shared``）—— 两条同宽曲线交叉时，合并段在几何上
       **真属于双方**；若宽度明显大于类中心（``share_relax`` 倍），允许被两条
       轨道同时认领。

    **反例留档：不要用"末端列比例"作轨道闸门。** 曾实现过一条闸门——"某轨道若
    过半样本是 ``censored``（本列宽度被邻列抬起）则整条丢弃"，意图是拦住沿粗
    虚线端列"跳"出来的幽灵轨道。实测该闸门**误删真曲线**：全部样本中 52% 被
    标为 ``censored``，因为 EDT 只有整数到根号的若干档（2.0 / 2.83 / 3.46 / 4.0…），
    斜笔画相邻两列本就常跨档，于是"被邻列抬起"成了常态而非异常。BW-D 因此
    从正确的 4 条掉到 2 条（细实线与粗虚线被误删）。
    正确做法是在**源头**修：``column_samples`` 沿 x 做宽度局部取大，
    端列低估当场消失，幽灵轨道不再产生。``Sample.censored`` 仅作诊断字段保留。
    """
    if not centers:
        return []
    for x, lst in enumerate(samples):
        for s in lst:
            s.cls = assign_class(s.w, centers, w_tol)

    active: list[dict] = []
    finished: list[Track] = []

    def refresh(tr: dict) -> None:
        sm = tr["samples"]
        k = min(fit_k, len(sm))
        if k >= 2:
            xs = np.array([p.x for p in sm[-k:]], dtype=float)
            ys = np.array([p.y for p in sm[-k:]], dtype=float)
            tr["slope"] = float(np.polyfit(xs, ys, 1)[0]) if np.ptp(xs) > 1e-9 else 0.0
        else:
            tr["slope"] = 0.0
        tr["last_x"] = sm[-1].x

    def to_track(tr: dict) -> Track:
        sm = sorted(tr["samples"], key=lambda s: s.x)
        return Track(samples=sm, cls=int(tr["cls"]),
                     shared_columns=int(tr["shared"]),
                     crossing_columns=int(tr["crossing"]))

    for x in range(len(samples)):
        col = samples[x]
        claimed: dict[int, int] = {}       # 本列内 id(sample) -> 已被认领次数
        if not col:
            # 整列无墨（虚线间隙 / 遮挡区 / 曲线越出面板）：轨道不掉队，只累计 dead。
            # **必须把超时的轨道交给 finished**——此处曾漏掉这一步，导致"被遮挡曲线
            # 的左侧半段"整段消失（BW-E 实测：只剩右半段 217 点，缺口 0 个，
            # 看起来像"这条曲线本来就只有右半段"，是典型的静默数据丢失）。
            # 锁死见 tests/test_bwline_units.py::test_occluded_curve_keeps_both_sides。
            still = []
            for tr in active:
                tr["dead"] += 1
                if tr["dead"] > gap_patience:
                    finished.append(to_track(tr))
                else:
                    still.append(tr)
            active = still
            continue

        for tr in active:
            if tr["dead"] > gap_patience:
                continue
            dx = max(1, x - tr["last_x"])
            pred = tr["samples"][-1].y + tr["slope"] * dx
            tol = max(base_tol, slope_gain * abs(tr["slope"]) * dx + base_tol)
            best, best_d = None, math.inf
            for s in col:
                if s.cls != tr["cls"]:
                    continue
                d = abs(s.y - pred)
                if d < best_d:
                    best, best_d = s, d
            if best is None or best_d > tol:
                tr["dead"] += 1
                continue
            seen = claimed.get(id(best), 0)
            wide = best.w >= share_relax * centers[best.cls]
            if seen > 0 and not (allow_shared and wide):
                tr["dead"] += 1
                continue
            if seen > 0:
                tr["shared"] += 1
            # 记录"本列墨团比本类正常宽度明显更宽"——这是**交叉/重叠**的运行时标志。
            # 它不依赖"被两条轨道同时认领"（后者的成立需要两条轨道都在场），
            # 因而在单条轨道经过交叉区时也能被记下。
            if wide:
                tr["crossing"] += 1
            claimed[id(best)] = seen + 1
            tr["samples"].append(best)
            tr["dead"] = 0
            refresh(tr)

        for s in col:
            if claimed.get(id(s), 0) == 0 and s.cls >= 0:
                active.append({"samples": [s], "dead": 0, "slope": 0.0,
                               "last_x": s.x, "shared": 0, "crossing": 0,
                               "cls": s.cls})

        still: list[dict] = []
        for tr in active:
            if tr["dead"] > gap_patience:
                finished.append(to_track(tr))
            else:
                still.append(tr)
        active = still

    for tr in active:
        finished.append(to_track(tr))

    return [t for t in finished if t.n >= min_points]


def _gap_compatible(
    dy: float,
    gap: float,
    slope_a: float,
    slope_b: float,
    slope_bound: float,
    extend_tol: float,
    chord_margin: float = 0.6,
    chord_cap: float = 6.0,
) -> bool:
    """缺口两侧是否可由**同一条曲线**跨过。两条判据取"或"。

    ``dy`` 是带符号的纵向落差（右端 − 左端），``gap`` 是缺口的 x 长度。

    **判据一（原有）——平均斜率有界**：``|dy| <= slope_bound·gap + extend_tol``。
    对缓变曲线有效，但在**陡峭**处必然失败：自审实测 H5 用例（两条 1.6/1.9 pt
    实线两次交叉）在 x≈350 处断开，缺口仅 33 px 而落差 47.5 px，
    平均斜率 1.44 —— 远超 ``slope_bound=0.60``，于是一条**完好的实线**永远
    接不回去，两条真值曲线各裂成两段（序列数 2 → 4）。

    **判据二（2026-09-17 新增）——弦斜率须落在两端切线之间**：
    对 :math:`C^1` 曲线，缺口上的**弦斜率**是两端导数之间的某个加权平均
    （中值定理），故应有
    ``min(sa, sb) − chord_margin <= dy/gap <= max(sa, sb) + chord_margin``。
    该判据**与曲线的陡缓无关**（陡曲线的弦斜率本来就大），恰好补上判据一的盲区；
    而它比判据一**更紧**的地方在于：还要求左右切向一致（``slope_tol`` 另行把关）。
    ``chord_cap`` 是兜底绝对上限，拦"跨过缺口后跑到别处去了"的荒谬配对。

    ``gap <= 0``（两段在列上相邻甚至共享同一列）时无法定义弦斜率，退化为
    "纵向落差不超过 ``extend_tol``"。原实现对缺口用的是**严格**不等式
    ``0 < gap``，使"恰好在某列处断开"的同一曲线**永远不可能再合并**——
    这是一处真实的判定盲区，一并放行。
    """
    if gap <= 0.0:
        return abs(dy) <= extend_tol
    if abs(dy) <= slope_bound * gap + extend_tol:
        return True
    chord = dy / gap
    if abs(chord) > chord_cap:
        return False
    lo, hi = min(slope_a, slope_b), max(slope_a, slope_b)
    return (lo - chord_margin) <= chord <= (hi + chord_margin)


def merge_tracks(
    tracks: Sequence[Track],
    centers: Sequence[float],
    axis_w: float,
    gap_max: float | None = None,
    slope_bound: float = 0.60,
    extend_tol: float = 6.0,
    slope_tol: float = 2.5,
    min_points: int = 6,
    overlap_tol: float = 5.0,
) -> list[Track]:
    """把「同宽度类 + x 不重叠 + 端点几何相容」的两条轨道合并。

    用途：**遮挡**。缺口长度往往远大于 ``gap_patience``（图例框一挡就是上百像素），
    跟踪阶段只能把一条实线断成两条轨道；本步骤按几何相容性把它们接回去，
    缺口本身照旧记入 ``gaps``，不会被填上。

    接续判据（三条同时满足）：

    * 缺口 ``B.x_first − A.x_last`` 落在 ``[0, gap_max]``（``0`` 表示两段在列上
      相邻，见 :func:`_gap_compatible` 对严格不等式的修正说明）；
    * **切向变化有界**：``|A 末端斜率 − B 首端斜率| ≤ slope_tol``；
    * **落差相容**：由 :func:`_gap_compatible` 判定，取"平均斜率有界"与
      "弦斜率落在两端切线之间（中值定理）"两条判据之**或**。

    **为什么不用"线性外推预测点"作主判据**：长缺口 + 弯曲线时该判据必然失败。
    BW-E 实测：遮挡缺口 162 px，曲线 ``1.35·sin(0.8x)`` 在缺口左端斜率为 −1.32
    px/px，线性外推预测点距右端实际位置逾 200 px——于是"同一条被遮挡的实线"
    永远接不回去。改用**平均斜率有界**：缺口两端的纵向落差 69 px 除以缺口长度
    162 px 得平均斜率 0.43，远在界内。线性外推是"逐点可导"的强假设，而平均
    斜率有界只要求"整体上没跑偏"，后者才是长缺口下可用的信息。

    代价是判据更宽，故必须与 ``slope_tol`` 联合使用，且**只在同宽度类之间**
    考虑——主分离轴仍由宽度类把守。
    """
    if gap_max is None:
        gap_max = max(40.0, 0.35 * axis_w)
    pool = sorted(tracks, key=lambda t: t.x_first)
    changed = True
    while changed:
        changed = False
        out: list[Track] = []
        used = [False] * len(pool)
        for i, a in enumerate(pool):
            if used[i]:
                continue
            cur = a
            used[i] = True
            progressed = True
            while progressed:
                progressed = False
                for j, b in enumerate(pool):
                    if used[j] or b is cur:
                        continue
                    if b.cls != cur.cls:
                        # 跨宽度类只在"其中一条是碎片"时放行（理由同 dedupe_tracks：
                        # 相切处细线的一段会被粗线墨团"传染"成粗宽度而被分进另一类，
                        # 但它在几何上属于细线）。两条**同量级**的异宽曲线不得跨类合并
                        # ——主分离轴仍由宽度类把守。
                        na, nb = cur.n, b.n
                        if min(na, nb) > 0.5 * max(na, nb):
                            continue
                    gap = b.x_first - cur.x_last
                    # 允许**小幅负缺口**：两条轨道在 x 上重叠几列是"被切开的同一
                    # 曲线"的常态（自审实测 H2：细线碎片 x=[56,90] 与细线主轨道
                    # x=[87,554] 重叠 3 列，gap=−3，被 `gap >= 0` 一票否决，
                    # 于是碎片永远接不回去）。真正的"两条不同曲线在 x 上并存"
                    # 重叠量在数百列量级（实测 H5 为 −342），与 5 px 差两个数量级，
                    # 不会被这块容差放进。
                    if not (-overlap_tol <= gap <= gap_max):
                        continue
                    sa = cur.tail_slope()
                    sb = b.head_slope()
                    if abs(sa - sb) > slope_tol:
                        continue
                    dy_signed = float(b.samples[0].y - cur.samples[-1].y)
                    if not _gap_compatible(dy_signed, float(gap), sa, sb,
                                           slope_bound, extend_tol):
                        continue
                    # 合并后的宽度类取**较长**那条的类：跨类合并只可能发生在
                    # "碎片 + 主轨道"之间，主轨道（较长）的类才是该曲线的真实宽度类。
                    cur = Track(
                        samples=cur.samples + b.samples,
                        cls=cur.cls if cur.n >= b.n else b.cls,
                        shared_columns=cur.shared_columns + b.shared_columns,
                        crossing_columns=cur.crossing_columns + b.crossing_columns,
                        joined_by="extrapolation",
                    )
                    used[j] = True
                    progressed = True
                    changed = True
            out.append(cur)
        pool = out
    out2 = [t for t in pool if t.n >= min_points]
    return _bridge_isolated_pairs(out2, centers, gap_max,
                                  slope_bound=2.0 * slope_bound,
                                  extend_tol=extend_tol, min_points=min_points)


def _bridge_isolated_pairs(
    tracks: Sequence[Track],
    centers: Sequence[float],
    gap_max: float,
    slope_bound: float,
    extend_tol: float,
    min_points: int,
) -> list[Track]:
    """第二档合并：**孤立碎片配对**（弱判据，必须留痕）。

    为什么需要这一档：**长缺口 + 弯曲线**时"几何外推"必然失效。BW-E 实测：
    遮挡缺口 162 px，曲线 ``1.35·sin(0.8x)`` 在缺口两端落差 157 px，平均斜率
    0.96 px/px——第一档的 ``slope_bound=0.60`` 直接否掉，于是"同一条被遮挡的
    实线"永远接不回去。

    这一档改用**计数论证**（也正是人眼的做法）：某个宽度类当前只剩**两条**
    轨道、二者的 x 区间**不相交**、缺口不超过 ``gap_max``，则它们**很可能是**
    同一条曲线被遮挡切开的两半。

    **该论证不是充分条件（自审修正）**：原文写作"必然是……除非该类本来就有两条
    同宽曲线，而那种情形两条曲线会在 x 上并存"。这一附加前提**不成立**：
    * 同一个宽度类里出现两条**在 x 上先后**出现的同宽曲线是常见的（分区间绘制的
      两条同线宽序列、被裁图截断的曲线、同宽度但分段绘制的实验/理论曲线）；
    * 若某条曲线被遮挡切成 3 段以上，本档因"不是恰好两条"而整组跳过；
    * 若两条轨道的 x 区间**交错**（而非顺序相接），``gap > 0`` 的前提也不成立。

    故本档的正确表述是「**一个启发式假设**，须由 ``joined_by="isolated-pair"``
    留痕供人工复核」，而非逻辑推论。交付物中凡带该标记的接续都应当被复核。

    ``slope_bound`` 放宽一倍（默认到 1.2）作为兜底闸门：它只拦"跨越缺口后
    跑到别处去了"的荒谬配对，不要求逐点可导。

    **留痕**：合并结果标记 ``joined_by="isolated-pair"``，交付物里可查。
    强判据（``extrapolation``）与弱判据必须可区分——否则人工无从判断哪些
    接续值得复核。这是本档唯一的存在理由，也是它必须被记录的理由。
    """
    if not tracks:
        return []
    by_cls: dict[int, list[Track]] = {}
    for t in tracks:
        by_cls.setdefault(t.cls, []).append(t)

    merged: list[Track] = []
    for cls, group in by_cls.items():
        if len(group) != 2:
            merged.extend(group)          # 不是"恰好两条"，不敢用计数论证
            continue
        a, b = sorted(group, key=lambda t: t.x_first)
        gap = b.x_first - a.x_last
        # 缺口须**非负**（负值意味着两条轨道在 x 上交错，计数论证不适用）。
        # 允许 gap == 0：两段在列上相邻，同属一条曲线的可能性不变。
        ok = (0.0 <= gap <= gap_max)
        if ok:
            dy = float(b.samples[0].y - a.samples[-1].y)
            ok = _gap_compatible(dy, float(gap), a.tail_slope(), b.head_slope(),
                                 slope_bound, extend_tol)
        if not ok:
            merged.extend(group)
            continue
        joined = Track(
            samples=a.samples + b.samples,
            cls=cls,
            shared_columns=a.shared_columns + b.shared_columns,
            crossing_columns=a.crossing_columns + b.crossing_columns,
            joined_by="isolated-pair",
        )
        if joined.n >= min_points:
            merged.append(joined)
        else:                             # 合并后反而低于门槛：退回两条
            merged.extend(group)
    return merged


def dedupe_tracks(
    tracks: Sequence[Track],
    tol_px: float = 4.0,
    min_overlap: float = 0.6,
) -> list[Track]:
    """剔除「同一条曲线被拆成多条轨道」造成的重复。

    与颜色引擎的 :func:`chartdigit.extract.dedupe_series` 同一思想：按点数降序
    保留，若候选轨道有 ``min_overlap`` 以上比例的点落在已保留轨道的 ``tol_px``
    之内，即判为重复。

    为什么需要它：跟踪阶段会因"轨道瞬时死亡—重生"而**并行**出两条追同一条
    曲线的轨道（BW-B 实测：细虚线被拆成 357 + 39 + 31 三段，后两段的点与
    主轨道逐点重合）。它们不是两条曲线，而是同一曲线的两份副本；不去掉会让
    "检出序列数"虚高，下游无法判断该信谁。

    比较方式：把**已保留轨道**的 ``y(x)`` 线性插值到**候选轨道**的每个 x 上求差。
    插值会跨过缺口，因此对遮挡区两侧也能比较——这是有意的：遮挡不改变曲线的
    身份。仅当两条轨道 x 区间无交集时才会漏判（此时它们本就不是重复）。

    **跨宽度类的判重（2026-09-17 自审后新增）**：宽度类不同时，仅当候选轨道是
    **碎片**（``t.n <= 0.5 * k.n``）才比较。原因见 ``separate_bw`` docstring
    「自审实测边界」第 3 条：浅角相切处，细线的一段会被粗线墨团"传染"成粗宽度，
    从而**分进另一个宽度类**，按"同类才比较"永远删不掉。而两条**同量级**、
    宽度类不同的曲线则可能是真正的异宽曲线，不得凭 4 px 邻域判重——
    故用不对称条件（碎片）把该风险排除。
    """
    if len(tracks) <= 1:
        return list(tracks)

    def xy(t: Track) -> tuple[np.ndarray, np.ndarray]:
        s = sorted(t.samples, key=lambda q: q.x)
        xs = np.array([q.x for q in s], dtype=float)
        ys = np.array([q.y for q in s], dtype=float)
        ux, idx = np.unique(xs, return_index=True)
        return ux, ys[idx]

    kept: list[Track] = []
    for t in sorted(tracks, key=lambda q: -q.n):
        tx, ty = xy(t)
        dup = False
        for k in kept:
            # 宽度类不同也允许判重，但**只在候选是"碎片"时**（点数不足对照轨道的
            # 一半）。理由（自审实测 H2）：浅角相切处细线的一段与粗线墨团相连，
            # 于是**被测成粗线宽度**并分进粗线类，长出一条几十点的碎轨道；此时
            # 按"同类才比较"就永远删不掉它（它天然不属于细线类）。
            # 而**同量级**的两条曲线若宽度类不同，则可能是真正的两条异宽曲线，
            # 不得凭 4 px 邻域判重——故用"碎片"这个不对称条件把它排除在外。
            if k.cls != t.cls and t.n > 0.5 * k.n:
                continue
            kx, ky = xy(k)
            if tx.size < 2 or kx.size < 2:
                continue
            lo, hi = max(tx[0], kx[0]), min(tx[-1], kx[-1])
            if hi <= lo:
                continue
            mask = (tx >= lo) & (tx <= hi)
            if mask.sum() < 2:
                continue
            d = np.abs(ty[mask] - np.interp(tx[mask], kx, ky))
            if float((d < tol_px).mean()) >= min_overlap:
                dup = True
                break
        if not dup:
            kept.append(t)
    return kept


# ------------------------------------------------------------------ 步骤 5：划线模式


def segment_intervals(track: Track, min_gap_cols: int = 3) -> list[tuple[int, int]]:
    """把轨道的采样点按 x 上的**连续段**归并成区间列表。

    ``min_gap_cols=3`` 表示「相邻采样点 x 差 ≥ 3（即**连续缺 2 列**）才算断开」。
    这个数字不是随手取的：设为 2 会把**单列缺失**也当成断开，而单列缺失在
    交叉区（合并段被归属给另一条曲线）、抗锯齿抖动处频繁发生——实测 BW-C 的
    实线因此被切成 34 段，段数密度 6.0 段/100px，**越过虚线判据，实线被误判为虚线**。
    缺 1 列与缺 10 列是本质不同的两件事，阈值必须把它们分开。
    """
    if not track.samples:
        return []
    xs = [s.x for s in sorted(track.samples, key=lambda s: s.x)]
    segs: list[tuple[int, int]] = []
    start = prev = xs[0]
    for x in xs[1:]:
        if x - prev >= min_gap_cols:
            segs.append((start, prev))
            start = x
        prev = x
    segs.append((start, prev))
    return segs


def classify_dash(track: Track, min_gap_cols: int = 3) -> str:
    """按「单位长度内的段数」判定 ``solid`` / ``dashed`` / ``unknown``。

    这是唯一能同时把「虚线」与「被遮挡的实线」分开的判据：两者都表现为
    多段 + 缺口，缺口长度还可能反过来（遮挡缺口比虚线间隙更短），
    但段数密度差一个数量级。阈值见 :data:`MIN_DASH_SEGMENTS` /
    :data:`MIN_DASH_DENSITY`。

    轨道过短（span < 40 px）时返回 ``unknown``——样本不足以支撑密度统计。
    """
    segs = segment_intervals(track, min_gap_cols=min_gap_cols)
    if not segs:
        return "unknown"
    span = segs[-1][1] - segs[0][0]
    if span < 40:
        return "unknown"
    density = len(segs) / (span / 100.0)
    if len(segs) >= MIN_DASH_SEGMENTS and density >= MIN_DASH_DENSITY:
        return "dashed"
    return "solid"


def track_gaps(track: Track, min_gap_cols: int = 4) -> list[tuple[float, float]]:
    """轨道的缺口列表（相邻段的 ``(左段末列, 右段首列)`` 像素 x）。

    ``min_gap_cols`` 比 :func:`segment_intervals` 更严一档，使抗锯齿级别的
    单列缺失不进缺口表——缺口表是给人看的，只保留**显著**缺口。
    """
    segs = segment_intervals(track, min_gap_cols=min_gap_cols)
    return [(float(a[1]), float(b[0])) for a, b in zip(segs, segs[1:])]


# ------------------------------------------------------------------ 对外主入口


def separate_bw(
    rgb: np.ndarray,
    region: tuple[int, int, int, int],
    background: Sequence[int] | None = None,
    ink_tol: float = INK_TOL,
    frame_trim_px: int = 3,
    w_tol: float = 0.35,
    gap_patience: int = 16,
    min_points: int = 20,
    allow_color: bool = False,
    min_color_pixels: int = 50,
) -> list[Series]:
    """在 ``region`` 内按「线宽 × 划线」分离黑白曲线，返回 ``Series`` 列表。

    ``region`` 为整图像素矩形 ``(left, top, right, bottom)``，与
    :func:`chartdigit.extract.extract_series` 同语义，故两者可互换调用。

    **彩色守卫**：若区域内彩色像素 ≥ ``min_color_pixels`` 且 ``allow_color=False``，
    直接返回空表——此时颜色才是正确的分离轴，黑白引擎硬上只会与颜色引擎
    给出两套互相打架的答案。调用方可用 :func:`has_color` 先行判断。

    输出现约定（相对颜色引擎的差异，必须让下游知道）：

    * ``Series.kind`` 一律为 ``"line"``（保持下游 ``deliver`` 兼容）；
    * ``Series.color`` 一律为 ``(0,0,0)``——黑白图本就没有颜色信息，**不得**伪造；
    * ``Series.meta`` 携带 ``engine="bwline"``、``width_px``、``dash``、
      ``segments``、``gaps``、``n_gaps``、``track_id``，使"这条线凭什么被单独分出"
      成为可追溯的事实。
    """
    if _ndi is None:
        raise RuntimeError("scipy 未安装：黑白线画分离需要 scipy.ndimage")

    left, top, right, bottom = (int(v) for v in region)
    H, W = rgb.shape[:2]
    left = max(0, min(left, W - 1))
    right = max(left + 1, min(right, W))
    top = max(0, min(top, H - 1))
    bottom = max(top + 1, min(bottom, H))
    sub = rgb[top:bottom, left:right]
    if sub.size == 0:
        return []

    if not allow_color and has_color(sub, min_color_pixels):
        return []

    ink = binarize(sub, background=background, ink_tol=ink_tol)
    ink = trim_frame(ink, frame_trim_px)
    if not ink.any():
        return []
    wmap = edt_width_map(ink)

    # 谷切分需要一个"中间"的宽度提示：用中位数近似，再迭代一次收紧
    w_hint = float(np.median(wmap[ink]))
    if w_hint <= 0:
        w_hint = 3.0
    samples = column_samples(ink, wmap, w_hint=w_hint)
    ws = [s.w for col in samples for s in col]
    centers = width_classes(ws)
    if not centers:
        return []

    # 用分型结果反过来修正谷切分门槛，再采一次（一次迭代即收敛）
    refined = float(np.median(centers))
    if abs(refined - w_hint) > 0.5:
        samples = column_samples(ink, wmap, w_hint=refined)
        ws = [s.w for col in samples for s in col]
        centers = width_classes(ws) or centers

    tracks = track_samples(samples, centers, w_tol=w_tol,
                           gap_patience=gap_patience, min_points=min_points)
    # 先去重（同一曲线的并行副本）再合并（跨大缺口的同一条曲线），最后再去一次：
    # 合并可能把"原本看不出来的副本"接成一条长轨道，需要再清一遍。
    tracks = dedupe_tracks(tracks)
    tracks = merge_tracks(tracks, centers, axis_w=float(right - left),
                          min_points=min_points)
    tracks = dedupe_tracks(tracks)
    for t in tracks:
        t.dash = classify_dash(t)
        t.gaps = track_gaps(t)

    # 「同类的两条轨道在 x 上重叠」= 宽度轴分不开它们（只能靠 y 连续跟踪）。
    # 这是身份互换风险的**结构性来源**，逐条统计供下游判断该信几分。
    overlap_n: dict[int, int] = {}
    for i2, a2 in enumerate(tracks):
        for j2, b2 in enumerate(tracks):
            if i2 >= j2 or a2.cls != b2.cls:
                continue
            lo2, hi2 = max(a2.x_first, b2.x_first), min(a2.x_last, b2.x_last)
            if hi2 <= lo2:
                continue
            short = min(a2.x_last - a2.x_first, b2.x_last - b2.x_first)
            if short > 0 and (hi2 - lo2) / short >= 0.3:
                overlap_n[i2] = overlap_n.get(i2, 0) + 1
                overlap_n[j2] = overlap_n.get(j2, 0) + 1

    order = sorted(range(len(tracks)),
                   key=lambda i: (centers[tracks[i].cls] if tracks[i].cls >= 0 else 0.0,
                                  -tracks[i].samples[0].y))
    seq: dict[tuple[int, str], int] = {}
    out: list[Series] = []
    for rank, i in enumerate(order, start=1):
        t = tracks[i]
        w = centers[t.cls] if t.cls >= 0 else t.w_median
        _sm = t.samples
        _steps = [abs(b.y - a.y) for a, b in zip(_sm, _sm[1:]) if b.x - a.x <= 3]
        max_step = max(_steps) if _steps else 0.0
        key = (t.cls, t.dash)
        seq[key] = seq.get(key, 0) + 1
        name = f"bw_w{w:.1f}_{t.dash}"
        if seq[key] > 1:
            name = f"{name}#{seq[key]}"
        pts = [(float(s.x + left), float(s.y + top)) for s in t.samples]
        segs = segment_intervals(t)
        # 「同宽度类的其他序列是否在 x 上与本条大量重叠」——若重叠，说明宽度轴
        # 无法把二者分开，只能靠 y 连续跟踪；此时**交叉处身份可能互换**。
        n_overlap = overlap_n.get(i, 0)
        warn = (t.crossing_columns > 0) or (n_overlap > 0) or (t.joined_by != "trace")
        out.append(Series(
            name=name,
            kind="line",
            color=(0, 0, 0),
            points=pts,
            meta={
                "engine": "bwline",
                "track_id": rank,
                "width_px": round(float(w), 3),
                "width_class_index": int(t.cls),
                "dash": t.dash,
                "segments": [[int(a), int(b)] for a, b in segs],
                "n_segments": len(segs),
                "gaps": [[float(a), float(b)] for a, b in t.gaps],
                "n_gaps": len(t.gaps),
                "shared_columns": int(t.shared_columns),
                "crossing_columns": int(t.crossing_columns),
                "same_class_overlapping_series": int(n_overlap),
                "joined_by": t.joined_by,
                "censored_columns": sum(1 for s in t.samples if s.censored),
                "max_step_px": round(max_step, 3),
                "identity_warning": bool(warn),
                "note": "黑白线画引擎；color 恒为 (0,0,0)，缺口不插值"
                        + ("；⚠ 本序列经过交叉/重叠区或由非 trace 接续拼成，"
                           "可能与同类序列在交叉处互换身份，交付前请人工核对"
                           if warn else ""),
            },
        ))
    return out


def stroke_dictionary(
    rgb: np.ndarray,
    region: tuple[int, int, int, int],
    background: Sequence[int] | None = None,
    **kw,
) -> dict:
    """给出该图的**线型字典**与能力判定，不产出数据点。

    面向人的诊断入口：回答"这张图里有几种线型、各多少条、引擎能不能处理"。
    返回 ``has_color`` / ``color_pixels`` / ``ink_pixels`` / ``width_classes`` /
    ``entries``（每条线型一条记录：宽度、划线、轨道数、点数、显著缺口数）。
    """
    left, top, right, bottom = (int(v) for v in region)
    H, W = rgb.shape[:2]
    sub = rgb[max(0, top):min(H, bottom), max(0, left):min(W, right)]
    cpx = color_pixel_count(sub)

    # 参数透传：``min_color_pixels`` / ``ink_tol`` 必须与 ``separate_bw`` 用**同一套**
    # 值，否则同一个参数下两个入口会给出互相矛盾的结论（自审实测：调用方把
    # ``min_color_pixels`` 调大后，``separate_bw`` 不再让位（返回 1 条），
    # 而本函数硬编码 50 仍报 `has_color=True` / `verdict="已让位"`）。
    min_color_px = int(kw.get("min_color_pixels", 50))
    ink_tol = float(kw.get("ink_tol", INK_TOL))

    ink = binarize(sub, background=background, ink_tol=ink_tol)
    ink_trimmed = trim_frame(ink, int(kw.get("frame_trim_px", 3)))
    series = separate_bw(rgb, region, background=background, **kw)

    entries: list[dict] = []
    for s in series:
        m = s.meta
        entries.append({
            "name": s.name,
            "width_px": m["width_px"],
            "dash": m["dash"],
            "n_points": len(s.points),
            "n_segments": m["n_segments"],
            "n_gaps": m["n_gaps"],
            "shared_columns": m["shared_columns"],
            "crossing_columns": m.get("crossing_columns", 0),
            "same_class_overlapping_series": m.get("same_class_overlapping_series", 0),
            "joined_by": m["joined_by"],
            "max_step_px": m.get("max_step_px", 0.0),
            "identity_warning": m.get("identity_warning", False),
        })
    centers = sorted({round(e["width_px"], 3) for e in entries})
    n_warn = sum(1 for e in entries if e["identity_warning"])
    return {
        "has_color": cpx >= min_color_px,
        "color_pixels": int(cpx),
        "ink_pixels": int(ink_trimmed.sum()),
        "n_series": len(series),
        "n_identity_warning": n_warn,
        "width_classes": centers,
        "entries": entries,
        "verdict": (
            "含彩色像素：应由颜色引擎处理，黑白引擎已让位"
            if cpx >= 50 else
            "零彩色：黑白线画引擎适用"
        ),
    }
