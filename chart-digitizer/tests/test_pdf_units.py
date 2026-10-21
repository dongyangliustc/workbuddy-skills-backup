"""文献 PDF 定位/裁剪链路的确定性单元测试（S1）。

**本文件锁的是什么**

端到端精度测试依赖光栅化的**偶然结果**——同一处缺陷换个几何就不触发，测试随即失去
意义（见 ``test_extract_units.py`` 的引言）。文献 PDF 这一层更甚：真实文献会被移动、
替换、甚至不在机器上。故本文件**全部用自包含夹具**（``figures/caseP1_paper.pdf`` /
``caseP1_fallback.pdf``，由 ``make_pdf_cases.py`` 编程生成）做确定性断言，零真实文献依赖。

**两层断言：陷阱有效性 + 规则判别力**

SPEC §4.2 为夹具规定了 9 类刻意布置的陷阱（正文引用干扰项、6.6 pt 图例文字、嵌套色块、
近黑刻度短线……）。一个"能跑通"的实现完全可能在夹具上通过，因为这些陷阱**本来就该被
绕过**——测试若不逐条断言"陷阱确实在文件里、且规则确实把它挡住了"，夹具就是摆设。故对
每个陷阱写两层断言：

1. **陷阱存在**：直接读 PDF 文本层/矢量对象，确认干扰项真在文件里（例如"近黑刻度线
   **确实**落进图例色块的几何候选集"）；
2. **规则可判别**：SPEC §3.2.1/§3.2.2/§3.2.3 的判据以**独立参考实现**（``_spec_*``）
   复现于本文件，逐条件断言其判别力，并给出"去掉该条件就会误判"的反证。

``_spec_*`` 是实现无关的规格复现，**不是** S2 的 ``legend.py``；S2 落地后其自身测试须再
对本文件锁定的同一组事实做断言。两道锁互不依赖，任一环节写歪都会被另一处抓住。

约定：每个测试对应一处 SPEC 条款或已修复的实测缺陷，注释写明依据。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from chartdigit import legend as L  # noqa: E402
from chartdigit import pdf as P  # noqa: E402
from chartdigit.audit import PATTERN_NONUNIFORM, PATTERN_UNIFORM, classify_spacing  # noqa: E402
from chartdigit.detect import detect, load_image, ticks_for_box  # noqa: E402
from chartdigit.extract import apply_exclude_mask, color_mask, extract_series  # noqa: E402

FIG = HERE / "figures"
PAPER = FIG / "caseP1_paper.pdf"
FALLBACK = FIG / "caseP1_fallback.pdf"
MANIFEST = FIG / "caseP1_manifest.json"
CLI = HERE.parent / "scripts" / "chartdigit.py"

#: 装有 pymupdf/numpy 的解释器（CLI 以子进程方式调用，必须用同一个）
PY = Path(sys.executable)
_VENV = Path(
    r"C:\Users\Administrator.DESKTOP-7RU274I\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)
if _VENV.is_file():
    PY = _VENV

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


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _lines_in(page: pymupdf.Page, rect: pymupdf.Rect) -> list[tuple]:
    """矩形内全部文字行 → ``[(x0, y0, y1, size, text), ...]``（y 自顶向下）。"""
    out: list[tuple] = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type", 0) != 0:
            continue
        for ln in b.get("lines", ()):
            bb = pymupdf.Rect(ln["bbox"])
            if not bb.intersects(rect):
                continue
            txt = "".join(s.get("text", "") for s in ln.get("spans", ()))
            out.append((float(bb.x0), float(bb.y0), float(bb.y1),
                        round(P._line_size(ln), 3), txt))
    out.sort(key=lambda r: r[1])
    return out


def _same_rect(a, b, tol: float = 0.05) -> bool:
    """两矩形是否同一（容差 0.05 pt）。

    不能直接比 ``round(...,3)``：manifest 里的 bbox 记账到 0.01 pt（``236.28``），
    而 PDF 侧给的是二进制浮点还原值（``236.276``），三位小数取整后二者不等。
    """
    return all(abs(float(a[i]) - float(b[i])) <= tol for i in range(4))


# ==================================================== SPEC §3.2.x 规则的参考实现

def _spec_legend_groups(
    rows: list[tuple],
    *,
    size_ratio: float = 0.80,
    align_tol: float = 3.0,
    spacing_ratio: float = 1.6,
    min_items: int = 2,
) -> list[list[tuple]]:
    """SPEC §3.2.1「图例文字几何签名」的独立参考实现（**仅测试用**）。

    ``rows`` 形如 ``(x0, y0, y1, size, text)``。四条件**必须同时满足**才成组：

    * ``size < 0.80 × 区域内最大字号``
    * 组内左边界 x 极差 ``≤ 3 pt``
    * 行距（**行推进量**，取 top→top）``< 1.6 × 组内行高``
    * 组内条目数 ``≥ 2``

    「行距」取 top→top 推进量（排版学上的行距含义）。夹具里行 bbox 高 9.06 pt 而推进量
    仅 6.7 pt（bbox 相互重叠），任何"bottom→top 间隙"式读法在本夹具上恒为负、恒满足；
    故参考实现取更严格的那种读法。
    """
    if not rows:
        return []
    max_size = max(r[3] for r in rows)
    cands = sorted((r for r in rows if r[3] < size_ratio * max_size), key=lambda r: r[1])
    if not cands:
        return []

    groups: list[list[tuple]] = []
    cur = [cands[0]]
    for r in cands[1:]:
        merged = cur + [r]
        spread = max(x[0] for x in merged) - min(x[0] for x in merged)
        line_h = max(x[2] - x[1] for x in merged)
        step = r[1] - cur[-1][1]
        if spread <= align_tol and step < spacing_ratio * line_h:
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    groups.append(cur)
    return [g for g in groups if len(g) >= min_items]


def _spec_geo_candidates(drawings: list[dict], *, y0: float, y1: float, x0: float) -> list[dict]:
    """SPEC §3.2.2 第 1 步：图例色块的**几何**候选集（尚未做颜色剔除）。"""
    return [
        d for d in drawings
        if d["rect"].y0 > y0 - 3 and d["rect"].y1 < y1 + 3
        and d["rect"].x1 <= x0 + 0.5 and d["rect"].width > 3
    ]


def _is_neutral(d: dict, *, drop_gray: float = 25.0) -> bool:
    """SPEC §3.2.2 第 2 步的「近灰/近黑」判据（刻度线与文字的颜色特征）。"""
    c = d.get("color") if d["type"] == "s" else d.get("fill")
    if not c:
        return True
    return (max(c) - min(c)) * 255.0 < drop_gray


def _spec_pick_swatch(drawings: list[dict], *, y0: float, y1: float, x0: float):
    """SPEC §3.2.2 取色优先级参考实现 → ``(几何候选数, rgb 或 None, 类型)``。

    优先级：``type=="s"``（描边）的颜色 → 否则 ``type=="f"``（填充）中**饱和度最高**者
    （同一 y 区间出现多个嵌套 fill 时内层才是芯色）；近灰/近黑一律跳过。
    """
    geo = _spec_geo_candidates(drawings, y0=y0, y1=y1, x0=x0)
    kept = [d for d in geo if not _is_neutral(d)]
    strokes = [d for d in kept if d["type"] == "s" and d.get("color")]
    if strokes:
        return len(geo), strokes[0]["color"], "s"
    fills = [d for d in kept if d["type"] == "f" and d.get("fill")]
    if not fills:
        return len(geo), None, ""
    best = max(fills, key=lambda d: max(d["fill"]) - min(d["fill"]))
    return len(geo), best["fill"], "f"


def _spec_verify_legend_colors(
    rgb: np.ndarray, legend_colors, *, tol: float = 64.0, min_pixels: int = 30
):
    """SPEC §3.2.3（R1 颜色一致性校验）的**契约**参考实现 → ``(ok, 命中数组)``。

    真实实现归 S2 的 ``legend.py``；此处只锁 SPEC 明写的三态语义，并按要求**复用**
    ``extract.color_mask`` 的距离函数（SPEC 原话："不要另写一套"）。
    """
    hits = [int(color_mask(rgb, c, tol=tol).sum()) for c in legend_colors]
    found = [n >= min_pixels for n in hits]
    ok = len(legend_colors) == len(hits) and found.count(True) == len(legend_colors)
    return ok, hits


# ============================================================ 1. 图注正则

def test_caption_regex() -> None:
    """SPEC §3.1.2 第 2 步：按实测格式匹配，且**不**咬正文引用。"""
    print("== 图注正则 ==")

    m = P.caption_match("Fig. 3 | Test figure for digitizer.")
    check("命中夹具图注：编号 3、无面板字母、分隔符为 '|'",
          m is not None and m.group("num") == "3" and m.group("panel") is None
          and P.has_separator("Fig. 3 | Test figure for digitizer.", m.end()),
          f"m={m}")

    # P2 实测陷阱：正文句子以 "Figure 3" 起头，正则**会**命中 → 消歧门不可省。
    # 对照：句中引用（非行首）连正则都不命中。
    trap = "Figure 3 for a particular set of fit parameters."
    check("正文引用干扰项会被正则命中，而句中引用不命中（两边的边界都要锁住）",
          P.caption_match(trap) is not None and P.caption_match(trap).group("num") == "3"
          and P.caption_match("as shown in Figure 3 for a particular set of fit") is None)

    # 开发期实测缺陷：编号后紧跟空白再接大写字母时，"TPES" 的首字母曾被吃成面板字母
    m2 = P.caption_match("Fig. 2 TPES and TPEPICO")
    m3 = P.caption_match("Fig. 12a | panel a")
    check("面板字母边界：'Fig. 2 TPES' 不误读 T，'Fig. 12a' 仍取到 a；"
          "表注（TABLE 1）走独立分支、与图注分属不同标签命名空间",
          m2 is not None and m2.group("panel") is None
          and m3 is not None and (m3.group("num"), m3.group("panel")) == ("12", "a")
          and P.caption_kind(P.caption_match("TABLE 1: Valence Band Energies")) == "table"
          and P.caption_kind(P.caption_match("Fig. 1")) == "figure",
          f"m2={None if m2 is None else m2.group('panel')!r}")


def test_caption_disambiguation() -> None:
    """SPEC §3.1.2 第 3 步：逐条断言消歧规则（全部不满足者丢弃）。"""
    print("== 图注消歧门 ==")

    def decide(**kw):
        base = dict(line_text="Fig. 3 | x", size=9.0, body_size=10.0,
                    block_lines=1, is_first_line=True, separator=True)
        base.update(kw)
        return P.caption_decision(**base)

    # G1 段落结构：夹具陷阱行是 9 行块的第 3 行（index 2）→ 直接拦下
    got = decide(line_text="Figure 3 for a particular set of fit parameters.",
                 size=10.0, block_lines=9, is_first_line=False, separator=False)
    check("正文块中的续行被 G1 拦下（not_first_line）",
          got == ("not_first_line", "") and decide(block_lines=2, is_first_line=False)[0]
          in ("ok", "font_not_caption"),
          f"got={got}")

    check("G2-a 字号：图注惯例小于正文（P3 实测 8.0 vs 9.5）→ 接受，分支=size；"
          "G2-b 显式分隔符：同字号但分隔符 + block≤4 → 接受，block=5 → 拒绝",
          decide() == ("ok", "size")
          and decide(size=10.0, separator=True, block_lines=3) == ("ok", "separator")
          and decide(size=10.0, separator=True, block_lines=5)[0] == "font_not_caption")

    check("G2-c 孤立单行块：短文本接受、超长（>200 字符）拒绝（避免吞下整段正文）；"
          "字号信息缺失 → no_font_metrics（不静默接受）",
          decide(size=10.0, separator=False, block_lines=1,
                 line_text="Fig. 3 Comparison of the measured yields")
          == ("ok", "single-line")
          and decide(size=10.0, separator=False, block_lines=1,
                     line_text="Fig. 3 " + "x" * 260)[0] == "font_not_caption"
          and decide(body_size=0.0)[0] == "no_font_metrics")


def test_caption_lines_on_fixture() -> None:
    """夹具上：5 条正则命中、4 条 ok、1 条被拒——陷阱行的身份必须可逐条核对。"""
    print("== 夹具图注行 ==")

    doc = pymupdf.open(PAPER)
    hits = P.find_caption_lines(doc[0], page_no=0)
    ok = [h for h in hits if h.ok]
    rejected = [h for h in hits if not h.ok]

    check("命中 5 条、通过 4 条、拒绝 1 条", 
          (len(hits), len(ok), len(rejected)) == (5, 4, 1),
          f"hits={len(hits)} ok={len(ok)} rej={len(rejected)}")
    check("被拒者正是正文引用干扰项，且 4 条通过项全走强信号分支",
          len(rejected) == 1
          and rejected[0].line_text.startswith("Figure 3 for a particular set")
          and rejected[0].reason == "not_first_line"
          and all(h.strong and h.separator for h in ok)
          and sorted(h.label for h in ok) == ["3", "4", "5", "5"],
          f"rej={[h.line_text[:40] for h in rejected]} ok={[(h.label, h.branch) for h in ok]}")
    doc.close()


# ============================================================ 2. 图形区域

def test_graphic_region() -> None:
    """SPEC §3.1.2 第 4 步：图注**同栏、上方**，且不含图注自身。"""
    print("== 图形区域粗定 ==")

    man = _manifest()
    doc = pymupdf.open(PAPER)
    page = doc[0]
    cands = {c.label: c for c in P.locate_figures(doc)}
    f3, f4 = cands["3"], cands["4"]
    hits = {h.label: h for h in P.find_caption_lines(page, page_no=0) if h.ok}
    cap = pymupdf.Rect(hits["3"].line_bbox)

    check("区域底边 = 图注行顶边（严格在上方），且与图注行不相交",
          f3.rect.y1 <= cap.y0 + 0.01 and not pymupdf.Rect(f3.rect).intersects(cap),
          f"rect.y1={f3.rect.y1:.2f} cap_y0={cap.y0:.2f}")

    gutter = P.find_column_gutter(page)
    tx0, tx1 = P._text_extent(page)
    check("同栏约束：Fig.3 不越过栏间空带向右，Fig.4 不越过栏间空带向左",
          f3.rect.x0 >= tx0 - 0.01 and f3.rect.x1 <= gutter + 0.01
          and f4.rect.x0 >= gutter - 0.01 and f4.rect.x1 <= tx1 + 0.5,
          f"gutter={gutter:.2f} f3={f3.rect.x0:.2f}..{f3.rect.x1:.2f} "
          f"f4={f4.rect.x0:.2f}..{f4.rect.x1:.2f}")

    g = man["paper"]["elements"]["fig3"]["graphics_rect"]
    check("区域完整覆盖夹具记录的图形矩形（不切边），并保留轴标题（y1 ≥ 339）"
          "与 y 轴刻度标签（x0 ≤ 70.5）",
          f3.rect.x0 <= g[0] + 0.01 and f3.rect.y0 <= g[1] + 0.01
          and f3.rect.x1 >= g[2] - 0.01 and f3.rect.y1 >= g[3] - 0.01
          and f3.rect.x0 <= 70.5 and f3.rect.y1 >= 339.0,
          f"rect={[round(v, 2) for v in f3.bbox_pt]} graphics={g}")
    doc.close()


# ============================================================ 3. 先粗定后收紧

def test_tighten() -> None:
    """SPEC §3.1.2 收紧：合成「图 + 四周留白」→ 输出恰为图的边界；异常尺寸必须丢弃。"""
    print("== 收紧与校验 ==")

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)

    page.draw_rect(pymupdf.Rect(100, 200, 300, 400), color=(0, 0, 0), width=1)
    out, info = P.tighten_rect(page, pymupdf.Rect(60, 160, 340, 440))
    got = None if out is None else (out.x0, out.y0, out.x1, out.y1)
    check("图+四周 40 pt 留白 → 收紧结果落在内容边界 ±2 pt 内（留白被切掉）",
          got is not None
          and abs(got[0] - 100) <= 2 and abs(got[1] - 200) <= 2
          and abs(got[2] - 300) <= 2 and abs(got[3] - 400) <= 2
          and (got[2] - got[0]) < 240 and (got[3] - got[1]) < 280,
          f"got={None if got is None else [round(v, 2) for v in got]}")

    rgb = np.full((200, 200, 3), 255, dtype=np.uint8)
    rgb[20:60, 20:180] = 249    # 灰度差 6（<8）→ 背景
    rgb[120:160, 20:180] = 230  # 灰度差 25（>8）→ 内容
    b = P.content_bounds(rgb, bg_tol=P.BG_TOL)
    check("背景判据的边界：灰度差 6 判为背景、差 25 判为内容",
          b is not None and b[1] >= 110 and b[3] <= 170, f"bounds={b}")

    bdoc = pymupdf.open()
    bpage = bdoc.new_page(width=595, height=842)
    out2, info2 = P.tighten_rect(page, pymupdf.Rect(100, 200, 100.5, 400))
    out3, info3 = P.tighten_rect(bpage, pymupdf.Rect(60, 60, 500, 400))
    bdoc.close()

    pdoc = pymupdf.open()
    ppage = pdoc.new_page(width=595, height=842)
    ppage.draw_rect(pymupdf.Rect(100, 300, 500, 310), color=(0, 0, 0), width=1)
    out4, info4 = P.tighten_rect(ppage, pymupdf.Rect(90, 290, 510, 320))
    ppage.draw_rect(pymupdf.Rect(100, 600, 160, 660), color=(0, 0, 0), width=1)
    out5, info5 = P.tighten_rect(ppage, pymupdf.Rect(95, 595, 165, 665))
    out6, _ = P.tighten_rect(ppage, pymupdf.Rect(95, 595, 165, 665), validate=False)
    check("四条拒绝路径各就其位：粗定退化 / 无内容 / 长宽比越界 / 面积不足全页 1%；"
          "validate=False 时不丢弃（供诊断复用）",
          out2 is None and info2["reject"] == "coarse-too-small"
          and out3 is None and info3["reject"] == "no-content"
          and out4 is None and info4["reject"] == "aspect-out-of-range"
          and out5 is None and info5["reject"] == "area-too-small"
          and out6 is not None,
          f"{info2.get('reject')}/{info3.get('reject')}/"
          f"{info4.get('reject')}/{info5.get('reject')}")
    pdoc.close()
    doc.close()


# ============================================================ 4. 颜色与坐标换算

def test_hex_color() -> None:
    """SPEC §4.2：RGB 浮点 → #rrggbb，含 .5 进位这一舍入边界。"""
    print("== hex_color 与舍入边界 ==")

    check("舍入边界：0.5 → '80'（.5 进位），而 127/255=0.498 → '7f'（不误进到 80）",
          P.hex_color((0.5, 0.5, 1.0)) == "#8080ff"
          and P.hex_color((127 / 255, 127 / 255, 1.0)) == "#7f7fff",
          f"{P.hex_color((0.5, 0.5, 1.0))} / {P.hex_color((127 / 255, 127 / 255, 1.0))}")
    check("夹具嵌套色块的两层色各自复现；越界分量先钳到 [0,1]",
          P.hex_color((127 / 255, 229 / 255, 127 / 255)) == "#7fe57f"
          and P.hex_color((63 / 255, 216 / 255, 63 / 255)) == "#3fd83f"
          and P.hex_color((-0.2, 1.3, 0.5)) == "#00ff80")

    bad = 0
    for args in ((0.1, 0.2), (float("nan"), 0.0, 0.0)):
        try:
            P.hex_color(args)
        except ValueError:
            bad += 1
    check("分量个数错误 / NaN 均报 ValueError（不静默出错）", bad == 2, f"bad={bad}")


