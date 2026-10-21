"""生成带解析真值的合成测试图，用于量化 chart-digitizer 的提取精度。

四张图覆盖用户确认的全部图表类型：
  A. 多曲线折线图（3 条不同颜色，含交叉）
  B. 散点 / 实验数据点（两种标记、两种颜色）
  C. 带误差棒的散点序列（对称 y 误差）
  D. 2x2 多子图面板（各含独立轴与曲线）

同时输出 truth_*.json，记录每条曲线的解析式真值与生成时使用的轴范围，
供 verify_accuracy.py 做逐点误差比对。

另外调用 ``make_pdf_cases.build_all()`` 生成文献 PDF 夹具
（``figures/caseP1_paper.pdf`` / ``caseP1_fallback.pdf`` + manifest）——
它是 ``test_pdf_units.py`` 的全部输入，故与 PNG 夹具同批重建。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).parent
OUT = HERE / "figures"
OUT.mkdir(exist_ok=True)

# 统一风格：白色背景、黑色轴框、无网格，贴近论文插图
plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": "black",
    "axes.linewidth": 1.2,
    "savefig.dpi": 100,
    "font.size": 10,
})

C1 = "#d62728"  # 红
C2 = "#1f77b4"  # 蓝
C3 = "#2ca02c"  # 绿


def fig_a() -> dict:
    """多曲线折线：3 条曲线，其中两条交叉。"""
    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=100)
    x = np.linspace(0, 10, 401)
    y1 = np.sin(x)
    y2 = 0.6 * np.cos(1.5 * x)
    y3 = 0.04 * (x - 5) ** 2 - 0.5
    for y, c, lab in ((y1, C1, "sin"), (y2, C2, "cos"), (y3, C3, "parabola")):
        ax.plot(x, y, color=c, lw=2.0, label=lab)
    ax.set_xlim(0, 10)
    ax.set_ylim(-1.2, 1.2)
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    fig.tight_layout()
    p = OUT / "caseA_multicurve.png"
    fig.savefig(p)
    # 记录数据区像素矩形（用 matplotlib 内部变换取出精确值）
    fig.canvas.draw()
    bbox = ax.get_window_extent()
    h = fig.canvas.get_width_height()[1]
    box = [bbox.x0, h - bbox.y1, bbox.x1, h - bbox.y0]
    plt.close(fig)
    return {
        "image": str(p),
        "kind": "multicurve",
        "axes_box_px": [round(v, 3) for v in box],
        "xlim": [0, 10],
        "ylim": [-1.2, 1.2],
        "truth": {
            "sin": {"x": x.tolist(), "y": y1.tolist()},
            "cos": {"x": x.tolist(), "y": y2.tolist()},
            "parabola": {"x": x.tolist(), "y": y3.tolist()},
        },
    }


def fig_b() -> dict:
    """散点：两组不同颜色、不同形状的实验点。"""
    rng = np.random.default_rng(7)
    x = np.linspace(0.5, 9.5, 14)
    y1 = 2.0 + 0.8 * np.sin(x) + rng.normal(0, 0.05, x.size)
    y2 = 1.0 + 0.05 * x + rng.normal(0, 0.05, x.size)
    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=100)
    ax.scatter(x, y1, s=64, facecolors="none", edgecolors=C1, linewidths=2.0)
    ax.scatter(x + 0.0, y2, s=64, facecolors="none", edgecolors=C2, linewidths=2.0, marker="s")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 4)
    fig.tight_layout()
    p = OUT / "caseB_scatter.png"
    fig.savefig(p)
    fig.canvas.draw()
    bbox = ax.get_window_extent()
    h = fig.canvas.get_width_height()[1]
    box = [bbox.x0, h - bbox.y1, bbox.x1, h - bbox.y0]
    plt.close(fig)
    return {
        "image": str(p),
        "kind": "scatter",
        "axes_box_px": [round(v, 3) for v in box],
        "xlim": [0, 10],
        "ylim": [0, 4],
        "truth": {
            "groupA": {"x": x.tolist(), "y": y1.tolist()},
            "groupB": {"x": x.tolist(), "y": y2.tolist()},
        },
    }


def fig_c() -> dict:
    """误差棒：单组散点 + 对称 y 误差，误差棒与标记点同色。"""
    rng = np.random.default_rng(11)
    x = np.linspace(1, 9, 10)
    y = 1.5 + 0.6 * np.cos(x)
    err = 0.25 + 0.12 * rng.random(x.size)
    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=100)
    ax.errorbar(x, y, yerr=err, fmt="o", color=C1, ecolor=C1,
                markersize=6, capsize=3.5, elinewidth=1.6, markerfacecolor="none",
                markeredgewidth=1.6)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3)
    fig.tight_layout()
    p = OUT / "caseC_errorbar.png"
    fig.savefig(p)
    fig.canvas.draw()
    bbox = ax.get_window_extent()
    h = fig.canvas.get_width_height()[1]
    box = [bbox.x0, h - bbox.y1, bbox.x1, h - bbox.y0]
    plt.close(fig)
    return {
        "image": str(p),
        "kind": "errorbar",
        "axes_box_px": [round(v, 3) for v in box],
        "xlim": [0, 10],
        "ylim": [0, 3],
        "truth": {
            "data": {
                "x": x.tolist(),
                "y": y.tolist(),
                "yerr": err.tolist(),
            }
        },
    }


def fig_d() -> dict:
    """2x2 多子图面板，每个面板独立轴范围与曲线。"""
    fig, axes = plt.subplots(2, 2, figsize=(7.5, 6), dpi=100)
    x = np.linspace(0, 10, 401)
    specs = [
        (np.sin(x), [0, 10], [-1.1, 1.1], C1),
        (np.exp(-x / 4) * np.cos(3 * x), [0, 10], [-1.1, 1.1], C2),
        (np.sqrt(x), [0, 10], [0, 3.4], C3),
        (0.1 * x ** 1.5, [0, 10], [0, 3.4], "#9467bd"),
    ]
    for ax, (y, xl, yl, c) in zip(axes.ravel(), specs):
        ax.plot(x, y, color=c, lw=1.8)
        ax.set_xlim(*xl)
        ax.set_ylim(*yl)
    fig.tight_layout()
    p = OUT / "caseD_panels.png"
    fig.savefig(p)
    # 面板框必须在 tight_layout 之后取，否则记录的是布局前的坐标
    fig.canvas.draw()
    h = fig.canvas.get_width_height()[1]
    panels = []
    for ax, (y, xl, yl, c) in zip(axes.ravel(), specs):
        bbox = ax.get_window_extent()
        panels.append({
            "box_px": [round(bbox.x0, 3), round(h - bbox.y1, 3),
                       round(bbox.x1, 3), round(h - bbox.y0, 3)],
            "xlim": xl, "ylim": yl,
        })
    plt.close(fig)
    return {
        "image": str(p),
        "kind": "panels",
        "panels": panels,
        "truth": {
            f"p{i}": {"x": x.tolist(), "y": y.tolist()}
            for i, (y, _, _, _) in enumerate(specs)
        },
    }


def _axes_box(fig, ax) -> list[float]:
    """取数据区像素矩形（图像坐标系，y 向下）。"""
    fig.canvas.draw()
    h = fig.canvas.get_width_height()[1]
    b = ax.get_window_extent()
    return [round(b.x0, 3), round(h - b.y1, 3), round(b.x1, 3), round(h - b.y0, 3)]


def fig_e() -> dict:
    """半对数：y 对数轴（1e-2 .. 1e3），x 线性（0 .. 10）。

    网格线默认**开启**——这是有意为之。实测浅色抗锯齿候选色会在 ``tol`` 容差内
    吞掉浅灰网格线，必须靠 ``drop_antialias_colors`` 在源头剔除。若本用例不开网格，
    该缺陷不会被覆盖。
    """
    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=100)
    x = np.linspace(0.0, 10.0, 400)
    y = 0.05 * np.exp(0.85 * x)             # 0.05 → 245.7（上限 1e3，不裁切）
    ax.semilogy(x, y, color=C1, lw=1.6)
    ax.set_xlim(0, 10)
    ax.set_ylim(1e-2, 1e3)
    ax.grid(True, which="major", alpha=0.3)
    fig.tight_layout()
    p = OUT / "caseE_semilog.png"
    fig.savefig(p)
    box = _axes_box(fig, ax)
    plt.close(fig)
    return {
        "image": str(p), "kind": "semilog", "axes_box_px": box,
        "xlim": [0.0, 10.0], "ylim": [1e-2, 1e3],
        "xscale": "linear", "yscale": "log",
        "truth": {"power": {"x": x.tolist(), "y": y.tolist()}},
    }


def fig_f() -> dict:
    """双对数：x 1..1e3、y 1..1e5，y = 2·x^1.5（幂律，对数轴上为直线）。"""
    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=100)
    x = np.logspace(0, 3, 400)
    y = 2.0 * x ** 1.5
    ax.loglog(x, y, color=C2, lw=1.6)
    ax.set_xlim(1, 1e3)
    ax.set_ylim(1e0, 1e5)
    ax.grid(True, which="major", alpha=0.3)
    fig.tight_layout()
    p = OUT / "caseF_loglog.png"
    fig.savefig(p)
    box = _axes_box(fig, ax)
    plt.close(fig)
    return {
        "image": str(p), "kind": "loglog", "axes_box_px": box,
        "xlim": [1.0, 1e3], "ylim": [1e0, 1e5],
        "xscale": "log", "yscale": "log",
        "truth": {"power": {"x": x.tolist(), "y": y.tolist()}},
    }


def main() -> None:
    cases = [fig_a(), fig_b(), fig_c(), fig_d(), fig_e(), fig_f()]
    truth = {"cases": cases}
    (HERE / "truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for c in cases:
        p = Path(c["image"])
        from PIL import Image

        with Image.open(p) as im:
            size = im.size
        print(f"{p.name:26s} {size[0]}x{size[1]}  kind={c['kind']}")
    print(f"真值已写出: {HERE / 'truth.json'}")

    # 文献 PDF 夹具（test_pdf_units.py 的全部输入）
    import make_pdf_cases

    pdf_man = make_pdf_cases.build_all(verbose=False)
    for key in ("paper", "fallback"):
        print(f"{Path(pdf_man[key]['pdf']).name:26s} PDF 夹具（{key}）")
    print(f"PDF manifest 已写出: {make_pdf_cases.MANIFEST_JSON}")


if __name__ == "__main__":
    main()
