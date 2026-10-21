"""chart-digitizer 命令行入口。

子命令一览::

    doctor                     环境自检（Python 依赖 / GetData 可用性 / 格式能力）
    from-pdf PAPER.pdf         从文献 PDF 定位插图并裁出 PNG（本技能的前置输入适配层）
    detect   IMAGE             仅做结构检测，输出面板、轴框、刻度与置信度报告
    extract  IMAGE             主命令：提取曲线/散点/误差棒并交付数据
    to-gdw   IMAGE             生成 GetData 工作区（不提取，仅把已有结果转格式）
    open     WORKSPACE.gdw     用 GetData 打开工作区做目视复核
    dump     WORKSPACE.gdw     解析工作区，回读为数据文件

文献 PDF 工作流
---------------
PDF 里的插图要先变成 PNG 才能进入 ``extract``。``from-pdf`` 只负责这一步，
链路为::

    from-pdf paper.pdf --list              # 1. 看有哪些图（无副作用，随时可跑）
    from-pdf paper.pdf --figure 3 -o fig3  # 2. 裁出图（并打印下一步的 extract 命令）
    extract fig3.png --axes ... --xlim ... --ylim ...

**绝不猜**：同一编号出现多个候选、或定位失败时，``from-pdf`` 以退出码 1 结束并列出
全部候选，由调用方改用 ``--page`` + ``--rect`` 手工框选。宁可要求一次人工确认，
也不给出一张看似合理的错图。

设计原则：**绝不臆造坐标轴数值**。数值必须来自 ``--xlim/--ylim``（配合 ``--axes``）
或 ``--xcal/--ycal``（任意两个标定点）。缺失时命令会明确报错并打印检测到的
轴框候选，交给调用方（智能体视觉判读或人工）补齐。

标定审核
--------
轴**数值**可由外部给定，但轴**尺度（线性/对数）**极易被静默默认：漏加 ``--log-y``
会让全部数值偏 3–5 个数量级，而输出外观毫无异样。为此 ``extract`` 输出一段
「标定审核」，标注每轴尺度的**来源**（用户指定 / 默认）、以绝对值给出范围、并用
刻度间距几何做交叉校验（非等距且非整数倍 ⇒ 该轴几乎必然不是线性刻度）。人工核对后
加 ``--confirm-scale`` 把确认动作与时间戳写进交付物；自动化场景可用 ``--strict-scale``
让存在警告的提取以退出码 4 结束。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .audit import SOURCE_DEFAULT, SOURCE_EXPLICIT, build_audit
from .calibration import Calibration
from .deliver import (digitize, legend_header, legend_rows, summarize, write_csv,
                      write_json, write_xlsx)
from .detect import detect, load_image, render_diagnostic, ticks_for_box
from .extract import Series, extract_series
from .gdw import AxisAnchor
from .getdata import build_workspace

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FAILED = 2
EXIT_NO_GETDATA = 3
EXIT_NEEDS_REVIEW = 4


class _UsageError(ValueError):
    """用法错误（退出码 1）。与 ``EXIT_FAILED`` 区分：前者是"你没说清"，后者是"跑失败"。"""


# ------------------------------------------------------------------ 解析辅助


def _parse_box(text: str) -> tuple[int, int, int, int]:
    parts = [p for p in text.replace(";", ",").split(",") if p.strip()]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"矩形需要 4 个整数 left,top,right,bottom，收到: {text!r}"
        )
    vals = [int(round(float(p))) for p in parts]
    if vals[2] <= vals[0] or vals[3] <= vals[1]:
        raise argparse.ArgumentTypeError("矩形非法：需 right>left 且 bottom>top")
    return vals[0], vals[1], vals[2], vals[3]


def _parse_anchor(text: str) -> tuple[float, float, float]:
    parts = [p for p in text.replace(";", ",").split(",") if p.strip()]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"锚点需要 px,py,value，收到: {text!r}")
    return float(parts[0]), float(parts[1]), float(parts[2])


def _parse_color(text: str) -> tuple[int, int, int]:
    t = text.strip().lstrip("#")
    if "," in t:
        vals = [int(p) for p in t.split(",") if p.strip()]
    elif len(t) == 6:
        vals = [int(t[i : i + 2], 16) for i in (0, 2, 4)]
    elif len(t) == 3:
        vals = [int(c * 2, 16) for c in t]
    else:
        raise argparse.ArgumentTypeError(f"颜色格式无法识别: {text!r}")
    if len(vals) != 3:
        raise argparse.ArgumentTypeError(f"颜色需要 3 个分量: {text!r}")
    return vals[0], vals[1], vals[2]


def _parse_rect_pt(text: str) -> tuple[float, float, float, float]:
    """解析 **PDF 点**坐标矩形 ``left,top,right,bottom``（浮点）。

    与 ``_parse_box`` 的区别是这里不取整：PDF 图注行级 bbox 实测形如
    ``43.6, 130.0, 266.5, 372.0``，取整会引入最高 0.5 pt（600 dpi 下约 4 px）偏差。
    """
    parts = [p for p in text.replace(";", ",").split(",") if p.strip()]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"矩形需要 4 个数 left,top,right,bottom（PDF 点），收到: {text!r}"
        )
    try:
        vals = [float(p) for p in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"矩形分量不是数值: {text!r}") from exc
    if vals[2] <= vals[0] or vals[3] <= vals[1]:
        raise argparse.ArgumentTypeError("矩形非法：需 right>left 且 bottom>top")
    return (vals[0], vals[1], vals[2], vals[3])


def _out(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False))


def _die(msg: str, code: int = EXIT_FAILED) -> int:
    print(f"[错误] {msg}", file=sys.stderr)
    return code


# ------------------------------------------------------------------ 标定构造


def _axis_scale(args: argparse.Namespace, axis: str) -> tuple[str, str]:
    """确定单条轴的尺度及其来源。

    语义：``--log-{axis}`` 与 ``--scale-{axis}`` 都表示**用户显式声明**；二者都未给出
    时尺度落到默认值 ``linear``，标记来源为「默认」——这正是标定审核要警示的情形
    （漏加 ``--log-y`` 会让数值整体差 3–5 个数量级，而输出外观毫无异样）。

    返回 ``(scale, source)``。
    """
    explicit = getattr(args, f"scale_{axis}", None)
    log_flag = bool(getattr(args, f"log_{axis}", False))
    if explicit is not None and log_flag and explicit != "log":
        raise ValueError(
            f"--scale-{axis} {explicit} 与 --log-{axis} 相互冲突，请只保留一个"
        )
    if explicit is not None:
        return explicit, SOURCE_EXPLICIT
    if log_flag:
        return "log", SOURCE_EXPLICIT
    return "linear", SOURCE_DEFAULT


def build_calibration(args: argparse.Namespace, box: tuple[int, int, int, int]) -> Calibration | None:
    """按命令行参数构造标定；信息不足时返回 ``None``。"""
    xscale, _ = _axis_scale(args, "x")
    yscale, _ = _axis_scale(args, "y")

    if getattr(args, "xcal", None) and getattr(args, "ycal", None):
        x1, y1, v1 = args.xcal[0]
        x2, y2, v2 = args.xcal[1]
        a1, b1, w1 = args.ycal[0]
        a2, b2, w2 = args.ycal[1]
        return Calibration.from_axes_pair(
            (x1, y1), v1, (x2, y2), v2, (a1, b1), w1, (a2, b2), w2,
            xscale=xscale, yscale=yscale,
        )

    if getattr(args, "xlim", None) and getattr(args, "ylim", None):
        left, top, right, bottom = box
        return Calibration.from_box(
            left, top, right, bottom,
            x_min=float(args.xlim[0]), x_max=float(args.xlim[1]),
            y_min=float(args.ylim[0]), y_max=float(args.ylim[1]),
            xscale=xscale, yscale=yscale,
        )
    return None


# ------------------------------------------------------------------ 子命令


def cmd_doctor(args: argparse.Namespace) -> int:
    from .getdata import describe_environment

    info = describe_environment(args.exe)
    info["chart_digitizer_version"] = __version__
    info["python"] = sys.version.split()[0]
    _out(**info)
    print()
    print("环境自检摘要")
    print(f"  GetData 可执行文件 : {info.get('exe') or '未找到（GUI 复核不可用）'}")
    print(f"  GetData 原生格式   : {', '.join(info['native_formats'])}")
    print(f"  Pillow / numpy / scipy : {info.get('pillow')} / {info.get('numpy')} / {info.get('scipy')}")
    for n in info.get("notes", []):
        print(f"  · {n}")
    return EXIT_OK


def cmd_detect(args: argparse.Namespace) -> int:
    rep = detect(
        args.image,
        dark_threshold=args.dark_threshold,
        min_ratio=args.min_ratio,
        detect_ticks=not args.no_ticks,
    )
    payload = rep.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"图像: {rep.image}  {rep.size[0]}x{rep.size[1]}  背景≈{rep.background_rgb}")
        print(f"结构置信度: {rep.confidence}")
        print(f"面板数: {len(rep.panels)}")
        for p in rep.panels:
            print(
                f"  [{p.label}] box={tuple(round(v, 1) for v in p.box)} "
                f"size={tuple(round(v, 1) for v in p.size)} "
                f"{'闭合' if p.closed else '开放'} 边={p.edges_found} "
                f"内容占比={p.content_ratio:.3f}"
            )
        print(f"水平长线 {len(rep.horizontal_lines)} 条，垂直长线 {len(rep.vertical_lines)} 条")
        if rep.tick_x:
            print(f"x 轴刻度像素位置({len(rep.tick_x)}): {rep.tick_x[:24]}"
                  + (" ..." if len(rep.tick_x) > 24 else ""))
        if rep.tick_y:
            print(f"y 轴刻度像素位置({len(rep.tick_y)}): {rep.tick_y[:24]}"
                  + (" ..." if len(rep.tick_y) > 24 else ""))
        for w in rep.warnings:
            print(f"  ⚠ {w}")
        for n in rep.notes:
            print(f"  · {n}")
    if args.diag:
        img = load_image(args.image)
        p = render_diagnostic(img, rep, args.diag)
        print(f"\n诊断图已写出: {p}")
    return EXIT_OK


def _resolve_box(args: argparse.Namespace):
    """确定数据区像素矩形与（可选的）检测报告。"""
    if args.axes:
        return args.axes, None
    rep = detect(args.image, dark_threshold=args.dark_threshold, min_ratio=args.min_ratio)
    if not rep.panels:
        return None, rep
    if args.panel is not None:
        idx = args.panel - 1
        if not (0 <= idx < len(rep.panels)):
            return None, rep
        return rep.panels[idx].box, rep
    main = max(rep.panels, key=lambda p: p.size[0] * p.size[1])
    return main.box, rep


@dataclass
class LegendWiring:
    """``--exclude`` / ``--exclude-from-pdf`` 的解析结果。"""

    exclude_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    legend: object | None = None
    colors: list[tuple[int, int, int]] | None = None
    names: list[str] | None = None
    warnings: list[str] = field(default_factory=list)


def _region_crop(rgb, region):
    l, t, r, b = (int(v) for v in region)
    h, w = rgb.shape[:2]
    l = max(0, min(l, w - 1))
    r = max(l + 1, min(r, w))
    t = max(0, min(t, h - 1))
    b = max(t + 1, min(b, h))
    return rgb[t:b, l:r]


def resolve_legend_wiring(args: argparse.Namespace, img, region) -> LegendWiring:
    """把 ``--exclude`` / ``--exclude-from-pdf`` 变成「排除矩形 + 图例 + 配色」。

    回退原则（SPEC §0「图例是可选增强」）：**任何环节失败都只告警、不改链路结局**。
    PDF 打不开、页码越界、图例找不到、颜色校验不通过——一律降级到"没有图例"，
    继续用原有自动配色跑完整条链路。

    取值优先级：用户显式 ``--colors`` / ``--names`` > 图例 > 自动。
    图例只有在 **全部条目关联到色块且 R1 校验通过** 时才被采用（:meth:`LegendInfo.usable`）；
    校验不通过时**绝不硬用**，改为自动配色并给出可人工核对的对照。
    """
    from .legend import (exclusion_boxes_px, extract_legend, fallback_message,
                         resolve_dpi, swatch_boxes_px, verify_legend)

    out = LegendWiring()
    out.exclude_boxes = [tuple(float(v) for v in b) for b in (getattr(args, "exclude", None) or [])]

    pdf_arg = getattr(args, "exclude_from_pdf", None)
    if not pdf_arg:
        return out
    if args.page is None or args.rect is None:
        raise _UsageError("--exclude-from-pdf 必须与 --page 和 --rect 同时给出"
                          "（页码 + 该图的 PDF 点矩形，取自 from-pdf --list）")

    try:
        import pymupdf
    except ImportError as exc:
        out.warnings.append(f"pymupdf 不可用，图例与排除掩码已跳过（{exc}）")
        return out

    pdf_path = Path(pdf_arg)
    if not pdf_path.is_file():
        out.warnings.append(f"PDF 不存在，图例与排除掩码已跳过: {pdf_path}")
        return out

    doc = None
    try:
        doc = pymupdf.open(str(pdf_path))
        pg = int(args.page) - 1
        if not (0 <= pg < doc.page_count):
            out.warnings.append(
                f"页码 {args.page} 越界（该 PDF 共 {doc.page_count} 页），"
                "图例与排除掩码已跳过")
            return out
        rect = pymupdf.Rect(args.rect)
        sidecar = Path(str(args.image) + ".json")
        dpi, dpi_note = resolve_dpi(img.width, rect, sidecar=sidecar)
        if dpi_note:
            out.warnings.append(dpi_note)

        legend = extract_legend(doc[pg], rect, dpi=dpi)
        sw_px = swatch_boxes_px(legend, (rect.x0, rect.y0, rect.x1, rect.y1), dpi)
        verify_legend(legend, img.rgb, region, swatch_boxes_px=sw_px)
        legend.fallback_detail.setdefault("dpi", dpi)
        out.legend = legend

        # 文字行 bbox（SPEC §3.3 的强制部分）
        out.exclude_boxes += list(legend.exclude_boxes_px)
        # 图例色块本身也一并剔除：§3.3 要修的限制是「图例与曲线同色时被当数据一并
        # 提取」，只剔文字剔不掉色块；色块是 5×2.5 pt 的实心矩形，在 scatter 路径下
        # 会被当成一个标记点输出。此为 SPEC 字面之外、但服务于其既定目标的加固。
        out.exclude_boxes += list(sw_px)

        if getattr(args, "colors", None) or getattr(args, "names", None):
            # 用户显式指定优先，不被图例覆盖；但校验结论仍如实记录
            if not legend.usable:
                legend.fallback = ("图例颜色未通过一致性校验；本次按用户显式 "
                                   "--colors/--names 取色，未采用图例颜色。")
            return out
        if legend.usable:
            out.colors = legend.colors()
            out.names = legend.names()
        elif legend.found:
            sub = _region_crop(img.rgb, region)
            from .extract import dominant_colors, drop_antialias_colors
            found = dominant_colors(sub, background=img.background_rgb, top_k=6,
                                    bg_margin=args.tol)
            # 报"图内实际主色"时报**自动配色的有效候选**（已剔抗锯齿混合色），
            # 而不是原始直方图峰——后者会混进近黑轴线与浅色光晕，反而误导人工核对。
            counts = {tuple(c): int(n) for c, n in found}
            used = drop_antialias_colors([c for c, _ in found], img.background_rgb)
            msg, detail = fallback_message(legend, [(c, counts.get(tuple(c), 0))
                                                    for c in used])
            legend.fallback = msg
            legend.fallback_detail = {**legend.fallback_detail, **detail}
            out.warnings.append(msg)
        return out
    except Exception as exc:  # noqa: BLE001 —— 图例是可选增强，绝不让链路失败
        out.legend = None
        out.warnings.append(f"图例提取失败（已回退自动配色）: {exc}")
        return out
    finally:
        if doc is not None:
            doc.close()


def cmd_extract(args: argparse.Namespace) -> int:
    img = load_image(args.image)
    box, rep = _resolve_box(args)

    if box is None:
        _die("未能确定数据区。请用 --axes left,top,right,bottom 手动指定。")
        if rep:
            for p in rep.panels:
                print(f"  候选面板 [{p.label}] {p.box}", file=sys.stderr)
        return EXIT_FAILED

    calib = build_calibration(args, box)
    if calib is None:
        _die(
            "缺少轴数值标定。请提供 --xlim XMIN XMAX 与 --ylim YMIN YMAX（配合 --axes），"
            "或 --xcal/--ycal 各两个 px,py,value 锚点。"
        )
        print("  说明：本工具不做刻度 OCR，轴数值必须由外部给定，以避免静默错误标定。",
              file=sys.stderr)
        if rep:
            print(f"  检测到的数据区像素矩形: {tuple(round(v, 1) for v in box)}",
                  file=sys.stderr)
            if rep.tick_x:
                print(f"  x 轴刻度像素: {rep.tick_x[:24]}", file=sys.stderr)
            if rep.tick_y:
                print(f"  y 轴刻度像素: {rep.tick_y[:24]}", file=sys.stderr)
        return EXIT_USAGE

    inset = int(args.inset)
    region = (
        box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset,
    ) if inset else box
    if args.region:
        region = args.region

    # ---------------- 排除掩码 + 图例（可选增强；任何失败都只告警、不中断链路）
    try:
        wiring = resolve_legend_wiring(args, img, region)
    except _UsageError as exc:
        return _die(str(exc), EXIT_USAGE)
    for w in wiring.warnings:
        print(f"[警告] {w}", file=sys.stderr)

    colors = ([_parse_color(c) for c in args.colors] if args.colors
              else wiring.colors)
    names = list(args.names) if args.names else wiring.names

    series = extract_series(
        img.rgb,
        region,
        background=img.background_rgb,
        colors=colors,
        names=names,
        kind=args.kind,
        tol=args.tol,
        min_points=args.min_points,
        errorbar_aspect=args.errorbar_aspect,
        dedupe=not args.no_dedupe,
        exclude=wiring.exclude_boxes or None,
    )

    if not series:
        _die("未提取到任何序列。可调整 --tol、--kind，或用 --colors 显式指定曲线颜色。")
        if rep and rep.warnings:
            for w in rep.warnings:
                print(f"  ⚠ {w}", file=sys.stderr)
        return EXIT_FAILED

    if args.resample:
        from .extract import resample_polyline

        for s in series:
            if s.kind == "line":
                s.points = resample_polyline(s.points, n=args.resample)

    data = digitize(series, calib, sort_by_x=True)

    # ---------------- 标定审核：尺度来源标注 + 几何交叉校验 + 人工确认留痕
    x_scale, x_source = _axis_scale(args, "x")
    y_scale, y_source = _axis_scale(args, "y")
    tick_x, tick_y = ticks_for_box(img, box, dark_threshold=args.dark_threshold)
    audit = build_audit(
        box, x_scale, x_source, (calib.xmin.value, calib.xmax.value),
        y_scale, y_source, (calib.ymin.value, calib.ymax.value),
        tick_x=tick_x, tick_y=tick_y, confirmed=args.confirm_scale, calib=calib,
    )

    # 严格模式：审核有警告且无人签字确认时，不写出交付物。人工确认（--confirm-scale）
    # 可越过警告——这正是"交由人工审核"的落地形式：机器报警，人签字负责。
    if args.strict_scale and not audit.ok and not audit.confirmed:
        print("标定审核:")
        for ln in audit.render():
            print(f"  {ln}")
        _die(
            f"标定审核发现 {len(audit.warnings)} 条警告，--strict-scale 下拒绝写出交付物。"
            "请逐条核对；确认无误后加 --confirm-scale 重跑以留痕。",
            EXIT_NEEDS_REVIEW,
        )
        return EXIT_NEEDS_REVIEW

    stem = Path(args.image).stem
    base = Path(args.out) if args.out else Path(f"{stem}_digitized")
    formats = [f.lower() for f in args.format]
    written: list[Path] = []

    if "csv" in formats:
        written += write_csv(data, base.with_suffix(".csv"), source=str(args.image),
                             calib=calib, audit=audit, legend=wiring.legend)
    if "xlsx" in formats:
        written.append(
            write_xlsx(data, base.with_suffix(".xlsx"), source=str(args.image),
                       calib=calib, report=rep.to_dict() if rep else None, audit=audit,
                       legend=wiring.legend)
        )
    if "json" in formats:
        written.append(
            write_json(data, base.with_suffix(".json"), source=str(args.image),
                       calib=calib, report=rep.to_dict() if rep else None,
                       audit=audit,
                       legend=wiring.legend,
                       params={
                           "axes_box": list(box), "region": list(region),
                           "kind": args.kind, "tol": args.tol,
                           "inset": inset, "engine": args.engine,
                           "exclude_boxes": [list(b) for b in wiring.exclude_boxes],
                       })
        )

    gdw_path = None
    if args.gdw or args.open or args.engine == "getdata":
        target = Path(args.gdw) if args.gdw else base.with_suffix(".gdw")
        try:
            gdw_path, used_img = build_workspace(
                args.image, series, calib, target, captions=[s.name for s in series]
            )
            written.append(gdw_path)
            if not args.open:
                print(f"GetData 工作区: {gdw_path}（图片副本 {used_img}）")
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] 生成 GetData 工作区失败: {exc}", file=sys.stderr)

    if args.diag:
        p = render_diagnostic(
            img, rep, args.diag,
            curve_points={s.name: s.points for s in series},
        ) if rep else None
        if p:
            written.append(p)
            print(f"诊断图: {p}")
        else:
            # 诊断图需要在图内检出面板/轴线，故只在**未给 --axes** 时可用。
            # 用了 --axes（逐面板手工标定，开放脊多面板的常规做法）时 rep 为 None，
            # 此处**必须出声**而不是静默什么都不写——否则用户会以为诊断图已生成。
            print(
                f"[提示] 指定了 --diag 但同时给了 --axes，未生成诊断图："
                f"诊断图基于自动检测结果，与手工数据区无法叠加。"
                f"如需要，去掉 --axes 单独跑一次 detect："
                f"\n         chartdigit.py detect {args.image} --diag {args.diag}",
                file=sys.stderr,
            )

    print(f"源图像: {args.image}  {img.width}x{img.height}")
    print(f"数据区像素: {tuple(round(v, 1) for v in box)}   提取区域: {tuple(round(v, 1) for v in region)}")
    chk = calib.verify()
    print(
        f"标定: x=[{calib.xmin.value:g}, {calib.xmax.value:g}] "
        f"y=[{calib.ymin.value:g}, {calib.ymax.value:g}] "
        f"尺度=({calib.xscale},{calib.yscale}) "
        f"倾斜={chk['skew_degrees']}° 往返误差={chk['max_roundtrip_error_px']}px"
    )
    if rep:
        print(f"结构置信度: {rep.confidence}")
        for w in rep.warnings:
            print(f"  ⚠ {w}")
    print("提取结果:")
    print(summarize(data))
    print("标定审核:")
    for ln in audit.render():
        print(f"  {ln}")
    if wiring.legend is not None:
        print(f"{legend_header(wiring.legend)}:")
        print(f"  来源: {wiring.legend.source if wiring.legend.found else '未检出'}")
        for k, v in legend_rows(wiring.legend):
            print(f"  {k}: {v}")
        if wiring.exclude_boxes:
            print(f"  排除掩码: {len(wiring.exclude_boxes)} 个矩形已在颜色掩码阶段置背景")
    print("产物:")
    for p in written:
        print(f"  {p}")

    if args.json_report:
        _out(**{"calibration_check": chk, "scale_audit": audit.to_dict(),
                "series": [s.to_dict() for s in data],
                "legend": wiring.legend.to_dict() if wiring.legend else None})

    if args.open and gdw_path:
        from .getdata import launch

        try:
            launch(gdw_path, exe=args.exe)
            print(f"已用 GetData 打开 {gdw_path}")
        except FileNotFoundError as exc:
            print(f"[警告] {exc}", file=sys.stderr)
            return EXIT_NO_GETDATA
    return EXIT_OK


def cmd_to_gdw(args: argparse.Namespace) -> int:
    """把已有的数据文件（x,y[,...]）转成 GetData 工作区。"""
    import csv as _csv

    src = Path(args.data)
    if not src.is_file():
        return _die(f"数据文件不存在: {src}")
    rows: list[tuple[float, float]] = []
    with src.open("r", encoding="utf-8-sig", newline="") as fh:
        rd = _csv.reader(fh)
        header_skipped = False
        for row in rd:
            if not row:
                continue
            if row[0].lstrip().startswith("#"):
                continue
            if not header_skipped and not _is_float(row[0]):
                header_skipped = True
                continue
            if len(row) >= 2 and _is_float(row[0]) and _is_float(row[1]):
                rows.append((float(row[0]), float(row[1])))
    if not rows:
        return _die(f"未能从 {src} 解析出 x,y 数值行")

    if args.axes and args.xlim and args.ylim:
        left, top, right, bottom = args.axes
        calib = Calibration.from_box(
            left, top, right, bottom,
            float(args.xlim[0]), float(args.xlim[1]),
            float(args.ylim[0]), float(args.ylim[1]),
        )
    elif args.xcal and args.ycal:
        x1, y1, v1 = args.xcal[0]
        x2, y2, v2 = args.xcal[1]
        a1, b1, w1 = args.ycal[0]
        a2, b2, w2 = args.ycal[1]
        calib = Calibration.from_axes_pair((x1, y1), v1, (x2, y2), v2,
                                           (a1, b1), w1, (a2, b2), w2)
    else:
        return _die("需要 --axes 与 --xlim/--ylim，或 --xcal/--ycal 以建立像素->数据映射")

    pix = [calib.to_pixel(x, y) for x, y in rows]
    series = [Series(name=args.name, kind="line", points=pix)]
    target = Path(args.out) if args.out else src.with_suffix(".gdw")
    gdw_path, used = build_workspace(args.image, series, calib, target)
    print(f"已写出工作区: {gdw_path}")
    print(f"引用图片: {used}")
    if args.open:
        from .getdata import launch

        launch(gdw_path, exe=args.exe)
        print("已用 GetData 打开")
    return EXIT_OK


def cmd_open(args: argparse.Namespace) -> int:
    from .getdata import launch

    try:
        launch(args.workspace, exe=args.exe, wait=args.wait)
    except FileNotFoundError as exc:
        return _die(str(exc), EXIT_NO_GETDATA)
    print(f"已启动 GetData: {args.workspace}")
    return EXIT_OK


def cmd_dump(args: argparse.Namespace) -> int:
    from .getdata import read_workspace

    data, calib, ws = read_workspace(args.workspace)
    base = Path(args.out) if args.out else Path(args.workspace).with_suffix("")
    formats = [f.lower() for f in args.format]
    written: list[Path] = []
    if "csv" in formats:
        written += write_csv(data, base.with_suffix(".csv"), source=ws.image_path, calib=calib)
    if "xlsx" in formats:
        written.append(write_xlsx(data, base.with_suffix(".xlsx"), source=ws.image_path, calib=calib))
    if "json" in formats:
        written.append(write_json(data, base.with_suffix(".json"), source=ws.image_path, calib=calib))

    print(f"工作区: {args.workspace}  引用图片: {ws.image_path}  {ws.width}x{ws.height}")
    print(f"曲线数: {len(data)}")
    print(summarize(data))
    print("产物:")
    for p in written:
        print(f"  {p}")
    return EXIT_OK


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------ from-pdf（文献 PDF 前置层）

#: 手工框选指引。定位失败与歧义两条路径都要打印它——用户唯一的确定性出口。
_MANUAL_HINT = (
    "手工框选（PDF 点坐标，原点在页面左上角、y 向下）：\n"
    "  --page <页码，1 起> --rect <left,top,right,bottom>\n"
    "  例：--page 5 --rect 43.6,130,266.5,372"
)


def _figure_list_text(pdf_path: Path, page_count: int, cands) -> str:
    """``--list`` 的文本报告：页码、标签、bbox、来源、置信度、图注首 60 字。"""
    lines = [f"PDF: {pdf_path}（共 {page_count} 页）"]
    if not cands:
        lines.append("未检出任何图。")
        lines.append(_MANUAL_HINT)
        return "\n".join(lines)
    lines.append(f"检出 {len(cands)} 个候选：")
    for i, c in enumerate(cands, 1):
        x0, y0, x1, y1 = c.bbox_pt
        cap = " ".join(c.caption.split())[:60]
        amb = f"  [同编号 {c.ambiguous_with} 个候选，需消歧]" if c.ambiguous else ""
        lines.append(
            f"  #{i:<2} 第 {c.page_no + 1:>3} 页  标签={c.label:<4} "
            f"bbox_pt=({x0:.2f}, {y0:.2f}, {x1:.2f}, {y1:.2f})  "
            f"来源={c.source:<14} 置信度={c.confidence:.2f}{amb}"
        )
        if cap:
            lines.append(f"       图注: {cap}")
    return "\n".join(lines)


def _emit_crop(pdf_path: Path, doc, cand, out: str | None, dpi: int) -> int:
    """裁剪 + 打印溯源信息 + 打印可直接串起来的下一步命令。"""
    from .pdf import crop_figure

    stem = pdf_path.stem
    tag = cand.label if cand.label not in ("", "?") else "x"
    out_arg = out or f"{stem}_fig{tag}"
    out_path = Path(out_arg)
    if out_path.suffix.lower() != ".png":
        # 不用 with_suffix：它会把 "fig.3" 这类含点的名字截断
        out_path = Path(str(out_path) + ".png")

    crop = crop_figure(pdf_path, cand, out_path, dpi=dpi, doc=doc)
    x0, y0, x1, y1 = crop.rect_pt
    w, h = crop.px_size
    sidecar = Path(crop.image_path).with_suffix(".png.json")
    print(f"已裁出: {crop.image_path}")
    print(
        f"  第 {crop.page_no + 1} 页  rect_pt=({x0:.2f}, {y0:.2f}, {x1:.2f}, {y1:.2f})  "
        f"dpi={crop.dpi}  尺寸={w}×{h} px"
    )
    print(
        f"  来源={cand.source}  置信度={cand.confidence:.2f}"
        + (f"  图注: {' '.join(cand.caption.split())[:60]}" if cand.caption else "")
    )
    print(f"  溯源: {sidecar}")
    print()
    print("下一步：")
    entry = Path(__file__).resolve().parents[1] / "chartdigit.py"
    print(
        f'  "{sys.executable}" "{entry}" extract "{crop.image_path}" '
        "--axes L,T,R,B --xlim XMIN XMAX --ylim YMIN YMAX --format csv xlsx json"
    )
    print(
        "  说明：--axes 是**裁剪图内**的数据区像素矩形（例如 L,T,R,B 为 "
        f"0,0,{w},{h} 内的子矩形），可先跑 detect 看候选；--xlim/--ylim 为轴数值范围。"
    )
    print()
    print("  可选：把上面这条命令加上下面两个开关，即可从 PDF 文本层自动剔除图内文字"
          "（轴标题/面板标签/图例），并顺带提取图例、按图例配色：")
    print(f'    --exclude-from-pdf "{pdf_path}" --page {crop.page_no + 1} '
          f"--rect {x0:.2f},{y0:.2f},{x1:.2f},{y1:.2f}")
    print("  图例是可选增强：颜色校验不通过时自动回退自动配色，不影响取数。")
    return EXIT_OK


def cmd_from_pdf(args: argparse.Namespace) -> int:
    """文献 PDF → 定位图 → 局部渲染裁图。三通道定位见 ``chartdigit.pdf``。"""
    try:
        import pymupdf
    except ImportError as exc:  # 保持其余子命令在缺 pymupdf 时仍可用
        return _die(f"from-pdf 需要 pymupdf，但导入失败: {exc}")

    from .pdf import DEFAULT_DPI, MIN_DPI, FigureCandidate, locate_figures

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        return _die(f"PDF 不存在: {pdf_path}")

    # --- 选择器：三者互斥，且必须显式给出一个
    manual = args.page is not None or args.rect is not None
    chosen = [bool(args.list), args.figure is not None, manual]
    if sum(chosen) > 1:
        return _die(
            "--list / --figure / （--page + --rect）三者互斥，请只保留一个", EXIT_USAGE
        )
    if not any(chosen):
        return _die(
            "未指定要做什么。请用 --list 列出候选、--figure N 裁出指定图，"
            f"或 --page + --rect 手工框选。\n{_MANUAL_HINT}",
            EXIT_USAGE,
        )
    if (args.page is None) != (args.rect is None):
        return _die("--page 与 --rect 必须同时给出（手工框选路径）", EXIT_USAGE)

    dpi = int(args.dpi) if args.dpi else DEFAULT_DPI
    if dpi < MIN_DPI:
        return _die(
            f"--dpi 下限为 {MIN_DPI}（收到 {dpi}）；低于此等效精度显著劣化", EXIT_USAGE
        )

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:
        return _die(f"无法打开 PDF: {exc}")

    try:
        # --- 1. --list：无副作用，永远退出码 0
        if args.list:
            print(_figure_list_text(pdf_path, doc.page_count, locate_figures(doc)))
            return EXIT_OK

        # --- 2. 手工框选：不依赖任何定位
        if manual:
            pno = int(args.page) - 1
            if not (0 <= pno < doc.page_count):
                return _die(
                    f"页码超范围: --page {args.page}（本 PDF 共 {doc.page_count} 页）",
                    EXIT_USAGE,
                )
            cand = FigureCandidate(
                page_no=pno,
                label="?",
                rect=pymupdf.Rect(*args.rect),
                source="manual",
                caption="",
                confidence=1.0,
            )
            return _emit_crop(pdf_path, doc, cand, args.out, dpi)

        # --- 3. --figure N
        cands = locate_figures(doc)
        want = str(args.figure).strip()
        numbered = [c for c in cands if c.label != "?"]
        matched = [c for c in numbered if c.label == want]

        if not matched:
            print(_figure_list_text(pdf_path, doc.page_count, cands), file=sys.stderr)
            unnumbered = [c for c in cands if c.label == "?"]
            if unnumbered and not numbered:
                why = (
                    f"本 PDF 未检出带编号的图注：{len(unnumbered)} 个候选均无编号"
                    f"（来源 {unnumbered[0].source}），--figure 无法选中。"
                )
            else:
                why = f"未找到编号为 {want} 的图。"
            return _die(f"{why}\n{_MANUAL_HINT}", EXIT_USAGE)

        if len(matched) > 1:
            print(
                f"[错误] 编号 {want} 有 {len(matched)} 个候选，无法确定是哪一张"
                "——本命令不做猜测：",
                file=sys.stderr,
            )
            for i, c in enumerate(matched, 1):
                x0, y0, x1, y1 = c.bbox_pt
                print(
                    f"  候选 {i}: 第 {c.page_no + 1} 页  "
                    f"bbox_pt=({x0:.2f}, {y0:.2f}, {x1:.2f}, {y1:.2f})  "
                    f"来源={c.source}  图注: {' '.join(c.caption.split())[:60]}",
                    file=sys.stderr,
                )
            print(f"\n{_MANUAL_HINT}\n可直接复制的候选：", file=sys.stderr)
            for c in matched:
                x0, y0, x1, y1 = c.bbox_pt
                print(
                    f"  --page {c.page_no + 1} --rect {x0:.2f},{y0:.2f},{x1:.2f},{y1:.2f}",
                    file=sys.stderr,
                )
            return EXIT_USAGE

        return _emit_crop(pdf_path, doc, matched[0], args.out, dpi)
    finally:
        doc.close()


# ------------------------------------------------------------------ 参数定义


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chart-digitizer",
        description="从图表图片提取曲线/散点/误差棒数据，并可与 GetData Graph Digitizer 联用做复核。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"chart-digitizer {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--exe", help="GetDataW.exe 路径（默认自动定位）")

    # --- doctor
    sp = sub.add_parser("doctor", help="环境自检")
    add_common(sp)
    sp.set_defaults(func=cmd_doctor)

    # --- from-pdf
    sp = sub.add_parser("from-pdf", help="从文献 PDF 定位插图并裁出 PNG（无副作用探测用 --list）")
    sp.add_argument("pdf", help="文献 PDF 路径")
    sp.add_argument("--list", action="store_true",
                    help="列出 PDF 内检出的全部候选图（不写任何文件，随时可跑）")
    sp.add_argument("--figure", metavar="N",
                    help="要裁的图编号，如 3 / 3a（对应 --list 输出的「标签」）")
    sp.add_argument("--page", type=int, metavar="N",
                    help="手工框选：页码（1 起，同 PDF 阅读器显示）")
    sp.add_argument("--rect", type=_parse_rect_pt, metavar="L,T,R,B",
                    help="手工框选：PDF 点坐标矩形 left,top,right,bottom")
    sp.add_argument("--dpi", type=int, default=None,
                    help="裁剪分辨率（默认 600；下限 300）")
    sp.add_argument("-o", "--out", help="输出 PNG 路径或前缀（默认 <PDF名>_figN）")
    add_common(sp)
    sp.set_defaults(func=cmd_from_pdf)

    # --- detect
    sp = sub.add_parser("detect", help="仅做结构检测并输出报告")
    sp.add_argument("image")
    sp.add_argument("--dark-threshold", type=int, default=110,
                    help="暗色前景阈值（0-255，默认 110）")
    sp.add_argument("--min-ratio", type=float, default=None,
                    help="判定为轴线的长度占比阈值；缺省启用多尺度自适应搜索")
    sp.add_argument("--no-ticks", action="store_true", help="跳过刻度检测")
    sp.add_argument("--json", action="store_true", help="输出 JSON 而非文本")
    sp.add_argument("--diag", help="输出标注了检测结果的诊断图路径")
    add_common(sp)
    sp.set_defaults(func=cmd_detect)

    # --- extract
    sp = sub.add_parser("extract", help="提取数据（主命令）")
    sp.add_argument("image")
    sp.add_argument("-o", "--out", help="输出路径或前缀（默认 <图像名>_digitized）")
    sp.add_argument("--format", nargs="+", default=["csv"],
                    choices=["csv", "xlsx", "json"], help="交付格式（可多选）")
    sp.add_argument("--engine", choices=["native", "getdata", "auto"], default="auto",
                    help="native=纯算法提取；getdata=同时生成工作区；auto=默认（等同 native+可选工作区）")
    sp.add_argument("--axes", type=_parse_box,
                    help="数据区像素矩形 left,top,right,bottom（缺省则自动检测）")
    sp.add_argument("--panel", type=int, help="选用第 N 个面板（1 起，配合自动检测）")
    sp.add_argument("--region", type=_parse_box, help="覆盖提取区域（像素矩形）")
    sp.add_argument("--inset", type=int, default=2,
                    help="数据区内缩像素数，避开轴框线（默认 2）")
    sp.add_argument("--xlim", nargs=2, metavar=("XMIN", "XMAX"),
                    help="x 轴数值范围（需配合 --axes 或自动检测到的数据区）")
    sp.add_argument("--ylim", nargs=2, metavar=("YMIN", "YMAX"), help="y 轴数值范围")
    sp.add_argument("--xcal", nargs=2, type=_parse_anchor, metavar="PX,PY,VAL",
                    help="x 轴两个标定锚点（支持倾斜轴）")
    sp.add_argument("--ycal", nargs=2, type=_parse_anchor, metavar="PX,PY,VAL",
                    help="y 轴两个标定锚点")
    sp.add_argument("--log-x", action="store_true", help="x 轴为对数刻度")
    sp.add_argument("--log-y", action="store_true", help="y 轴为对数刻度")
    sp.add_argument("--scale-x", choices=["linear", "log"], default=None,
                    help="x 轴尺度的显式声明（--log-x 的通用形式）。不给出时按默认 "
                         "linear 处理，并在标定审核中标注来源为「默认」")
    sp.add_argument("--scale-y", choices=["linear", "log"], default=None,
                    help="y 轴尺度的显式声明。不给出时按默认 linear 处理并标注为「默认」")
    sp.add_argument("--confirm-scale", action="store_true",
                    help="声明已人工核对尺度、范围与单位；审核状态与时间戳写入全部交付物")
    sp.add_argument("--strict-scale", action="store_true",
                    help="标定审核存在警告时以退出码 4 结束（供自动化流水线把关）")
    sp.add_argument("--colors", nargs="+", help="显式指定曲线颜色，如 '#d62728' 或 '214,39,40'")
    sp.add_argument("--names", nargs="+", help="序列名称（与 --colors 顺序对应）")
    sp.add_argument("--exclude", type=_parse_box, action="append", metavar="L,T,R,B",
                    help="排除矩形（**图像像素**坐标，可多次给出）；在颜色掩码生成后、"
                         "连通域分析前置为背景，用于剔除文字标注区")
    sp.add_argument("--exclude-from-pdf", metavar="PDF",
                    help="从文献 PDF 的文本层自动生成排除矩形，并提取该图的图例"
                         "（条目名 + 精确 RGB + 图元类型提示 + 颜色一致性校验）；"
                         "需与 --page/--rect 同时给出。失败只告警并回退自动配色")
    sp.add_argument("--page", type=int, metavar="N",
                    help="--exclude-from-pdf 用：图所在页码（1 起，同 PDF 阅读器显示）")
    sp.add_argument("--rect", type=_parse_rect_pt, metavar="L,T,R,B",
                    help="--exclude-from-pdf 用：该图的 PDF 点矩形（取自 from-pdf --list）")
    sp.add_argument("--kind", choices=["auto", "line", "scatter", "errorbar"], default="auto",
                    help="图元类型（默认按连通域形态自动判别）")
    sp.add_argument("--tol", type=float, default=64.0, help="颜色匹配容差（默认 64）")
    sp.add_argument("--min-points", type=int, default=8,
                    help="序列最少点数（默认 8；稀疏噪声序列多由此滤除）")
    sp.add_argument("--errorbar-aspect", type=float, default=2.2,
                    help="误差棒连通域高宽比阈值（默认 2.2）")
    sp.add_argument("--resample", type=int, metavar="N", help="对曲线重采样为 N 个点")
    sp.add_argument("--no-dedupe", action="store_true",
                    help="关闭抗锯齿边缘色去重（默认开启；仅在同色多曲线被误合并时使用）")
    sp.add_argument("--dark-threshold", type=int, default=110,
                    help="暗色前景阈值（0-255，默认 110）")
    sp.add_argument("--min-ratio", type=float, default=None,
                    help="判定为轴线的长度占比阈值；缺省启用多尺度自适应搜索")
    sp.add_argument("--gdw", help="额外生成 GetData 工作区（路径）")
    sp.add_argument("--open", action="store_true", help="生成工作区后立即用 GetData 打开")
    sp.add_argument("--diag", help="输出诊断图（原图 + 提取点叠加）")
    sp.add_argument("--json-report", action="store_true", help="额外打印 JSON 报告")
    add_common(sp)
    sp.set_defaults(func=cmd_extract)

    # --- to-gdw
    sp = sub.add_parser("to-gdw", help="把已有数据文件转为 GetData 工作区")
    sp.add_argument("data", help="含 x,y 列的 CSV/文本文件")
    sp.add_argument("image", help="对应的原始图片")
    sp.add_argument("-o", "--out", help="输出 .gdw 路径")
    sp.add_argument("--name", default="Curve 1", help="曲线名称")
    sp.add_argument("--axes", type=_parse_box)
    sp.add_argument("--xlim", nargs=2)
    sp.add_argument("--ylim", nargs=2)
    sp.add_argument("--xcal", nargs=2, type=_parse_anchor)
    sp.add_argument("--ycal", nargs=2, type=_parse_anchor)
    sp.add_argument("--open", action="store_true")
    add_common(sp)
    sp.set_defaults(func=cmd_to_gdw)

    # --- open
    sp = sub.add_parser("open", help="用 GetData 打开工作区")
    sp.add_argument("workspace")
    sp.add_argument("--wait", action="store_true", help="等待 GetData 退出")
    add_common(sp)
    sp.set_defaults(func=cmd_open)

    # --- dump
    sp = sub.add_parser("dump", help="解析工作区并回读为数据文件")
    sp.add_argument("workspace")
    sp.add_argument("-o", "--out", help="输出路径或前缀")
    sp.add_argument("--format", nargs="+", default=["csv"],
                    choices=["csv", "xlsx", "json"])
    add_common(sp)
    sp.set_defaults(func=cmd_dump)

    return p


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        return _die(str(exc))
    except ValueError as exc:
        return _die(str(exc))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
