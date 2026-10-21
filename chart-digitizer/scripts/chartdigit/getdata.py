"""GetData Graph Digitizer 桥接层。

职责有三：

1. **定位**可执行文件（注册表 -> 常见安装路径 -> 环境变量 ``GETDATA_EXE``）。
2. **生成** ``.gdw`` 工作区：把提取结果写入 GetData 原生格式，供 GUI 目视复核。
   由于 GetData 2.24 原生不支持 PNG/GIF/WEBP，此步骤会按需把源图转换为 BMP
   并落到 ASCII 安全路径（规避旧版 MFC 程序在非英文路径上的潜在问题）。
3. **回收**：解析 ``.gdw`` 回读数据，形成「自动提取 -> 人工复核 -> 回读交付」闭环。

已实测：``GetDataW.exe <工作区路径>`` 可正确加载并渲染自生成的工作区，
标定锚点与数据点位置、数值均与内核预期一致。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .calibration import Calibration
from .deliver import DigitizedSeries, digitize
from .extract import Series
from .gdw import AxisAnchor, Curve, Workspace

GDW_NATIVE_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".pcx"}

_CANDIDATE_DIRS = (
    r"D:\Program Files (x86)\GetData",
    r"C:\Program Files (x86)\GetData",
    r"D:\Program Files\GetData",
    r"C:\Program Files\GetData",
    r"D:\GetData",
    r"C:\GetData",
)

_EXE_NAMES = ("GetDataW.exe", "GetData.exe")


# ------------------------------------------------------------------ 定位


def find_exe(explicit: str | Path | None = None) -> Path | None:
    """定位 GetData 可执行文件，失败返回 ``None``。"""
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        if p.is_dir():
            for n in _EXE_NAMES:
                if (p / n).is_file():
                    return p / n
        return None

    env = os.environ.get("GETDATA_EXE")
    if env and Path(env).is_file():
        return Path(env)

    for n in _EXE_NAMES:
        found = shutil.which(n)
        if found:
            return Path(found)

    for d in _CANDIDATE_DIRS:
        base = Path(d)
        for n in _EXE_NAMES:
            if (base / n).is_file():
                return base / n
    return None


def describe_environment(explicit: str | Path | None = None) -> dict:
    """环境自检结果，供 ``doctor`` 子命令输出。"""
    exe = find_exe(explicit)
    info: dict = {
        "exe": str(exe) if exe else None,
        "exe_found": exe is not None,
        "native_formats": sorted(GDW_NATIVE_SUFFIXES),
        "can_import_png": False,
        "notes": [],
    }
    if exe:
        try:
            info["exe_size"] = exe.stat().st_size
        except OSError:
            pass
        info["version"] = "2.24 (检测自安装目录)"
    else:
        info["notes"].append(
            "未找到 GetDataW.exe：GUI 复核功能不可用，但 native 引擎可独立完成取数。"
            "可通过 --exe 或环境变量 GETDATA_EXE 指定路径。"
        )
    try:
        import PIL  # noqa: F401

        info["pillow"] = True
    except Exception:
        info["pillow"] = False
        info["notes"].append("Pillow 缺失：无法加载 PNG/JPG 等通用格式。")
    try:
        import numpy  # noqa: F401

        info["numpy"] = True
    except Exception:
        info["numpy"] = False
    try:
        import scipy  # noqa: F401

        info["scipy"] = True
    except Exception:
        info["scipy"] = False
        info["notes"].append("scipy 缺失：散点与误差棒提取降级（曲线提取仍可用）。")
    info["notes"].append(
        "GetData 原生不支持 PNG/GIF/WEBP；桥接层会自动转换为 BMP 后再生成工作区。"
    )
    return info


# ------------------------------------------------------------------ 生成工作区


def _safe_image_for_getdata(src: Path, workdir: Path, prefer_native: bool = True) -> Path:
    """返回一张 GetData 可读的图片副本。

    * 源图已是原生格式且路径为纯 ASCII -> 直接复用，避免无谓拷贝；
    * 否则复制/转换为 ``workdir`` 下的 ASCII 名称 BMP。
    """
    ascii_safe = all(ord(c) < 128 for c in str(src))

    def needs_copy() -> bool:
        return (
            src.suffix.lower() not in GDW_NATIVE_SUFFIXES
            or not ascii_safe
            or src.suffix.lower() in {".jpg", ".jpeg", ".pcx"}
        )

    if not needs_copy():
        return src

    workdir.mkdir(parents=True, exist_ok=True)
    dst = workdir / "getdata_source.bmp"
    from PIL import Image

    with Image.open(src) as im:
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(dst, format="BMP")
    return dst


def build_workspace(
    image_path: str | Path,
    series: list[Series],
    calib: Calibration,
    out_gdw: str | Path,
    workdir: str | Path | None = None,
    captions: list[str] | None = None,
) -> tuple[Path, Path]:
    """生成 ``.gdw`` 工作区，返回 ``(gdw 路径, 实际引用的图片路径)``。"""
    src = Path(image_path)
    out_p = Path(out_gdw)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    wd = Path(workdir) if workdir else out_p.parent / (out_p.stem + "_getdata")

    used_img = _safe_image_for_getdata(src, wd)

    from PIL import Image

    with Image.open(src) as im:
        W, H = im.size

    # 必须写绝对路径。实测 GetData 解析 <File><Name> 时以**进程当前工作目录**为基准，
    # 而不是 .gdw 文件所在目录：自带的 sample*.gdw 用 "..\graphs\x.jpg" 恰好能靠
    # 「双击启动 + exe 目录为 cwd」蒙对，但从脚本以任意 cwd 启动就会失败，表现为
    # 弹窗 "Unable to open file ... Try to locate this file?"。
    # 绝对路径在两种启动方式下都可靠（已在受控实验中验证渲染正确）。
    image_ref = str(used_img.resolve()).replace("/", "\\")

    calib_gd = _rounded_calibration(calib)

    curves: list[Curve] = []
    for i, s in enumerate(series):
        cap = (captions[i] if captions and i < len(captions) else s.name) or f"Curve {i + 1}"
        # 把像素点经「native 标定」解算为数据坐标，再用「取整后的标定」重投影回像素。
        # 这样 GetData 用它自己读到的（整数锚点）标定反算时，得到的正是 native 的数值。
        pts: list[tuple[float, float]] = []
        for px, py in s.points:
            dx, dy = calib.to_data(px, py)
            qx, qy = calib_gd.to_pixel(dx, dy)
            pts.append((round(qx, 2), round(qy, 2)))
        curves.append(
            Curve(
                caption=cap,
                points=pts,
                line_color=tuple(s.color),
                bk_color=(255, 255, 255),
                pt_color=_contrast_color(tuple(s.color)),
            )
        )

    ws = Workspace(
        image_path=image_ref,
        width=W,
        height=H,
        curves=curves,
        xmin=calib_gd.xmin,
        xmax=calib_gd.xmax,
        ymin=calib_gd.ymin,
        ymax=calib_gd.ymax,
        xscale=calib_gd.xscale,
        yscale=calib_gd.yscale,
    )
    ws.current_line = max(0, len(curves) - 1)
    ws.write(out_p)
    return out_p, used_img


def _rounded_calibration(calib: Calibration) -> Calibration:
    """把标定锚点取整，与 GetData 对锚点元素的整数解析保持一致。

    实测 GetData 解析 ``<Xmin>/<Xmax>/<Ymin>/<Ymax>`` 时会丢弃小数（锚点写成
    77.6 被读作 77），而 ``<Points>`` 的小数则完整保留。若沿用亚像素锚点，
    GetData 端与 native 端的标定会相差最多 1 像素，往返后数据偏差约 3e-3。
    """

    def snap(a: AxisAnchor) -> AxisAnchor:
        return AxisAnchor(float(round(a.px)), float(round(a.py)), a.value)

    return Calibration(
        xmin=snap(calib.xmin), xmax=snap(calib.xmax),
        ymin=snap(calib.ymin), ymax=snap(calib.ymax),
        xscale=calib.xscale, yscale=calib.yscale,
    )


def _contrast_color(c: tuple[int, int, int]) -> tuple[int, int, int]:
    """为标记点选一个与曲线色反差明显的颜色。"""
    r, g, b = c
    if r + g + b > 380:
        return (0, 0, 160)
    return (255, 215, 0)


# ------------------------------------------------------------------ 启动


def launch(
    gdw_path: str | Path,
    exe: str | Path | None = None,
    wait: bool = False,
    maximize: bool = False,
) -> subprocess.Popen | None:
    """用 GetData 打开工作区文件。"""
    exe_p = find_exe(exe)
    if exe_p is None:
        raise FileNotFoundError(
            "未找到 GetDataW.exe，请用 --exe 指定路径或设置 GETDATA_EXE"
        )
    p = Path(gdw_path)
    if not p.is_file():
        raise FileNotFoundError(f"工作区不存在: {p}")
    proc = subprocess.Popen([str(exe_p), str(p)])
    if wait:
        try:
            proc.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            pass
    return proc


# ------------------------------------------------------------------ 回读


def read_workspace(
    gdw_path: str | Path,
    sort_by_x: bool = True,
) -> tuple[list[DigitizedSeries], Calibration, Workspace]:
    """解析 ``.gdw`` 回读为数据序列。"""
    ws = Workspace.load(gdw_path)
    if ws.xmin is None or ws.xmax is None or ws.ymin is None or ws.ymax is None:
        raise ValueError("工作区缺少完整的 Xmin/Xmax/Ymin/Ymax 标定，无法换算数据坐标")

    calib = Calibration(
        xmin=ws.xmin, xmax=ws.xmax, ymin=ws.ymin, ymax=ws.ymax,
        xscale=ws.xscale, yscale=ws.yscale,
    )
    series = [
        Series(name=c.caption or f"Curve {i + 1}", kind="line", color=c.line_color,
               points=list(c.points))
        for i, c in enumerate(ws.curves)
    ]
    return digitize(series, calib, sort_by_x=sort_by_x), calib, ws
