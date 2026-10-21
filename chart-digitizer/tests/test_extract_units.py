"""针对已知缺陷的确定性单元测试。

**为什么不只用图形化的端到端测试**

本技能的端到端精度测试依赖 matplotlib 光栅化的**偶然结果**。实测过这样的情形：
同一个缺陷（浅色抗锯齿候选色吞掉浅灰网格线）在一种图几何下触发、在另一种下不触发
——判据是「光晕色到网格灰的距离」是否小于 `tol=64`：

    图 A（subplots_adjust）光晕 (244,200,200)，到网格灰 (231,231,231) 距离 52.9 → 触发
    图 B（tight_layout）   光晕 (241,184,184)，到网格灰 (231,231,231) 距离 67.2 → 不触发

于是「换个图就不覆盖了」，测试失去意义。本文件把这些机制写成**与渲染无关**的
确定性断言，直接锁住修复本身。

约定：每个测试对应一处已修复的实测缺陷，注释里写明缺陷现象。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from chartdigit import Calibration  # noqa: E402
from chartdigit.extract import (  # noqa: E402
    Series,
    color_mask,
    dedupe_series,
    dominant_colors,
    drop_antialias_colors,
    extract_series,
    _components,
    _halo_tolerance,
    _infer_kind,
    _looks_like_text,
)

WHITE = (255, 255, 255)
CORE_RED = (217, 54, 55)

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


# ---------------------------------------------------------------- 1. 抗锯齿色剔除

def test_drop_antialias() -> None:
    """缺陷：浅色光晕色被当作独立曲线，且其掩码会吞掉浅灰网格线。"""
    print("== drop_antialias_colors ==")

    # 纯色芯与背景之间 t=0.71 处的混合色（实测光晕）
    halo = (244, 200, 200)
    grid_gray = (228, 228, 228)

    kept = drop_antialias_colors([CORE_RED, halo, grid_gray], WHITE)
    check("剔除混合色，保留纯色芯", CORE_RED in kept and halo not in kept,
          f"kept={kept}")

    # 关键：网格灰不能因「也偏浅」被误杀（它与 CORE→WHITE 连线不共线）
    check("不误杀网格线灰 (228,228,228)", grid_gray in kept, f"kept={kept}")

    # 顺序无关：先给光晕也不应影响结果
    kept2 = drop_antialias_colors([halo, CORE_RED], WHITE)
    check("候选顺序不影响结果", kept2 == [CORE_RED], f"kept={kept2}")

    # 与像素计数无关：光晕像素数多于纯色芯时仍应保留纯色芯
    many_halo = [halo] * 5 + [CORE_RED]
    kept3 = drop_antialias_colors(many_halo, WHITE)
    check("不以出现次数决定保留谁", kept3 == [CORE_RED], f"kept={kept3}")

    # 边界：单色 / 无背景时原样返回
    check("单色原样返回", drop_antialias_colors([CORE_RED], WHITE) == [CORE_RED])
    check("无背景色原样返回",
          drop_antialias_colors([CORE_RED, halo], None) == [CORE_RED, halo])

    # 两条真实曲线（互不共线）都应保留
    blue = (31, 119, 180)
    both = drop_antialias_colors([CORE_RED, blue], WHITE)
    check("两条真实曲线均保留", len(both) == 2, f"kept={both}")

    # 位于线段延长线 t>1（比纯色芯更远离背景）的色不应被当作混合色
    beyond = tuple(int(min(255, CORE_RED[i] + 1.5 * (WHITE[i] - CORE_RED[i])))
                   for i in range(3))
    kept4 = drop_antialias_colors([CORE_RED, beyond], WHITE)
    check("t>0.94 的近背景色不判为混合色（交由 bg_margin 处理）",
          beyond in kept4, f"kept={kept4} beyond={beyond}")


# ---------------------------------------------------------------- 2. 背景剔除阈值

def test_bg_margin_matches_tol() -> None:
    """缺陷：背景剔除阈值(硬编码60) < 匹配容差(64) 的窗口 → 候选色吞掉整个背景。"""
    print("== dominant_colors 的 bg_margin ==")

    # 构造：白底 + 一块与白距离 61.0 的浅色 + 一块深色
    light = np.array([214, 236, 214])          # 到白距离 sqrt(41²+19²+41²)=61.0
    dark = np.array([40, 40, 40])
    dist = float(np.linalg.norm(light.astype(float) - np.array(WHITE, float)))
    check("构造的浅色到背景距离落在 (60,64) 窗口内", 60.0 < dist < 64.0,
          f"实测 {dist:.2f}")

    img = np.full((60, 60, 3), 255, dtype=np.uint8)
    img[5:25, 5:55] = light       # 1200 px
    img[30:50, 5:55] = dark       # 1200 px
    rgb = img.reshape(-1, 3).reshape(img.shape)

    got_60 = [c for c, _ in dominant_colors(rgb, background=WHITE, bg_margin=60.0)]
    got_64 = [c for c, _ in dominant_colors(rgb, background=WHITE, bg_margin=64.0)]

    check("bg_margin=60（旧硬编码值）会放行该浅色 → 这正是缺陷",
          any(abs(c[1] - 236) < 6 and abs(c[0] - 214) < 6 for c in got_60),
          f"got={got_60}")
    check("bg_margin=64（≡ tol）会拒绝它",
          not any(abs(c[1] - 236) < 6 and abs(c[0] - 214) < 6 for c in got_64),
          f"got={got_64}")
    check("深色在两档下都被保留",
          any(max(c) < 100 for c in got_60) and any(max(c) < 100 for c in got_64),
          f"60={got_60} 64={got_64}")


# ---------------------------------------------------------------- 3. 散点光晕容差

def _scatter(color, centers, meta=None) -> Series:
    return Series(name="s", kind="scatter", color=color,
                  points=list(centers), meta=meta or {})


def test_halo_tolerance_radius() -> None:
    """缺陷：散点光晕位于标记圆周（距圆心≈半径），超出点对点容差 4px 而漏判。"""
    print("== _halo_tolerance ==")

    # 标记面积 78.5 px² → 半径 5.0
    s = _scatter(CORE_RED, [(0, 0)], {"marker_area_median": np.pi * 25.0})
    check("按面积反推半径 5.0 → 容差 = 4 + 5 = 9", abs(_halo_tolerance(s, 4.0) - 9.0) < 1e-6,
          f"got {_halo_tolerance(s, 4.0):.4f}")

    line = Series(name="l", kind="line", color=CORE_RED, points=[(0, 0)])
    check("折线不受影响（光晕紧贴曲线芯）", _halo_tolerance(line, 4.0) == 4.0)

    noarea = _scatter(CORE_RED, [(0, 0)])
    check("缺面积元数据时退回基础容差", _halo_tolerance(noarea, 4.0) == 4.0)


def test_dedupe_removes_scatter_halo() -> None:
    """缺陷：散点图多出一条浅色「伪折线」（实测蓝色散点派生 28 点浅蓝序列）。"""
    print("== dedupe_series 散点光晕 ==")

    radius = 5.0
    centers = [(100.0 + 40 * i, 100.0 + 20 * ((i * 7) % 5)) for i in range(8)]
    # 光晕位于标记圆周：沿 x 方向偏移半径
    halo_pts = [(x + radius, y) for x, y in centers]

    d = np.hypot(*np.subtract(np.array(halo_pts), np.array(centers)).T)
    check("构造前提：光晕到圆心的距离 ≈ 半径 5px（> 基础容差 4px）",
          bool(np.allclose(d, radius)), f"距离={d[:3]}")

    core = _scatter(CORE_RED, centers, {"marker_area_median": np.pi * radius ** 2})
    halo = Series(name="h", kind="scatter", color=(244, 200, 200),
                  points=halo_pts, meta={"marker_area_median": np.pi * radius ** 2})

    kept = dedupe_series([core, halo], background=WHITE)
    check("偏离标记圆周的光晕被剔除", len(kept) == 1 and kept[0].color == CORE_RED,
          f"kept={[x.color for x in kept]}")

    # 若容差不随半径放宽，则这批点距圆心 5px > 4px，全部落在容差外 → 漏判。
    # 直接断言「半径放宽后容差覆盖 5px 偏移」是本修复的充要条件。
    check("放宽后的容差覆盖 5px 偏移（基础容差 4px 覆盖不到）",
          _halo_tolerance(core, 4.0) > radius >= 4.0,
          f"tol={_halo_tolerance(core, 4.0):.2f}")

    # 两组真实散点相距很远，不得被合并
    far = _scatter((31, 119, 180), [(x, y + 300) for x, y in centers],
                   {"marker_area_median": np.pi * radius ** 2})
    kept2 = dedupe_series([core, far], background=WHITE)
    check("相距很远的两组散点不被合并", len(kept2) == 2, f"kept={len(kept2)}")


# ---------------------------------------------------------------- 4. 对数标定

def test_log_calibration() -> None:
    """缺陷：误用线性尺度 → 对数轴数值偏差数量级（实测 42–178 等效像素）。"""
    print("== 对数标定 ==")

    cal = Calibration.from_box(0, 0, 100, 100,
                               x_min=1.0, x_max=1000.0,
                               y_min=1.0, y_max=10000.0,
                               xscale="log", yscale="log")

    # 几何中点：u=0.5 → sqrt(1*1000)=31.62（线性会给 500.5）
    x_mid, _ = cal.to_data(50.0, 100.0)
    check("x 几何中点 = 31.62（对数插值）", abs(x_mid - 31.6227766) < 1e-4,
          f"got {x_mid:.6f}")

    x_lin, _ = cal.to_data(50.0, 100.0)
    cal_lin = Calibration.from_box(0, 0, 100, 100,
                                   x_min=1.0, x_max=1000.0,
                                   y_min=1.0, y_max=10000.0)
    check("对照：线性尺度下同一点给 500.5（差 15.8 倍）",
          abs(cal_lin.to_data(50.0, 100.0)[0] - 500.5) < 1e-6,
          f"got {cal_lin.to_data(50.0, 100.0)[0]}")

    # 往返一致
    errs = []
    for u in np.linspace(0.0, 1.0, 21):
        for v in np.linspace(0.0, 1.0, 21):
            px, py = 100.0 * u, 100.0 * (1.0 - v)
            dx, dy = cal.to_data(px, py)
            bx, by = cal.to_pixel(dx, dy)
            errs.append(max(abs(bx - px), abs(by - py)))
    check("对数标定往返误差 < 1e-9 px", max(errs) < 1e-9, f"max={max(errs):.3e}")

    rep = cal.verify()
    check("verify() 报往返误差 ≈ 0",
          rep.get("max_roundtrip_error_px", 1.0) < 1e-6, f"{rep}")

    # 非正锚点应报错而不是静默出错
    try:
        bad = Calibration.from_box(0, 0, 10, 10, x_min=0.0, x_max=10.0,
                                   y_min=1.0, y_max=10.0, xscale="log")
        bad.to_data(5.0, 5.0)
        ok = False
    except ValueError:
        ok = True
    check("对数轴锚点非正时报 ValueError（不静默出错）", ok)


# -------------------------------------- 5. 陡曲线被切断后误判为误差棒（R8）

C_STEEP = (63, 63, 255)
C_SMOOTH = (255, 63, 63)
_R8_H, _R8_W = 760, 980


def _frag_steep_img(n_seg: int = 4, seg_w: int = 54, gap: int = 20,
                    slope: float = 2.5, thick: int = 5, y0: float = 735.0,
                    x0: int = 150, smooth: bool = True):
    """合成一幅「被遮挡切成 ``n_seg`` 段的陡粗曲线 +（可选）一条浅弧线」的图。

    陡（``slope``≥阈值所需）且粗（``thick``）的曲线本身会被判为 line；只有当它
    **被切断**成若干独立连通域后，每段才呈现「细高」形态（``aspect≈2.5``），从而
    触发 R8。返回 ``(img, segs)``。
    """
    img = np.full((_R8_H, _R8_W, 3), 255, dtype=np.uint8)
    segs, x = [], x0
    for _ in range(n_seg):
        segs.append((x, x + seg_w - 1))
        x += seg_w + gap
    for a, b in segs:
        xs = np.arange(a, b + 1)
        for x, yy in zip(xs, y0 - slope * (xs - x0)):
            yi = int(round(yy))
            img[yi:yi + thick, x] = C_STEEP
    if smooth:
        xs2 = np.arange(80, 900)
        for x, yy in zip(xs2, (120 + 45 * np.sin(xs2 / 130.0)).astype(int)):
            img[yy:yy + 2, x] = C_SMOOTH
    return img, segs


def _r8_tall(mask: np.ndarray):
    """返回 ``(分量数, aspect≥2.2 的个数, errorbar 判定阈值, 分量属性列表)``。"""
    props = [p for p in _components(mask)[1] if not _looks_like_text(p)]
    tall = sum(1 for p in props if p["aspect"] >= 2.2)
    return len(props), tall, max(3, int(0.25 * len(props))), props


def test_kind_auto_steep_fragmented_curve() -> None:
    """缺陷 R8（**未修，本测试只锁定其表征**）：陡粗曲线被遮挡切成数段后，
    每段都是细高连通域 → 判为 errorbar。

    实测来源：P3 文献 PDF 图 2 面板 a 的蓝色曲线 ``#3f3fff``（斜率接近横轴的
    3 倍且被相邻曲线遮挡成 3 段），``--kind auto`` 下该曲线**整条从输出中消失**。
    根因不是「陡」而是「被切断」：

        _infer_kind: tall(4) >= max(3, 0.25*4=1) = 3  →  "errorbar"
        errorbar 分支只用「细高分量」当数据点 → 4 点 < min_points(8) → 序列被丢弃

    下方同时锁住两种已观测表型（整条丢失 / 保住但标签与点数皆错），以及对照组
    （未打断的同斜率陡曲线判为 line，说明根因是切分而非斜率）。

    **为什么不修**：改 ``_infer_kind`` 属改动既有判定逻辑（会让既有精度基线失效），
    须单独立项并同时补陡/线性曲线的精度回归。当前处置 = 文档化规避
    （``--kind line``，见 ``troubleshooting.md`` DP5）+ 本测试锁定现有行为，
    使该缺陷**不会再"悄悄换一种图就消失"**。
    """
    print("== 陡曲线被切断 → _infer_kind (R8) ==")

    # ---- 对照组：未打断的同斜率陡曲线
    plain, _ = _frag_steep_img(n_seg=1, seg_w=250, gap=0, smooth=False)
    m_plain = color_mask(plain, C_STEEP, tol=64.0)
    n1, tall1, thr1, p1 = _r8_tall(m_plain)
    check("对照前提：未打断的陡曲线是单个连通域", n1 == 1, f"props={n1}")
    check("对照前提：该单分量本身就是「细高」的（aspect ≥ 2.2）——"
          "证明判成 errorbar 不是因为「陡」，而是因为「被切成多段」",
          tall1 == 1 and p1[0]["aspect"] >= 2.2,
          f"tall={tall1} aspect={p1[0]['aspect'] if p1 else None}")
    check("对照：单分量虽细高，但 1 < 阈值 3，仍判为 line（根因锁定为「切断」）",
          _infer_kind(m_plain, 2.2) == "line",
          f"got {_infer_kind(m_plain, 2.2)} tall={tall1}/{thr1}")

    # ---- 表型 A（P3 实测）：4 段 → 序列整条丢失
    img, segs = _frag_steep_img(n_seg=4)
    m = color_mask(img, C_STEEP, tol=64.0)
    n, tall, thr, _ = _r8_tall(m)
    check("构造前提：4 段各自成为独立连通域，且均超过 errorbar 高宽比",
          n == len(segs) == 4 and tall == 4, f"props={n} tall={tall}")
    check("构造前提：细高分量数 ≥ errorbar 判定阈值（这正是触发条件）",
          tall >= thr, f"tall={tall} thr={thr}")

    check("缺陷本体：_infer_kind 把该掩码判为 errorbar（应为 line）",
          _infer_kind(m, 2.2) == "errorbar", f"got {_infer_kind(m, 2.2)}")

    auto = {s.name: s for s in extract_series(
        img, (0, 0, _R8_W, _R8_H), colors=[C_STEEP, C_SMOOTH],
        names=["steep", "smooth"], kind="auto")}
    check("表型 A：--kind auto 下陡曲线整条从输出中消失",
          "steep" not in auto, f"names={sorted(auto)}")
    check("表型 A：同图的浅色曲线不受影响（说明是单条序列被误杀）",
          auto.get("smooth") is not None and auto["smooth"].kind == "line",
          f"{[(k, v.kind, len(v)) for k, v in auto.items()]}")

    line = {s.name: s for s in extract_series(
        img, (0, 0, _R8_W, _R8_H), colors=[C_STEEP, C_SMOOTH],
        names=["steep", "smooth"], kind="line")}
    check("显式 --kind line 可取回该曲线，且点数远多于「段数」",
          line.get("steep") is not None and line["steep"].kind == "line"
          and len(line["steep"]) > 10 * len(segs),
          f"{[(k, v.kind, len(v)) for k, v in line.items()]}")

    # ---- 表型 B：段数够多 → 保住但标签与点数皆错
    img9, segs9 = _frag_steep_img(n_seg=9, seg_w=30, gap=10)
    m9 = color_mask(img9, C_STEEP, tol=64.0)
    n9, tall9, thr9, _ = _r8_tall(m9)
    check("构造前提：9 段变体同样触发 errorbar 判定",
          tall9 >= thr9 and _infer_kind(m9, 2.2) == "errorbar",
          f"tall={tall9} thr={thr9}")

    auto9 = {s.name: s for s in extract_series(
        img9, (0, 0, _R8_W, _R8_H), colors=[C_STEEP, C_SMOOTH],
        names=["steep", "smooth"], kind="auto")}
    s9 = auto9.get("steep")
    check("表型 B：段数 ≥ min_points 时序列被保住，但标签错为 errorbar",
          s9 is not None and s9.kind == "errorbar",
          f"{[(k, v.kind, len(v)) for k, v in auto9.items()]}")
    # 关键区别：errorbar 分支的「点」是每个细高分量的质心，个数 ≡ 分量数，
    # 而不是曲线的真实采样点；因此点数与真实点数差一个量级。
    check("表型 B：其点数 ≡ 细高分量数（质心当数据点，非真实采样）",
          s9 is not None and len(s9) == tall9
          and all(e[0] > 0 and e[1] > 0 for e in s9.yerr),
          f"points={len(s9) if s9 else None} tall={tall9}")

    line9 = {s.name: s for s in extract_series(
        img9, (0, 0, _R8_W, _R8_H), colors=[C_STEEP, C_SMOOTH],
        names=["steep", "smooth"], kind="line")}
    check("表型 B：显式 --kind line 给出真实采样点数（高出 20 倍以上）",
          line9.get("steep") is not None
          and len(line9["steep"]) > 20 * len(s9),
          f"line={len(line9['steep']) if line9.get('steep') else None} "
          f"auto={len(s9)}")


def main() -> int:
    test_drop_antialias()
    test_bg_margin_matches_tol()
    test_halo_tolerance_radius()
    test_dedupe_removes_scatter_halo()
    test_log_calibration()
    test_kind_auto_steep_fragmented_curve()
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
