"""生成「黑白线画」分离引擎的合成用例与解析真值。

为什么必须自造图
----------------
native 引擎的序列分离轴是**颜色聚类**（``dominant_colors`` → ``color_mask``）。
零彩色图（纯黑白线画，2000 年前印刷期刊常见）**不存在**任何颜色差异可利用：
四条线即使靠「线宽 × 划线模式」区分，颜色上也完全同色，颜色引擎只能给出**并集**。

要开发「线宽 + 划线」这条新分离轴，就必须有一个**真值可信**的评测集。用真实文献
截图做评测不行——曲线真值本身要靠人眼读，误差与被测对象的误差同量级，无法判定
引擎好坏。本模块改用**生成参数即真值**：

* 笔画宽度 = ``linewidth``（点） × ``dpi / 72``，是渲染前就写死的数；
* 划线模式 = ``(0, (on, off))`` 元组，同样写死；
* 曲线坐标 = 解析式 ``f(x)``，逐点精确；
* 数据区像素矩形 = matplotlib 自己的 ``ax.get_window_extent()``，不是估计的。

因此本文件产出的 ``truth_bw.json`` 属于**生成级真值**，精度远高于任何引擎的输出精度，
可以做严格的逐点比对（见 ``verify_bw_accuracy.py``）。

六类用例
--------
======  ==========================================================  ==================
 代号    几何                                                        考察点
======  ==========================================================  ==================
 BW-A   两条**实线**、线宽不同（细/粗），两次交叉                     线宽是有效分离轴
 BW-B   两条**虚线**、线宽不同                                       线宽 × 划线可组合
 BW-C   **同线宽**：一实线 + 一虚线                                  划线是独立分离轴
 BW-D   四条线 = {细,粗} × {实线,虚线}（Cacelli FIG.2 下图拓扑同构）   四路分离全链路
 BW-E   实线被白色图例框**遮挡成两段**                                缺口**不得插值**
 BW-F   负对照：与 BW-D 同几何但**着色**                              颜色引擎负责，黑白引擎须让位
======  ==========================================================  ==================

关于 BW-F：它不是「黑白用例」。它存在的意义是守住分工边界——引擎在
``has_color=True`` 时**必须报告并让位给颜色引擎**，而不是硬把彩色图当黑白图处理。

关于虚线真值：``lines.scale_dashes`` 被显式设为 ``False``，使 ``(on, off)`` 按**点**的
绝对值解释（matplotlib ≥2.0 默认会再乘一次线宽，那样真值就得跟着线宽变）。因此
"on 像素数" 可以直接由 ``on_pt × dpi / 72`` 算出，是本文件与测试之间的一处硬约定。

用法::

    python make_bw_cases.py          # 写出 figures/caseBW-*.png 与 truth_bw.json
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

HERE = Path(__file__).parent
OUT = HERE / "figures"
OUT.mkdir(exist_ok=True)
TRUTH_BW = HERE / "truth_bw.json"

DPI = 100

# 黑白线画风格：白底、黑线、黑轴框，**不引入任何彩色**
plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": "black",
    "axes.labelcolor": "black",
    "axes.linewidth": 1.2,
    "xtick.color": "black",
    "ytick.color": "black",
    "savefig.dpi": DPI,
    "font.size": 10,
    # 虚线样式按「点」绝对值解释；默认 True 会再乘线宽，真值将随线宽变化
    "lines.scale_dashes": False,
})

# ---------------------------------------------------------------- 线型常量
LW_THIN = 1.0      # 点 -> 1.389 px @100dpi
LW_THICK = 2.6     # 点 -> 3.611 px @100dpi
LW_MID = 2.0       # 点 -> 2.778 px @100dpi

DASH_SHORT = (6.0, 4.0)    # 点 -> 8.33 on / 5.56 off px
DASH_LONG = (9.0, 6.0)     # 点 -> 12.5 on / 8.33 off px

BLACK = "black"


def px(lw_pt: float) -> float:
    """点 -> 像素（本模块全部用例均为 ``DPI`` dpi）。"""
    return lw_pt * DPI / 72.0


# ---------------------------------------------------------------- 绘图与取框


def _axes_box(fig, ax) -> list[float]:
    """取数据区像素矩形（图像坐标系，y 向下）。

    必须在 ``tight_layout`` 之后再调用；否则取到的是布局前的坐标。
    """
    fig.canvas.draw()
    h = fig.canvas.get_width_height()[1]
    b = ax.get_window_extent()
    return [round(b.x0, 3), round(h - b.y1, 3), round(b.x1, 3), round(h - b.y0, 3)]


def _draw(ax, x, y, lw_pt: float, dash_pt: tuple[float, float] | None,
          color: str = BLACK) -> None:
    """画一条定宽曲线；``dash_pt=None`` 为实线。

    端点一律用 ``butt`` 帽：圆帽会让每一段虚线两端各多出半个线宽的墨，
    使「实线 vs 虚线」的像素统计被系统性污染。
    """
    if dash_pt is None:
        ax.plot(x, y, color=color, lw=lw_pt, solid_capstyle="butt")
    else:
        ax.plot(x, y, color=color, lw=lw_pt, linestyle=(0, dash_pt),
                dash_capstyle="butt")


def _stroke(name: str, x: np.ndarray, y: np.ndarray, lw_pt: float,
            dash_pt: tuple[float, float] | None, **extra) -> dict:
    """构造一条曲线的真值记录。

    ``width_px`` 是**渲染标称宽度**（点 × dpi/72）。实际墨宽会因抗锯齿阈值
    略大 0–0.6 px，故测试断言用的是「序关系 + 相对容差」，而非等值。
    """
    rec = {
        "name": name,
        "lw_pt": lw_pt,
        "width_px": round(px(lw_pt), 4),
        "dash": "solid" if dash_pt is None else "dashed",
        "dash_pattern_pt": list(dash_pt) if dash_pt else None,
        "dash_on_px": round(px(dash_pt[0]), 4) if dash_pt else None,
        "dash_off_px": round(px(dash_pt[1]), 4) if dash_pt else None,
        "x": x.tolist(),
        "y": y.tolist(),
    }
    rec.update(extra)
    return rec


def _finish(fig, ax, path: Path, case: dict) -> dict:
    """保存图并补齐像素级元数据。"""
    fig.tight_layout()
    fig.savefig(path)
    case["axes_box_px"] = _axes_box(fig, ax)
    plt.close(fig)
    case["image"] = str(path)
    case["dpi"] = DPI
    return case


# ---------------------------------------------------------------- BW-A


def case_a() -> dict:
    """两条实线、线宽不同，两次交叉。

    曲线在 ``x≈2.3`` 与 ``x≈7.0`` 附近两次相交。交叉处两笔画合并成一团，
    本用例用于检验：**合并区不得污染宽度估计**（若按列内连续段长度测宽，
    交叉区会给出一个虚高的宽度，进而把两条线都划进"粗线"类）。
    """
    fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=DPI)
    x = np.linspace(0.0, 10.0, 1400)
    y1 = 1.15 * np.sin(0.95 * x) + 0.20          # 细
    y2 = 1.55 * np.cos(0.62 * x + 0.45) - 0.10   # 粗
    _draw(ax, x, y1, LW_THIN, None)
    _draw(ax, x, y2, LW_THICK, None)
    ax.set_xlim(0, 10)
    ax.set_ylim(-2.4, 2.4)
    ax.tick_params(direction="out")
    return _finish(fig, ax, OUT / "caseBWA_two_solid_widths.png", {
        "kind": "bw_line",
        "case": "BW-A",
        "title": "两条实线，线宽不同，两次交叉",
        "xlim": [0.0, 10.0],
        "ylim": [-2.4, 2.4],
        "has_color": False,
        "n_expected_tracks": 2,
        "strokes": [
            _stroke("thin_solid", x, y1, LW_THIN, None),
            _stroke("thick_solid", x, y2, LW_THICK, None),
        ],
        "occlusion_px": None,
    })


# ---------------------------------------------------------------- BW-B


def case_b() -> dict:
    """两条虚线、线宽不同，纵向分开。

    虚线的分离比实线难一层：每条曲线在 x 上被切成几十个碎片，
    必须靠「间隙容忍 + 切向外推」把它们缝回一条轨道。
    """
    fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=DPI)
    x = np.linspace(0.0, 10.0, 1400)
    y1 = 0.90 * np.sin(1.05 * x) + 1.35
    y2 = 0.75 * np.cos(0.80 * x) - 1.45
    _draw(ax, x, y1, LW_THIN, DASH_SHORT)
    _draw(ax, x, y2, LW_THICK, DASH_LONG)
    ax.set_xlim(0, 10)
    ax.set_ylim(-2.6, 2.6)
    ax.tick_params(direction="out")
    return _finish(fig, ax, OUT / "caseBWB_two_dashed_widths.png", {
        "kind": "bw_line",
        "case": "BW-B",
        "title": "两条虚线，线宽不同",
        "xlim": [0.0, 10.0],
        "ylim": [-2.6, 2.6],
        "has_color": False,
        "n_expected_tracks": 2,
        "strokes": [
            _stroke("thin_dashed", x, y1, LW_THIN, DASH_SHORT),
            _stroke("thick_dashed", x, y2, LW_THICK, DASH_LONG),
        ],
        "occlusion_px": None,
    })


# ---------------------------------------------------------------- BW-C


def case_c() -> dict:
    """同线宽：一实线 + 一虚线。

    这是「划线模式」作为**独立分离轴**的唯一干净检验——线宽完全相同，
    颜色完全相同，引擎只剩划线这一个判据可用。
    """
    fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=DPI)
    x = np.linspace(0.0, 10.0, 1400)
    y1 = 1.05 * np.sin(0.85 * x) + 1.30
    y2 = 1.05 * np.sin(0.85 * x) - 1.30
    _draw(ax, x, y1, LW_MID, None)
    _draw(ax, x, y2, LW_MID, DASH_SHORT)
    ax.set_xlim(0, 10)
    ax.set_ylim(-2.8, 2.8)
    ax.tick_params(direction="out")
    return _finish(fig, ax, OUT / "caseBWC_solid_vs_dash.png", {
        "kind": "bw_line",
        "case": "BW-C",
        "title": "同线宽：一实线 + 一虚线",
        "xlim": [0.0, 10.0],
        "ylim": [-2.8, 2.8],
        "has_color": False,
        "n_expected_tracks": 2,
        "strokes": [
            _stroke("mid_solid", x, y1, LW_MID, None),
            _stroke("mid_dashed", x, y2, LW_MID, DASH_SHORT),
        ],
        "occlusion_px": None,
    })


# ---------------------------------------------------------------- BW-D


def case_d() -> dict:
    """四条线 = {细, 粗} × {实线, 虚线}，纵向四层分开。

    **Cacelli 1998 FIG. 2 下图（cross section）的拓扑同构**：该图四条曲线正是靠
    IC(粗)/SC(细) × LG(实线)/VG(虚线) 区分，且全图零彩色。本用例若能被正确
    四路分离，即说明「线宽 + 划线」引擎在该拓扑上成立。

    四条线在纵向上留足间距（约 1.2 数据单位 ≈ 90 px），使「轨道不得串线」
    成为可判定的硬指标。
    """
    fig, ax = plt.subplots(figsize=(6.4, 5.4), dpi=DPI)
    x = np.linspace(0.0, 10.0, 1400)
    base = np.sin(0.72 * x)
    levels = [
        ("thin_solid", 3.60, LW_THIN, None),
        ("thick_solid", 1.20, LW_THICK, None),
        ("thin_dashed", -1.20, LW_THIN, DASH_SHORT),
        ("thick_dashed", -3.60, LW_THICK, DASH_LONG),
    ]
    strokes = []
    for name, off, lw, dash in levels:
        y = 0.70 * base + off
        _draw(ax, x, y, lw, dash)
        strokes.append(_stroke(name, x, y, lw, dash, y_offset=off))
    ax.set_xlim(0, 10)
    ax.set_ylim(-4.8, 4.8)
    ax.tick_params(direction="out")
    return _finish(fig, ax, OUT / "caseBWD_four_linetypes.png", {
        "kind": "bw_line",
        "case": "BW-D",
        "title": "四条线 = {细,粗} × {实线,虚线}（Cacelli FIG.2 下图拓扑同构）",
        "xlim": [0.0, 10.0],
        "ylim": [-4.8, 4.8],
        "has_color": False,
        "n_expected_tracks": 4,
        "strokes": strokes,
        "occlusion_px": None,
    })


# ---------------------------------------------------------------- BW-E


def case_e() -> dict:
    """实线被白色图例框遮挡成两段 -> 缺口**不得插值**。

    遮挡框故意**不画边框**（``edgecolor='none'``）。理由是让本用例只考察一件事：
    缺口诚信。若给框加黑边，框边自身也成了一条笔画，断言会被额外的轨道干扰，
    而那是另一个待考察的点（S2 的「非数据元素剔除」）。

    期望行为：引擎把两段并入**同一条**轨道，并如实登记一个缺口；
    缺口区间内**不得**出现任何数据点（不许线性补上）。
    """
    fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=DPI)
    x = np.linspace(0.0, 10.0, 1400)
    y = 1.35 * np.sin(0.80 * x)
    _draw(ax, x, y, LW_MID, None)
    occ_x = (3.30, 6.10)
    occ_y = (-2.40, 2.60)
    ax.add_patch(Rectangle((occ_x[0], occ_y[0]),
                           occ_x[1] - occ_x[0], occ_y[1] - occ_y[0],
                           facecolor="white", edgecolor="none", zorder=5))
    ax.set_xlim(0, 10)
    ax.set_ylim(-2.6, 2.6)
    ax.tick_params(direction="out")
    return _finish(fig, ax, OUT / "caseBWE_occluded_solid.png", {
        "kind": "bw_line",
        "case": "BW-E",
        "title": "实线被白色图例框遮挡成两段（缺口不得插值）",
        "xlim": [0.0, 10.0],
        "ylim": [-2.6, 2.6],
        "has_color": False,
        "n_expected_tracks": 1,
        "strokes": [_stroke("occluded_solid", x, y, LW_MID, None)],
        "occlusion_px": [occ_x[0], occ_x[1]],
        "occlusion_note": "遮挡框无边框，白色填充覆盖曲线中段",
    })


# ---------------------------------------------------------------- BW-F


def case_f() -> dict:
    """负对照：与 BW-D 同几何，但**着色**。

    期望行为不是"黑白引擎把它分开"，而是——
    **黑白引擎报告 ``has_color=True`` 并让位**。若它照样输出轨道，说明分工边界没守住，
    彩色图上会与颜色引擎结果打架（同一张图两套答案）。
    """
    fig, ax = plt.subplots(figsize=(6.4, 5.4), dpi=DPI)
    x = np.linspace(0.0, 10.0, 1400)
    base = np.sin(0.72 * x)
    levels = [
        ("thin_solid", 3.60, LW_THIN, None, "#1f77b4"),
        ("thick_solid", 1.20, LW_THICK, None, "#d62728"),
        ("thin_dashed", -1.20, LW_THIN, DASH_SHORT, "#2ca02c"),
        ("thick_dashed", -3.60, LW_THICK, DASH_LONG, "#9467bd"),
    ]
    strokes = []
    for name, off, lw, dash, col in levels:
        y = 0.70 * base + off
        _draw(ax, x, y, lw, dash, color=col)
        strokes.append(_stroke(name, x, y, lw, dash, y_offset=off, color=col))
    ax.set_xlim(0, 10)
    ax.set_ylim(-4.8, 4.8)
    ax.tick_params(direction="out")
    return _finish(fig, ax, OUT / "caseBWF_colored_control.png", {
        "kind": "bw_line",
        "case": "BW-F",
        "title": "负对照：同几何但着色（黑白引擎须让位）",
        "xlim": [0.0, 10.0],
        "ylim": [-4.8, 4.8],
        "has_color": True,
        "n_expected_tracks": 0,
        "strokes": strokes,
        "occlusion_px": None,
        "negative_control": "黑白引擎须报 has_color=True；分离交由颜色引擎",
    })


# ---------------------------------------------------------------- 汇总


def build_all(verbose: bool = True) -> dict:
    """生成全部用例并写出 ``truth_bw.json``。"""
    cases = [case_a(), case_b(), case_c(), case_d(), case_e(), case_f()]
    truth = {
        "tool": "chart-digitizer/make_bw_cases",
        "purpose": "黑白线画（零彩色）分离引擎的生成级真值；参数即真值",
        "dpi": DPI,
        "dash_scale_disabled": True,
        "cases": cases,
    }
    TRUTH_BW.write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")

    if verbose:
        for c in cases:
            p = Path(c["image"])
            n_ink = _count_color(c["image"])
            print(f"{p.name:34s} {c['case']:5s} 笔画数={len(c['strokes'])} "
                  f"彩色像素={n_ink['color']} 墨像素={n_ink['ink']}")
        print(f"真值已写出: {TRUTH_BW}")
    return truth


def _count_color(path: str) -> dict:
    """统计图内的彩色像素数与墨像素数（供打印核对黑白属性）。"""
    from PIL import Image

    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGB"), dtype=np.int16)
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    color = int(((mx - mn) > 25).sum())
    ink = int((mx < 200).sum())
    return {"color": color, "ink": ink}


def main() -> int:
    build_all(verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