def test_px_roundtrip() -> None:
    """SPEC §4.2：dpi + rect_pt 偏移下的点↔像素往返一致（S2 排除掩码即依赖此换算）。"""
    print("== 点↔像素换算 ==")

    rect = (43.6, 130.0, 266.5, 372.0)
    errs = [max(abs(P.px_to_pt(P.pt_to_px(pt, rect, 600), rect, 600)[i] - pt[i])
                for i in range(2))
            for pt in ((43.6, 130.0), (266.5, 372.0), (100.0, 200.0), (200.5, 350.25))]
    check("dpi=600、含矩形偏移：72 pt ≡ 600 px，往返误差 < 1e-9 pt",
          P.pt_to_px((72.0, 72.0), (0.0, 0.0), 600) == (600.0, 600.0)
          and max(errs) < 1e-9, f"max={max(errs):.3e}")

    box = P.box_pt_to_px((50.0, 160.0, 250.0, 360.0), rect, 600)
    check("box_pt_to_px 与逐点换算逐位一致",
          box[:2] == P.pt_to_px((50.0, 160.0), rect, 600)
          and box[2:] == P.pt_to_px((250.0, 360.0), rect, 600), f"box={box}")


# ==================================================== 5. 图例几何签名（陷阱有效性）

def test_legend_geometry_signature() -> None:
    """SPEC §3.2.1：四条件**必须同时满足**才成组；缺任一条即不成组（逐条负例）。"""
    print("== 图例几何签名 ==")

    man = _manifest()
    doc = pymupdf.open(PAPER)
    page = doc[0]
    f3 = man["paper"]["elements"]["fig3"]
    cands = {c.label: c for c in P.locate_figures(doc)}
    rows = _lines_in(page, pymupdf.Rect(cands["3"].rect))

    groups = _spec_legend_groups(rows)
    check("区域内字号两档（6.6 / 9.5）→ 恰成 1 组、含 3 条同左边界（极差 0 ≤ 3 pt）"
          "的 6.6 pt 文字",
          sorted({r[3] for r in rows}) == [6.6, 9.5]
          and len(groups) == 1 and len(groups[0]) == 3
          and all(r[3] == 6.6 for r in groups[0])
          and len({round(r[0], 3) for r in groups[0]}) == 1,
          f"sizes={sorted({r[3] for r in rows})} groups={[len(g) for g in groups]}")

    big = [r for r in rows if r[3] >= 9.0]
    check("负例·字号：只喂轴标题字号的行走不成组（没有'显著更小'的候选）",
          len(big) == 2 and _spec_legend_groups(big) == [], f"n_big={len(big)}")

    stub = [(101.0, 0.0, 9.06, 6.6, "a"), (105.0, 6.7, 15.76, 6.6, "b"),
            (109.0, 13.4, 22.46, 6.6, "c"), (50.0, 40.0, 49.06, 9.5, "ref")]
    check("负例·左对齐：极差 8 pt > 3 pt 不成组；放宽阈值后同一批数据成组（反证条件在起作用）",
          _spec_legend_groups(stub) == [] and len(_spec_legend_groups(stub, align_tol=1e9)) == 1,
          f"{_spec_legend_groups(stub)}")

    loose = [(101.0, 0.0, 9.06, 6.6, "a"), (101.0, 20.0, 29.06, 6.6, "b"),
             (50.0, 60.0, 69.06, 9.5, "ref")]
    check("负例·行距：推进 20 pt ≥ 1.6×行高 9.06 不成组；放宽阈值后成组（反证）",
          _spec_legend_groups(loose) == []
          and len(_spec_legend_groups(loose, spacing_ratio=1e9)) == 1,
          f"{_spec_legend_groups(loose)}")

    panel_x = [r[0] for r in rows if "m/z" in r[4]]
    alone = [(101.0, 0.0, 9.06, 6.6, "RH"), (50.0, 40.0, 49.06, 9.5, "ref")]
    check("负例·条目数：单条不成组；真·干扰项面板标签 'm/z 70' 被字号与对齐双重排除",
          _spec_legend_groups(alone) == []
          and panel_x == [95.0] and 95.0 != 101.0
          and all("m/z" not in r[4] for r in groups[0]),
          f"alone={_spec_legend_groups(alone)} panel_x={panel_x}")
    doc.close()


