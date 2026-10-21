"""黑白线画分离引擎的**机制类**确定性单元测试。

为什么机制类缺陷必须用单元测试锁定
----------------------------------
图形测试（``verify_bw_accuracy.py``）只能告诉你"这张图结果不对"，说不出**机制**
哪里错了。例如"虚线末端列的宽度被低估"这个缺陷，在两个用例上恰好没触发、在第三个
用例上触发了——修的时候无据可依、修完也无从确认没修坏。机制类缺陷必须用
**最小合成输入**把判据本身钉死：输入是构造出来的，期望值是可以手算的。

本文件锁定的是引擎赖以成立的那几条**判据**，不是某张图的结果：

========================================  ==========================================
 测试                                      锁定的机制 / 曾经踩过的坑
========================================  ==========================================
 ``test_width_scale_contract``              ``2·EDT`` 不得被再乘一次 2（真实踩过）
 ``test_width_slope_invariance``            EDT 测宽免疫斜率；列内段长测宽不免疫
 ``test_valley_split_*``                    交叉合并段按 EDT 谷切回两条笔画
 ``test_dash_end_width_not_deflated``       虚线末端列不得被误测为细线（幽灵轨道根因）
 ``test_width_classes_no_chaining``         直方图取峰不得被"交叉尾巴"串联成一类
 ``test_segment_single_missing_col``        缺 1 列不算断开（否则实线被误判虚线）
 ``test_dash_vs_occlusion``                 遮挡实线不得被判为虚线；虚线不得被判实线
 ``test_occluded_curve_keeps_both_sides``   整列无墨时轨道必须交给 finished（静默丢失）
 ``test_gap_not_interpolated``              缺口不得插值
 ``test_colored_input_defers``              彩色图必须让位颜色引擎
 ``test_dedupe_keeps_parallel_curves``      去重不得吞掉真正的平行曲线
========================================  ==========================================
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from chartdigit.bwline import (  # noqa: E402
    COLOR_CHANNEL_SPREAD,
    MIN_DASH_DENSITY,
    Sample,
    Track,
    _split_run_at_valleys,
    binarize,
    classify_dash,
    color_pixel_count,
    column_samples,
    dedupe_tracks,
    edt_width_map,
    segment_intervals,
    separate_bw,
    width_classes,
)


# ------------------------------------------------------------------ 断言收集


class Checker:
    """轻量断言收集器，与其余阶段测试文件同风格（打印 [OK]/[FAIL] 标签）。"""

    def __init__(self) -> None:
        self.n = 0
        self.fail = 0

    def ok(self, cond: bool, label: str, detail: str = "") -> None:
        self.n += 1
        if cond:
            print(f"[OK]   {label}" + (f"  {detail}" if detail else ""))
        else:
            self.fail += 1
            print(f"[FAIL] {label}" + (f"  {detail}" if detail else ""))


# ------------------------------------------------------------------ 合成图工具


def stroke_img(h: int, w: int, p0, p1, width: float) -> np.ndarray:
    """把一条定宽线段画进布尔图。

    判据是**点到线段的垂距 ≤ width/2**，因此笔画宽度是精确的、与斜率无关的，
    不含任何抗锯齿。这正是"测宽是否免疫斜率"所要求的干净输入。
    """
    yy, xx = np.mgrid[0:h, 0:w]
    ax, ay = p0
    bx, by = p1
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    t = ((xx - ax) * vx + (yy - ay) * vy) / L2
    t = np.clip(t, 0.0, 1.0)
    px = ax + t * vx
    py = ay + t * vy
    return np.hypot(xx - px, yy - py) <= width / 2.0


def run_length_width(ink: np.ndarray) -> float:
    """**天真**的测宽法：列内连续段长度的中位数。

    这是被否掉的那条路，本文件把它实现出来作为**对照**——没有对照，
    "EDT 免疫斜率"就只是一句断言。
    """
    lens = []
    for x in range(ink.shape[1]):
        idx = np.flatnonzero(ink[:, x])
        if idx.size == 0:
            continue
        brk = np.flatnonzero(np.diff(idx) > 1)
        starts = np.concatenate(([0], brk + 1))
        ends = np.concatenate((brk + 1, [idx.size]))
        lens.append(max(e - s for s, e in zip(starts, ends)))
    return float(np.median(lens)) if lens else float("nan")


def edt_width(ink: np.ndarray, trim: int = 6) -> float:
    """引擎口径的测宽：逐列段内 ``2·max(EDT)`` 的中位数（去掉两端各 ``trim`` 列）。"""
    wmap = edt_width_map(ink)
    vals = []
    for x in range(trim, ink.shape[1] - trim):
        idx = np.flatnonzero(ink[:, x])
        if idx.size == 0:
            continue
        vals.append(float(wmap[idx, x].max()))
    return float(np.median(vals)) if vals else float("nan")


# ------------------------------------------------------------------ 1. 刻度契约


def test_width_scale_contract(chk: Checker) -> None:
    """``edt_width_map`` 返回的就是宽度，调用方不得再乘 2。

    真实踩过的坑：``edt_width_map`` 已返回 ``2·EDT``，而 ``column_samples``
    又乘了一次 2，测得宽度恒为真值的两倍 → 全部宽度分型错位（真值 1.39/3.61 px
    的图被分成 4.2/5.8/9.0 三类）。本测试用一条已知宽度的笔画把该契约钉死。

    容差说明：EDT 测宽对**奇数宽**的栅格带有一个**固有的 +1 偏置**。宽度 w 的
    带栅格化成 ``2⌊w/2⌋+1`` 个像素后，带心到最近背景的欧氏距离是
    ``(band+1)/2``，故 ``2·EDT = band+1``。因此 4 px 笔画测得 6.0、9 px 测得
    10.0 都是**正确**的，不是翻倍。判据因此用"不超过真值的 1.6 倍"——
    ×2 缺陷会让 4 变成 8（= 2.0 倍），越界；正常偏置 6.0（= 1.5 倍）不越界。
    """
    print("\n--- test_width_scale_contract：宽度刻度契约（防 ×2 重复计数）")
    for true_w in (4.0, 9.0):
        ink = stroke_img(160, 320, (40, 80), (280, 80), true_w)
        got = edt_width(ink)
        chk.ok(0.8 * true_w <= got <= 1.6 * true_w,
               f"真宽 {true_w:.0f}px 的实测宽度在 [0.8, 1.6] 倍带内（不翻倍）",
               f"实测 {got:.2f}px（×2 缺陷会给出 {2*true_w:.0f}px）")
    ink = stroke_img(160, 320, (40, 80), (280, 80), 4.0)
    got = edt_width(ink)
    chk.ok(got < 7.0, "4px 笔画不得被测成 8px（×2 重复计数的判据）",
           f"实测 {got:.2f}px")


# ------------------------------------------------------------------ 2. 斜率免疫


def test_width_slope_invariance(chk: Checker) -> None:
    """宽度估计必须免疫斜率——引擎成立的第一原理。

    对照实验：同一条 5 px 宽的笔画，从 0° 转到 85°。
    * 列内段长法（天真做法）测得宽度按 ``1/sin θ`` 膨胀，跨度可达数倍；
    * EDT 法（引擎做法）测得宽度基本不变。
    没有这个对照，"免疫斜率"就只是口号。
    """
    print("\n--- test_width_slope_invariance：测宽免疫斜率（含天真法对照）")
    H = W = 400
    cx, cy = 200.0, 200.0
    R = 150.0
    true_w = 5.0
    edt_vals, rl_vals = [], []
    for deg in (0, 15, 30, 45, 60, 75, 85):
        th = np.deg2rad(deg)
        dx, dy = np.cos(th) * R, np.sin(th) * R
        ink = stroke_img(H, W, (cx - dx, cy - dy), (cx + dx, cy + dy), true_w)
        edt_vals.append(edt_width(ink, trim=30))
        rl_vals.append(run_length_width(ink))
    e = np.asarray(edt_vals)
    r = np.asarray(rl_vals)
    print(f"       EDT 法: " + " ".join(f"{v:.2f}" for v in e))
    print(f"       段长法: " + " ".join(f"{v:.2f}" for v in r))

    chk.ok(e.max() / e.min() <= 1.6,
           "EDT 测宽在 0°–85° 间的跨度 ≤ 1.6 倍",
           f"最大/最小={e.max()/e.min():.2f}（{e.min():.2f}–{e.max():.2f}px）")
    chk.ok(abs(float(np.median(e)) - true_w) <= 1.5,
           "EDT 测宽中位数接近真值",
           f"median={np.median(e):.2f}px 真值={true_w}")
    chk.ok(r.max() / r.min() >= 3.0,
           "对照：段长法跨度 ≥ 3 倍（证明该缺陷真实存在，非杜撰）",
           f"最大/最小={r.max()/r.min():.2f}（{r.min():.2f}–{r.max():.2f}px）")
    # 75° 与 85° 处段长法必须显著膨胀，否则对照无效
    chk.ok(r[-1] > 2.0 * true_w,
           "对照：85° 处段长法膨胀 > 2 倍真值",
           f"{r[-1]:.2f}px vs 真值 {true_w}")


# ------------------------------------------------------------------ 3. 谷切分


def test_valley_split(chk: Checker) -> None:
    """交叉合并段必须按 EDT 剖面的显著谷切回两条笔画。

    直接对函数做一维机制测试——这样峰/谷位置与期望分割点是可手算的，
    比"画一张交叉图看结果对不对"可靠得多。
    """
    print("\n--- test_valley_split：按 EDT 谷切分合并段")
    n = 80
    idx = np.arange(n)

    def prof2(p1, p2, valley: float) -> np.ndarray:
        """两峰 + 指定谷底高度的剖面（谷外为 0）。"""
        p = np.zeros(n)
        p[14:20] = np.array([1.0, 2.0, 3.0, 3.0, 2.0, 1.0])
        p[44:50] = np.array([1.0, 2.0, 3.0, 3.0, 2.0, 1.0])
        p[p1:p2] = valley
        return p

    w_hint = 6.0                       # 期望宽度 6px → 半宽 3px，与峰高一致
    # 深谷（谷底 0）→ 应切为 2 段
    prof = prof2(20, 44, 0.0)
    subs = _split_run_at_valleys(idx, prof, w_hint)
    chk.ok(len(subs) == 2, "双峰（峰间深谷）切为 2 段",
           f"实际 {len(subs)} 段，分割点 {[int(s[0]) for s in subs]}")

    # 平台型深谷（谷底连续多像素取同一最小值）**只出一刀**，不得切成十几段
    chk.ok(len(subs) <= 2, "平台型谷底只归为一簇（不得每个平台像素都切）",
           f"实际 {len(subs)} 段")

    # 负例 1：单峰（一条笔画）不得被切开
    single = np.zeros(n)
    single[36:44] = np.array([1.0, 2.0, 3.0, 3.0, 3.0, 2.0, 1.0, 0.5])
    subs1 = _split_run_at_valleys(idx, single, w_hint)
    chk.ok(len(subs1) == 1, "单峰不得被误切（否则一条笔画变两条）",
           f"实际 {len(subs1)} 段")

    # 负例 2：谷底未跌破门槛（0.62×半宽 = 1.86）→ 不得切分。
    # 注意剖面必须**整条都高于门槛**——若谷外留 0 基线，峰与基线之间又会形成
    # 真正跌破门槛的"肩谷"，那是应当切分的，会把本负例变成假失败
    # （本测试第一版就写错成 prof2(..., 2.6)，因肩谷被切成 3 段而误报）。
    shallow = np.full(n, 2.6)
    shallow[14:20] = np.array([2.6, 2.7, 3.0, 3.0, 2.7, 2.6])
    shallow[44:50] = np.array([2.6, 2.7, 3.0, 3.0, 2.7, 2.6])
    subs2 = _split_run_at_valleys(idx, shallow, w_hint)
    chk.ok(len(subs2) == 1, "浅谷不得切分（谷底未跌破门槛）",
           f"实际 {len(subs2)} 段（谷底 2.6 > 门槛 {0.62 * w_hint / 2:.2f}）")

    # 正例边界：谷底 1.5 恰好跌破门槛 1.86 → 应当切分（证明上一条不是"恒不切"）
    border = np.full(n, 2.6)
    border[14:20] = np.array([2.6, 2.7, 3.0, 3.0, 2.7, 2.6])
    border[44:50] = np.array([2.6, 2.7, 3.0, 3.0, 2.7, 2.6])
    border[20:44] = 1.5
    subs3 = _split_run_at_valleys(idx, border, w_hint)
    chk.ok(len(subs3) == 2, "谷底跌破门槛即切分（边界正例）",
           f"实际 {len(subs3)} 段（谷底 1.5 < 门槛 {0.62 * w_hint / 2:.2f}）")

    # 负例 3：段太短（< 6 px）不得切分
    tiny = _split_run_at_valleys(np.arange(5), prof[:5], w_hint)
    chk.ok(len(tiny) == 1, "短段不得切分（避免碎片化）", f"实际 {len(tiny)} 段")


# ------------------------------------------------------------------ 4. 虚线末端


def test_dash_end_width_not_deflated(chk: Checker) -> None:
    """虚线末端列的宽度不得被低估——幽灵轨道的**根因**。

    缺陷机理：列内段长为 L 时，段内 EDT 的上界是 ``⌈(L+1)/2⌉``。虚线末端列只
    切到极短一截，EDT 由"段有多长"决定而非"笔画有多宽"，于是**粗虚线的末端列
    被测成细线宽度**。实测后果：BW-B/BW-D 的粗虚线沿途长出 39/69 点的"幽灵细线"
    轨道（颜色同黑、位置同粗线、不与任何真值对应）。

    本测试直接构造一根"虚线中的一段"：真宽 8 px、长 20 px，比较
    **末列宽度** 与 **中列宽度**。修复前末列会低到 ~2 px；修复后应一致。
    """
    print("\n--- test_dash_end_width_not_deflated：虚线末端列不得被误测为细线")
    true_w, dash_len = 8.0, 20
    ink = np.zeros((60, 120), dtype=bool)
    # 一段位于 y=30、x=40..59 的粗横线（模拟一个虚线段）
    ink[30 - int(true_w) // 2: 30 + int(true_w) // 2, 40:40 + dash_len] = True
    wmap = edt_width_map(ink)

    # 引擎口径：逐列段内 max(EDT)
    col_w = []
    for x in range(40, 40 + dash_len):
        idx = np.flatnonzero(ink[:, x])
        col_w.append(float(wmap[idx, x].max()))
    col_w = np.asarray(col_w)
    end_raw = max(col_w[0], col_w[1], col_w[-1], col_w[-2])
    mid_raw = float(np.median(col_w[2:-2]))
    print(f"       末列原始宽度={end_raw:.2f}px  中列={mid_raw:.2f}px  真值={true_w}")
    # 末列最多只有中列宽度的 60%（实测 8px 真宽 -> 末列 4.0px，恰为中列之半）
    chk.ok(end_raw <= 0.6 * mid_raw,
           "反例确认：末列原始宽度确实被严重低估（缺陷真实存在）",
           f"末列 {end_raw:.2f} ≤ 0.6×中列 {0.6*mid_raw:.2f}")

    # 修复机制：column_samples 沿 x 做 ±2 的宽度局部取大
    samples = column_samples(ink, wmap, w_hint=true_w)
    got = [max(s.w for s in samples[x]) for x in range(40, 40 + dash_len)]
    g = np.asarray(got)
    end_fixed = max(g[0], g[1], g[-1], g[-2])
    mid_fixed = float(np.median(g[2:-2]))
    print(f"       末列修复后={end_fixed:.2f}px  中列={mid_fixed:.2f}px")
    chk.ok(end_fixed >= 0.9 * mid_fixed,
           "修复后：末列宽度恢复到与中列一致",
           f"末列 {end_fixed:.2f} vs 中列 {mid_fixed:.2f}")
    chk.ok(abs(mid_fixed - true_w) <= 1.5, "中列宽度接近真值",
           f"{mid_fixed:.2f}px vs {true_w}")

    # 修复后整段宽度类必须唯一（不再分裂出"细线"）
    cents = width_classes([s.w for col in samples for s in col])
    chk.ok(len(cents) == 1, "修复后一个虚线段只产生 1 个宽度类",
           f"检出 {[round(c, 2) for c in cents]}")


# ------------------------------------------------------------------ 5. 宽度分型


def test_width_classes_no_chaining(chk: Checker) -> None:
    """宽度分型不得被"交叉尾巴"串联成一类。

    交叉区会把若干样本的宽度抬到两类之间，形成一条从细到粗的连续尾巴。
    **排序聚类会被这条尾巴串联**（细类上界一路接上粗类下界，两类并成一类），
    而直方图取峰只认"哪里样本最多"，尾巴再长也不构成峰。本测试构造出这条尾巴，
    断言取峰法仍给出 2 类。
    """
    print("\n--- test_width_classes_no_chaining：交叉尾巴不得串联两类")
    rng = np.random.default_rng(20260917)
    thin = rng.normal(2.2, 0.18, 500)
    thick = rng.normal(5.0, 0.25, 500)
    tail = np.linspace(2.5, 4.7, 90)          # 交叉区抬高的连续尾巴
    ws = np.concatenate([thin, tail, thick])

    # 对照：朴素排序聚类（相对容差 35%）会被尾巴串联
    naive = sorted(ws.tolist())
    groups: list[list[float]] = [[naive[0]]]
    for v in naive[1:]:
        if v <= groups[-1][-1] * 1.35:
            groups[-1].append(v)
        else:
            groups.append([v])
    n_naive = len(groups)

    got = width_classes(ws)
    print(f"       朴素链式聚类给出 {n_naive} 类；直方图取峰给出 "
          f"{[round(c, 2) for c in got]}")
    chk.ok(len(got) == 2, "直方图取峰给出 2 个宽度类",
           f"检出 {[round(c, 2) for c in got]}")
    if len(got) == 2:
        chk.ok(abs(got[0] - 2.2) < 0.4 and abs(got[1] - 5.0) < 0.5,
               "两个峰位分别落在真值附近",
               f"{got[0]:.2f} / {got[1]:.2f}（真值 2.2 / 5.0）")
    chk.ok(n_naive != 2,
           "对照：朴素链式聚类确实被串联（证明该设计点必要）",
           f"朴素法给出 {n_naive} 类")
    # 单独一条尾巴不得产生峰
    only_tail = width_classes(tail)
    chk.ok(len(only_tail) <= 1, "均匀尾巴自身不构成峰",
           f"检出 {[round(c, 2) for c in only_tail]}")


# ------------------------------------------------------------------ 6. 分段阈值


def test_segment_single_missing_col(chk: Checker) -> None:
    """缺 1 个采样列**不算**断开——否则实线会被误判为虚线。

    真实踩过：``min_gap_cols=2`` 时"相邻采样点 x 差 ≥ 2"即成断点，而单列缺失在
    交叉区（合并段被归属给另一条曲线）与抗锯齿抖动处频繁发生。实测 BW-C 的实线
    被切成 34 段，段数密度 6.0 段/100px **越过虚线判据**，实线被判成虚线。
    """
    print("\n--- test_segment_single_missing_col：缺 1 列不算断开")
    xs = list(range(0, 200))
    xs = [x for x in xs if x not in (50, 120)]        # 两处单列缺失
    t = Track(samples=[Sample(x=x, y=float(x), w=4.0, n=4) for x in xs], cls=0)
    segs = segment_intervals(t)
    chk.ok(len(segs) == 1, "含单列缺失的实线仍判为 1 段",
           f"检出 {len(segs)} 段 {segs}")

    # 真缺口（连续缺 6 列）必须断开
    xs2 = [x for x in range(0, 200) if not (60 <= x < 66)]
    t2 = Track(samples=[Sample(x=x, y=float(x), w=4.0, n=4) for x in xs2], cls=0)
    segs2 = segment_intervals(t2)
    chk.ok(len(segs2) == 2, "连续缺 6 列判为 2 段", f"检出 {len(segs2)} 段 {segs2}")


# ------------------------------------------------------------------ 7. 划线判定


def test_dash_vs_occlusion(chk: Checker) -> None:
    """虚线 ≠ 被遮挡的实线：判据是**段数密度**，不是缺口长度。

    两者都表现为多段 + 缺口，且缺口长度还可能反过来（遮挡缺口可以比虚线间隙
    更短）。唯一稳定的差异是每百像素的段数：虚线约 7 段/100px，遮挡实线约
    0.35 段/100px，相差一个数量级。
    """
    print("\n--- test_dash_vs_occlusion：段数密度区分虚线与被遮挡实线")

    def mk(xs: list[int]) -> Track:
        return Track(samples=[Sample(x=x, y=0.0, w=4.0, n=4) for x in xs], cls=0)

    # 虚线：on 8 / off 5，共 560 px -> 约 43 段
    dash_x, x = [], 0
    while x < 560:
        dash_x.extend(range(x, min(x + 8, 560)))
        x += 13
    t_dash = mk(dash_x)
    chk.ok(classify_dash(t_dash) == "dashed", "规则虚线判为 dashed",
           f"{len(segment_intervals(t_dash))} 段 / span {segment_intervals(t_dash)[-1][1]}")

    # 被遮挡的实线：1 个 160 px 的大缺口（远大于虚线间隙），但只有 2 段
    solid_x = [x for x in range(0, 560) if not (200 <= x < 360)]
    t_occ = mk(solid_x)
    segs = segment_intervals(t_occ)
    gap = segs[1][0] - segs[0][1]
    chk.ok(classify_dash(t_occ) == "solid",
           "被遮挡实线判为 solid（缺口虽大但段数只有 2）",
           f"{len(segs)} 段，缺口 {gap}px")

    # 反向陷阱：缺口**比虚线间隙还短**的遮挡，仍不得判为虚线
    many_occ = [x for x in range(0, 560) if not (x in (200, 201, 400, 401))]
    t_many = mk(many_occ)
    chk.ok(classify_dash(t_many) == "solid",
           "缺 4 个单列（缺口 2px < 虚线间隙）仍判 solid",
           f"{len(segment_intervals(t_many))} 段")

    # 门槛的显式性：段数够多但密度不足 -> solid
    sparse_x, x = [], 0
    while x < 560:
        sparse_x.extend(range(x, min(x + 8, 560)))
        x += 130
    t_sparse = mk(sparse_x)
    chk.ok(classify_dash(t_sparse) == "solid",
           "段数少且密度低判 solid", f"{len(segment_intervals(t_sparse))} 段")

    # 过短轨道不得下结论
    t_short = mk(list(range(0, 30)))
    chk.ok(classify_dash(t_short) == "unknown", "过短轨道判 unknown",
           f"span={segment_intervals(t_short)[-1][1]}")

    print(f"       阈值留档: MIN_DASH_DENSITY={MIN_DASH_DENSITY} 段/100px")


# ------------------------------------------------------------------ 8. 缺口诚信


def _solid_with_hole(h: int, w: int, hole_x: tuple[int, int]) -> np.ndarray:
    """一条 5px 粗的横线，中段挖掉一个白洞（模拟图例框遮挡）。"""
    ink = np.zeros((h, w), dtype=bool)
    ink[h // 2 - 2: h // 2 + 3, :] = True
    ink[:, hole_x[0]:hole_x[1]] = False
    return ink


def test_occluded_curve_keeps_both_sides(chk: Checker) -> None:
    """整列无墨时，超时轨道**必须**交给 finished——否则静默丢数据。

    真实踩过的坑：``track_samples`` 的"整列无墨"分支只把 ``dead`` 累加并
    **直接过滤掉**超时轨道，没有走 ``finished.append``。后果是"被遮挡曲线的左侧
    半段"整段消失，输出看起来像"这条曲线本来就只有右半段"——缺口 0 个、段数 1，
    完全自洽，人工审核根本看不出来。BW-E 实测只剩右半段 217 点。
    """
    print("\n--- test_occluded_curve_keeps_both_sides：遮挡两侧都不得丢")
    ink = _solid_with_hole(60, 400, (150, 260))
    rgb = np.where(ink[:, :, None], 0, 255).astype(np.uint8)
    rgb = np.repeat(rgb, 3, axis=2) if rgb.shape[2] == 1 else rgb
    ser = separate_bw(rgb, (0, 0, 400, 60), min_points=10, frame_trim_px=0)
    chk.ok(len(ser) == 1, "遮挡后仍为 1 条曲线（两段被接回）",
           f"检出 {len(ser)} 条")
    if len(ser) == 1:
        pts = np.asarray(ser[0].points, dtype=float)
        left = int((pts[:, 0] < 145).sum())
        right = int((pts[:, 0] > 265).sum())
        chk.ok(left >= 100, "左侧半段存在（原缺陷即在此处丢失）",
               f"左半段点数={left}")
        chk.ok(right >= 100, "右侧半段存在", f"右半段点数={right}")
        chk.ok(ser[0].meta["n_gaps"] >= 1, "缺口被如实登记",
               f"缺口数={ser[0].meta['n_gaps']}")
        chk.ok(ser[0].meta["segments"] and len(ser[0].meta["segments"]) == 2,
               "段区间表恰为 2 段（缺口在中间）",
               f"segments={ser[0].meta['segments']}")


def test_gap_not_interpolated(chk: Checker) -> None:
    """缺口不得插值——"未观测"不得伪装成"已观测"。

    这是项目的硬不变量（MEMORY §6）。若引擎在缺口内补点，缺口就看不见了，
    下游会把补出来的值当测量值用。
    """
    print("\n--- test_gap_not_interpolated：缺口不得插值")
    ink = _solid_with_hole(60, 400, (150, 260))
    rgb = np.where(ink[:, :, None], 0, 255).astype(np.uint8)
    ser = separate_bw(rgb, (0, 0, 400, 60), min_points=10, frame_trim_px=0)
    chk.ok(len(ser) == 1, "构造了 1 条被测曲线", f"{len(ser)}")
    if len(ser) == 1:
        pts = np.asarray(ser[0].points, dtype=float)
        inner = ((pts[:, 0] > 154) & (pts[:, 0] < 256)).sum()
        chk.ok(int(inner) == 0, "遮挡区内部零数据点",
               f"遮挡区 [154,256] 内点数={int(inner)}")
        n_expected = int(ink.sum(axis=0).astype(bool).sum())
        chk.ok(len(pts) <= n_expected,
               "点数不超过有墨列数（没有凭空补点）",
               f"点数={len(pts)} ≤ 有墨列数={n_expected}")


# ------------------------------------------------------------------ 9. 彩色让位


def test_colored_input_defers(chk: Checker) -> None:
    """彩色图必须让位给颜色引擎。

    分工边界：颜色轴存在时，颜色才是正确的分离轴。黑白引擎若照样输出轨道，
    同一张图就会有两套互相打架的答案，且无法判断该信谁。
    """
    print("\n--- test_colored_input_defers：彩色图必须让位")
    ink = _solid_with_hole(60, 400, (150, 260))
    grey = np.where(ink, 0, 255).astype(np.uint8)
    chk.ok(color_pixel_count(np.repeat(grey[:, :, None], 3, axis=2)) == 0,
           "灰阶图彩色像素数为 0（判据本身）", "")

    colored = np.repeat(grey[:, :, None], 3, axis=2).copy()
    # 把整条线染成蓝色：R=B=0, G 通道抬起来会变成绿…直接写成纯蓝 (0,0,255)
    colored[ink] = np.array([0, 0, 255], dtype=np.uint8)
    n_col = color_pixel_count(colored)
    chk.ok(n_col >= 100, "着色图彩色像素数显著 > 0", f"彩色像素={n_col}")

    ser = separate_bw(colored, (0, 0, 400, 60), min_points=10, frame_trim_px=0,
                      min_color_pixels=50)
    chk.ok(len(ser) == 0, "默认在彩色图上返回空（让位）", f"返回 {len(ser)} 条")
    ser2 = separate_bw(colored, (0, 0, 400, 60), min_points=10, frame_trim_px=0,
                       allow_color=True)
    chk.ok(len(ser2) >= 1, "allow_color=True 时可强制处理（留出逃生门）",
           f"返回 {len(ser2)} 条")

    # 阈值语义：彩色像素数低于门槛时不触发让位
    tiny = np.repeat(grey[:, :, None], 3, axis=2).copy()
    tiny[30, 200:205] = np.array([0, 0, 255], dtype=np.uint8)
    n_tiny = color_pixel_count(tiny)
    chk.ok(n_tiny < 50, "极少量彩色像素不触发让位",
           f"彩色像素={n_tiny}（阈值 50）")


# ------------------------------------------------------------------ 10. 去重边界


def test_dedupe_keeps_parallel_curves(chk: Checker) -> None:
    """去重必须剔除"同一曲线的并行副本"，但**不得**吞掉真正的平行曲线。

    这是去重的两个方向，只测一个方向会误认为"越激进越好"。BW-C 正是这个边界：
    两条曲线**同线宽**（同一宽度类）、x 区间完全重叠，只靠 y 相隔——去重若按
    "同宽度类 + x 重叠"判定就会把真曲线删掉。
    """
    print("\n--- test_dedupe_keeps_parallel_curves：去重的两个方向")

    def line(y: float, x0: int = 0, x1: int = 300, n: int = 120) -> Track:
        xs = np.linspace(x0, x1, n).astype(int)
        return Track(samples=[Sample(x=int(x), y=y, w=4.0, n=4) for x in xs], cls=0)

    main = line(100.0)
    dup = line(101.0)                  # 与 main 重合（副本）
    parallel = line(220.0)             # 真正的平行曲线
    partial = line(100.0, x0=310, x1=600)   # x 不相交（遮挡切成的另一半成一线）

    kept = dedupe_tracks([main, dup, parallel, partial])
    ys = sorted(round(float(np.median([s.y for s in t.samples])), 1) for t in kept)
    print(f"       输入 4 条（y=100,101,220,100@x>310）-> 保留 y={ys}")
    chk.ok(len(kept) == 3, "剔除 1 条重合副本，保留 3 条",
           f"保留 {len(kept)} 条")
    chk.ok(any(abs(y - 220.0) < 1e-6 for y in ys), "平行曲线 y=220 未被误删", "")
    chk.ok(sum(1 for y in ys if abs(y - 100.0) < 1e-6) == 2,
           "x 不相交的两段（同一曲线被切开）都保留", f"y≈100 的记录数={sum(1 for y in ys if abs(y-100.0)<1e-6)}")

    # 反例：若 min_overlap 提到 1.0，副本仍有 100% 点重合 -> 仍应剔除
    kept2 = dedupe_tracks([main, dup], min_overlap=0.95)
    chk.ok(len(kept2) == 1, "重合率 100% 的副本在 min_overlap=0.95 下仍被剔除",
           f"保留 {len(kept2)} 条")
    # 反例：两色/两线相隔 8px（> tol_px=4）不得判重
    kept3 = dedupe_tracks([main, line(108.0)])
    chk.ok(len(kept3) == 2, "相隔 8px 的两条线不判重（tol_px=4）",
           f"保留 {len(kept3)} 条")


# ------------------------------------------------------------------ 11. 端到端最小


def test_end_to_end_minimal(chk: Checker) -> None:
    """最小端到端：一张自造图上 {细,粗} × {实,虚} 四路分离。

    与 ``verify_bw_accuracy.py`` 的区别：这里只用**纯二进制合成图**（不经
    matplotlib、不依赖任何夹具文件），所以它是"引擎在理想输入下是否自洽"的
    最干净判据——若这条也失败，说明问题在算法本身而非渲染细节。
    """
    print("\n--- test_end_to_end_minimal：合成图四路分离")
    H, W = 420, 620
    ink = np.zeros((H, W), dtype=bool)

    def hline(y: int, w: int, dash=None) -> None:
        lo = y - w // 2
        if dash is None:
            ink[lo:lo + w, 20:600] = True
        else:
            x = 20
            while x < 600:
                ink[lo:lo + w, x:min(x + dash[0], 600)] = True
                x += dash[0] + dash[1]

    hline(70, 3)                       # 细实线
    hline(160, 9)                      # 粗实线
    hline(250, 3, dash=(10, 7))        # 细虚线
    hline(340, 9, dash=(10, 7))        # 粗虚线
    rgb = np.repeat(np.where(ink, 0, 255).astype(np.uint8)[:, :, None], 3, axis=2)

    ser = separate_bw(rgb, (0, 0, W, H), min_points=20, frame_trim_px=0)
    chk.ok(len(ser) == 4, "四路分离出 4 条", f"检出 {len(ser)} 条")
    got = sorted(((s.meta["dash"], round(s.meta["width_px"])) for s in ser),
                 key=lambda q: q[1])
    print(f"       检出: {got}")
    if len(ser) == 4:
        chk.ok(sum(1 for d, _ in got if d == "solid") == 2, "2 条实线", "")
        chk.ok(sum(1 for d, _ in got if d == "dashed") == 2, "2 条虚线", "")
        thin = [w for _, w in got][:2]
        thick = [w for _, w in got][2:]
        chk.ok(max(thin) < min(thick), "细/粗两组宽度不重叠",
               f"细={thin} 粗={thick}")
        chk.ok(min(thick) / max(thin) >= 1.6, "粗/细宽度比 ≥ 1.6",
               f"{min(thick)/max(thin):.2f}")
        # 每条轨道都应落在自己的 y 层（不许串线）
        ys = sorted(float(np.median([p[1] for p in s.points])) for s in ser)
        chk.ok(all(b - a > 40 for a, b in zip(ys, ys[1:])),
               "四条轨道分居四层，无串线", f"y 中位={[round(v) for v in ys]}")


# ------------------------------------------------------------------ 主入口


def main() -> int:
    chk = Checker()
    print("=" * 78)
    print("黑白线画分离引擎 · 机制类单元测试")
    print("=" * 78)
    test_width_scale_contract(chk)
    test_width_slope_invariance(chk)
    test_valley_split(chk)
    test_dash_end_width_not_deflated(chk)
    test_width_classes_no_chaining(chk)
    test_segment_single_missing_col(chk)
    test_dash_vs_occlusion(chk)
    test_occluded_curve_keeps_both_sides(chk)
    test_gap_not_interpolated(chk)
    test_colored_input_defers(chk)
    test_dedupe_keeps_parallel_curves(chk)
    test_end_to_end_minimal(chk)
    print("-" * 78)
    print(f"共 {chk.n} 项检查，失败 {chk.fail}")
    if chk.fail:
        print("存在失败项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
