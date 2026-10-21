"""chart-digitizer：从图表图片中提取可复用的数值数据。

对外暴露两个层次的能力：

* **native 引擎**（默认）：纯 Python 图像处理，支持 PNG/JPG/BMP/TIFF/GIF/WEBP 等
  任意 Pillow 可读格式，自动完成面板分割、曲线分离、散点与误差棒识别，秒级出数。
* **getdata 引擎**（复核）：生成 GetData Graph Digitizer 原生 ``.gdw`` 工作区，
  可在 GUI 中目视核对，再回读为数据；两引擎共用同一套标定数学，结果可比对。

典型用法::

    from chartdigit import load_image, detect, Calibration, extract_series, digitize, write_csv

    img = load_image("figure.png")
    rep = detect("figure.png")
    box = rep.panels[0].box                      # (left, top, right, bottom)
    calib = Calibration.from_box(*box, x_min=0, x_max=10, y_min=-1, y_max=1)
    series = extract_series(img.rgb, box, background=img.background_rgb)
    data = digitize(series, calib)
    write_csv(data, "out.csv", calib=calib)
"""

from __future__ import annotations

__version__ = "1.0.0"

from .calibration import Calibration
from .audit import (
    SOURCE_DEFAULT,
    SOURCE_EXPLICIT,
    AxisAudit,
    CalibrationAudit,
    audit_axis,
    build_audit,
    classify_spacing,
)
from .deliver import DigitizedSeries, digitize, summarize, write_csv, write_json, write_xlsx
from .detect import (
    DetectReport,
    ImageData,
    Panel,
    auto_tick_band,
    detect,
    find_ticks,
    load_image,
    render_diagnostic,
    ticks_for_box,
    uniform_tick_spacing,
)
from .extract import Series, dominant_colors, extract_series, resample_polyline
from .bwline import (
    Track,
    classify_dash,
    color_pixel_count,
    column_samples,
    edt_width_map,
    has_color,
    separate_bw,
    stroke_dictionary,
    width_classes,
)
from .gdw import AxisAnchor, Curve, Workspace
from .getdata import build_workspace, describe_environment, find_exe, launch, read_workspace

__all__ = [
    "__version__",
    "Calibration",
    "AxisAudit",
    "CalibrationAudit",
    "audit_axis",
    "build_audit",
    "classify_spacing",
    "SOURCE_EXPLICIT",
    "SOURCE_DEFAULT",
    "AxisAnchor",
    "Curve",
    "Workspace",
    "ImageData",
    "Panel",
    "DetectReport",
    "load_image",
    "detect",
    "render_diagnostic",
    "find_ticks",
    "auto_tick_band",
    "ticks_for_box",
    "uniform_tick_spacing",
    "Series",
    "extract_series",
    "dominant_colors",
    "resample_polyline",
    "Track",
    "separate_bw",
    "stroke_dictionary",
    "color_pixel_count",
    "has_color",
    "edt_width_map",
    "column_samples",
    "width_classes",
    "classify_dash",
    "DigitizedSeries",
    "digitize",
    "write_csv",
    "write_xlsx",
    "write_json",
    "summarize",
    "find_exe",
    "describe_environment",
    "build_workspace",
    "launch",
    "read_workspace",
]
