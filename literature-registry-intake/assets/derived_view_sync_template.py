"""One-off: sync the three derived index views (README / by_topic / by_relevance)
after the single-paper intake of Jie et al. 2026 (arXiv:2607.00336).

Preserves CRLF, asserts every edit matched exactly once (so a silent no-op is
impossible), and pads new table rows to the widths declared by the table's
separator row. Whitespace-alignment-sensitive lines use regex.
"""
import io
import re

CRLF = "\r\n"
BASE = "D:/WORK/workbuddy/spherical_wave_literature/"

NEW_PDF = "Jie_2026_asymptotic_wavefunctions_PAD_anions_arXiv.pdf"


def read(path):
    with io.open(BASE + path, encoding="utf-8", newline="") as f:
        text = f.read()
    assert "\n" not in text.replace(CRLF, ""), "found bare LF in %s" % path
    return text.split(CRLF)


def write(path, lines):
    with io.open(BASE + path, "w", encoding="utf-8", newline="") as f:
        f.write(CRLF.join(lines))


def sub(lines, old, new, label):
    joined = CRLF.join(lines)
    n = joined.count(old)
    assert n == 1, "%s: expected 1 match, got %d for %r" % (label, n, old[:60])
    return joined.replace(old, new).split(CRLF)


def subre(lines, pattern, repl, label):
    joined = CRLF.join(lines)
    new, n = re.subn(pattern, repl, joined)
    assert n == 1, "%s: expected 1 match, got %d for %r" % (label, n, pattern)
    return new.split(CRLF)


def find(lines, pred, label):
    idx = [i for i, L in enumerate(lines) if pred(L)]
    assert len(idx) == 1, "%s: expected 1 line, got %d" % (label, len(idx))
    return idx[0]


def table_widths_before(lines, anchor, label):
    """Cell widths as declared by the table separator row just above `anchor`."""
    for j in range(anchor - 1, -1, -1):
        stripped = lines[j].strip()
        if set(stripped) <= set("|-: "):
            cells = lines[j].split("|")[1:-1]
            return [len(c) - 2 for c in cells]
        if stripped and not stripped.startswith("|"):
            break
    raise AssertionError("%s: no table separator found above line %d" % (label, anchor))


def row(cells, widths):
    out = "|"
    for c, w in zip(cells, widths):
        out += " " + c + " " * max(0, w - len(c)) + " |"
    return out


# ─────────────────────────────── by_topic.md ───────────────────────────────
def do_by_topic(lines):
    lines = sub(lines,
                "`registry/candidates.yaml`（63）→ 全文可用 **68 篇**",
                "`registry/candidates.yaml`（65）→ 全文可用 **69 篇**",
                "by_topic: 数据源计数")
    lines = sub(lines,
                "> 计数时点：2026-09-11 11:05（registry 可能被并行调研追加，若数字不符请以 registry 为准）。",
                "> 计数时点：2026-09-28 15:50（本轮单篇入库 Jie 2026；registry 可能被并行调研追加，若数字不符请以 registry 为准）。",
                "by_topic: 计数时点")
    lines = subre(lines, r"(\| T4\s+\| 连续态表示层增强\s+\| )8(\s+\|)", r"\g<1>9\g<2>", "by_topic: T4 计数")
    lines = sub(lines,
                "## T4 · 连续态表示层增强（8 篇）",
                "## T4 · 连续态表示层增强（9 篇）",
                "by_topic: T4 标题")
    lines = sub(lines,
                "这一组给出四类表示层增强范式（Lobatto 形状函数混合、STO/B-spline 混合、多项式高斯、复高斯 cGTO），以及波函数近似（平面波 vs Coulomb 波）的定量评估。",
                "这一组给出五类表示层增强范式（Lobatto 形状函数混合、STO/B-spline 混合、多项式高斯、复高斯 cGTO、**渐近尾部修正**），以及波函数近似（平面波 vs Coulomb 波）的定量评估。",
                "by_topic: T4 引言")
    anchor = find(lines, lambda L: L.startswith("| ⭐⭐⭐⭐ | Marante, Argenti"), "by_topic: Marante 行")
    widths = table_widths_before(lines, anchor, "by_topic: T4 分隔行")
    lines.insert(anchor + 1, row(
        ["⭐⭐⭐⭐",
         "Jie, Zhang, Chen, Liu, Ning (2026) — arXiv:2607.00336",
         "**GTO 增广指数 Slater 尾（~e^-ξr）修正长程渐近行为**；小束缚能阴离子 PAD 与实验比对",
         "`continuum_wave`、`angular_reduction`、`method_comparison`、`benchmark_strategy`"],
        widths))
    return lines


