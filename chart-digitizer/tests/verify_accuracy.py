"""端到端精度验证：把 chart-digitizer 的输出与解析真值逐点比对。

对四类图表分别检验：
  * 折线      —— 提取 y 值与真值函数的 RMSE / 最大偏差（数据单位 + 像素当量）
  * 散点      —— 标记点质心与真值的定位误差（像素）
  * 误差棒    —— 中心值与误差幅度的还原误差
  * 多子图    —— 面板切分 + 每面板独立标定后的提取误差

每种情形跑两遍：
  (a) 用真值轴框   -> 隔离评估「提取算法」本身；
  (b) 用自动检测轴框 -> 评估「检测 + 提取」端到端链路的累积误差。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from chartdigit import Calibration, detect, digitize, extract_series, load_image  # noqa: E402

TB = Path(__file__).resolve().parent
OUTDIR = TB / "verify_out"
OUTDIR.mkdir(exist_ok=True)

C_RED = (214, 39, 40)
C_BLUE = (31, 119, 180)
C_GREEN = (44, 160, 44)
C_PURPLE = (148, 103, 189)


def rmse(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a ** 2)))


def curve_errors(ext_x, ext_y, tru_x, tru_y) -> tuple[float, float]:
    """把真值插值到提取点的 x 上，返回 (RMSE, 最大绝对偏差)。"""
    ex = np.asarray(ext_x, dtype=float)
    ey = np.asarray(ext_y, dtype=float)
    tx = np.asarray(tru_x, dtype=float)
    ty = np.asarray(tru_y, dtype=float)
    lo, hi = max(ex.min(), tx.min()), min(ex.max(), tx.max())
    keep = (ex >= lo) & (ex <= hi)
    if keep.sum() < 2:
        return float("nan"), float("nan")
    ref = np.interp(ex[keep], tx, ty)
    d = ey[keep] - ref
    return rmse(d), float(np.max(np.abs(d)))


def scatter_errors(ext_pts, tru_x, tru_y) -> tuple[float, float, int]:
    """最近邻匹配，返回 (平均距离, 最大距离, 匹配对数)，单位=数据单位。"""
    ex = np.asarray([p[0] for p in ext_pts], dtype=float)
    ey = np.asarray([p[1] for p in ext_pts], dtype=float)
    tx = np.asarray(tru_x, dtype=float)
    ty = np.asarray(tru_y, dtype=float)
    if ex.size == 0:
        return float("nan"), float("nan"), 0
    d = np.sqrt((ex[:, None] - tx[None, :]) ** 2 + (ey[:, None] - ty[None, :]) ** 2)
    near = d.min(axis=1)
    return float(near.mean()), float(near.max()), int(ex.size)


def px_per_unit(calib: Calibration, axis: str) -> float:
    if axis == "y":
        span_px = abs(calib.ymax.py - calib.ymin.py) or abs(calib.ymax.px - calib.ymin.px)
        span_val = abs(calib.ymax.value - calib.ymin.value)
    else:
        span_px = abs(calib.xmax.px - calib.xmin.px) or abs(calib.xmax.py - calib.xmin.py)
        span_val = abs(calib.xmax.value - calib.xmin.value)
    return span_px / span_val if span_val else float("nan")


def calib_from_spec(spec: dict) -> Calibration:
    return Calibration.from_box(
        *spec["box_px"],
        x_min=spec["xlim"][0], x_max=spec["xlim"][1],
        y_min=spec["ylim"][0], y_max=spec["ylim"][1],
    )


def line_case(case: dict, rows: list[dict], tag: str) -> None:
    img = load_image(case["image"])
    det = detect(case["image"])
    det_box = max(det.panels, key=lambda p: p.size[0] * p.size[1]).box if det.panels else None

    variants = [("truth-box", tuple(float(v) for v in case["axes_box_px"]))]
    if det_box:
        variants.append(("auto-box", tuple(det_box)))

    colors = [C_RED, C_BLUE, C_GREEN]
    names = list(case["truth"].keys())
    for label, box in variants:
        calib = Calibration.from_box(
            *box, x_min=case["xlim"][0], x_max=case["xlim"][1],
            y_min=case["ylim"][0], y_max=case["ylim"][1],
        )
        series = extract_series(
            img.rgb, box, background=img.background_rgb,
            colors=colors, names=names, kind="line", tol=90,
        )
        data = digitize(series, calib)
        ppu = px_per_unit(calib, "y")
        for d in data:
            tv = case["truth"].get(d.name)
            if tv is None:
                continue
            r, mx = curve_errors(d.x, d.y, tv["x"], tv["y"])
            rows.append({
                "case": case["kind"], "series": d.name, "mode": f"{tag}/{label}",
                "n": d.n, "rmse": r, "max": mx, "rmse_px": r * ppu, "max_px": mx * ppu,
            })
        rows.append({
            "case": case["kind"], "series": f"<检出序列数>", "mode": f"{tag}/{label}",
            "n": len(data), "rmse": float("nan"), "max": float("nan"),
            "rmse_px": float("nan"), "max_px": float("nan"),
        })


def scatter_case(case: dict, rows: list[dict], tag: str) -> None:
    img = load_image(case["image"])
    det = detect(case["image"])
    variants = [("truth-box", tuple(float(v) for v in case["axes_box_px"]))]
    if det.panels:
        b = tuple(max(det.panels, key=lambda p: p.size[0] * p.size[1]).box)
        variants.append(("auto-box", b))

    colors = [C_RED, C_BLUE]
    names = list(case["truth"].keys())
    for label, box in variants:
        calib = Calibration.from_box(
            *box, x_min=case["xlim"][0], x_max=case["xlim"][1],
            y_min=case["ylim"][0], y_max=case["ylim"][1],
        )
        series = extract_series(
            img.rgb, box, background=img.background_rgb,
            colors=colors, names=names, kind="scatter", tol=90, min_points=3,
        )
        data = digitize(series, calib)
        ppu = max(px_per_unit(calib, "x"), px_per_unit(calib, "y"))
        for d in data:
            tv = case["truth"].get(d.name)
            if tv is None:
                continue
            mean_d, max_d, n = scatter_errors(
                list(zip(d.x, d.y)), tv["x"], tv["y"]
            )
            rows.append({
                "case": case["kind"], "series": d.name, "mode": f"{tag}/{label}",
                "n": n, "rmse": mean_d, "max": max_d,
                "rmse_px": mean_d * ppu, "max_px": max_d * ppu,
            })
        rows.append({
            "case": case["kind"], "series": "<检出序列数>", "mode": f"{tag}/{label}",
            "n": len(data), "rmse": float("nan"), "max": float("nan"),
            "rmse_px": float("nan"), "max_px": float("nan"),
        })


def errorbar_case(case: dict, rows: list[dict], tag: str) -> None:
    img = load_image(case["image"])
    det = detect(case["image"])
    variants = [("truth-box", tuple(float(v) for v in case["axes_box_px"]))]
    if det.panels:
        b = tuple(max(det.panels, key=lambda p: p.size[0] * p.size[1]).box)
        variants.append(("auto-box", b))

    tv = case["truth"]["data"]
    for label, box in variants:
        calib = Calibration.from_box(
            *box, x_min=case["xlim"][0], x_max=case["xlim"][1],
            y_min=case["ylim"][0], y_max=case["ylim"][1],
        )
        series = extract_series(
            img.rgb, box, background=img.background_rgb,
            colors=[C_RED], names=["data"], kind="errorbar", tol=90, min_points=3,
        )
        data = digitize(series, calib)
        ppu = px_per_unit(calib, "y")
        if not data:
            rows.append({"case": "errorbar", "series": "data", "mode": f"{tag}/{label}",
                         "n": 0, "rmse": float("nan"), "max": float("nan"),
                         "rmse_px": float("nan"), "max_px": float("nan")})
            continue
        d = data[0]
        # 中心值误差：最近邻匹配到真值点
        ex = np.asarray(d.x)
        ey = np.asarray(d.y)
        tx = np.asarray(tv["x"])
        ty = np.asarray(tv["y"])
        te = np.asarray(tv["yerr"])
        idx = np.abs(ex[:, None] - tx[None, :]).argmin(axis=1)
        e_center = np.abs(ey - ty[idx])
        rows.append({"case": "errorbar", "series": "center", "mode": f"{tag}/{label}",
                     "n": d.n, "rmse": rmse(e_center), "max": float(e_center.max()),
                     "rmse_px": rmse(e_center) * ppu, "max_px": float(e_center.max()) * ppu})
        if d.yerr_low is not None:
            e_bar = np.abs(np.asarray(d.yerr_low) - te[idx])
            rows.append({"case": "errorbar", "series": "yerr_low", "mode": f"{tag}/{label}",
                         "n": d.n, "rmse": rmse(e_bar), "max": float(e_bar.max()),
                         "rmse_px": rmse(e_bar) * ppu, "max_px": float(e_bar.max()) * ppu})
        rows.append({"case": "errorbar", "series": "<检出序列数>", "mode": f"{tag}/{label}",
                     "n": len(data), "rmse": float("nan"), "max": float("nan"),
                     "rmse_px": float("nan"), "max_px": float("nan")})


def panels_case(case: dict, rows: list[dict], tag: str) -> None:
    img = load_image(case["image"])
    det = detect(case["image"])
    colors = [C_RED, C_BLUE, C_GREEN, C_PURPLE]
    names = [f"p{i}" for i in range(len(case["panels"]))]

    for order, (spec, auto) in enumerate(
        zip(case["panels"], det.panels if len(det.panels) == len(case["panels"]) else [None] * 99)
    ):
        nm = names[order]
        variants = [("truth-box", tuple(float(v) for v in spec["box_px"]))]
        if auto is not None:
            variants.append(("auto-box", tuple(auto.box)))
        for label, box in variants:
            calib = Calibration.from_box(
                *box, x_min=spec["xlim"][0], x_max=spec["xlim"][1],
                y_min=spec["ylim"][0], y_max=spec["ylim"][1],
            )
            series = extract_series(
                img.rgb, box, background=img.background_rgb,
                colors=[colors[order]], names=[nm], kind="line", tol=90,
            )
            data = digitize(series, calib)
            ppu = px_per_unit(calib, "y")
            tv = case["truth"][nm]
            for d in data:
                r, mx = curve_errors(d.x, d.y, tv["x"], tv["y"])
                rows.append({
                    "case": "panels", "series": f"{nm}({label})", "mode": f"{tag}",
                    "n": d.n, "rmse": r, "max": mx, "rmse_px": r * ppu, "max_px": mx * ppu,
                })
            if not data:
                rows.append({
                    "case": "panels", "series": f"{nm}({label})", "mode": f"{tag}",
                    "n": 0, "rmse": float("nan"), "max": float("nan"),
                    "rmse_px": float("nan"), "max_px": float("nan"),
                })


def auto_color_case(case: dict, rows: list[dict]) -> None:
    """自动颜色聚类路径：**不**显式指定 colors，完全依赖 ``dominant_colors``。

    该路径此前未被本脚本覆盖（其余用例都显式传入 C_RED/C_BLUE 等），因此漏掉了
    「散点标记的抗锯齿光晕被当作独立序列输出」这一缺陷——光晕位于标记圆周而非
    圆心，点对点距离判据（容差 4px）会漏判。

    本函数以「检出序列数 vs 真值序列数」为主判据：该指标对上述缺陷敏感，且不依赖
    序列命名（自动路径的序列名是 cN_hex，无法与真值名直接对应）。
    """
    img = load_image(case["image"])
    det = detect(case["image"])
    if not det.panels:
        rows.append({
            "case": case["kind"], "series": "<auto-color>", "mode": "E/auto-color",
            "n": 0, "rmse": float("nan"), "max": float("nan"),
            "rmse_px": float("nan"), "max_px": float("nan"),
            "expect": len(case["truth"]), "note": "未检出面板",
        })
        return

    if case["kind"] == "panels":
        # 面板按阅读顺序聚类排序（与 detect 内部一致，此处独立实现以便交叉验证）
        panels = sorted(det.panels, key=lambda p: (round(p.top / 6.0), p.left))
        jobs = []
        for i, p in enumerate(panels[: len(case["panels"])]):
            spec = case["panels"][i]
            jobs.append((f"p{i}", tuple(p.box), spec["xlim"], spec["ylim"],
                         {f"p{i}": case["truth"][f"p{i}"]}))
    else:
        biggest = max(det.panels, key=lambda p: p.size[0] * p.size[1])
        jobs = [("main", tuple(biggest.box), case["xlim"], case["ylim"],
                 case["truth"])]

    for label, box, xlim, ylim, truth in jobs:
        calib = Calibration.from_box(
            *box, x_min=xlim[0], x_max=xlim[1], y_min=ylim[0], y_max=ylim[1]
        )
        # 与 CLI 默认一致：标定用完整 box，提取区域向内缩 2px 以避开轴框线
        inset = 2.0
        region = (box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset)
        series = extract_series(img.rgb, region, background=img.background_rgb,
                                kind="auto", tol=64)
        data = digitize(series, calib)
        expect = len(truth) if isinstance(truth, dict) else 1
        ok = len(data) == expect
        rows.append({
            "case": case["kind"], "series": f"<检出序列数>{label}",
            "mode": "E/auto-color", "n": len(data),
            "rmse": float("nan"), "max": float("nan"),
            "rmse_px": float("nan"), "max_px": float("nan"),
            "expect": expect, "note": "" if ok else "序列数不符",
        })
        if not ok:
            continue
        # 序列数正确时，进一步验证是否与真值序列一一对应。
        # 注意：自动路径的序列名是 cN_hex，输出顺序按像素量降序，与真值字典顺序
        # 无关，因此绝不能按位置硬配对（那样会把 sin 与 cos 错配，RMSE 虚高两个
        # 数量级）。此处用最小代价贪心匹配。
        truth_items = list(truth.items()) if isinstance(truth, dict) else [("p", truth)]
        cand: list[tuple[float, int, int]] = []
        for di, d in enumerate(data):
            for ti, (nm, tv) in enumerate(truth_items):
                if case["kind"] == "scatter":
                    cost, _, _ = scatter_errors(list(zip(d.x, d.y)), tv["x"], tv["y"])
                else:
                    cost, _ = curve_errors(d.x, d.y, tv["x"], tv["y"])
                if cost == cost:  # 非 NaN
                    cand.append((cost, di, ti))
        cand.sort()
        used_d: set[int] = set()
        used_t: set[int] = set()
        for cost, di, ti in cand:
            if di in used_d or ti in used_t:
                continue
            used_d.add(di)
            used_t.add(ti)
            d = data[di]
            nm, tv = truth_items[ti]
            if case["kind"] == "scatter":
                md, mx, n = scatter_errors(list(zip(d.x, d.y)), tv["x"], tv["y"])
            else:
                md, mx = curve_errors(d.x, d.y, tv["x"], tv["y"])
                n = d.n
            ppu = max(px_per_unit(calib, "x"), px_per_unit(calib, "y"))
            rows.append({
                "case": case["kind"], "series": f"{nm}{label}", "mode": "E/auto-color",
                "n": n, "rmse": md, "max": mx,
                "rmse_px": md * ppu, "max_px": mx * ppu, "expect": None, "note": "",
            })


def main() -> int:
    truth = json.loads((TB / "truth.json").read_text(encoding="utf-8"))
    rows: list[dict] = []
    for case in truth["cases"]:
        kind = case["kind"]
        if kind == "multicurve":
            line_case(case, rows, "A")
        elif kind == "scatter":
            scatter_case(case, rows, "B")
        elif kind == "errorbar":
            errorbar_case(case, rows, "C")
        elif kind == "panels":
            panels_case(case, rows, "D")
        auto_color_case(case, rows)

    (OUTDIR / "accuracy.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    hdr = (f"{'case':10s} {'series':18s} {'mode':22s} {'n':>5s} {'RMSE':>10s} "
           f"{'MAX':>10s} {'RMSE_px':>9s} {'MAX_px':>8s} {'expect':>6s}")
    print(hdr)
    print("-" * len(hdr))
    bad: list[str] = []
    for r in rows:
        def f(v: float, w: int = 10) -> str:
            return f"{v:>{w}.5g}" if v == v else f"{'--':>{w}}"
        exp = r.get("expect")
        exp_s = f"{exp:>6d}" if isinstance(exp, int) else f"{'--':>6}"
        note = r.get("note") or ""
        print(
            f"{r['case']:10s} {r['series']:18s} {r['mode']:22s} {r['n']:>5d} "
            f"{f(r['rmse'])} {f(r['max'])} {f(r['rmse_px'], 9)} {f(r['max_px'], 8)} "
            f"{exp_s} {note}"
        )
        if note:
            bad.append(f"{r['case']}/{r['series']}: {note} (检出 {r['n']}, 期望 {exp})")
    print(f"\n明细已存: {OUTDIR / 'accuracy.json'}")
    if bad:
        print("\n[序列数不符]")
        for b in bad:
            print("  " + b)
        return 1
    print("\n[序列数核对] 全部用例的检出序列数与真值一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
