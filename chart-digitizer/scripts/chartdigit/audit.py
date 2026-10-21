"""标定审核：尺度来源标注、几何交叉校验、人工确认留痕。

设计动机
--------

标定的两个要素——**数值范围**与**尺度类型（线性/对数）**——都无法从图像几何中直接读出，
必须由外部提供。其中范围填错、尺度漏标都会造成**系统性**偏差，而输出外观完全正常
（单调、平滑、像真数据），因此必须由人工把关。

本模块把"人工把关"从一句期望变成三件可执行的事：

1. **标注来源**：区分「用户明确声明」与「工具默认」。默认的 linear 是最危险的情形——
   若图上其实是对数刻度，数值会差 3–5 个数量级，而报告里原本看不出这个默认是"没想过"
   还是"确认过"。
2. **几何交叉校验**：刻度线的**间距模式**携带尺度信息。线性轴的刻度必然等距，或成整数倍
   （漏检若干刻度）。因此「非等距且非整数倍」可**判定该轴不是线性刻度**——这是一条可靠
   的单向推断，能在用户漏加 ``--log-y`` 时给出硬警告。反向推断不成立（仅含整数量级主刻度
   的对数轴与线性轴在几何上无法区分），故只作软提示，不含糊其辞。
3. **确认留痕**：``--confirm-scale`` 把"已核对"写进交付物，形成审计痕迹，使"人工审核"
   成为可追溯的事实而非默认假设。

注意：本模块**不做 OCR、不猜数值**。几何校验只判断"刻度间距模式与所声明的尺度是否自洽"，
不试图读取刻度标签上的数字。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

import numpy as np

from .detect import uniform_tick_spacing

SOURCE_EXPLICIT = "用户指定"
SOURCE_DEFAULT = "默认"

PATTERN_UNIFORM = "uniform"
PATTERN_INTEGER = "integer-multiple"
PATTERN_NONUNIFORM = "nonuniform"
PATTERN_INSUFFICIENT = "insufficient"

_PATTERN_TEXT = {
    PATTERN_UNIFORM: "等距",
    PATTERN_INTEGER: "等距的整数倍（疑有刻度漏检）",
    PATTERN_NONUNIFORM: "非等距",
    PATTERN_INSUFFICIENT: "刻度不足，无法校验",
}

# 判定「间隙是最小间隙的整数倍」时容许的相对偏差
_INT_MULT_TOL = 0.20

# 审核块中最多列出的刻度值个数（超出则首尾各半、中间省略）
_MAX_TICKS_SHOWN = 8


@dataclass
class AxisAudit:
    """单条轴的标定审核记录。"""

    axis: str                       # "x" | "y"
    scale: str                      # "linear" | "log"
    source: str                     # SOURCE_EXPLICIT | SOURCE_DEFAULT
    vmin: float
    vmax: float
    n_ticks: int = 0
    pattern: str = PATTERN_INSUFFICIENT
    regularity: float = 0.0
    # 最大间隙 / 最小间隙：非等距程度的量化，人工据此判断"差得有多离谱"
    gap_ratio: float | None = None
    # 每格数值增量（线性轴，数据单位）——由**标定换算**得到，不是 (vmax-vmin)/(n-1) 硬算
    step_units: float | None = None
    # 每格数量级增量（对数轴）
    step_decades: float | None = None
    # 检出的刻度按当前标定换算出的数据值（升序），供人工与图中标注的数字逐一对齐
    tick_values: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def pattern_text(self) -> str:
        t = _PATTERN_TEXT.get(self.pattern, self.pattern)
        if self.pattern == PATTERN_NONUNIFORM and self.gap_ratio is not None:
            t += f"（最大/最小间隙={self.gap_ratio:g}）"
        return t

    @property
    def step_text(self) -> str | None:
        """「每格」的人类可读表述（按尺度选择单位）。

        仅在间距**等距或成整数倍**时给出：刻度非等距时"每格"没有单一含义（实测对数轴
        46 个长短刻度取中位数会得到毫无意义的数字），此处刻意返回 ``None`` 以免误导。
        """
        if self.pattern not in (PATTERN_UNIFORM, PATTERN_INTEGER):
            return None
        if self.scale == "log" and self.step_decades is not None:
            d = self.step_decades
            return f"{d:.4g} 个数量级（{10 ** d:.4g} 倍）"
        if self.step_units is not None:
            return _fmt(self.step_units)
        return None

    @property
    def tick_values_text(self) -> str | None:
        """全部检出刻度经当前标定换算出的数据值。

        **刻意列全、而不是只给首末**：首末刻度常恰好压在轴框角上，与声明的范围端点
        必然相等（循环论证，无信息量）；真正能暴露尺度/范围错误的是中间刻度——例如把
        对数轴误当线性，中间刻度会算成 200/400/601/801，而图上印的是 0.1/1/10/100。
        """
        if len(self.tick_values) < 2:
            return None
        parts = [_fmt(v) for v in self.tick_values]
        if len(parts) > _MAX_TICKS_SHOWN:
            head = _MAX_TICKS_SHOWN // 2
            parts = parts[:head] + ["…"] + parts[-head:]
        return " / ".join(parts)

    def to_dict(self) -> dict:
        return {
            "axis": self.axis, "scale": self.scale, "source": self.source,
            "range": [self.vmin, self.vmax],
            "n_ticks": self.n_ticks, "spacing_pattern": self.pattern,
            "spacing_regularity": round(self.regularity, 3),
            "gap_ratio": self.gap_ratio,
            "step_units": (round(self.step_units, 10)
                           if self.step_units is not None else None),
            "step_decades": (round(self.step_decades, 10)
                             if self.step_decades is not None else None),
            "tick_values": [round(v, 10) for v in self.tick_values],
            "warnings": list(self.warnings),
        }


def _tick_value(p: float, p_lo: float, v_lo: float, p_hi: float, v_hi: float,
                scale: str) -> float | None:
    """按**所声明的尺度**把刻度像素位置换算为数据值。

    这一步把"尺度"从一个抽象声明变成可以逐格对照的数字：只要把算出的刻度值与图上
    印的数字比一比，尺度或范围填错就会立刻现形。用的是标定锚点自身的像素位置，因此
    倾斜轴的审核同样成立。
    """
    if p_hi == p_lo:
        return None
    u = (float(p) - float(p_lo)) / (float(p_hi) - float(p_lo))
    if scale == "log":
        if v_lo <= 0 or v_hi <= 0:
            return None
        return float(v_lo) * (float(v_hi) / float(v_lo)) ** u
    return float(v_lo) + u * (float(v_hi) - float(v_lo))


def classify_spacing(ticks: Sequence[float]) -> dict:
    """由刻度像素位置判定间距模式。

    判别逻辑（单向可靠）：

    * 把所有相邻间隙除以**最小间隙**，若结果近似整数 → 该轴刻度是等距的（``uniform``），
      或等距但漏检了若干刻度（``integer-multiple``）。
    * 若结果明显非整数 → ``nonuniform``。**线性轴的刻度不可能如此**，故这是一个可靠的
      "该轴不是线性刻度"的证据。

    实测对照（同一坐标系内的两种轴）：

    | 轴类型 | 间隙 / 最小间隙 | 判定 |
    |--------|----------------|------|
    | 线性（6 刻度等距） | 1,1,1,1,1 | uniform |
    | 对数（整数量级主刻度 + 2..9 次刻度） | 6.5, 3.8, 2.7, 2.1, 1.7, 1.5, 1.25, 1.1, 1.0 | nonuniform |
    """
    t = sorted({float(v) for v in ticks})
    out = {"pattern": PATTERN_INSUFFICIENT, "n": len(t),
           "regularity": 0.0, "max_int_deviation": None,
           "max_gap_ratio": None}
    if len(t) < 3:
        return out
    g = np.diff(np.asarray(t, dtype=float))
    g = g[g > 1.0]                       # 剔除因聚类残留产生的极短间隙
    if g.size < 2:
        return out
    base = float(g.min())
    mult = g / base
    dev = float(np.max(np.abs(mult - np.round(mult))))
    out["max_int_deviation"] = round(dev, 4)
    out["max_gap_ratio"] = round(float(g.max() / base), 3)
    out["regularity"] = round(uniform_tick_spacing(t), 3)
    out["n"] = len(t)
    if dev <= _INT_MULT_TOL:
        peak = float(np.max(np.round(mult)))
        out["pattern"] = PATTERN_UNIFORM if peak <= 1.5 else PATTERN_INTEGER
    else:
        out["pattern"] = PATTERN_NONUNIFORM
    return out


def audit_axis(axis: str, scale: str, source: str, vmin: float, vmax: float,
               ticks: Sequence[float], anchors=None) -> AxisAudit:
    """对一条轴做审核：几何交叉校验 + 绝对值标注 + 生成警告。

    ``anchors`` 为该轴的两个标定锚点 ``(px_at_vmin, vmin, px_at_vmax, vmax)``。给出时
    会把每个检出刻度按所声明的尺度换算成**数据值**，从而给出"每格"与"首末刻度值"两项
    绝对标注；不给定时退化为仅做间距模式判定。
    """
    info = classify_spacing(ticks)
    a = AxisAudit(axis=axis, scale=scale, source=source,
                  vmin=vmin, vmax=vmax, n_ticks=int(info["n"]),
                  pattern=str(info["pattern"]),
                  regularity=float(info["regularity"]),
                  gap_ratio=info["max_gap_ratio"])

    if anchors is not None:
        p_lo, v_lo, p_hi, v_hi = (float(v) for v in anchors)
        vals: list[float] = []
        for t in sorted({float(x) for x in ticks}):
            val = _tick_value(t, p_lo, v_lo, p_hi, v_hi, scale)
            if val is None:
                continue
            if scale == "log" and val <= 0:
                continue
            vals.append(val)
        vals.sort()
        if len(vals) >= 2:
            a.tick_values = vals
            if scale == "log":
                a.step_decades = float(np.median(np.diff(np.log10(vals))))
            else:
                a.step_units = float(np.median(np.diff(vals)))

    where = f"{axis} 轴"
    q = _quote(vmin, vmax)

    if a.pattern == PATTERN_NONUNIFORM and scale == "linear":
        a.warnings.append(
            f"{where}：刻度{_PATTERN_TEXT[PATTERN_NONUNIFORM]}（最大/最小间隙="
            f"{info['max_gap_ratio']}），而线性轴的刻度必等距或成整数倍。"
            f"**该轴很可能不是线性刻度**——请确认是否漏加 "
            f"`--log-{axis}`（当前范围 {q}）。"
        )
    elif a.pattern == PATTERN_UNIFORM and scale == "log":
        a.warnings.append(
            f"{where}：已声明对数刻度，但刻度完全等距。仅含整数量级主刻度的对数轴与线性轴"
            f"在几何上无法区分，工具无法替您排除——**请目视确认该轴确实是对数刻度**。"
        )
    elif a.pattern == PATTERN_NONUNIFORM and scale == "log":
        pass                              # 与对数刻度一致，无需提示
    elif a.pattern == PATTERN_INSUFFICIENT:
        a.warnings.append(
            f"{where}：仅检出 {a.n_ticks} 个刻度，无法做几何交叉校验，请目视确认尺度与范围。"
        )

    if source == SOURCE_DEFAULT and scale == "linear":
        a.warnings.append(
            f"{where}：**尺度取自默认值（线性），您未明确声明**。若图上该轴为对数刻度，"
            f"全部数值将偏差 3–5 个数量级且输出外观正常。请确认，或显式声明 "
            f"`--scale-{axis} linear`。"
        )
    return a


def _quote(vmin: float, vmax: float) -> str:
    return f"[{_fmt(vmin)}, {_fmt(vmax)}]"


def _fmt(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e4 or a < 1e-3:
        return f"{v:.6g}"
    return f"{v:g}"


@dataclass
class CalibrationAudit:
    """一次提取的完整标定审核记录。"""

    x: AxisAudit
    y: AxisAudit
    box_px: tuple[float, float, float, float]
    confirmed: bool = False
    confirmed_at: str | None = None

    @property
    def warnings(self) -> list[str]:
        return list(self.x.warnings) + list(self.y.warnings)

    @property
    def ok(self) -> bool:
        return not self.warnings

    def confirm(self) -> "CalibrationAudit":
        self.confirmed = True
        self.confirmed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return self

    def to_dict(self) -> dict:
        return {
            "x": self.x.to_dict(),
            "y": self.y.to_dict(),
            "box_px": [round(float(v), 3) for v in self.box_px],
            "reviewed_by_human": self.confirmed,
            "reviewed_at": self.confirmed_at,
            "ready_for_downstream": bool(self.confirmed),
            "warnings": self.warnings,
        }

    def render(self, width: int = 72) -> list[str]:
        """渲染为注释行列表（供 CSV 头部 / 终端输出复用）。"""
        line = "=" * width
        out = [line, "标定审核（请人工确认后使用）", line]
        for a in (self.x, self.y):
            src = "" if a.source == SOURCE_EXPLICIT else "（默认）"
            out.append(f"  {a.axis} 轴: 尺度={a.scale}{src}  "
                       f"范围={_quote(a.vmin, a.vmax)}")
            detail = f"        刻度 {a.n_ticks} 个  间距={a.pattern_text}"
            if a.step_text is not None:
                detail += f"  每格≈{a.step_text}"
            out.append(detail)
            if a.tick_values_text is not None:
                out.append(f"        按当前标定换算的刻度值: {a.tick_values_text}")
        out.append(f"  审核状态: "
                   f"{'已确认 ' + str(self.confirmed_at) if self.confirmed else '未确认'}")
        if self.x.tick_values_text is not None or self.y.tick_values_text is not None:
            out.append("  对照方法: 把上面每一个「按当前标定换算的刻度值」与图中同位置印的数字对齐；")
            out.append("            任一对不上即说明尺度或范围有误（先查 ×10ⁿ 量级标注与单位）。")
        else:
            out.append("  需目视核对: 刻度标签数值、量级标注(×10ⁿ)、物理单位")
        if self.warnings:
            out.append("  警告:")
            for w in self.warnings:
                out.append(f"    - {w}")
        else:
            out.append("  警告: 无（几何校验未发现不一致）")
        out.append(line)
        return out


def build_audit(box_px, x_scale: str, x_source: str, xlim,
                y_scale: str, y_source: str, ylim,
                tick_x: Sequence[float] = (), tick_y: Sequence[float] = (),
                confirmed: bool = False, calib=None) -> CalibrationAudit:
    """组装审核记录。

    ``tick_x``/``tick_y`` 为刻度像素位置（见 :func:`~chartdigit.detect.ticks_for_box`）。
    ``calib`` 给出时，用其**锚点自身的像素位置**换算刻度数据值，因此倾斜轴同样成立。

    坐标选取注意：沿 x 轴的像素坐标是 ``px``（列），沿 y 轴的是 ``py``（行）。:meth:`Calibration.from_box`
    构造的两个 y 锚点共用同一个 ``px``（都在轴框左边），若误取 ``px`` 会得到零跨度、静默
    算不出任何刻度值——这一处踩过，故显式写死。
    """
    x_anchors = y_anchors = None
    if calib is not None:
        x_anchors = (calib.xmin.px, calib.xmin.value,
                     calib.xmax.px, calib.xmax.value)
        y_anchors = (calib.ymin.py, calib.ymin.value,
                     calib.ymax.py, calib.ymax.value)

    au = CalibrationAudit(
        x=audit_axis("x", x_scale, x_source, float(xlim[0]), float(xlim[1]),
                     tick_x, x_anchors),
        y=audit_axis("y", y_scale, y_source, float(ylim[0]), float(ylim[1]),
                     tick_y, y_anchors),
        box_px=tuple(float(v) for v in box_px),
    )
    if confirmed:
        au.confirm()
    return au