# ───────────────────────────── by_relevance.md ─────────────────────────────
def do_by_relevance(lines):
    lines = sub(lines,
                "> **范围**：68 篇（全文可用，参与分级）。",
                "> **范围**：69 篇（全文可用，参与分级）。",
                "by_relevance: 范围")
    lines = sub(lines,
                "| ⭐⭐⭐⭐ | 中耦合：能改变实现细节或架构选择，或需交叉验证的平行路线领头工作 | 18 |",
                "| ⭐⭐⭐⭐ | 中耦合：能改变实现细节或架构选择，或需交叉验证的平行路线领头工作 | 19 |",
                "by_relevance: 档位表")
    lines = sub(lines,
                "## ⭐⭐⭐⭐ 重要参考（18 篇）",
                "## ⭐⭐⭐⭐ 重要参考（19 篇）",
                "by_relevance: 4★ 标题")
    lines = sub(lines,
                "### 表示层增强（5 篇）",
                "### 表示层增强（6 篇）",
                "by_relevance: 表示层增强标题")
    lines = sub(lines,
                "> 边界说明：本清单的 ⭐ 分级**只覆盖有全文的 68 篇**。",
                "> 边界说明：本清单的 ⭐ 分级**只覆盖有全文的 69 篇**。",
                "by_relevance: 边界说明")
    for n in range(24, 18, -1):  # descending: 19..24 -> 20..25
        lines = sub(lines, "**%d. " % n, "**%d. " % (n + 1), "by_relevance: renumber %d" % n)
    anchor = find(lines, lambda L: L.startswith("**18. Marante"), "by_relevance: Marante 条目")
    entry = [
        "**19. Jie, Zhang, Chen, Liu, Ning (2026)** — *arXiv:2607.00336*",
        "→ **GTO 增广指数 Slater 尾（~e^-ξr）**修正阴离子长程渐近行为：束缚能小（~1 eV）时 GTO 的 ~e^-αr² 衰减远快于正确的 ~e^-ξr，偶极算符的 r 权重又进一步放大长程贡献 → PAD 对渐近行为格外敏感",
        "→ O₂⁻/NO⁻ 振动分辨 PAD 与实验显著改善；强极性 AsO⁻/SbO⁻ 成功反证「长程偶极场出射通道散射」解释；NO⁻ 最低振动态的残余偏差归于 **Born-Oppenheimer / 冻结轨道近似失效**（弱束缚阴离子）",
        "→ 对应模块：`continuum_wave`、`angular_reduction`、`method_comparison`、`benchmark_strategy`",
    ]
    for k, line in enumerate(entry):
        lines.insert(anchor + 1 + k, line)
    return lines


# ──────────────────────────────── README.md ────────────────────────────────
def do_readme(lines):
    lines = sub(lines,
                "> 目录结构最近同步：2026-09-11（对齐实际文件树，68 篇；论文明细表同步完成）",
                "> 目录结构最近同步：2026-09-28（对齐实际文件树，69 篇；论文明细表同步完成）",
                "README: 同步日期")
    lines = subre(lines, r"(├── papers/\s+← PDF 按主题子目录存放（)68( 篇）)", r"\g<1>69\g<2>", "README: papers 计数")
    lines = sub(lines,
                "（Cacelli-Moccia-Rizzo 系列 + 递归积分）【14】",
                "（Cacelli-Moccia-Rizzo 系列 + 递归积分 + Slater 尾修正）【15】",
                "README: GTO_continuum 计数")
    lines = sub(lines,
                "**分布**：⭐⭐⭐⭐⭐ 6 · ⭐⭐⭐⭐ 18 · ⭐⭐⭐ 33 · ⭐⭐ 11 = **68 篇**（全文可用）。",
                "**分布**：⭐⭐⭐⭐⭐ 6 · ⭐⭐⭐⭐ 19 · ⭐⭐⭐ 33 · ⭐⭐ 11 = **69 篇**（全文可用）。",
                "README: 分布")
    lines = sub(lines,
                "> **2026-09-11 状态**：三方一致（`papers/` 68 篇 = registry `paths.pdf` 68 = `sources.yaml` 68），由 `tools/check_three_way.py` 核对（PASS）。",
                "> **2026-09-28 状态**：三方一致（`papers/` 69 篇 = registry `paths.pdf` 69 = `sources.yaml` 69），由 `tools/check_three_way.py` 核对（PASS）。"
                " 本轮单篇入库 Jie, Zhang, Chen, Liu, Ning (2026) — arXiv:2607.00336（GTO + 指数 Slater 尾），归 **T4 ⭐⭐⭐⭐**。",
                "README: 三方状态")
    anchor = find(lines, lambda L: "Marante_2014_hybrid_GTO_BSpline_PRA.pdf" in L, "README: 明细表锚点")
    widths = table_widths_before(lines, anchor, "README: 明细表分隔行")
    lines.insert(anchor + 1, row(
        ["", "Jie et al.", "2026", "arXiv:2607.00336", "`%s`" % NEW_PDF],
        widths))
    return lines


if __name__ == "__main__":
    for path, fn in (("index/by_topic.md", do_by_topic),
                     ("index/by_relevance.md", do_by_relevance),
                     ("index/README.md", do_readme)):
        lines = read(path)
        before = len(lines)
        lines = fn(lines)
        write(path, lines)
        print("%-26s lines %d -> %d  OK" % (path, before, len(lines)))