# ============================================ 6. 色块 / 嵌套芯色 / 刻度短线的排除

def test_legend_swatch_and_ticks() -> None:
    """SPEC §3.2.2：色块关联、嵌套芯色优先、近黑刻度短线必须排除。"""
    print("== 图例色块与刻度线 ==")

    man = _manifest()
    f3 = man["paper"]["elements"]["fig3"]
    doc = pymupdf.open(PAPER)
    page = doc[0]
    drawings = page.get_drawings()
    cands = {c.label: c for c in P.locate_figures(doc)}
    rows = [r for r in _lines_in(page, pymupdf.Rect(cands["3"].rect)) if r[3] == 6.6]

    got = []
    for line in rows:
        _, rgb, kind = _spec_pick_swatch(drawings, y0=line[1], y1=line[2], x0=line[0])
        got.append((line[4], None if rgb is None else P.hex_color(rgb), kind))
    want = [(rec["name"], rec["nested_core"] or rec["hex"]) for rec in f3["legend"]]
    check("三个图例条目各自由几何条件唯一捞到色块并取到预期色（描边优先级不误伤填充）",
          [(n, h) for n, h, _ in got] == want and all(k == "f" for _, _, k in got),
          f"got={got} want={want}")

    box1 = f3["legend"][1]["box_rect"]
    nested = [P.hex_color(d["fill"]) for d in drawings
              if d["type"] == "f" and d.get("fill") and _same_rect(d["rect"], box1)]
    check("嵌套色块：同一矩形两层 fill 都在文件里（外层 #7fe57f / 内层 #3fd83f）",
          sorted(nested) == ["#3fd83f", "#7fe57f"], f"nested={nested}")
    check("取芯色（饱和度最高者）→ #3fd83f，而非外层 #7fe57f",
          [h for n, h, _ in got if n == "TD 493 K"] == ["#3fd83f"])

    ticks = [d for d in drawings
             if any(_same_rect(d["rect"], t) for t in f3["tick_rects"])]
    geo_hits = sum(
        1 for line in [r for r in rows if r[4] != "TD 415 K"]
        for d in _spec_geo_candidates(drawings, y0=line[1], y1=line[2], x0=line[0])
        if any(_same_rect(d["rect"], t) for t in f3["tick_rects"])
    )
    check("夹具含 4 条近黑（#000000）5×0.5 pt 刻度短线，且它们**确实**落进几何候选集"
          "（≥2 处）——一旦不排除就会被当成色块",
          len(ticks) == 4 and all(P.hex_color(d["fill"]) == "#000000" for d in ticks)
          and geo_hits >= 2,
          f"n={len(ticks)} geo_hits={geo_hits}")
    check("近黑/近灰判据（max-min < 25）把全部 4 条刻度线剔除，且不误伤任何真色块",
          all(_is_neutral(d) for d in ticks)
          and sum(1 for d in drawings if d["type"] == "f" and d.get("fill")
                  and not _is_neutral(d)) >= 4,
          f"neutral_ok={all(_is_neutral(d) for d in ticks)}")
    doc.close()


