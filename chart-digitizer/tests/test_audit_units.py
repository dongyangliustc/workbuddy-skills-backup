"""标定审核（尺度标注 + 几何交叉校验 + 人工确认）的确定性单元测试。

与本技能其它单元测试同一约定：**不依赖 matplotlib 光栅化的偶然结果**，全部用合成
数组直接构造几何，使判据与渲染无关。每个测试对应一处实测缺陷或一条明确的设计承诺。

覆盖的实测缺陷
--------------
A. 固定扫描带 ``(2,12)`` 会把**刻度数字标签**当刻度捞进来。实测 caseA：刻度在轴线下
   1..4 px，标签起于 10 px 处，第 12 px 已落在字形里 → 线性轴被误判成 ``integer-multiple``
   （最大/最小间隙=20），间距规整度 1.0 → 0.833。
B. ``find_ticks`` 把 ``lo``/``hi`` 当半开区间，**压在轴框边界上的首末刻度被丢掉**。
   实测 matplotlib 对数轴首末主刻度恰好如此 → 刻度数 6 变 4。
C. 「带内求和 ≥3 px」只捞得到长约 3 px 的**主刻度**，漏掉仅 1 px 长的**次刻度**；
   而次刻度正是"非等距 ⇒ 该轴不是线性刻度"这一硬判据的唯一几何证据。
D. ``Calibration.from_box`` 构造的两个 y 锚点**共用同一个 px**（都在轴框左边），
   换算 y 刻度值时必须取 ``py``；误取 ``px`` 会得到零跨度、静默算不出任何刻度值。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from chartdigit import Calibration  # noqa: E402
from chartdigit.audit import (  # noqa: E402
    PATTERN_INSUFFICIENT,
    PATTERN_INTEGER,
    PATTERN_NONUNIFORM,
    PATTERN_UNIFORM,
    SOURCE_DEFAULT,
    SOURCE_EXPLICIT,
    audit_axis,
    build_audit,
    classify_spacing,
)
from chartdigit.detect import auto_tick_band, find_ticks  # noqa: E402

failures: list[str] = []
count = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global count
    count += 1
    if cond:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}  {detail}")
        failures.append(f"{label} {detail}".strip())


# ---------------------------------------------------------------- 1. 间距模式判定

def test_classify_spacing() -> None:
    """三种间距模式 + 刻度不足，全部用确定性刻度序列。"""
    print("== classify_spacing ==")

    linear = [78, 178, 277, 377, 476, 576]          # 实测 caseA x 轴
    r = classify_spacing(linear)
    check("线性轴等距 → uniform", r["pattern"] == PATTERN_UNIFORM, f"{r}")

    # 实测 caseE y 轴（对数）：整数量级主刻度 + 2..9 次刻度
    dec = [0, 78, 82, 86, 91, 96, 104, 113, 127, 150, 228, 232, 236, 241, 246, 254, 263,
           277, 300, 378, 382, 386, 391, 396, 404, 413, 427, 450]
    r2 = classify_spacing(dec)
    check("对数轴（主刻度+次刻度）→ nonuniform", r2["pattern"] == PATTERN_NONUNIFORM,
          f"{r2}")
    check("nonuniform 给出最大/最小间隙比", r2["max_gap_ratio"] > 4.0,
          f"gap_ratio={r2['max_gap_ratio']}")

    sparse = [78, 178, 178 + 200, 178 + 400]        # 漏检一半刻度 → 整数倍
    r3 = classify_spacing(sparse)
    check("漏检刻度 → integer-multiple（仍属等距族，不报警）",
          r3["pattern"] == PATTERN_INTEGER, f"{r3}")

    check("刻度不足 3 个 → insufficient",
          classify_spacing([10, 20])["pattern"] == PATTERN_INSUFFICIENT)
    check("空序列不抛异常", classify_spacing([])["pattern"] == PATTERN_INSUFFICIENT)


# ---------------------------------------------------------------- 2. 警告矩阵

def _warn_text(a) -> str:
    return " | ".join(a.warnings)


def test_audit_warning_matrix() -> None:
    """非等距 + 声明线性 = 硬警告；非等距 + 声明对数 = 无警告。"""
    print("== audit_axis 警告矩阵 ==")

    nonuni = [0, 78, 82, 86, 91, 96, 104, 113, 127, 150, 228]
    uni = [78, 178, 277, 377, 476, 576]

    a = audit_axis("y", "linear", SOURCE_EXPLICIT, 0.01, 1000.0, nonuni)
    check("非等距 + 声明线性 → 报「很可能不是线性刻度」",
          "很可能不是线性刻度" in _warn_text(a) and "log-y" in _warn_text(a),
          _warn_text(a))

    b = audit_axis("y", "log", SOURCE_EXPLICIT, 0.01, 1000.0, nonuni)
    check("非等距 + 声明对数 → 无警告", not b.warnings, _warn_text(b))

    c = audit_axis("y", "log", SOURCE_EXPLICIT, 0.01, 1000.0, uni)
    check("等距 + 声明对数 → 软提示「请目视确认」",
          len(c.warnings) == 1 and "目视确认" in c.warnings[0], _warn_text(c))

    d = audit_axis("x", "linear", SOURCE_EXPLICIT, 0.0, 10.0, uni)
    check("等距 + 显式声明线性 → 完全无警告", not d.warnings, _warn_text(d))

    e = audit_axis("x", "linear", SOURCE_DEFAULT, 0.0, 10.0, uni)
    check("尺度来自默认值 → 单独提示「未明确声明」",
          len(e.warnings) == 1 and "未明确声明" in e.warnings[0], _warn_text(e))

    f = audit_axis("x", "linear", SOURCE_EXPLICIT, 0.0, 10.0, [10, 20])
    check("刻度不足 → 明确说无法校验，而非假装通过",
          f.pattern == PATTERN_INSUFFICIENT and "无法做几何交叉校验" in _warn_text(f),
          _warn_text(f))


# ---------------------------------------------------------------- 3. y 锚点坐标陷阱

def test_y_anchor_uses_py() -> None:
    """缺陷 D：y 刻度是**行**坐标，换算必须用锚点的 py；两者在 from_box 下 px 相同。"""
    print("== build_audit 的 y 锚点坐标 ==")

    cal = Calibration.from_box(58, 22, 576, 411,
                               x_min=0.0, x_max=10.0,
                               y_min=0.01, y_max=1000.0,
                               xscale="linear", yscale="log")

    check("陷阱确实存在：两个 y 锚点的 px 相同、py 不同",
          cal.ymin.px == cal.ymax.px and cal.ymin.py != cal.ymax.py,
          f"px={cal.ymin.px} vs {cal.ymax.px}; py={cal.ymin.py} vs {cal.ymax.py}")

    # 实测 caseE y 轴：6 个主刻度行位置
    ticks = [22, 100, 178, 256, 334, 411]
    au = build_audit((58, 22, 576, 411), "linear", SOURCE_EXPLICIT, (0.0, 10.0),
                     "log", SOURCE_EXPLICIT, (0.01, 1000.0),
                     tick_x=[], tick_y=ticks, calib=cal)

    check("y 轴「每格」可算出（若误用 px 则零跨度 → None）",
          au.y.step_decades is not None, f"step_decades={au.y.step_decades}")
    check("y 轴每格 ≈ 1 个数量级",
          au.y.step_decades is not None and abs(au.y.step_decades - 1.0) < 0.02,
          f"got {au.y.step_decades}")
    check("y 轴刻度值首末 ≈ 1000 与 0.01",
          len(au.y.tick_values) == 6
          and abs(au.y.tick_values[-1] - 1000.0) < 20
          and abs(au.y.tick_values[0] - 0.01) < 0.0002,
          f"{au.y.tick_values}")
    check("x 轴无刻度时不虚构刻度值", not au.x.tick_values and au.x.step_units is None)

    # 直接锁住失效机理：把 y 锚点的 px 当作沿轴坐标 → 零跨度 → 静默算不出任何值。
    # （这不是假设，是首版实现的真实 bug；修法是改取 py。）
    from chartdigit.audit import _tick_value
    check("零跨度返回 None，不静默编造刻度值",
          _tick_value(50, cal.ymin.px, cal.ymin.value,
                      cal.ymax.px, cal.ymax.value, "log") is None,
          f"px 同为 {cal.ymin.px}")
    check("改取 py 后能算出正常刻度值",
          _tick_value(100, cal.ymin.py, cal.ymin.value,
                      cal.ymax.py, cal.ymax.value, "log") is not None)
    check("对照：py 跨度非零（缺陷不成立的前提）",
          cal.ymin.py != cal.ymax.py, f"py={cal.ymin.py} vs {cal.ymax.py}")


def test_step_suppressed_when_nonuniform() -> None:
    """非等距时不得给出「每格」——46 个长短刻度取中位数会得到无意义的数字。"""
    print("== 非等距时抑制「每格」 ==")

    cal = Calibration.from_box(0, 0, 100, 100,
                               x_min=0.0, x_max=10.0,
                               y_min=0.0, y_max=100.0)
    au = build_audit((0, 0, 100, 100), "linear", SOURCE_EXPLICIT, (0.0, 10.0),
                     "log", SOURCE_EXPLICIT, (0.0, 100.0),
                     tick_x=[0, 10, 20, 35, 60, 100], tick_y=[], calib=cal)
    check("x 轴确为非等距", au.x.pattern == PATTERN_NONUNIFORM, au.x.pattern)
    check("非等距时 step_text 为 None", au.x.step_text is None, f"{au.x.step_text}")
    check("但仍列出各刻度值供人工对照", au.x.tick_values_text is not None,
          f"{au.x.tick_values_text}")
    check("刻度值文本列出全部 6 个", au.x.tick_values_text.count("/") == 5,
          f"{au.x.tick_values_text}")

    au2 = build_audit((0, 0, 100, 100), "linear", SOURCE_EXPLICIT, (0.0, 10.0),
                      "linear", SOURCE_EXPLICIT, (0.0, 100.0),
                      tick_x=[0, 20, 40, 60, 80, 100], tick_y=[], calib=cal)
    check("等距时 step_text 给出每格 2.0",
          au2.x.step_text is not None and abs(float(au2.x.step_text) - 2.0) < 0.01,
          f"{au2.x.step_text}")


# ---------------------------------------------------------------- 4. 刻度检出：pad

def test_find_ticks_pad() -> None:
    """缺陷 B：压在轴框边界上的刻度被半开区间排除。"""
    print("== find_ticks 的 pad ==")

    m = np.zeros((60, 200), dtype=bool)
    m[30, :] = True                                   # 轴线在 row 30
    for c in (20, 60, 100, 140, 180):                 # 5 个刻度，row 31..34
        m[31:35, c] = True

    lo, hi = 20, 180                                  # 首末刻度恰在区间端点上
    no_pad = find_ticks(m, 30, lo, hi, orientation="x", band=(1, 6), pad=0)
    with_pad = find_ticks(m, 30, lo, hi, orientation="x", band=(1, 6), pad=1)

    check("pad=0 丢掉上端刻度（缺陷形态）", 180 not in no_pad, f"{no_pad}")
    check("pad=0 只得到 4 个", len(no_pad) == 4, f"{no_pad}")
    check("pad=1 得到全部 5 个刻度", len(with_pad) == 5, f"{with_pad}")
    check("pad=1 位置正确", with_pad == [20, 60, 100, 140, 180], f"{with_pad}")
    check("pad=1 相对 pad=0 只多出端点刻度，不引入新列",
          set(with_pad) - set(no_pad) == {180}, f"{set(with_pad) ^ set(no_pad)}")


# ---------------------------------------------------------------- 5. 自适应扫描带

def test_auto_tick_band_excludes_labels() -> None:
    """缺陷 A：固定带宽把刻度数字标签当刻度。"""
    print("== auto_tick_band ==")

    m = np.zeros((60, 300), dtype=bool)
    m[30, 40:260] = True                              # 轴线
    tick_cols = [40, 80, 120, 160, 200, 240]
    for c in tick_cols:                               # 刻度：轴线外 1..4 px
        m[31:35, c] = True
    # 标签字形：轴线外 9..18 px。落在固定带 (2,12)→ 行 32..42 内的行有 4 行（≥ min_len=3），
    # 因此固定带必然把它们当成刻度。实测 caseA 的标签起于轴线下 10 px，同理被误捞。
    for c in range(35, 265, 6):
        m[39:49, c] = True
        m[39:49, c + 1] = True

    band = auto_tick_band(m, 30, 40, 260, orientation="x")
    check("自适应带止于标签之前", band == (1, 4), f"band={band}")

    ticks = find_ticks(m, 30, 40, 260, orientation="x", band=band or (1, 4),
                       pad=1, mode="any")
    check("自适应带下刻度数正确（无标签混入）", ticks == tick_cols, f"{ticks}")

    fixed = find_ticks(m, 30, 40, 260, orientation="x", band=(2, 12),
                       min_len=3, pad=1)
    check("对照：固定带 (2,12) 确实多捞出标签列（缺陷形态）",
          len(fixed) > len(tick_cols), f"fixed={len(fixed)} vs {len(tick_cols)}")

    r = classify_spacing(ticks)
    check("自适应带下判为 uniform", r["pattern"] == PATTERN_UNIFORM, f"{r}")
    check("对照：固定带下判为 integer-multiple（间距被垃圾刻度污染）",
          classify_spacing(fixed)["pattern"] != PATTERN_UNIFORM,
          f"{classify_spacing(fixed)}")

    check("无刻度时返回 None（交调用方回退）",
          auto_tick_band(np.zeros((60, 200), bool), 30, 0, 200, orientation="x") is None)


def test_find_ticks_mode_any() -> None:
    """缺陷 C：sum 模式漏掉次刻度，any 模式才行——次刻度是硬判据的唯一证据。"""
    print("== find_ticks 的 mode ==")

    m = np.zeros((60, 300), dtype=bool)
    m[30, 40:260] = True
    majors = [40, 140, 240]                           # 主刻度：长 4 px（行 31..34）
    for c in majors:
        m[31:35, c] = True
    # 次刻度：仅 1 px 长（行 31），位置取 log10(k)·每数量级宽度 100 px（k=2..9），
    # 即真实对数轴的次刻度几何
    offsets = [30, 48, 60, 70, 78, 85, 90, 95]
    minors = [c + o for c in (40, 140) for o in offsets]
    for c in minors:
        m[31, c] = True

    s = find_ticks(m, 30, 40, 260, orientation="x", band=(1, 5),
                   min_len=3, pad=1, mode="sum")
    a = find_ticks(m, 30, 40, 260, orientation="x", band=(1, 5),
                   pad=1, mode="any")

    check("sum 模式只得到主刻度（缺陷形态）", s == majors, f"{s}")
    check("any 模式得到主+次全部刻度", a == sorted(majors + minors), f"n={len(a)}")
    check("sum 模式判为 uniform → 漏掉「非等距」证据",
          classify_spacing(s)["pattern"] == PATTERN_UNIFORM)
    check("any 模式判为 nonuniform → 硬判据成立",
          classify_spacing(a)["pattern"] == PATTERN_NONUNIFORM,
          f"{classify_spacing(a)}")

    # 已知限制（有意为之，不是缺陷）：间隔成 1:2 整数倍的轴**不报警**。
    # 因为"漏检几个刻度"与"本来就是这个步长"在几何上无法区分，报出来只会是误报。
    limit = [40, 60, 80, 100, 120, 160, 200]
    r = classify_spacing(limit)
    check("已知限制：1:2 整数倍间距判为 integer-multiple（不报警）",
          r["pattern"] == PATTERN_INTEGER, f"{r}")
    au = audit_axis("x", "linear", SOURCE_EXPLICIT, 0.0, 10.0, limit)
    check("已知限制：该情形只余「目视确认」提示，无硬警告",
          all("很可能不是线性刻度" not in w for w in au.warnings),
          f"{au.warnings}")


# ---------------------------------------------------------------- 6. 人工确认留痕

def test_confirm_provenance() -> None:
    print("== 人工确认留痕 ==")

    cal = Calibration.from_box(0, 0, 100, 100,
                               x_min=0.0, x_max=10.0,
                               y_min=0.0, y_max=100.0)
    au = build_audit((0, 0, 100, 100), "linear", SOURCE_DEFAULT, (0.0, 10.0),
                     "linear", SOURCE_DEFAULT, (0.0, 100.0),
                     tick_x=[0, 50, 100], tick_y=[0, 50, 100], calib=cal)
    d = au.to_dict()
    check("未确认时 ready_for_downstream=False", d["ready_for_downstream"] is False)
    check("未确认时 reviewed_at 为 None", d["reviewed_at"] is None)
    check("审核记录含两条来源警告", len(d["warnings"]) == 2, f"{d['warnings']}")

    block0 = "\n".join(au.render())
    check("未确认时审核块标「未确认」", "审核状态: 未确认" in block0, block0[:120])
    check("审核块含逐刻度值对照行", "按当前标定换算的刻度值" in block0)

    au.confirm()
    d2 = au.to_dict()
    check("确认后 ready_for_downstream=True", d2["ready_for_downstream"] is True)
    check("确认后写入时间戳", isinstance(d2["reviewed_at"], str) and d2["reviewed_at"])
    check("确认不消除警告（人签字 ≠ 机器改口）", len(d2["warnings"]) == 2)

    block = "\n".join(au.render())
    check("审核块含已确认状态与时间戳", "已确认" in block, block[:200])
    check("JSON 中保留两轴尺度与来源",
          d2["x"]["source"] == SOURCE_DEFAULT and d2["y"]["scale"] == "linear")
    check("JSON 中刻度值按升序列出", d2["x"]["tick_values"] == [0.0, 5.0, 10.0],
          f"{d2['x']['tick_values']}")


def main() -> int:
    test_classify_spacing()
    test_audit_warning_matrix()
    test_y_anchor_uses_py()
    test_step_suppressed_when_nonuniform()
    test_find_ticks_pad()
    test_auto_tick_band_excludes_labels()
    test_find_ticks_mode_any()
    test_confirm_provenance()
    print(f"\n{'=' * 60}")
    print(f"共 {count} 项检查，失败 {len(failures)} 项")
    if failures:
        for f in failures:
            print("  [FAIL] " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
