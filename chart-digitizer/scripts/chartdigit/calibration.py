"""像素坐标 <-> 数据坐标的标定与换算。

标定语义与 GetData Graph Digitizer 内核保持一致：由四个锚点构成仿射变换，

* ``xmin`` / ``xmax``：x 轴上的两个「像素位置 + 数据值」锚点；
* ``ymin`` / ``ymax``：y 轴上的两个「像素位置 + 数据值」锚点。

当四个锚点构成轴对齐矩形时（绝大多数规整插图），变换退化为两轴独立的一维线性映射；
当扫描件存在轻微倾斜时，完整仿射形式依然成立。该一致性已被实验验证：
自生成工作区经 GetData 打开后，数据面板数值与本模块 ``to_data`` 的输出逐位吻合。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from .gdw import AxisAnchor

_LINEAR = "linear"
_LOG = "log"


@dataclass
class Calibration:
    """四锚点仿射标定。"""

    xmin: AxisAnchor
    xmax: AxisAnchor
    ymin: AxisAnchor
    ymax: AxisAnchor
    xscale: str = _LINEAR
    yscale: str = _LINEAR

    # ------------------------------------------------------------ 构造

    @classmethod
    def from_box(
        cls,
        left: float,
        top: float,
        right: float,
        bottom: float,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
        xscale: str = _LINEAR,
        yscale: str = _LINEAR,
    ) -> "Calibration":
        """由数据区像素矩形与两轴数值范围构造标定。

        参数中 ``top`` / ``bottom`` 采用图像坐标系（y 向下），即 ``top < bottom``。
        """
        if right <= left or bottom <= top:
            raise ValueError("数据区矩形非法：需满足 right>left 且 bottom>top")
        return cls(
            xmin=AxisAnchor(left, bottom, x_min),
            xmax=AxisAnchor(right, bottom, x_max),
            ymin=AxisAnchor(left, bottom, y_min),
            ymax=AxisAnchor(left, top, y_max),
            xscale=xscale,
            yscale=yscale,
        )

    @classmethod
    def from_axes_pair(
        cls,
        x_p1: tuple[float, float],
        x_v1: float,
        x_p2: tuple[float, float],
        x_v2: float,
        y_p1: tuple[float, float],
        y_v1: float,
        y_p2: tuple[float, float],
        y_v2: float,
        xscale: str = _LINEAR,
        yscale: str = _LINEAR,
    ) -> "Calibration":
        """由两轴各自的任意两个标定点构造（支持倾斜轴）。"""
        xmin = AxisAnchor(x_p1[0], x_p1[1], x_v1)
        xmax = AxisAnchor(x_p2[0], x_p2[1], x_v2)
        ymin = AxisAnchor(y_p1[0], y_p1[1], y_v1)
        ymax = AxisAnchor(y_p2[0], y_p2[1], y_v2)
        return cls(xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax,
                   xscale=xscale, yscale=yscale)

    # ------------------------------------------------------------ 内部量

    @property
    def origin(self) -> tuple[float, float]:
        """仿射原点（取 xmin 锚点像素）。"""
        return self.xmin.px, self.xmin.py

    @property
    def basis_x(self) -> tuple[float, float]:
        """x 基向量：由 xmin 指向 xmax 的像素位移。"""
        return self.xmax.px - self.xmin.px, self.xmax.py - self.xmin.py

    @property
    def basis_y(self) -> tuple[float, float]:
        """y 基向量：由 ymin 指向 ymax 的像素位移。"""
        return self.ymax.px - self.ymin.px, self.ymax.py - self.ymin.py

    @property
    def determinant(self) -> float:
        ax, ay = self.basis_x
        bx, by = self.basis_y
        return ax * by - bx * ay

    @property
    def skew_degrees(self) -> float:
        """两轴偏离正交的程度（度）。轴对齐的规整插图为 0。

        以 x 轴方向为基准，衡量 y 轴方向与该基准的夹角偏离 90° 的绝对值。
        注意不能简单用 ``ang_y - 90``：图像 y 轴向下，``basis_y`` 自身方向角
        就约为 -90°，直接相减会得出 180° 的假倾斜。
        """
        ax, ay = self.basis_x
        bx, by = self.basis_y
        if (ax == 0 and ay == 0) or (bx == 0 and by == 0):
            return 0.0
        theta = math.degrees(math.atan2(by, bx) - math.atan2(ay, ax))
        return abs((abs(theta) % 180.0) - 90.0)

    # ------------------------------------------------------------ 正/逆变换

    def to_data(self, px: float, py: float) -> tuple[float, float]:
        """像素坐标 -> 数据坐标。"""
        det = self.determinant
        if abs(det) < 1e-12:
            raise ValueError("标定退化：四个锚点共线，无法求解")
        ox, oy = self.origin
        ax, ay = self.basis_x
        bx, by = self.basis_y
        dx = px - ox
        dy = py - oy
        u = (dx * by - bx * dy) / det
        v = (ax * dy - dx * ay) / det
        return (
            _unscale(u, self.xmin.value, self.xmax.value, self.xscale),
            _unscale(v, self.ymin.value, self.ymax.value, self.yscale),
        )

    def to_data_many(self, pts: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
        return [self.to_data(x, y) for x, y in pts]

    def to_pixel(self, dx: float, dy: float) -> tuple[float, float]:
        """数据坐标 -> 像素坐标。"""
        u = _scale(dx, self.xmin.value, self.xmax.value, self.xscale)
        v = _scale(dy, self.ymin.value, self.ymax.value, self.yscale)
        ox, oy = self.origin
        ax, ay = self.basis_x
        bx, by = self.basis_y
        return ox + u * ax + v * bx, oy + u * ay + v * by

    # ------------------------------------------------------------ 诊断

    def verify(self) -> dict:
        """往返一致性自检，用于「报告确认」环节。"""
        checks = []
        worst = 0.0
        for tag, (px, py) in (
            ("xmin", (self.xmin.px, self.xmin.py)),
            ("xmax", (self.xmax.px, self.xmax.py)),
            ("ymin", (self.ymin.px, self.ymin.py)),
            ("ymax", (self.ymax.px, self.ymax.py)),
        ):
            dx, dy = self.to_data(px, py)
            rx, ry = self.to_pixel(dx, dy)
            err = math.hypot(rx - px, ry - py)
            worst = max(worst, err)
            checks.append({"anchor": tag, "roundtrip_px_error": round(err, 6)})
        return {
            "determinant": self.determinant,
            "skew_degrees": round(self.skew_degrees, 4),
            "max_roundtrip_error_px": round(worst, 6),
            "anchors": checks,
            "x_range": [self.xmin.value, self.xmax.value],
            "y_range": [self.ymin.value, self.ymax.value],
            "scales": [self.xscale, self.yscale],
        }

    def to_dict(self) -> dict:
        return {
            "xmin": [self.xmin.px, self.xmin.py, self.xmin.value],
            "xmax": [self.xmax.px, self.xmax.py, self.xmax.value],
            "ymin": [self.ymin.px, self.ymin.py, self.ymin.value],
            "ymax": [self.ymax.px, self.ymax.py, self.ymax.value],
            "xscale": self.xscale,
            "yscale": self.yscale,
            "box_px": self.box(),
        }

    def box(self) -> tuple[float, float, float, float]:
        """返回轴对齐的数据区像素矩形 (left, top, right, bottom)。

        轴倾斜时取四锚点的外接范围。
        """
        xs = [self.xmin.px, self.xmax.px, self.ymin.px, self.ymax.px]
        ys = [self.xmin.py, self.xmax.py, self.ymin.py, self.ymax.py]
        return min(xs), min(ys), max(xs), max(ys)

    # ------------------------------------------------------------ 便捷

    @classmethod
    def from_dict(cls, data: dict) -> "Calibration":
        def mk(key: str) -> AxisAnchor:
            v: Sequence[float] = data[key]
            return AxisAnchor(float(v[0]), float(v[1]), float(v[2]))

        return cls(
            xmin=mk("xmin"),
            xmax=mk("xmax"),
            ymin=mk("ymin"),
            ymax=mk("ymax"),
            xscale=data.get("xscale", _LINEAR),
            yscale=data.get("yscale", _LINEAR),
        )


# ---------------------------------------------------------------- 尺度插值


def _unscale(u: float, vmin: float, vmax: float, mode: str) -> float:
    """把仿射参数 u∈[0,1] 映射为数据值。"""
    if mode == _LOG:
        if vmin <= 0 or vmax <= 0:
            raise ValueError("对数轴要求锚点数值为正")
        return math.exp(math.log(vmin) + u * (math.log(vmax) - math.log(vmin)))
    return vmin + u * (vmax - vmin)


def _scale(value: float, vmin: float, vmax: float, mode: str) -> float:
    """数据值反解为仿射参数 u。"""
    if mode == _LOG:
        if vmin <= 0 or vmax <= 0 or value <= 0:
            raise ValueError("对数轴要求数值为正")
        return math.log(value / vmin) / math.log(vmax / vmin)
    if vmax == vmin:
        raise ValueError("轴范围为零，无法反解")
    return (value - vmin) / (vmax - vmin)