# ==================================================== 7. R1 颜色校验的三态（契约锁定）

def test_verify_legend_colors_contract() -> None:
    """SPEC §4.2/§3.2.3：图例颜色一致性校验的三态语义（真实实现归 S2 ``legend.py``）。"""
    print("== R1 颜色校验契约 ==")

    rgb = np.full((60, 60, 3), 255, dtype=np.uint8)
    rgb[5:35, 5:25] = (255, 0, 0)
    rgb[5:35, 25:45] = (0, 255, 0)
    rgb[5:35, 45:60] = (0, 0, 255)
    present = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    absent = (0, 255, 255)

    ok, hits = _spec_verify_legend_colors(rgb, present)
    check("态 1 · 全中 → ok=True（每个颜色在图内都能找到 ≥min_pixels 像素）",
          ok and len(hits) == 3 and min(hits) >= 30, f"hits={hits}")

    ok2, hits2 = _spec_verify_legend_colors(rgb, [(255, 0, 0), absent, (0, 0, 255)])
    check("态 2 · 有一个颜色 0 命中 → ok=False，且能指名是哪一个",
          not ok2 and hits2[1] == 0 and hits2[0] >= 30 and hits2[2] >= 30, f"hits={hits2}")

    ok3, hits3 = _spec_verify_legend_colors(rgb, present + [absent])
    check("态 3 · 命中数与图例条目数不匹配（4 条图例只有 3 色找到）→ ok=False",
          not ok3 and len(hits3) == 4 and sum(1 for n in hits3 if n >= 30) == 3,
          f"hits={hits3}")


# ============================================================ 8. 通道 B / C（兜底）

def test_fallback_channels() -> None:
    """SPEC §3.1.2：通道 B（位图）与通道 C（矢量簇）及其置信度上限。"""
    print("== 兜底通道 B / C ==")

    man = _manifest()
    doc = pymupdf.open(FALLBACK)

    r = man["fallback"]["pages"]["raster"]
    page0 = doc[r["page_no"]]
    rects = P.find_raster_rects(page0)
    full_present = any(
        pymupdf.Rect(b["bbox"]).width >= 0.9 * page0.rect.width
        for b in page0.get_text("dict")["blocks"] if b.get("type", 0) == 1
    )
    check("通道 B 只留候选插图（整页扫描位图与 5×5 pt 小图元都被滤掉），"
          "且整页位图确实在文件里（过滤非空操作）",
          len(rects) == 1 and r["expected_count"] == 1
          and abs(rects[0].x0 - r["figure_raster"][0]) < 1e-6 and full_present,
          f"n={len(rects)} full_present={full_present}")

    v = man["fallback"]["pages"]["vector"]
    page1 = doc[v["page_no"]]
    cl = P.find_vector_cluster(page1)
    check("通道 C 的簇完整包含图形矩形；x0 = 75 即向左伸出的 5 pt 刻度线",
          cl is not None
          and cl.x0 <= v["graphics_rect"][0] and cl.x1 >= v["graphics_rect"][2]
          and cl.y0 <= v["graphics_rect"][1] + 1e-6 and cl.y1 >= v["graphics_rect"][3] - 1e-6
          and abs(cl.x0 - 75.0) < 1e-6,
          f"cluster={cl}")

    diag: list[dict] = []
    cands = P.locate_figures(doc, diagnostics=diag)
    srcs = [(c.page_no, c.source, c.confidence, c.label) for c in cands]
    check("A→B→C 优先级生效：两页各出 1 个候选（raster / vector-cluster），"
          "均无编号且置信度不超 SPEC 上限",
          srcs == [(0, "raster", P.CONF_RASTER, "?"),
                   (1, "vector-cluster", P.CONF_VECTOR, "?")]
          and all(c.confidence <= 0.6 for c in cands)
          and [d["channel"] for d in diag] == ["raster", "vector-cluster"],
          f"srcs={srcs} diag={[d['channel'] for d in diag]}")
    doc.close()


# ============================================================ 9. 裁剪与溯源

