"""对数坐标验证：半对数 / 双对数图的提取精度。

覆盖三件事：

1. **尺度分支确实生效** —— 每个用例同时跑「正确尺度」与「误用线性尺度」，
   后者必须产生数量级偏差，否则说明 `--log-x/--log-y` 是空操作。
2. **等效像素误差** —— 对数轴上「相对误差」不是与轴跨度无关的判据：1 个像素在
   跨 5 个数量级的轴上就是 3.2% 的相对误差。可比量度是
   「decade 误差 ÷ 每像素数量级」，直接与线性轴的 0.8px 对照。
3. **序列数** —— 对数图常配网格线。浅色抗锯齿候选色会在 `tol` 容差内吞掉浅灰
   网格线，产生一条「伪序列」（实测掩码 4246 像素中 4 条水平网格线各占 495 像素）。
   本脚本断言序列数为 1，覆盖 `drop_antialias_colors` 这一修复。

用例图由 `make_testcases.py` 统一生成（caseE/caseF），真值取自 `truth.json`。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from chartdigit import Calibration, detect, digitize, extract_series, load_image  # noqa: E402

OUTDIR = HERE / "verify_out"
OUTDIR.mkdir(exist_ok=True)

LOG_KINDS = ("semilog", "loglog")


def decades_per_px(ylo: float, yhi: float, box_h: float) -> float:
    """y 轴每个像素对应多少个数量级。"""
    return math.log10(yhi / ylo) / box_h


def axis_errors(ext_x, ext_y, tru_x, tru_y, xscale: str) -> tuple[float, float, float]:
    """把真值插值到提取点，返回 (相对 RMSE, 相对最大, decade RMSE)。

    插值尺度必须与轴一致：x 为线性轴时在 x 上插值，x 为对数轴时在 log(x) 上插值
    （对数 x 上按线性插值会在低端严重失真，并触发 log(0) 警告）。
    """
    ex = np.asarray(ext_x, float)
    ey = np.asarray(ext_y, float)
    tx = np.asarray(tru_x, float)
    ty = np.asarray(tru_y, float)
    o = np.argsort(tx)
    tx, ty = tx[o], ty[o]
    lo, hi = max(ex.min(), tx.min()), min(ex.max(), tx.max())
    keep = (ex >= lo) & (ex <= hi)
    if keep.sum() < 2:
        nan = float("nan")
        return nan, nan, nan
    if xscale == "log":
        ux, ut = np.log(ex[keep]), np.log(tx)
    else:
        ux, ut = ex[keep], tx
    log10_ref = np.interp(ux, ut, np.log10(ty))
    ln_ref = np.interp(ux, ut, np.log(ty))
    dec = log10_ref - np.log10(ey[keep])
    rel = (ey[keep] - np.exp(ln_ref)) / np.exp(ln_ref)
    return (float(np.sqrt(np.mean(rel ** 2))),
            float(np.max(np.abs(rel))),
            float(np.sqrt(np.mean(dec ** 2))))


def run_case(case: dict, rows: list[dict]) -> None:
    img = load_image(case["image"])
    det = detect(case["image"])
    if not det.panels:
        rows.append({"case": case["kind"], "mode": "-", "scale": "-", "n_series": 0,
                     "rel_rmse": float("nan"), "dec_rmse": float("nan"),
                     "eq_px": float("nan"), "dpp": 0.0, "note": "未检出面板"})
        return

    auto = tuple(max(det.panels, key=lambda p: p.size[0] * p.size[1]).box)
    variants = [("auto-box", auto), ("truth-box", tuple(case["axes_box_px"]))]
    tv = case["truth"]["power"]

    for tag, box in variants:
        dpp = decades_per_px(case["ylim"][0], case["ylim"][1], box[3] - box[1])
        inset = 2.0
        region = (box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset)
        series = extract_series(img.rgb, region, background=img.background_rgb,
                                kind="auto", tol=64)
        for scale_label, xs, ys in (
            ("正确尺度", case["xscale"], case["yscale"]),
            ("误用线性", "linear", "linear"),
        ):
            calib = Calibration.from_box(
                *box, x_min=case["xlim"][0], x_max=case["xlim"][1],
                y_min=case["ylim"][0], y_max=case["ylim"][1],
                xscale=xs, yscale=ys,
            )
            data = digitize(series, calib)
            if not data:
                continue
            d = data[0]
            r, mx, dec = axis_errors(d.x, d.y, tv["x"], tv["y"], xs)
            rows.append({
                "case": case["kind"], "mode": tag, "scale": scale_label,
                "n_series": len(series), "n": d.n,
                "rel_rmse": r, "rel_max": mx, "dec_rmse": dec,
                "eq_px": dec / dpp if dpp else float("nan"),
                "dpp": dpp, "note": "",
            })


def main() -> int:
    truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
    cases = [c for c in truth["cases"] if c["kind"] in LOG_KINDS]
    if not cases:
        print("truth.json 中未找到对数用例；请先运行 make_testcases.py")
        return 1

    rows: list[dict] = []
    for c in cases:
        run_case(c, rows)

    (OUTDIR / "log_axes.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("每像素数量级（它决定『相对误差』的尺度，故须用等效像素做可比判据）")
    for c in cases:
        b = c["axes_box_px"]
        span = math.log10(c["ylim"][1] / c["ylim"][0])
        dpp = decades_per_px(c["ylim"][0], c["ylim"][1], b[3] - b[1])
        print(f"  {c['kind']:8s} y 跨 {span:.0f} 个数量级 / {b[3]-b[1]:.0f}px  ->  "
              f"1px = {dpp:.5f} decade = {100 * (10 ** dpp - 1):.2f}%")

    hdr = (f"\n{'case':9s} {'口径':10s} {'尺度':9s} {'序列数':>5s} {'相对RMSE':>11s} "
           f"{'decadeRMSE':>11s} {'等效像素':>9s}")
    print(hdr)
    print("-" * (len(hdr) - 1))
    for r in rows:
        print(f"{r['case']:9s} {r['mode']:10s} {r['scale']:9s} {r['n_series']:>5d} "
              f"{r['rel_rmse']:>11.3e} {r['dec_rmse']:>11.3e} {r['eq_px']:>8.3f}px"
              + (f"  {r['note']}" if r["note"] else ""))

    fails: list[str] = []
    for r in rows:
        label = f"{r['case']}/{r['mode']}/{r['scale']}"
        if r["scale"] == "正确尺度":
            if r["n_series"] != 1:
                fails.append(f"{label}: 检出序列数 {r['n_series']} ≠ 1")
            if not (r["eq_px"] < 2.0):
                fails.append(f"{label}: 等效像素 {r['eq_px']:.3f} ≥ 2px")
        elif not (r["rel_rmse"] > 1.0):
            fails.append(f"{label}: 误用线性尺度竟未产生显著偏差"
                         f"（相对RMSE {r['rel_rmse']:.3e}），尺度分支可能未生效")

    print(f"\n明细已存: {OUTDIR / 'log_axes.json'}")
    if fails:
        print("\n[对数轴校验失败]")
        for f in fails:
            print("  " + f)
        return 1
    print("\n[对数轴校验] 通过 —— 正确尺度下等效像素 < 2px，"
          "误用线性尺度产生数量级偏差（证明尺度分支生效），序列数正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
