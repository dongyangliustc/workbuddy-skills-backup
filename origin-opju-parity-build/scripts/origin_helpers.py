# -*- coding: utf-8 -*-
"""origin_helpers.py — 可复用的 originpro/COM 辅助函数库。

配合 SKILL.md 的七步流程使用；全部函数都经过 OriginPro 2023 教育版 +
originpro 1.1.15 实测验证（2026-09-17，REA TD 误差棒图全流程）。

用法骨架：
    import origin_helpers as oh
    import originpro as op
    oh.kill_other_instances()            # 可选：PowerShell 里做更稳
    op.set_show(True); op.new()
    ...建 book/sheet、plotxy...
    info = oh.layer_info(gp[0])          # 解析每个 plot 的来源
    ...按 (book, col, sheet) 分派样式...
    oh.set_log_axes(gl, (0.75, 620), (5e-19, 2.5e-12))
    oh.set_axis_titles('T (K)', 'REA rate coefficients cm\\+(-3) s\\+(-1)')
    oh.update_legend(fsize=11, x=1.6, y=2e-13)
    oh.export_preview(r"D:/dir", "origin_preview")
"""
import numpy as np
import originpro as op


# ---------------------------------------------------------------- LabTalk ---
def lt(cmd, critical=False):
    """单条 LabTalk 执行 + 失败打印。严禁把多条语句拼一个字符串发（会静默失败）。"""
    r = op.lt_exec(cmd)
    if not r:
        print(f"[FAIL] {cmd}")
        if critical:
            raise RuntimeError(cmd)
    return r


# ------------------------------------------------------- plot 来源解析 ------
def parse_sname(sn):
    """'FitCurves_B@3' -> ('FitCurves', 'B', 3)。

    plot 数据集名格式为 Book_列短名@Sheet序号（同一 book 内 @n 按 sheet 序号
    区分）。注意：Plot.lname 在部分环境返回空串，不能按长名匹配，必须走这里。
    """
    stem, _, sheet = sn.partition("@")
    book, _, col = stem.partition("_")
    return book, col, int(sheet) if sheet.isdigit() else 0


def layer_info(gl):
    """返回 [(idx, plot, book, col, sheet, symbol_kind), ...]（idx 从 0 起）。

    原生 COM 属性在 gl.obj（PyOrigin GraphLayer）上，不在 GLayer 包装类上。
    """
    info = []
    for idx, p in enumerate(gl.plot_list()):
        try:
            sn = gl.obj.GetStrProp(f"plot{idx + 1}.name") or ""
        except Exception:
            sn = ""
        try:
            sym = gl.obj.GetNumProp(f"plot{idx + 1}.symbol.kind")
        except Exception:
            sym = 0.0
        book, col, sheet = parse_sname(sn)
        info.append((idx, p, book, col, sheet, sym))
        print(f"plot {idx}: {sn!r} symbol.kind={sym}")
    return info


# ------------------------------------------------- 非对称误差棒（手工线段）--
def err_segments(T, td, up, dn, cap_factor=1.07, rel_thresh=0.005):
    """构造 NaN 分隔的误差棒折线顶点（垂直段 + 上下端帽）。

    背景：plotxy 4 列 iy=(X,Y,E1,E2) 只画对称 ±E1（E2 被忽略）；set -om/-op
    的 -op 在 Origin 2023 无效。故误差棒一律手工线段化，100% 可控。
    log 轴端帽用等比因子 cap_factor（1.07 ≈ matplotlib capsize 2.6 的视觉宽度）。

    返回 (xs, ys)；若所有点相对误差 < rel_thresh 或为 0，返回 None（跳过，
    避免图例垃圾条目）。
    """
    T, td, up, dn = (np.asarray(v) for v in (T, td, up, dn))
    pos_t = td > 0
    rel = 0.0
    if pos_t.any():
        rel = max(
            float(np.max(up[pos_t] / td[pos_t], initial=0)),
            float(np.max(dn[pos_t] / td[pos_t], initial=0)),
        )
    if rel < rel_thresh:
        return None
    xs, ys = [], []
    for Ti, tdi, upi, dni in zip(T, td, up, dn):
        if upi <= 0 and dni <= 0:
            continue
        ylo, yhi = tdi - dni, tdi + upi
        x1, x2 = Ti / cap_factor, Ti * cap_factor
        xs += [Ti, Ti, np.nan]; ys += [ylo, yhi, np.nan]   # 垂直棒
        xs += [x1, x2, np.nan]; ys += [ylo, ylo, np.nan]   # 下端帽
        xs += [x1, x2, np.nan]; ys += [yhi, yhi, np.nan]   # 上端帽
    return xs, ys


def write_err_sheet(wb, sheet_name, xs, ys, legend_label):
    """把误差段写入 wb 的新 sheet（列长名 = 图例条目文字）。"""
    wke = wb.add_sheet(sheet_name)
    wke.from_list(0, list(xs), "T")
    wke.from_list(1, list(ys), legend_label)
    return wke


# ---------------------------------------------------------------- 轴/图例 ---
def set_log_axes(gl, x_range, y_range, inc=1):
    """双 log 轴 + 范围 + 每十年主刻度。range 为 (min, max) 轴值。"""
    lt("layer.x.type=2")
    lt("layer.y.type=2")
    gl.set_xlim(*x_range)
    gl.set_ylim(*y_range)
    lt(f"layer.x.inc={inc}")
    lt(f"layer.y.inc={inc}")


def set_axis_titles(x_title, y_title):
    """轴标题；上标用 Origin 转义如 'cm\\+(-3) s\\+(-1)'（\\+()=上标）。"""
    lt(f'xb.text$="{x_title}"')
    lt(f'yl.text$="{y_title}"')


def update_legend(fsize=11, x=None, y=None):
    """按列长名重建图例并定位。x/y 用轴值坐标（log 轴直接给数值），
    锚点≈图例中心；条目多时先缩 fsize 再定位，否则溢出页面。"""
    lt("legendupdate mode:=lname")
    if fsize:
        lt(f"legend.fsize={fsize}")
    if x is not None:
        lt(f"legend.x={x}")
    if y is not None:
        lt(f"legend.y={y}")


# ---------------------------------------------------------------- 自检导出 --
def export_preview(path, name="origin_preview"):
    """导出 PNG 供 Read 工具视觉核验。不要带 igp:/tr1: 参数（会失败）。"""
    import os
    fp = f"{path}/{name}.png"
    if os.path.exists(fp):
        os.remove(fp)
    lt(f'expGraph type:=png path:="{path}" filename:="{name}"')
    ok = os.path.exists(fp)
    print("preview:", fp, ok)
    return ok