def test_crop_and_provenance() -> None:
    """SPEC §3.1.3 / §3.1.4：clip 局部渲染、dpi 下限、PNG + 溯源 sidecar。"""
    print("== 裁剪与溯源 ==")

    doc = pymupdf.open(PAPER)
    cand = {c.label: c for c in P.locate_figures(doc)}["3"]
    tmp = Path(tempfile.mkdtemp(prefix="chartdigit_pdf_"))
    out = tmp / "fig3.png"

    raised = None
    try:
        P.crop_figure(PAPER, cand, out, dpi=P.MIN_DPI - 1, doc=doc)
    except ValueError as exc:
        raised = str(exc)
    check("dpi 低于下限 300 → ValueError 且不降级出图（未写任何 PNG）",
          raised is not None and "300" in raised and not out.exists(), f"raised={raised}")

    crop = P.crop_figure(PAPER, cand, out, dpi=P.MIN_DPI, doc=doc)
    w = (crop.rect_pt[2] - crop.rect_pt[0]) * P.MIN_DPI / 72.0
    h = (crop.rect_pt[3] - crop.rect_pt[1]) * P.MIN_DPI / 72.0
    check("PNG 尺寸 = rect 尺寸 × dpi/72（±2 px 取整误差）——证明 clip 局部渲染生效",
          abs(crop.px_size[0] - w) <= 2 and abs(crop.px_size[1] - h) <= 2,
          f"px={crop.px_size} expect≈({w:.0f},{h:.0f})")

    side = out.with_suffix(".png.json")
    data = json.loads(side.read_text(encoding="utf-8"))
    need = {"pdf", "page_no", "rect_pt", "dpi", "px_size", "source",
            "caption", "confidence", "label", "generated_at"}
    check("溯源 JSON 字段齐备且可独立复核",
          out.is_file() and side.is_file() and need <= set(data)
          and data["source"] == "caption" and data["dpi"] == P.MIN_DPI
          and data["px_size"] == list(crop.px_size),
          f"missing={sorted(need - set(data))}")
    doc.close()


# ================================================ 10. 歧义判定与 CLI 口径（子进程）

def test_ambiguity_and_cli() -> None:
    """SPEC §3.1.5 + §4.2：歧义/定位失败以退出码 1 结束（**子进程口径**），--list 无副作用。"""
    print("== 歧义与 CLI 口径 ==")

    doc = pymupdf.open(PAPER)
    cands = P.locate_figures(doc)
    fives = [c for c in cands if c.label == "5"]
    check("同编号 5 的两个候选都被标为歧义（ambiguous_with=2），编号 3/4 不受影响；"
          "无编号候选（'?'）不参与计数（否则会报出虚假的 4 张歧义）",
          len(fives) == 2 and all(c.ambiguous_with == 2 for c in fives)
          and all(c.ambiguous_with == 1 for c in cands if c.label in ("3", "4"))
          and all(c.ambiguous_with == 1 for c in P.locate_figures(pymupdf.open(FALLBACK))),
          f"{[(c.label, c.ambiguous_with) for c in cands]}")
    doc.close()

    tmp = Path(tempfile.mkdtemp(prefix="chartdigit_pdf_cli_"))

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(PY), str(CLI), "from-pdf", *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(tmp), timeout=600)

    before = {p.name for p in tmp.iterdir()}
    p = run(str(PAPER), "--list")
    check("--list 退出码 0、列出检出图数与编号，且无副作用（运行前后目录无新增文件）",
          p.returncode == 0 and "检出 4 个候选" in p.stdout
          and {q.name for q in tmp.iterdir()} == before,
          f"rc={p.returncode} out={p.stdout[:60]!r}")

    p2 = run(str(PAPER), "--figure", "3", "-o", str(tmp / "fig3"), "--dpi", "300")
    check("--figure 3 退出码 0，产出 PNG + 溯源 JSON，并在 stdout 给出下一步 extract 命令",
          p2.returncode == 0 and (tmp / "fig3.png").is_file()
          and (tmp / "fig3.png.json").is_file()
          and "extract" in p2.stdout and "--axes" in p2.stdout and "--xlim" in p2.stdout,
          f"rc={p2.returncode} out={p2.stdout[-140:]!r}")

    n_before = len(list(tmp.iterdir()))
    p3 = run(str(PAPER), "--figure", "5", "-o", str(tmp / "fig5"))
    check("--figure 5（同编号 2 候选）退出码 1，stderr 列出全部候选并给出手工框选指引，"
          "且不写任何文件",
          p3.returncode == 1 and p3.stderr.count("候选") >= 2
          and "--page" in p3.stderr and "--rect" in p3.stderr
          and len(list(tmp.iterdir())) == n_before and not (tmp / "fig5.png").exists(),
          f"rc={p3.returncode} err={p3.stderr[:120]!r}")

    p4 = run(str(PAPER), "--figure", "99")
    p5 = run(str(FALLBACK), "--figure", "1")
    check("定位失败也退出码 1 且绝不猜：找不到编号、以及整份 PDF 无编号（改用 --page/--rect）",
          p4.returncode == 1 and p5.returncode == 1 and "编号" in p5.stderr,
          f"rc99={p4.returncode} rc_fallback={p5.returncode}")

    p6 = run(str(PAPER), "--figure", "3", "--dpi", "200")
    p7 = run(str(PAPER), "--list", "--figure", "3")
    check("用法校验：--dpi 低于下限 300、以及选择器互斥，均退出码 1",
          p6.returncode == 1 and p7.returncode == 1, f"{p6.returncode}/{p7.returncode}")

    p8 = run(str(PAPER), "--page", "1", "--rect", "70,190,281,340",
             "-o", str(tmp / "manual"), "--dpi", "300")
    p9 = run(str(PAPER), "--page", "99", "--rect", "70,190,281,340",
             "-o", str(tmp / "bad"))
    check("--page + --rect 手工路径可用（退 0、产出 PNG）；页码超范围退 1（不静默裁空白）",
          p8.returncode == 0 and (tmp / "manual.png").is_file() and p9.returncode == 1,
          f"rc={p8.returncode}/{p9.returncode}")


# ======================================================================
# 11. S2 · legend.py 的真实实现（与 §5–§7 的 _spec_* 参考实现互为独立锁）
#
# 上文 §5–§7 锁的是「夹具里的陷阱确实存在」与「SPEC 语义有判别力」；本节锁的是
# 「S2 的代码确实按该语义执行」。两处都写歪才会一起漏。
# ======================================================================


def _rows(items: list[tuple[float, float, float, str]], size: float = 6.6) -> list:
    """构造合成文字行：``(x0, y0, x1, text)``，行高按 bbox 惯例取 ``1.375×size``。"""
    h = 1.375 * size
    return [L.TextRow(x0=x0, y0=y0, x1=x1, y1=y0 + h, size=size, text=t)
            for x0, y0, x1, t in items]


def _d(rect, *, fill=None, color=None, dtype="f") -> dict:
    """构造合成绘图对象（与 ``page.get_drawings()`` 同形的字典）。"""
    return {"type": dtype, "rect": pymupdf.Rect(rect),
            "fill": fill, "color": color, "width": 1.0}


FIG3_RECT = (75.0, 195.0, 280.0, 315.0)
FIG4_RECT = (330.0, 195.0, 510.0, 315.0)
A_PNG = FIG / "caseA_multicurve.png"


