"""第 8 阶段：**真实文献**的端到端能力探测（文献不存在时必须优雅跳过）。

为什么单独成阶段
----------------

``test_pdf_units.py`` 全部用自包含夹具做确定性断言，零真实文献依赖——这是**刻意的**：
真实文献会被移动、替换、甚至不在机器上。但夹具毕竟是合成的，它不能回答"这套链路
在真期刊上到底能不能用"。本阶段补的就是这一层，并且严格限定断言强度：

* **文件不存在 → 打印"（跳过：文献不存在）"并计为通过**，且**不计入检查数**
  （计入会让"跳过的检查"看起来像"通过的检查"）。
* **存在 → 只断言"能力存在"**（能列出图、能裁出图、能检出图例、端到端能产出交付物），
  **不断言任何具体数值**（张数、颜色、点数都可能因文献换版而变）。
* 涉及的标签（``Figure N``）一律**从 `--list` 输出里现取**，不写死——写死就等于
  把测试绑在某一份 PDF 的排版上。

另含 SPEC §6 的**回归红线**（机器可判定的那一份）：A/B/C 两轴 ``uniform``、
E 的 x ``uniform`` 且 y ``nonuniform``、F 两轴 ``nonuniform``。线性轴一旦被误判为
``nonuniform``，标定审核的硬警告就开始**误报**——比漏报更糟。夹具图恒在，故这部分
无条件执行。

用法::

    python test_pdf_smoke.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "scripts"
FIG = HERE / "figures"
CLI = SKILL / "chartdigit.py"

sys.path.insert(0, str(SKILL))

from chartdigit import legend as L  # noqa: E402
from chartdigit.audit import PATTERN_NONUNIFORM, PATTERN_UNIFORM, classify_spacing  # noqa: E402
from chartdigit.detect import detect, load_image, ticks_for_box  # noqa: E402

PY = Path(sys.executable)
_VENV = Path(
    r"C:\Users\Administrator.DESKTOP-7RU274I\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)
if _VENV.is_file():
    PY = _VENV

#: 实测用真实文献（**可能被移走**；不存在即跳过，不让测试变红）
REAL_PDFS = {
    "P1": Path(r"D:/WORK/Coulomb/Fitting wave/Obara-1986-Efficient recursive computation of.pdf"),
    "P2": Path(r"D:/WORK/KHP_C5/respond/J. Phys. Chem. A 2007, 111, 10998-11005.pdf"),
    "P3": Path(r"D:/WORK/KHP_C5/respond/Communications Chemistry, 4(1), 72.pdf"),
}

#: ``from-pdf --list`` 的候选行（页码 1 起 / 标签 / bbox_pt / 来源）
LIST_RE = re.compile(
    r"#\d+\s+第\s*(\d+)\s*页\s+标签=(\S+?)\s+"
    r"bbox_pt=\(\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s+来源=(\S+)"
)

failures: list[str] = []
count = 0
skipped: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global count
    count += 1
    if cond:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}  {detail}")
        failures.append(f"{label} {detail}".strip())


def skip(msg: str) -> None:
    """不存在的文献：打印跳过并记录，**不**计入检查数。"""
    skipped.append(msg)
    print(f"  （跳过：{msg}）")


def run(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), str(CLI), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def parse_list(stdout: str) -> list[dict]:
    out = []
    for m in LIST_RE.finditer(stdout):
        out.append({
            "page": int(m.group(1)),
            "label": m.group(2),
            "rect": tuple(float(m.group(i)) for i in (3, 4, 5, 6)),
            "source": m.group(7),
            "ambiguous": "需消歧" in stdout[m.end():stdout.find("\n", m.end())],
        })
    return out


def pick_crop_target(cands: list[dict], prefer: str = "2") -> dict | None:
    """挑一个**无歧义且有编号**的候选用来自动裁剪。

    ``prefer`` 是 SPEC 点名要试的编号；不满足时退回"第一个无歧义的有编号候选"，
    以免文献换版后测试变成对某一份排版的硬绑定。
    """
    usable = [c for c in cands if c["label"] not in ("", "?") and not c["ambiguous"]]
    for c in usable:
        if c["label"] == prefer:
            return c
    return usable[0] if usable else None


# ============================================================ 回归红线（无条件执行）


def test_linear_axis_regression() -> None:
    """SPEC §6.2：线性轴必须仍判 ``uniform``（夹具图恒在，本检查无条件执行）。"""
    print("== 回归红线：线性轴仍判 uniform ==")
    want = {
        "A": (PATTERN_UNIFORM, PATTERN_UNIFORM, "A 多曲线"),
        "B": (PATTERN_UNIFORM, PATTERN_UNIFORM, "B 散点"),
        "C": (PATTERN_UNIFORM, PATTERN_UNIFORM, "C 误差棒"),
        "E": (PATTERN_UNIFORM, PATTERN_NONUNIFORM, "E 半对数"),
        "F": (PATTERN_NONUNIFORM, PATTERN_NONUNIFORM, "F 双对数"),
    }
    for tag, (wx, wy, name) in want.items():
        files = sorted(FIG.glob(f"case{tag}_*.png"))
        if not files:
            check(f"{name}：夹具缺失 {tag}", False, "请先运行 make_testcases.py")
            continue
        img = load_image(str(files[0]))
        rep = detect(str(files[0]))
        box = rep.panels[0].box if rep.panels else (0, 0, img.width, img.height)
        tx, ty = ticks_for_box(img, box)
        px, py = classify_spacing(tx)["pattern"], classify_spacing(ty)["pattern"]
        check(f"{name}：x={px} / y={py}（期望 x={wx} / y={wy}）",
              px == wx and py == wy,
              f"实测 x={px}({len(tx)} 刻度) y={py}({len(ty)} 刻度)")


# ==================================================== 真实文献能力探测（可跳过）


def test_real_pdf_listing(tmp: Path) -> dict[str, list[dict]]:
    """``from-pdf --list`` 在真实文献上必须能列出图，且两条通道都要覆盖。"""
    print("== 真实文献：--list 定位能力 ==")
    found: dict[str, list[dict]] = {}
    for tag, pdf in REAL_PDFS.items():
        if not pdf.is_file():
            skip(f"{tag} 文献不存在：{pdf}")
            continue
        p = run(["from-pdf", str(pdf), "--list"])
        cands = parse_list(p.stdout)
        check(f"{tag} --list 退出码 0（无副作用探测，随时可跑）",
              p.returncode == 0, f"rc={p.returncode} err={p.stderr[:200]!r}")
        check(f"{tag} 至少列出一张图（只断言「能力存在」，不断言张数）",
              len(cands) >= 1, f"检出 {len(cands)} 个候选")
        print(f"        {tag} 检出 {len(cands)} 个候选；来源构成 "
              f"{sorted({c['source'] for c in cands})}")
        found[tag] = cands

    if "P1" in found:
        check("P1（扫描件页）走通道 B：候选中含 source=raster",
              any(c["source"] == "raster" for c in found["P1"]),
              f"{sorted({c['source'] for c in found['P1']})}")
    if "P3" in found:
        check("P3（矢量图页）走通道 A：候选中含 source=caption",
              any(c["source"] == "caption" for c in found["P3"]),
              f"{sorted({c['source'] for c in found['P3']})}")
    return found


def test_real_pdf_end_to_end(tmp: Path, found: dict[str, list[dict]]) -> None:
    """真实文献的裁剪 → 图例 → 端到端提取（只断言链路与交付物存在）。"""
    print("== 真实文献：裁剪 → 图例 → 端到端 ==")
    for tag in ("P3", "P2", "P1"):
        if tag not in found:
            continue
        cand = pick_crop_target(found[tag])
        if cand is None:
            skip(f"{tag} 无可用的无歧义候选，跳过裁剪")
            continue
        pdf = REAL_PDFS[tag]
        out = tmp / f"{tag}_fig"
        p = run(["from-pdf", str(pdf), "--figure", cand["label"],
                 "-o", str(out), "--dpi", "600"])
        png = out.with_suffix(".png")
        check(f"{tag} --figure {cand['label']} 裁出 PNG 且 600 dpi clip 生效（宽 > 1000 px）",
              p.returncode == 0 and png.is_file() and load_image(str(png)).width > 1000,
              f"rc={p.returncode} exists={png.is_file()} "
              f"w={load_image(str(png)).width if png.is_file() else '-'}")
        if not png.is_file():
            continue

        img = load_image(str(png))
        # 图例能力探测（复用 legend.py 的真实入口）
        doc = pymupdf.open(str(pdf))
        try:
            page = doc[cand["page"] - 1]
            rows = L.text_rows(page, pymupdf.Rect(cand["rect"]))
            groups = L.group_legend_lines(rows)
            info = L.extract_legend(page, pymupdf.Rect(cand["rect"]), dpi=600)
        finally:
            doc.close()
        if tag == "P3":
            check("P3 图例：在该图区域内检出 ≥2 条**同左 x 的小字号**文字（几何签名命中）",
                  any(len(g) >= 2 and max(r.x0 for r in g) - min(r.x0 for r in g) <= 3.0
                      for g in groups),
                  f"文字行 {len(rows)} 条、组 {[len(g) for g in groups]}")
            print(f"        P3 图例条目：{[e.name for e in info.entries]}；"
                  f"颜色：{[e.color for e in info.entries]}")

        # 端到端：from-pdf → extract（带 --exclude-from-pdf 与图例）
        rep = detect(str(png))
        box = rep.panels[0].box if rep.panels else (0, 0, img.width, img.height)
        axes = ",".join(str(int(v)) for v in box)
        base = tmp / f"{tag}_e2e"
        pe = run(["extract", str(png), "--axes", axes, "--xlim", "0", "10",
                  "--ylim", "0", "10", "--exclude-from-pdf", str(pdf),
                  "--page", str(cand["page"]),
                  "--rect", ",".join(f"{v:.2f}" for v in cand["rect"]),
                  "--format", "csv", "json", "-o", str(base)])
        js = base.with_suffix(".json")
        csvs = sorted(tmp.glob(f"{tag}_e2e*.csv"))
        n_series = 0
        if js.is_file():
            n_series = len(json.loads(js.read_text(encoding="utf-8"))["series"])
        check(f"{tag} 端到端可跑通并产出交付物（CSV 与 JSON；序列数 ≥1，不断言数值）",
              pe.returncode == 0 and bool(csvs) and js.is_file() and n_series >= 1,
              f"rc={pe.returncode} csv={len(csvs)} series={n_series} "
              f"err={pe.stderr.strip()[:200]!r}")

        if js.is_file():
            d = json.loads(js.read_text(encoding="utf-8"))
            leg = d.get("legend")
            check(f"{tag} JSON 含 legend 字段且带 color_check 结论"
                  f"（通过对配色生效，未通过对配色回退——两者都是合法结局）",
                  isinstance(leg, dict) and "found" in leg and "color_check" in leg,
                  f"{str(leg)[:160]}")
            if isinstance(leg, dict) and leg.get("found"):
                print(f"        {tag} 图例：usable={leg.get('usable')} "
                      f"color_check={(leg.get('color_check') or {}).get('ok')} "
                      f"fallback={'有' if leg.get('fallback') else '无'}")
            if csvs:
                head = csvs[0].read_text(encoding="utf-8-sig")
                check(f"{tag} CSV 头部含「图例」区块与配色结论",
                      "图例" in head and ("颜色校验" in head or "未检出" in head),
                      head[:200])

        tx, _ty = ticks_for_box(img, box)
        pat = classify_spacing(tx)["pattern"]
        if tag == "P3":
            check(f"{tag} 裁图回归红线：横轴（线性）仍判 uniform（SPEC §4.3 点名的用例，"
                  f"{len(tx)} 个刻度）",
                  pat == PATTERN_UNIFORM, f"x={pat}")
        else:
            # 红线要防的是**误报硬警告**（把线性轴判成 nonuniform），而不是"检不出刻度"
            # ——后者只是"无法校验"，属于已有的保守行为。真实文献的裁图里可能根本
            # 没有刻度（面板检测落到了别的区域），把它判红会让测试对文献排版产生
            # 硬依赖，正是 SPEC §4.1 要避免的。
            check(f"{tag} 裁图未把横轴误报为 nonuniform（{len(tx)} 个刻度 → {pat}）",
                  pat != PATTERN_NONUNIFORM, f"x={pat}")

        cand_info = (tmp / f"{tag}_e2e.json")
        if cand_info.is_file():
            ex = json.loads(cand_info.read_text(encoding="utf-8"))
            print(f"        排除掩码："
                  f"{len(ex.get('extraction_params', {}).get('exclude_boxes', []))} 个矩形")


def main() -> int:
    print(f"真实文献可用性：")
    for tag, pdf in REAL_PDFS.items():
        print(f"  {tag}: {'存在' if pdf.is_file() else '不存在'}  {pdf}")

    tmp = Path(tempfile.mkdtemp(prefix="chartdigit_smoke_"))
    print(f"临时输出目录: {tmp}\n")

    test_linear_axis_regression()
    found = test_real_pdf_listing(tmp)
    test_real_pdf_end_to_end(tmp, found)

    print(f"\n{'=' * 60}")
    print(f"共 {count} 项检查，失败 {len(failures)} 项")
    if skipped:
        print(f"跳过 {len(skipped)} 项（不计入检查数）：")
        for s in skipped:
            print(f"  - {s}")
    if failures:
        for f in failures:
            print("  [FAIL] " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
