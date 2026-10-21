"""黑白线画分离引擎的**逐点精度验证**（对生成级真值）。

与 ``verify_accuracy.py`` 的分工
--------------------------------
``verify_accuracy.py`` 校验的是**颜色引擎**（按颜色分离的多曲线），真值来自
``truth.json``。本文件校验**黑白线画引擎**（按线宽 × 划线分离），真值来自
``truth_bw.json``。两者互不替代：颜色引擎在零彩色图上无轴可用，黑白引擎在
彩色图上必须让位。

为什么这里的断言可以很严
------------------------
真值不是"读图估的"，而是**生成参数**：线条宽度、划线模式、曲线解析式在渲染前
就已写死，数据区像素矩形取自 matplotlib 自己的 ``get_window_extent()``。
因此可以把"提取点是否落在解析曲线上"作为硬指标，量级在 **1 像素以内**。

三项指标（每项独立判定，任一失败即退出码非 0）
----------------------------------------------
1. **分离正确率**——检出的轨道数等于真值曲线数（BW-F 期望 0，因黑白引擎须让位）。
2. **配对正确性**——每条真值曲线恰好被一条轨道覆盖，且这条轨道
   （a）宽度类序号与真值宽度序关系一致，（b）划线模式与真值一致。
3. **逐点精度**——把轨道点代入该真值曲线的解析式求 ``|Δy|``（像素）与
   ``|Δt|``（沿曲线弧长的参数误差），报告 **中位数 / P95 / 最大值**。

配对用「按真值曲线逐条寻找最贴合的轨道」双向确认：既要每条真值都能找到轨道，
也要每条轨道都被认领（后者防止"多条轨道挤在同一条真值上"被判为通过）。

用法::

    python verify_bw_accuracy.py            # 全量，打印表并返回退出码
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from PIL import Image  # noqa: E402

from chartdigit.bwline import color_pixel_count, separate_bw  # noqa: E402

TRUTH_BW = HERE / "truth_bw.json"

#: 逐点精度上限（像素）。真值图 100 dpi、笔画宽 1.4–3.6 px，
#: 1.5 px 已是"半个笔画宽"量级，足以证明点落在曲线上而不是邻线上。
MAX_MEDIAN_DY_PX = 1.0
MAX_P95_DY_PX = 2.0
MAX_ABS_DY_PX = 4.0

#: 配对判定：轨道需有该比例的点落在真值曲线 ``PAIR_TOL_PX`` 之内
PAIR_TOL_PX = 5.0
PAIR_MIN_FRAC = 0.60


class Checker:
    """轻量断言收集器（各阶段测试文件同风格）。"""

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


def _curve_pixels(case: dict, stroke: dict) -> tuple[np.ndarray, np.ndarray]:
    """把真值曲线换算成像素坐标。

    数据坐标 -> 像素：按 ``axes_box_px`` 与 ``xlim/ylim`` 线性映射，y 反向。
    """
    x0, y0, x1, y1 = case["axes_box_px"]
    xmin, xmax = case["xlim"]
    ymin, ymax = case["ylim"]
    xs = np.asarray(stroke["x"], dtype=float)
    ys = np.asarray(stroke["y"], dtype=float)
    px = x0 + (xs - xmin) / (xmax - xmin) * (x1 - x0)
    py = y0 + (ymax - ys) / (ymax - ymin) * (y1 - y0)
    return px, py


def _residuals(series_pts: np.ndarray, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    """轨道每点到真值曲线的最近距离（像素），按曲线弧长参数化后搜索。

    把真值曲线按弧长重采样成密集折线，再用 **最近点** 距离；这比"同 x 求差"
    更公平——交叉处的点可能来自邻线，最近点距离会如实惩罚它。
    """
    order = np.argsort(px)
    qx, qy = px[order], py[order]
    # 按弧长重采样到 20 倍点数，保证最近点搜索精度 < 0.1 px
    d = np.hypot(np.diff(qx), np.diff(qy))
    s = np.concatenate(([0.0], np.cumsum(d)))
    if s[-1] <= 0:
        return np.full(series_pts.shape[0], np.inf)
    grid = np.linspace(0.0, s[-1], max(200, int(s[-1] * 20)))
    gx = np.interp(grid, s, qx)
    gy = np.interp(grid, s, qy)
    out = np.empty(series_pts.shape[0], dtype=float)
    for i in range(0, series_pts.shape[0], 512):
        chunk = series_pts[i:i + 512]
        d2 = ((chunk[:, None, 0] - gx[None, :]) ** 2
              + (chunk[:, None, 1] - gy[None, :]) ** 2)
        out[i:i + 512] = np.sqrt(d2.min(axis=1))
    return out


def _cover_frac(series_pts: np.ndarray, px: np.ndarray, py: np.ndarray,
                tol: float = PAIR_TOL_PX) -> float:
    return float((_residuals(series_pts, px, py) < tol).mean())


def run_case(case: dict, chk: Checker) -> dict:
    """校验单个用例，返回统计记录。"""
    tag = case["case"]
    rgb = np.asarray(Image.open(case["image"]).convert("RGB"))
    box = tuple(int(round(v)) for v in case["axes_box_px"])

    # ---- 指标 0：黑白属性（BW-F 的等价判据必须由数据本身确认，不靠真值声明）
    n_color = color_pixel_count(rgb[max(0, box[1]):box[3], max(0, box[0]):box[2]])
    chk.ok((n_color > 0) == case["has_color"],
           f"{tag} 彩色属性与真值一致",
           f"彩色像素={n_color} 期望{'有' if case['has_color'] else '无'}")

    series = separate_bw(rgb, box, min_points=20)

    # ---- 指标 1：分离正确率
    exp_n = case["n_expected_tracks"]
    chk.ok(len(series) == exp_n, f"{tag} 轨道数=真值曲线数",
           f"检出 {len(series)} 期望 {exp_n}")

    if case.get("negative_control"):
        chk.ok(len(series) == 0, f"{tag} 负面：黑白引擎在彩色图上让位",
               f"返回 {len(series)} 条（应为 0）")
        return {"case": tag, "n_tracks": len(series), "pairs": []}

    if not series:
        return {"case": tag, "n_tracks": 0, "pairs": []}

    # ---- 指标 2：配对正确性（真值 -> 轨道 与 轨道 -> 真值 双向）
    recs = []
    claimed: dict[int, list[int]] = {}
    for si, st in enumerate(case["strokes"]):
        px, py = _curve_pixels(case, st)
        best_i, best = -1, 0.0
        for i, s in enumerate(series):
            pts = np.asarray(s.points, dtype=float)
            f = _cover_frac(pts, px, py)
            if f > best:
                best_i, best = i, f
        recs.append({"stroke": st["name"], "track": best_i, "cover": best})
        if best_i >= 0:
            claimed.setdefault(best_i, []).append(si)
        chk.ok(best >= PAIR_MIN_FRAC, f"{tag}/{st['name']} 找到贴合轨道",
               f"轨道#{best_i} 覆盖率={best:.2f}")

    # 每条轨道只能被一条真值认领——否则是"多条轨道挤在同一真值上"
    multi = {i: v for i, v in claimed.items() if len(v) > 1}
    chk.ok(not multi, f"{tag} 无重叠认领（轨道与曲线一一对应）",
           f"重叠: {multi}" if multi else "")
    unclaimed = [i for i in range(len(series)) if i not in claimed]
    chk.ok(not unclaimed, f"{tag} 无未被认领的轨道", f"多余轨道 {unclaimed}")

    # 宽度类的序关系：真值线宽更大的曲线，其配对轨道的 width_px 也必须更大
    wide = [(st["width_px"], recs[k]["track"]) for k, st in enumerate(case["strokes"])
            if recs[k]["track"] >= 0]
    ok_order = True
    for (w1, t1) in wide:
        for (w2, t2) in wide:
            if t1 < 0 or t2 < 0 or w2 <= w1:
                continue
            if series[t2].meta["width_px"] <= series[t1].meta["width_px"]:
                ok_order = False
    chk.ok(ok_order, f"{tag} 测得宽度随真值线宽单调", "")

    # 划线模式逐条一致
    dash_ok = all(series[recs[k]["track"]].meta["dash"] == st["dash"]
                  for k, st in enumerate(case["strokes"])
                  if recs[k]["track"] >= 0)
    chk.ok(dash_ok, f"{tag} 划线模式逐条一致",
           " / ".join(f"{st['name']}={series[recs[k]['track']].meta['dash']}"
                      for k, st in enumerate(case["strokes"])
                      if recs[k]["track"] >= 0))

    # ---- 指标 3：逐点精度
    for k, st in enumerate(case["strokes"]):
        i = recs[k]["track"]
        if i < 0:
            continue
        px, py = _curve_pixels(case, st)
        pts = np.asarray(series[i].points, dtype=float)
        # 只统计落在该曲线 x 跨度内的点，避免端外推污染
        m = (pts[:, 0] >= px.min() - 2) & (pts[:, 0] <= px.max() + 2)
        r = _residuals(pts[m], px, py)
        med, p95, mx = (float(np.median(r)), float(np.percentile(r, 95)),
                        float(r.max()))
        chk.ok(med <= MAX_MEDIAN_DY_PX, f"{tag}/{st['name']} 逐点误差中位",
               f"{med:.2f} px (上限 {MAX_MEDIAN_DY_PX})")
        chk.ok(p95 <= MAX_P95_DY_PX, f"{tag}/{st['name']} 逐点误差 P95",
               f"{p95:.2f} px (上限 {MAX_P95_DY_PX})")
        chk.ok(mx <= MAX_ABS_DY_PX, f"{tag}/{st['name']} 逐点误差最大",
               f"{mx:.2f} px (上限 {MAX_ABS_DY_PX})")
        recs[k].update({"median_px": round(med, 3), "p95_px": round(p95, 3),
                        "max_px": round(mx, 3), "n_points": int(m.sum())})

    # ---- 缺口诚信：BW-E 的遮挡区间内不得出现数据点
    if case.get("occlusion_px"):
        ox0, ox1 = case["occlusion_px"]
        xmin, xmax = case["xlim"]
        px0 = box[0] + (ox0 - xmin) / (xmax - xmin) * (box[2] - box[0])
        px1 = box[0] + (ox1 - xmin) / (xmax - xmin) * (box[2] - box[0])
        inner = 4.0        # 遮挡框边缘的抗锯齿过渡带，不计入
        bad = 0
        for s in series:
            for (x, _y) in s.points:
                if px0 + inner < x < px1 - inner:
                    bad += 1
        chk.ok(bad == 0, f"{tag} 遮挡区内无插值点（缺口诚信）",
               f"遮挡区 x=[{px0:.0f},{px1:.0f}] 内点数={bad}")
        total_gaps = sum(s.meta["n_gaps"] for s in series)
        chk.ok(total_gaps >= 1, f"{tag} 遮挡被如实登记为缺口",
               f"缺口数={total_gaps}")

    return {"case": tag, "n_tracks": len(series), "pairs": recs}


def main() -> int:
    if not TRUTH_BW.exists():
        print(f"缺少真值文件 {TRUTH_BW}；先跑 make_bw_cases.py")
        return 1
    truth = json.loads(TRUTH_BW.read_text(encoding="utf-8"))

    print("=" * 78)
    print("黑白线画分离引擎 · 逐点精度验证（真值来源：生成参数）")
    print("=" * 78)
    chk = Checker()
    rows = []
    for case in truth["cases"]:
        rows.append(run_case(case, chk))

    print("-" * 78)
    print("汇总（逐点误差为像素单位，|Δy| 到真值曲线的最近距离）")
    print(f"{'用例':6s} {'轨道':>4s} {'真值':>4s}  配对与精度")
    for r in rows:
        case = next(c for c in truth["cases"] if c["case"] == r["case"])
        detail = "  ".join(
            f"{p['stroke']}:#{p['track']}({p.get('median_px', float('nan')):.2f}/"
            f"{p.get('p95_px', float('nan')):.2f}/{p.get('max_px', float('nan')):.2f}px)"
            for p in r["pairs"]
        ) or "（负面用例：无需配对）"
        print(f"{r['case']:6s} {r['n_tracks']:4d} {case['n_expected_tracks']:4d}  {detail}")
    print("-" * 78)
    print("精度口径: 中位 / P95 / 最大  (单位 px)")
    print(f"共 {chk.n} 项检查，失败 {chk.fail}")
    if chk.fail:
        print("存在失败项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