def test_s2_legend_geometry() -> None:
    """SPEC §3.2.1 的**真实实现**：四条件同时满足才成组，缺任一条即不成组。"""
    print("== S2 · 图例几何签名（真实实现）==")

    doc = pymupdf.open(PAPER)
    page = doc[0]
    man = _manifest()
    rows = L.text_rows(page, FIG3_RECT)
    groups = L.group_legend_lines(rows)
    names = [rec["name"] for rec in man["paper"]["elements"]["fig3"]["legend"]]
    check("夹具 Fig.3：真实实现恰成 1 组、3 条、字号全为 6.6、左边界极差为 0",
          len(groups) == 1 and len(groups[0]) == 3
          and {g.size for g in groups[0]} == {6.6}
          and max(g.x0 for g in groups[0]) - min(g.x0 for g in groups[0]) == 0.0,
          f"groups={[[g.text for g in grp] for grp in groups]}")
    check("组内条目与 manifest 声明的三条图例逐一对应（顺序即由上到下）",
          [g.text for g in groups[0]] == names, f"{[g.text for g in groups[0]]} vs {names}")

    big = [r for r in rows if r.size >= 9.0]
    check("负例·字号：只喂 ≥9 pt 的行走不成组（无「显著更小」的候选）",
          len(big) >= 1 and L.group_legend_lines(big) == [], f"n_big={len(big)}")

    stub = _rows([(101.0, 0.0, 130.0, "a"), (105.0, 6.7, 134.0, "b"),
                  (109.0, 13.4, 138.0, "c")]) + [L.TextRow(50.0, 40.0, 180.0, 49.06, 9.5, "ref")]
    check("负例·对齐：左边界极差 8 pt > 3 pt 不成组；放宽 align_tol 后同一批数据成组（反证）",
          L.group_legend_lines(stub) == []
          and len(L.group_legend_lines(stub, align_tol=1e9)) == 1,
          f"{[[g.text for g in x] for x in L.group_legend_lines(stub)]}")

    loose = _rows([(101.0, 0.0, 130.0, "a"), (101.0, 20.0, 130.0, "b")]) + [
        L.TextRow(50.0, 60.0, 180.0, 69.06, 9.5, "ref")]
    check("负例·行距：推进 20 pt ≥ 1.6×行高 9.075 不成组；放宽 spacing_ratio 后成组（反证）",
          L.group_legend_lines(loose) == []
          and len(L.group_legend_lines(loose, spacing_ratio=1e9)) == 1,
          f"{[[g.text for g in x] for x in L.group_legend_lines(loose)]}")

    alone = _rows([(101.0, 0.0, 130.0, "RH")]) + [
        L.TextRow(50.0, 40.0, 180.0, 49.06, 9.5, "ref")]
    panel = [r for r in rows if "m/z" in r.text]
    check("负例·条目数：单条不成组；真·干扰项面板标签（9.5 pt, x=95）既不在组内，"
          "自身也不成组",
          L.group_legend_lines(alone) == [] and len(panel) == 1 and panel[0].x0 == 95.0
          and all("m/z" not in g.text for g in groups[0]),
          f"alone_n={len(L.group_legend_lines(alone))} panel={[p.text for p in panel]}")

    g1 = _rows([(101.0, 0.0, 130.0, "only")])
    g3 = _rows([(101.0, 100.0, 130.0, "a"), (101.0, 106.7, 130.0, "b"),
                (101.0, 113.4, 130.0, "c")])
    g3b = _rows([(101.0, 50.0, 130.0, "x"), (101.0, 56.7, 130.0, "y"),
                 (101.0, 63.4, 130.0, "z")])
    check("多组并存时 pick_legend_group 取条目最多者；条目数并列时取最靠上者"
          "（图内孤立的双行小字会先成组，按位置取会误选）",
          [t.text for t in L.pick_legend_group([g1, g3])] == ["a", "b", "c"]
          and [t.text for t in L.pick_legend_group([g3, g3b])] == ["x", "y", "z"]
          and L.pick_legend_group([]) is None,
          f"{[t.text for t in L.pick_legend_group([g3, g3b])]}")
    doc.close()


def test_s2_legend_swatch() -> None:
    """SPEC §3.2.2 的**真实实现**：取色优先级、芯色、刻度线排除、距离加固、类型提示。"""
    print("== S2 · 色块关联（真实实现）==")

    doc = pymupdf.open(PAPER)
    page = doc[0]
    man = _manifest()
    f3 = man["paper"]["elements"]["fig3"]
    drawings = page.get_drawings()
    rows = [r for r in L.text_rows(page, FIG3_RECT) if r.size == 6.6]

    got = []
    for r in rows:
        sw = L.pick_swatch(drawings, y0=r.y0, y1=r.y1, x0=r.x0)
        got.append((r.text, sw.hex if sw else None, sw.kind_hint if sw else None))
    want = [(rec["name"], rec["nested_core"] or rec["hex"]) for rec in f3["legend"]]
    check("三条图例各由几何条件唯一捞到色块并取到预期色（含嵌套芯色 #3fd83f）",
          [(n, h) for n, h, _ in got] == want and len(rows) == 3,
          f"got={got} want={want}")
    check("嵌套条目：几何候选恰 2 个（外层 #7fe57f + 芯色 #3fd83f），取饱和度最高者",
          [sw.n_geo for sw in (L.pick_swatch(drawings, y0=r.y0, y1=r.y1, x0=r.x0)
                               for r in rows)][1] == 2
          and [h for n, h, _ in got if n == "TD 493 K"] == ["#3fd83f"],
          f"{[h for n, h, _ in got]}")

    tick_hits = lambda **kw: sum(  # noqa: E731
        1 for r in rows for d in L.geo_candidates(drawings, y0=r.y0, y1=r.y1, x0=r.x0, **kw)
        if any(_same_rect(d["rect"], t) for t in f3["tick_rects"]))
    check("陷阱有效：把距离加固放宽到无限后，4 条近黑（#000000）5×0.5 pt 刻度短线"
          "确实落进几何候选集（≥2 处）——SPEC §3.2.2 面对的就是这个局面",
          tick_hits(max_gap=1e9) >= 2,
          f"tick_geo={tick_hits(max_gap=1e9)}")
    check("陷阱被挡住：近黑判据**本身**即可剔除全部刻度线（不依赖距离加固），"
          "且真实实现从不返回 #000000",
          all(L.is_neutral(d) for d in drawings
              if any(_same_rect(d["rect"], t) for t in f3["tick_rects"]))
          and all(h != "#000000" for _, h, _ in got)
          and L.pick_swatch(drawings, y0=rows[0].y0, y1=rows[0].y1, x0=rows[0].x0,
                            max_gap=1e9, min_width=0.0).hex == "#7f7fff"
          and all(not L.is_neutral(d) for d in drawings
                  if d.get("type") == "f" and d.get("fill")
                  and any(_same_rect(d["rect"], rec["box_rect"]) for rec in f3["legend"])),
          f"{[h for _, h, _ in got]}")

    stroke = _d((93.0, 4.0, 98.0, 6.0), color=(0.0, 0.0, 1.0), dtype="s")
    fill_hi = _d((93.0, 4.0, 98.0, 6.0), fill=(0.0, 1.0, 0.0))
    check("取色优先级：同一区间同时存在 s 描边与 f 填充时取描边色（线段样本的真实颜色）",
          L.pick_swatch([fill_hi, stroke], y0=0.0, y1=9.075, x0=101.0).hex == "#0000ff",
          f"{L.pick_swatch([fill_hi, stroke], y0=0.0, y1=9.075, x0=101.0)}")

    far = _d((60.0, 4.0, 65.0, 6.0), fill=(0.0, 1.0, 0.0))
    near = _d((93.0, 4.0, 98.0, 6.0), fill=(1.0, 0.0, 0.0))
    check("距离加固（SPEC 字面之外）：30 pt 之外的同类对象不被误取（否则会把曲线残段"
          "当图例色块，而 R1 抓不住它）",
          L.pick_swatch([far], y0=0.0, y1=9.075, x0=101.0) is None
          and L.pick_swatch([far, near], y0=0.0, y1=9.075, x0=101.0).hex == "#ff0000"
          and len(L.geo_candidates([far, near], y0=0.0, y1=9.075, x0=101.0)) == 1,
          f"{L.pick_swatch([far], y0=0.0, y1=9.075, x0=101.0)}")

    Y0, Y1 = 0.0, 20.0
    bar = _d((85.0, 8.0, 95.0, 10.0), fill=(0.0, 0.0, 1.0))      # 10×2 → line
    blob = _d((95.0, 8.0, 100.0, 12.0), fill=(0.0, 0.0, 1.0))    # 5×4 → marker
    col = _d((94.0, 2.0, 98.0, 16.0), fill=(0.0, 0.0, 1.0))      # 4×14 → errorbar
    cap = _d((94.5, 8.0, 95.5, 11.0), fill=(0.0, 0.0, 1.0))      # 端点垂直短划
    check("图元类型提示：横条 → line；近方块 → marker；竖条（w/h ≤ 1/3）→ errorbar；"
          "末端带垂直短划 → errorbar（仅作 `--kind` 参考，不作强制）",
          L.pick_swatch([bar], y0=Y0, y1=Y1, x0=101.0).kind_hint == "line"
          and L.pick_swatch([blob], y0=Y0, y1=Y1, x0=101.0).kind_hint == "marker"
          and L.pick_swatch([col], y0=Y0, y1=Y1, x0=101.0).kind_hint == "errorbar"
          and L.pick_swatch([bar, cap], y0=Y0, y1=Y1, x0=101.0).kind_hint == "errorbar",
          f"{L.pick_swatch([bar], y0=Y0, y1=Y1, x0=101.0).kind_hint} "
          f"{L.pick_swatch([blob], y0=Y0, y1=Y1, x0=101.0).kind_hint} "
          f"{L.pick_swatch([col], y0=Y0, y1=Y1, x0=101.0).kind_hint} "
          f"{L.pick_swatch([bar, cap], y0=Y0, y1=Y1, x0=101.0).kind_hint}")
    doc.close()


