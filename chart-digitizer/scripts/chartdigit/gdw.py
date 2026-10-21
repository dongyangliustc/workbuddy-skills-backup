"""GetData Graph Digitizer 工作区文件 (``.gdw``) 的读写实现。

格式逆向自 GetData 2.24 自带的 ``workspaces/sample*.gdw``，要点如下：

* UTF-8 XML，根元素 ``<GetDataWorkspace>``；``<version>`` 为程序版本串，
  实测 ``2.22`` 与 ``2.24`` 可互读。
* ``<File>`` 记录源图片路径与像素尺寸，路径可相对 ``.gdw`` 所在目录。
* 每个 ``<Line>`` 代表一条被数字化的曲线，内含若干 ``<Points>`` 分段。
  每段最多 10 个点，``MF`` 属性为格式标记，取值为 ``9``。
* ``<Xmin>/<Xmax>/<Ymin>/<Ymax>`` 各含「像素 x, 像素 y, 数据值」三元组，
  共同构成 GetData 内部的仿射标定。``Xmin`` 与 ``Ymin`` 通常共用同一个原点像素。

由于标定信息与数据点均以像素坐标存储，本模块可以完全脱离 GUI 生成或解析工作区，
这是「双引擎」架构的基础：自研引擎负责取数，GetData 负责可视化复核。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

GDW_VERSION = "2.22"
POINTS_PER_SEGMENT = 10

_NUM_SPLIT = re.compile(r"[,\s]+")


@dataclass
class AxisAnchor:
    """坐标轴标定锚点：图像像素位置 + 该位置对应的数据值。"""

    px: float
    py: float
    value: float

    def to_text(self) -> str:
        """序列化为 ``.gdw`` 中使用的 ``px,py,value`` 三元组。"""
        return f"{self.px:.0f},{self.py:.0f},{self.value:.12e}"

    @classmethod
    def parse(cls, text: str) -> "AxisAnchor":
        parts = [p for p in _NUM_SPLIT.split(text.strip()) if p]
        if len(parts) < 3:
            raise ValueError(f"轴锚点格式非法: {text!r}")
        return cls(float(parts[0]), float(parts[1]), float(parts[2]))


@dataclass
class Curve:
    """一条数字化曲线：像素坐标点序列 + 外观元数据。"""

    caption: str = "Curve 1"
    points: list[tuple[float, float]] = field(default_factory=list)
    line_color: tuple[int, int, int] = (0, 0, 0)
    bk_color: tuple[int, int, int] = (255, 255, 255)
    pt_color: tuple[int, int, int] = (255, 0, 0)
    line_type: int = 0

    def __len__(self) -> int:
        return len(self.points)

    def segments(self) -> list[list[tuple[float, float]]]:
        """按 GetData 约定切分为每段不超过 10 个点的列表。"""
        n = POINTS_PER_SEGMENT
        return [self.points[i : i + n] for i in range(0, len(self.points), n)]


def _fmt_color(c: Sequence[int]) -> str:
    r, g, b = (int(v) for v in c[:3])
    return f"{r},{g},{b}"


def _parse_color(text: str | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if not text:
        return default
    try:
        vals = [int(v) for v in _NUM_SPLIT.split(text.strip()) if v]
    except ValueError:
        return default
    if len(vals) < 3:
        return default
    return (vals[0], vals[1], vals[2])


@dataclass
class Workspace:
    """``.gdw`` 工作区的内存表示。"""

    image_path: str
    width: int
    height: int
    curves: list[Curve] = field(default_factory=list)
    xmin: AxisAnchor | None = None
    xmax: AxisAnchor | None = None
    ymin: AxisAnchor | None = None
    ymax: AxisAnchor | None = None
    xscale: str = "linear"
    yscale: str = "linear"
    current_line: int = 0

    # ---------------------------------------------------------------- 写出

    def to_xml(self) -> str:
        """渲染为 GetData 可加载的 XML 文本。"""
        lines: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>', "<GetDataWorkspace>"]
        lines.append(f"  <version>{GDW_VERSION}</version>")
        lines.append("  <File>")
        lines.append(f"    <Name>{_xml_escape(self.image_path)}</Name>")
        lines.append(f"    <Width>{int(self.width)}</Width>")
        lines.append(f"    <Height>{int(self.height)}</Height>")
        lines.append("  </File>")

        for curve in self.curves:
            lines.append("  <Line>")
            lines.append(f"    <Caption>{_xml_escape(curve.caption)}</Caption>")
            lines.append(f"    <LineColor>{_fmt_color(curve.line_color)}</LineColor>")
            lines.append(f"    <BkColor>{_fmt_color(curve.bk_color)}</BkColor>")
            lines.append(f"    <PtColor>{_fmt_color(curve.pt_color)}</PtColor>")
            lines.append(f"    <LineType>{int(curve.line_type)}</LineType>")
            for seg in curve.segments():
                # 保留两位小数。实测 GetData 能完整解析 <Points> 中的小数坐标
                # （极简二点实验：写入 y=201.50 后数据面板显示 Y=0.00954907，
                # 与按 201.5 换算的 0.009549 一致），故无需为格式牺牲亚像素精度。
                # 注意 <Xmin> 等标定锚点则必须取整——GetData 对锚点做整数截断，
                # 若写入 77.6 它读作 77，会造成两通道标定不一致。
                flat = ",".join(f"{x:.2f},{y:.2f}" for x, y in seg)
                lines.append(f'    <Points MF="9">{flat}</Points>')
            lines.append("  </Line>")

        lines.append(f"  <CurrentLine>{int(self.current_line)}</CurrentLine>")
        for tag, anchor in (
            ("Xmin", self.xmin),
            ("Xmax", self.xmax),
            ("Ymin", self.ymin),
            ("Ymax", self.ymax),
        ):
            if anchor is not None:
                lines.append(f'  <{tag} MF="9">{anchor.to_text()}</{tag}>')
        lines.append(f"  <XScale>{self.xscale}</XScale>")
        lines.append(f"  <YScale>{self.yscale}</YScale>")
        # 以下为窗口状态与数字化参数，取 GetData 自带样例的默认值以保证可加载性。
        lines.append("  <Position>277,126</Position>")
        lines.append("  <Zoom>")
        lines.append("    <Scale>100.0</Scale>")
        lines.append("  </Zoom>")
        lines.append("  <Digitize>")
        lines.append("    <IgnorePixels>1</IgnorePixels>")
        lines.append("    <LineIDMethod>0</LineIDMethod>")
        lines.append("  </Digitize>")
        lines.append("  <AutoTrace>")
        lines.append("    <TraceDistance>10</TraceDistance>")
        lines.append("    <SmoothLines>1</SmoothLines>")
        lines.append("  </AutoTrace>")
        lines.append("  <DigitizeArea>")
        lines.append("    <LineOrArea>0</LineOrArea>")
        lines.append("    <AreaType>1</AreaType>")
        lines.append("    <GridType>0</GridType>")
        lines.append("    <GoThroughX>0.00000000000000e+000</GoThroughX>")
        lines.append("    <GoThroughY>0.00000000000000e+000</GoThroughY>")
        lines.append("    <ShiftX>1</ShiftX>")
        lines.append("    <ShiftY>1</ShiftY>")
        lines.append("    <RotateAngle>0.000000</RotateAngle>")
        lines.append("    <dx>10.000000</dx>")
        lines.append("  </DigitizeArea>")
        lines.append("  <GridSettings>")
        lines.append("    <ShowGrid>0</ShowGrid>")
        lines.append("    <AreaType>1</AreaType>")
        lines.append("    <deltaX>1.00000000000000e+000</deltaX>")
        lines.append("    <deltaY>1.00000000000000e+000</deltaY>")
        lines.append("    <deltaXUnits>1</deltaXUnits>")
        lines.append("    <deltaYUnits>1</deltaYUnits>")
        lines.append("    <GoThroughX>0.00000000000000e+000</GoThroughX>")
        lines.append("    <GoThroughY>0.00000000000000e+000</GoThroughY>")
        lines.append("    <dx>15.000000</dx>")
        lines.append("    <dy>15.000000</dy>")
        lines.append("  </GridSettings>")
        lines.append("</GetDataWorkspace>")
        return "\n".join(lines) + "\n"

    def write(self, path: str | Path) -> Path:
        """写出工作区文件。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_xml(), encoding="utf-8")
        return p

    # ---------------------------------------------------------------- 读入

    @classmethod
    def load(cls, path: str | Path) -> "Workspace":
        """解析既有工作区文件。"""
        p = Path(path)
        root = ET.fromstring(p.read_text(encoding="utf-8", errors="replace"))

        file_el = root.find("File")
        image_path = ""
        width = height = 0
        if file_el is not None:
            image_path = (file_el.findtext("Name") or "").strip()
            width = _int_or(file_el.findtext("Width"), 0)
            height = _int_or(file_el.findtext("Height"), 0)

        curves: list[Curve] = []
        for ln in root.findall("Line"):
            curve = Curve(caption=(ln.findtext("Caption") or "").strip() or "Curve")
            curve.line_color = _parse_color(ln.findtext("LineColor"), (0, 0, 0))
            curve.bk_color = _parse_color(ln.findtext("BkColor"), (255, 255, 255))
            curve.pt_color = _parse_color(ln.findtext("PtColor"), (255, 0, 0))
            curve.line_type = _int_or(ln.findtext("LineType"), 0)
            for seg in ln.findall("Points"):
                curve.points.extend(_parse_points(seg.text or ""))
            curves.append(curve)

        def anchor(tag: str) -> AxisAnchor | None:
            el = root.find(tag)
            if el is None or not (el.text or "").strip():
                return None
            try:
                return AxisAnchor.parse(el.text or "")
            except ValueError:
                return None

        return cls(
            image_path=image_path,
            width=width,
            height=height,
            curves=curves,
            xmin=anchor("Xmin"),
            xmax=anchor("Xmax"),
            ymin=anchor("Ymin"),
            ymax=anchor("Ymax"),
            xscale=(root.findtext("XScale") or "linear").strip(),
            yscale=(root.findtext("YScale") or "linear").strip(),
            current_line=_int_or(root.findtext("CurrentLine"), 0),
        )

    # ------------------------------------------------------------ 便捷构造

    @classmethod
    def from_curves(
        cls,
        image_path: str,
        width: int,
        height: int,
        curves: Iterable[Curve],
        calibration,  # noqa: ANN001 - Calibration，避免循环导入
        rel_image_path: str | None = None,
    ) -> "Workspace":
        """由像素点曲线与标定对象构造工作区。"""
        from .calibration import Calibration

        if not isinstance(calibration, Calibration):  # pragma: no cover - 防御
            raise TypeError("calibration 必须是 Calibration 实例")
        ws = cls(
            image_path=rel_image_path or image_path,
            width=width,
            height=height,
            curves=list(curves),
            xmin=calibration.xmin,
            xmax=calibration.xmax,
            ymin=calibration.ymin,
            ymax=calibration.ymax,
            xscale=calibration.xscale,
            yscale=calibration.yscale,
        )
        ws.current_line = max(0, len(ws.curves) - 1)
        return ws


def _parse_points(text: str) -> list[tuple[float, float]]:
    nums = [n for n in _NUM_SPLIT.split(text.strip()) if n]
    if len(nums) % 2:
        nums = nums[:-1]
    out: list[tuple[float, float]] = []
    for i in range(0, len(nums), 2):
        try:
            out.append((float(nums[i]), float(nums[i + 1])))
        except ValueError:
            continue
    return out


def _int_or(text: str | None, default: int) -> int:
    try:
        return int(float((text or "").strip()))
    except (TypeError, ValueError):
        return default


def _xml_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