def test_s2_r1_real_impl() -> None:
    """SPEC §3.2.3 的**真实实现**：三态语义 + 复用 color_mask + 色块排除加固。"""
    print("== S2 · R1 颜色校验（真实实现）==")

    rgb = np.full((60, 60, 3), 255, dtype=np.uint8)
    rgb[5:35, 5:25] = (255, 0, 0)
    rgb[5:35, 25:45] = (0, 255, 0)
    rgb[5:35, 45:60] = (0, 0, 255)
    present = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    absent = (0, 255, 255)

    c1 = L.verify_legend_colors(rgb, None, present)
    check("态 1 · 全中 → ok=True，且命中数与 extract.color_mask 的直接求和逐位一致"
          "（SPEC：「不要另写一套」）",
          c1.ok and c1.checked == 3 and c1.matched == 3
          and c1.hits == [int(color_mask(rgb, c).sum()) for c in present],
          f"{c1.to_dict()}")

    c2 = L.verify_legend_colors(rgb, None, [(255, 0, 0), absent, (0, 0, 255)])
    check("态 2 · 任一个 0 命中 → ok=False，并在 missing 里点名该颜色",
          (not c2.ok) and c2.missing == ["#00ffff"] and c2.matched == 2,
          f"{c2.to_dict()}")

    c3 = L.verify_legend_colors(rgb, None, present + [absent])
    check("态 3 · 命中数与条目数不匹配（4 条只找到 3 色）→ ok=False",
          (not c3.ok) and c3.checked == 4 and c3.matched == 3, f"{c3.to_dict()}")

    thin = np.full((60, 60, 3), 255, dtype=np.uint8)
    thin[10:11, 10:39] = (0, 0, 255)          # 恰 29 像素
    thin2 = np.full((60, 60, 3), 255, dtype=np.uint8)
    thin2[10:11, 10:40] = (0, 0, 255)         # 恰 30 像素
    check("min_pixels 边界：命中 29 → False，30 → True（阈值确实在起作用）",
          not L.verify_legend_colors(thin, None, [(0, 0, 255)]).ok
          and L.verify_legend_colors(thin2, None, [(0, 0, 255)]).ok
          and L.verify_legend_colors(thin, None, [(0, 0, 255)]).hits == [29],
          f"{L.verify_legend_colors(thin, None, [(0,0,255)]).hits}")

    only_swatch = np.full((60, 60, 3), 255, dtype=np.uint8)
    only_swatch[20:30, 20:40] = (255, 0, 0)      # 色块带：该色**只**存在于色块内
    chk = L.verify_legend_colors(only_swatch, None, [(255, 0, 0)],
                                 swatch_boxes=[(20, 20, 40, 30)])
    check("加固项（SPEC 字面之外的补充条件）：目标色**只**出现在图例色块内时，"
          "区域命中足额但 plot_hits=0 → ok=False。没有这一条，R1 会被色块自己喂饱而失效",
          chk.hits == [200] and chk.plot_hits == [0] and not chk.ok,
          f"{chk.to_dict()}")

    check("入参口径：#rrggbb / 0..1 浮点 / 8 bit 整数三种写法给出同一判定与同一 hits",
          L.verify_legend_colors(rgb, None, ["#0000ff", (0.0, 0.0, 1.0)], tol=64.0).hits
          == L.verify_legend_colors(rgb, None, [(0, 0, 255)]).hits * 2
          and L.verify_legend_colors(rgb, None, ["#00ffff"]).missing == ["#00ffff"],
          f"{L.verify_legend_colors(rgb, None, ['#0000ff']).to_dict()}")


def test_s2_fixture_end_to_end() -> None:
    """夹具上的正/负例：Fig.4 校验通过并被采用；Fig.3 校验失败并回退。"""
    print("== S2 · 夹具端到端（正负例）==")

    doc = pymupdf.open(PAPER)
    page = doc[0]
    res = {}
    for tag, box in (("fig3", FIG3_RECT), ("fig4", FIG4_RECT)):
        rect = pymupdf.Rect(box)
        info = L.extract_legend(page, rect, dpi=600)
        pm = page.get_pixmap(dpi=600, clip=rect, colorspace=pymupdf.csRGB)
        arr = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, 3)
        sw_px = L.swatch_boxes_px(info, box, 600)
        L.verify_legend(info, arr, (0, 0, pm.width, pm.height), swatch_boxes_px=sw_px)
        res[tag] = (info, arr.shape)

    info3, shape3 = res["fig3"]
    check("Fig.3：图例检出 3 条，来源标为 pdf-text+vector，且排除框覆盖图内全部文字行",
          info3.found and len(info3.entries) == 3 and info3.source == "pdf-text+vector"
          and len(info3.exclude_boxes_px) == 4 and len(info3.swatch_boxes_pt) == 3,
          f"found={info3.found} n={len(info3.entries)} box={len(info3.exclude_boxes_px)}")

    green = [e for e in info3.entries if e.name == "TD 493 K"][0]
    check("Fig.3：R1 失败（芯色 #3fd83f 在曲线区找不到）→ usable=False，"
          "且 reason 点名该颜色",
          (not info3.color_check.ok) and (not info3.usable)
          and "#3fd83f" in info3.color_check.reason,
          f"{info3.color_check.to_dict()}")

    check("Fig.3 反证「只数区域总命中」会被色块喂饱：绿色色块自身 882 px ≥ min_pixels=30，"
          "但剔除色块后为 0 → 必须靠 plot_hits 才能发现",
          green.matched_pixels >= 30 and green.matched_pixels_plot == 0,
          f"hits={green.matched_pixels} plot={green.matched_pixels_plot}")

    check("Fig.3：matched 必须同时计入「剔除色块后不足」这一类失败"
          "（否则出现 checked=3 matched=3 ok=False 的自相矛盾组合，而 matched 是"
          "人扫报告时唯一的数量信号）",
          info3.color_check.checked == 3 and info3.color_check.matched == 2
          and info3.color_check.matched != info3.color_check.checked,
          f"checked={info3.color_check.checked} matched={info3.color_check.matched}")

    info4, shape4 = res["fig4"]
    check("Fig.4（两条且曲线与图例同色）：R1 通过 → usable=True，"
          "可直接采用图例配色",
          info4.color_check.ok and info4.usable and len(info4.colors()) == 2
          and all(p >= 30 for p in info4.color_check.plot_hits),
          f"{info4.color_check.to_dict()}")

    check("SPEC §3.2.4 命名规则：名称**始终与颜色 hex 并存**；未关联到色块时退化为纯名称"
          "（否则错命名看起来可信，无法被发现）",
          info4.names() == ["TD 415 K #7f7fff", "RH #ff7f7f"]
          and L.legend_series_name(L.LegendEntry(name="X")) == "X"
          and L.legend_series_name(L.LegendEntry(name=" X ", color="#010203")) == "X #010203",
          f"{info4.names()}")
    doc.close()


def test_s2_exclude_geometry() -> None:
    """SPEC §3.3：PDF 点 → 裁剪图像素 的换算（全链路最容易静默错位的一步）。"""
    print("== S2 · 点↔像素换算 ==")

    rect = (75.0, 195.0, 280.0, 315.0)
    box = L.text_box_to_px((101.0, 226.31, 121.0, 235.37), rect, 600)
    want = (214.666667, 258.916667, 385.333333, 338.416667)
    check("text_box_to_px：dpi=600 下按 (pt−rect_pt)×600/72 换算并外扩 2 px"
          "（对照手算值，而非对照自身公式）",
          all(abs(a - b) < 1e-5 for a, b in zip(box, want)), f"{box} vs {want}")

    back = P.px_to_pt(L.text_box_to_px((101.0, 226.31, 121.0, 235.37), rect, 600, pad_px=0),
                      rect, 600)
    check("往返一致：pad_px=0 时 px_to_pt ∘ text_box_to_px = 恒等（1e-9 内）；"
          "且 pad 只做**外扩**——pad_px=0 与默认 2 的差恰为 2 px",
          all(abs(a - b) < 1e-9 for a, b in
              zip(back, (101.0, 226.31, 121.0, 235.37)))
          and all(abs(a - b) == 2.0 for a, b in
                  zip(L.text_box_to_px((101.0, 226.31, 121.0, 235.37), rect, 600),
                      L.text_box_to_px((101.0, 226.31, 121.0, 235.37), rect, 600, pad_px=0))),
          f"{back}")

    tmp = Path(tempfile.mkdtemp(prefix="chartdigit_dpi_"))
    good = tmp / "good.png.json"
    good.write_text(json.dumps({"rect_pt": list(rect), "dpi": 600}), encoding="utf-8")
    bad = tmp / "bad.png.json"
    bad.write_text(json.dumps({"rect_pt": [0.0, 0.0, 100.0, 100.0], "dpi": 600}),
                   encoding="utf-8")
    d_ok, w_ok = L.resolve_dpi(1709, rect, sidecar=good)
    d_bad, w_bad = L.resolve_dpi(1709, rect, sidecar=bad)
    d_infer, _ = L.resolve_dpi(1709, rect)
    check("resolve_dpi：sidecar 与 --rect 一致时采信 sidecar（1709/(205pt) ×72 = 600）",
          d_ok == 600 and w_ok == "", f"{d_ok} {w_ok!r}")
    check("resolve_dpi：sidecar 的矩形与 --rect 不符时**拒绝采信**并告警（说明用户拿的是"
          "另一张图的 PNG，任何换算都是错的），同时用反推值兜底",
          d_bad == 600 and "不一致" in w_bad and d_infer == 600,
          f"{d_bad} {w_bad!r}")

    doc = pymupdf.open(PAPER)
    rows = L.text_rows(doc[0], FIG3_RECT)
    doc.close()
    boxes = L.exclusion_boxes_px(rows, rect, 600)
    check("夹具 Fig.3：排除框与文字行一一对应（4 行 → 4 框），且全部落在裁剪图内",
          len(boxes) == len(rows) == 4
          and all(-1 <= x0 < x1 <= 1709 and -1 <= y0 < y1 <= 1000
                  for x0, y0, x1, y1 in boxes)
          and all(abs((x0 + 2) - (r.x0 - rect[0]) * 600 / 72) < 1e-6
                  for (x0, _, _, _), r in zip(boxes, rows)),
          f"{boxes}")


def test_s2_exclude_extract() -> None:
    """SPEC §3.3：``extract_series(exclude=...)`` 的机制与**默认路径逐位不变**。"""
    print("== S2 · 排除掩码 ==")

    mask = np.ones((20, 20), dtype=bool)
    out = apply_exclude_mask(mask, [(5, 5, 10, 10)], 0, 0)
    check("apply_exclude_mask：指定矩形被清空，区域外一位不变",
          out is mask and int(mask.sum()) == 400 - 25
          and not mask[5:10, 5:10].any() and mask[4, :].all() and mask[:, 4].all(),
          f"sum={int(mask.sum())}")

    m2 = np.ones((10, 10), dtype=bool)
    apply_exclude_mask(m2, [(-5, -5, 3, 3), (8, 8, 50, 50)], 0, 0)
    same = np.ones((10, 10), dtype=bool)
    check("越界矩形自动裁剪、不抛异常；boxes 为空时**原样返回同一对象**（不触碰任何像素）",
          int(m2.sum()) == 100 - 9 - 4
          and apply_exclude_mask(same, []) is same and int(same.sum()) == 100,
          f"sum={int(m2.sum())}")

    img = load_image(str(A_PNG))
    box = detect(str(A_PNG)).panels[0].box
    base = extract_series(img.rgb, box, background=img.background_rgb)
    none_ = extract_series(img.rgb, box, background=img.background_rgb, exclude=None)
    empty = extract_series(img.rgb, box, background=img.background_rgb, exclude=[])

    def sig(ss):
        return [(s.name, s.kind, tuple(s.color), tuple(map(tuple, s.points))) for s in ss]

    check("回归红线：exclude=None（以及空列表）时与不传该参数**逐位一致**"
          "——序列名、类型、颜色、全部点坐标完全相同",
          len(base) >= 3 and sig(base) == sig(none_) == sig(empty),
          f"n={len(base)}/{len(none_)}/{len(empty)}")

    x0, y0 = box[0], box[1]
    hit = [p for s in base for p in s.points]
    cut = (x0 + 0.3 * (box[2] - x0), y0, x0 + 0.5 * (box[2] - x0), box[3])
    sub = extract_series(img.rgb, box, background=img.background_rgb, exclude=[cut])
    check("机制生效：把数据区左中段整条竖带排除后，序列的点数确实下降"
          "（掩码在连通域之前生效，不是事后过滤）",
          hit and all(len(s.points) < len(b) for s, b in
                      zip(sub, [s for s in base if s.points])),
          f"{[len(s.points) for s in sub]} vs {[len(s.points) for s in base]}")

    whole = extract_series(img.rgb, box, background=img.background_rgb,
                           exclude=[(0, 0, img.width, img.height)])
    check("排除整个数据区 → 全部序列的点数都低于 min_points，返回空列表"
          "（不会输出一条 0 点的伪序列）",
          whole == [], f"{whole}")


def main() -> int:
    for path in (PAPER, FALLBACK, MANIFEST):
        if not path.is_file():
            print(f"[错误] 夹具缺失: {path}")
            print("请先运行: python make_pdf_cases.py")
            return 1

    test_caption_regex()
    test_caption_disambiguation()
    test_caption_lines_on_fixture()
    test_graphic_region()
    test_tighten()
    test_hex_color()
    test_px_roundtrip()
    test_legend_geometry_signature()
    test_legend_swatch_and_ticks()
    test_verify_legend_colors_contract()
    # ---- S2：legend.py 真实实现（与上面 §3.2.x 的参考实现互不依赖，两道锁）
    test_s2_legend_geometry()
    test_s2_legend_swatch()
    test_s2_r1_real_impl()
    test_s2_fixture_end_to_end()
    test_s2_exclude_geometry()
    test_s2_exclude_extract()
    test_fallback_channels()
    test_crop_and_provenance()
    test_ambiguity_and_cli()

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
