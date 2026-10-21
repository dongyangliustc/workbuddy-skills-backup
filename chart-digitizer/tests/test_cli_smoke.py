"""CLI 冒烟测试：直接以子进程方式调用命令行，校验退出码、产物与序列数。

**为什么需要这个脚本**

``verify_accuracy.py`` 走的是 Python API，因此只能覆盖 API 的默认参数。而 CLI 的
默认值可能与 API 不同——实测就出现过这样的缺陷：``extract`` 子命令的 ``--min-ratio``
默认值被写成 0.55（而 API 与 ``detect`` 子命令都是 ``None``，即多尺度自适应搜索）。
0.55 恰好使 2x2 多子图检出 0 个面板，导致 ``--panel N`` 全部失败，但 API 路径一切
正常，验证脚本完全没发现。

结论：**凡是用户会走的口径，都必须以该口径本身来验收。** 本脚本即为 CLI 口径的
回归防线。

用法::

    python test_cli_smoke.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "scripts"
TB = HERE
FIG = TB / "figures"

# 选用装有 pillow/numpy/scipy 的解释器；优先同目录的受管 venv
PY = Path(sys.executable)
_VENV = Path(r"C:\Users\Administrator.DESKTOP-7RU274I\.workbuddy\binaries\python\envs\default\Scripts\python.exe")
if _VENV.is_file():
    PY = _VENV

failures: list[str] = []
checks = 0


def run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), str(SKILL / "chartdigit.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if cond:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}  {detail}")
        failures.append(f"{label} {detail}".strip())


def series_count(json_path: Path) -> int:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return len(data["series"])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="chartdigit_cli_"))
    print(f"临时输出目录: {tmp}\n")

    # --- doctor
    print("== doctor ==")
    p = run(["doctor"])
    check("doctor 退出码为 0", p.returncode == 0, f"rc={p.returncode}")
    check("doctor 报告 GetData 已定位", '"exe_found": true' in p.stdout)

    # --- 案例定义：(名称, 图, 额外参数, 期望序列数)
    cases = [
        ("A 多曲线", FIG / "caseA_multicurve.png",
         ["--xlim", "0", "10", "--ylim", "-1.2", "1.2"], 3),
        ("B 散点(自动颜色)", FIG / "caseB_scatter.png",
         ["--xlim", "0", "10", "--ylim", "0", "4"], 2),
        ("C 误差棒", FIG / "caseC_errorbar.png",
         ["--xlim", "0", "10", "--ylim", "0", "3"], 1),
        ("D panel1", FIG / "caseD_panels.png",
         ["--panel", "1", "--xlim", "0", "10", "--ylim", "-1.1", "1.1"], 1),
        ("D panel2", FIG / "caseD_panels.png",
         ["--panel", "2", "--xlim", "0", "10", "--ylim", "-1.1", "1.1"], 1),
        ("D panel3", FIG / "caseD_panels.png",
         ["--panel", "3", "--xlim", "0", "10", "--ylim", "0", "3.4"], 1),
        ("D panel4", FIG / "caseD_panels.png",
         ["--panel", "4", "--xlim", "0", "10", "--ylim", "0", "3.4"], 1),
        # 对数轴——必须走 CLI 覆盖，因为 --log-x/--log-y 是纯 CLI 开关，
        # API 层测试（verify_log_axes 直接传 xscale/yscale）覆盖不到参数解析。
        ("E 半对数", FIG / "caseE_semilog.png",
         ["--xlim", "0", "10", "--ylim", "0.01", "1000", "--log-y"], 1),
        ("F 双对数", FIG / "caseF_loglog.png",
         ["--xlim", "1", "1000", "--ylim", "1", "100000",
          "--log-x", "--log-y"], 1),
    ]

    for name, img, extra, expect in cases:
        print(f"== extract {name} ==")
        slug = "".join(ch for ch in name if ch.isalnum() or ch in "-_")
        out = tmp / f"out_{slug}"
        p = run(["extract", str(img), *extra, "--format", "json", "-o", str(out)])
        check(f"{name} 退出码为 0", p.returncode == 0,
              f"rc={p.returncode} stderr={p.stderr.strip()[:200]}")
        jp = out.with_suffix(".json")
        check(f"{name} 生成 JSON", jp.exists(), str(jp))
        if jp.exists():
            n = series_count(jp)
            check(f"{name} 序列数={expect}", n == expect, f"实际 {n}")

    # --- detect 与 extract 的面板一致（防御 CLI 默认值漂移）
    print("== detect / extract 面板一致性 ==")
    p = run(["detect", str(FIG / "caseD_panels.png")])
    check("detect caseD 检出 4 面板", "面板数: 4" in p.stdout, p.stdout[-200:])

    # --- 交付格式完整性
    print("== 交付格式 ==")
    out2 = tmp / "formats"
    p = run(["extract", str(FIG / "caseA_multicurve.png"),
             "--xlim", "0", "10", "--ylim", "-1.2", "1.2",
             "--format", "csv", "xlsx", "json", "-o", str(out2),
             "--diag", str(tmp / "diag.png")])
    for suf in (".xlsx", ".json", ".csv"):
        found = list(tmp.glob(f"formats*{suf}"))
        check(f"生成 {suf}", bool(found), f"未找到 *{suf}")
    check("生成诊断图", (tmp / "diag.png").exists())

    # --- GetData 工作区往返
    print("== GetData 工作区往返 ==")
    gdw = tmp / "roundtrip.gdw"
    p = run(["extract", str(FIG / "caseA_multicurve.png"),
             "--xlim", "0", "10", "--ylim", "-1.2", "1.2",
             "--format", "json", "-o", str(tmp / "rt_native"),
             "--gdw", str(gdw)])
    check("生成 .gdw", gdw.exists(), p.stderr.strip()[:200])
    if gdw.exists():
        native = json.loads((tmp / "rt_native.json").read_text(encoding="utf-8"))
        pre = run(["dump", str(gdw), "--format", "json", "-o", str(tmp / "rt_gdw")])
        check("dump 退出码为 0", pre.returncode == 0, pre.stderr.strip()[:200])
        g = json.loads((tmp / "rt_gdw.json").read_text(encoding="utf-8"))
        check("往返序列数一致",
              len(native["series"]) == len(g["series"]),
              f"{len(native['series'])} vs {len(g['series'])}")
        if len(native["series"]) == len(g["series"]):
            worst = 0.0
            for a, b in zip(native["series"], g["series"]):
                m = min(len(a["y"]), len(b["y"]))
                for i in range(m):
                    worst = max(worst, abs(a["y"][i] - b["y"][i]))
            check("往返最大偏差 < 1e-3 数据单位", worst < 1e-3, f"实测 {worst:.3e}")
            print(f"        （实测最大偏差 {worst:.3e} 数据单位）")

    # --- 标定审核（尺度来源标注 / 几何交叉校验 / 人工确认留痕）
    # 这几条同样只能走 CLI：--scale-x/--scale-y/--confirm-scale/--strict-scale 全是
    # 纯 CLI 开关，且"是否写产物""退出码是不是 4"都属于用户口径的行为。
    print("== 标定审核 ==")
    semilog = str(FIG / "caseE_semilog.png")
    semilog_args = ["--xlim", "0", "10", "--ylim", "0.01", "1000"]

    # (1) 漏加 --log-y：次刻度使间距非等距 → 必须给出硬警告
    p = run(["extract", semilog, *semilog_args, "--scale-x", "linear",
             "--format", "csv", "-o", str(tmp / "audit_miss")])
    check("漏加 --log-y 时提示「该轴很可能不是线性刻度」",
          "很可能不是线性刻度" in p.stdout, p.stdout[-300:])
    check("漏加 --log-y 时明确指向 --log-y",
          "--log-y" in p.stdout, p.stdout[-300:])

    # (2) 正确声明 → 无硬警告
    p = run(["extract", semilog, *semilog_args, "--log-y", "--scale-x", "linear",
             "--confirm-scale", "--format", "csv", "-o", str(tmp / "audit_ok")])
    check("正确声明 --log-y 后不再出现硬警告",
          "很可能不是线性刻度" not in p.stdout, p.stdout[-300:])
    check("正确声明后审核块报「无警告」", "警告: 无" in p.stdout, p.stdout[-300:])

    # (3) 审核块必须落到 CSV 头部，而不是只打印在终端
    csv_ok = tmp / "audit_ok.csv"
    check("生成带审核块的 CSV", csv_ok.exists())
    if csv_ok.exists():
        head = csv_ok.read_text(encoding="utf-8-sig")
        check("CSV 头部含「标定审核」", "标定审核" in head)
        check("CSV 头部含尺度来源标注", "尺度=log" in head)
        check("CSV 头部含人工确认状态", "审核状态: 已确认" in head)
        check("CSV 头部列出逐刻度换算值", "按当前标定换算的刻度值" in head)

    # (4) JSON 的机器可判定准入字段
    p = run(["extract", semilog, *semilog_args, "--log-y", "--scale-x", "linear",
             "--format", "json", "-o", str(tmp / "audit_json")])
    jp = tmp / "audit_json.json"
    if jp.exists():
        au = json.loads(jp.read_text(encoding="utf-8")).get("scale_audit")
        check("JSON 含 scale_audit", isinstance(au, dict))
        if isinstance(au, dict):
            check("未加 --confirm-scale 时 reviewed_by_human=False",
                  au["reviewed_by_human"] is False, f"{au['reviewed_by_human']}")
            check("未确认时 ready_for_downstream=False",
                  au["ready_for_downstream"] is False)
            check("审核记录含两轴尺度", au["x"]["scale"] == "linear"
                  and au["y"]["scale"] == "log", f"{au['x']['scale']},{au['y']['scale']}")
            check("y 轴给出每格数量级", au["y"]["step_decades"] is not None,
                  f"{au['y']['step_decades']}")

    # (5) --strict-scale 闸门：有警告且无人签字 → 退出码 4 且不写产物
    strict_out = tmp / "audit_strict"
    p = run(["extract", semilog, *semilog_args, "--log-y", "--strict-scale",
             "--format", "csv", "-o", str(strict_out)])
    check("--strict-scale 未确认时退出码为 4", p.returncode == 4, f"rc={p.returncode}")
    check("--strict-scale 拒绝时不写产物",
          not (tmp / "audit_strict.csv").exists())

    # (6) 人工签字后放行
    p = run(["extract", semilog, *semilog_args, "--log-y", "--scale-x", "linear",
             "--strict-scale", "--confirm-scale", "--format", "csv",
             "-o", str(strict_out)])
    check("--strict-scale + --confirm-scale 退出码为 0", p.returncode == 0,
          f"rc={p.returncode} stderr={p.stderr.strip()[:200]}")
    check("签字后写出产物", (tmp / "audit_strict.csv").exists())

    # (7) 尺度声明冲突必须报错而非静默取其一
    p = run(["extract", semilog, *semilog_args, "--log-y", "--scale-y", "linear",
             "--format", "csv", "-o", str(tmp / "audit_conflict")])
    check("--log-y 与 --scale-y linear 冲突时报错", p.returncode != 0,
          f"rc={p.returncode}")
    check("冲突信息明确指向两个开关",
          "scale-y" in (p.stderr + p.stdout) and "log-y" in (p.stderr + p.stdout),
          (p.stderr + p.stdout)[-200:])

    # (8) XLSX 元数据表也带审核区块
    p = run(["extract", semilog, *semilog_args, "--log-y", "--scale-x", "linear",
             "--confirm-scale", "--format", "xlsx", "-o", str(tmp / "audit_xlsx")])
    xp = tmp / "audit_xlsx.xlsx"
    check("生成带审核块的 XLSX", xp.exists())
    if xp.exists():
        try:
            from openpyxl import load_workbook
            wb = load_workbook(xp, read_only=True)
            cells = [str(c) for row in wb["Metadata"].iter_rows(values_only=True)
                     for c in row if c is not None]
            joined = " ".join(cells)
            check("XLSX 元数据含标定审核区块", "标定审核" in joined)
            check("XLSX 元数据含人工确认状态", "已确认" in joined)
        except ImportError:
            print("        （跳过 XLSX 内部校验：未安装 openpyxl）")

    # --- 排除掩码与图例（--exclude / --exclude-from-pdf）
    # 这两组开关是纯 CLI 口径（参数解析、退出码、交付物注入），API 层测试覆盖不到。
    print("== 排除掩码与图例 ==")
    multicurve = str(FIG / "caseA_multicurve.png")
    common = ["--xlim", "0", "10", "--ylim", "-1.2", "1.2", "--format", "json"]

    p = run(["extract", multicurve, *common, "-o", str(tmp / "ex_none")])
    n_none = sum(len(s["x"]) for s in
                 json.loads((tmp / "ex_none.json").read_text(encoding="utf-8"))["series"])
    p1 = run(["extract", multicurve, *common, "--exclude", "200,150,400,250",
              "-o", str(tmp / "ex_one")])
    n_one = sum(len(s["x"]) for s in
                json.loads((tmp / "ex_one.json").read_text(encoding="utf-8"))["series"])
    check("--exclude 单次：退出码 0，且排除矩形确实减少了提取到的点数"
          "（掩码在连通域之前生效）",
          p1.returncode == 0 and 0 < n_one < n_none, f"rc={p1.returncode} {n_one} vs {n_none}")

    p2 = run(["extract", multicurve, *common, "--exclude", "100,50,200,150",
              "--exclude", "300,50,400,150", "-o", str(tmp / "ex_two")])
    check("--exclude 可多次给出（多个矩形并集），退出码 0",
          p2.returncode == 0 and (tmp / "ex_two.json").is_file(), f"rc={p2.returncode}")

    paper = str(FIG / "caseP1_paper.pdf")
    fig4_rect = "330,195,510,315"
    fig3_rect = "75,195,280,315"
    for tag, rect in (("fig4", fig4_rect), ("fig3", fig3_rect)):
        rc = run(["from-pdf", paper, "--page", "1", "--rect", rect,
                  "-o", str(tmp / tag), "--dpi", "300"])
        check(f"夹具 {tag} 裁图成功（供后续 --exclude-from-pdf 端到端使用）",
              rc.returncode == 0 and (tmp / f"{tag}.png").is_file(), f"rc={rc.returncode}")
    fig4 = str(tmp / "fig4.png")
    fig3 = str(tmp / "fig3.png")

    # --- 用法校验：--exclude-from-pdf 必须与 --page/--rect 同时给出
    p = run(["extract", fig4, *common, "--exclude-from-pdf", paper,
             "-o", str(tmp / "ex_missing")])
    check("--exclude-from-pdf 缺 --page/--rect → 退出码 1，且提示指向这两个开关",
          p.returncode == 1 and "--page" in p.stderr and "--rect" in p.stderr,
          f"rc={p.returncode} err={p.stderr[:160]!r}")

    # --- 失败只告警、不改链路结局（图例是可选增强）
    p = run(["extract", fig4, *common, "--exclude-from-pdf", str(tmp / "nope.pdf"),
             "--page", "1", "--rect", fig4_rect, "-o", str(tmp / "ex_nopdf")])
    check("PDF 不存在时仍退出码 0（静默回退到原行为），stderr 给出告警",
          p.returncode == 0 and (tmp / "ex_nopdf.json").is_file()
          and "PDF 不存在" in p.stderr, f"rc={p.returncode} err={p.stderr[:160]!r}")

    p = run(["extract", fig4, *common, "--exclude-from-pdf", paper,
             "--page", "99", "--rect", fig4_rect, "-o", str(tmp / "ex_badpage")])
    check("页码越界时仍退出码 0 并告警（不静默按错误页码取文字层）",
          p.returncode == 0 and "越界" in p.stderr, f"rc={p.returncode} err={p.stderr[:160]!r}")

    # --- 正例：图例颜色校验通过 → 采用图例配色，名称带 hex
    ax4 = ["--axes", "2,2,1498,998", "--xlim", "0", "10", "--ylim", "0", "10"]
    p = run(["extract", fig4, *ax4, "--exclude-from-pdf", paper, "--page", "1",
             "--rect", fig4_rect, "--format", "csv", "xlsx", "json",
             "-o", str(tmp / "leg_ok")])
    jp = tmp / "leg_ok.json"
    check("正例（Fig.4 曲线与图例同色）：退出码 0，序列名称为「图例名 + hex」",
          p.returncode == 0 and jp.is_file()
          and {s["name"] for s in json.loads(jp.read_text(encoding="utf-8"))["series"]}
          == {"TD 415 K #7f7fff", "RH #ff7f7f"},
          f"rc={p.returncode} {p.stderr[:120]!r}")
    if jp.is_file():
        leg = json.loads(jp.read_text(encoding="utf-8"))["legend"]
        check("正例：JSON legend.color_check.ok=True，条目含 hex / kind_hint / 命中像素数",
              leg["found"] and leg["color_check"]["ok"] and leg["usable"]
              and all(e["color"] and e["kind_hint"] and e["matched_pixels"] >= 30
                      for e in leg["entries"]),
              f"{str(leg)[:200]}")
    csvs = sorted(tmp.glob("leg_ok*.csv"))
    head = csvs[0].read_text(encoding="utf-8-sig") if csvs else ""
    check("正例：CSV 头部含「图例」区块、hex、kind_hint 与「颜色校验: 通过」",
          bool(csvs) and "图例" in head and "#7f7fff" in head
          and "kind_hint" in head and "颜色校验: 通过" in head,
          head[:160])

    # --- 负例：图例色与图内色不符（Fig.3 的嵌套芯色 #3fd83f 不在曲线区）
    p = run(["extract", fig3, "--axes", "2,2,1705,998", "--xlim", "0", "10",
             "--ylim", "0", "10", "--exclude-from-pdf", paper, "--page", "1",
             "--rect", fig3_rect, "--format", "csv", "xlsx", "json",
             "-o", str(tmp / "leg_bad")])
    jp3 = tmp / "leg_bad.json"
    check("负例（Fig.3 校验失败）：退出码仍为 0——图例失败**绝不**让链路失败",
          p.returncode == 0 and jp3.is_file(), f"rc={p.returncode} {p.stderr[:160]!r}")
    if jp3.is_file():
        d3 = json.loads(jp3.read_text(encoding="utf-8"))
        leg3 = d3["legend"]
        check("负例：JSON legend.color_check.ok=False，且 missing 点名 #3fd83f",
              (not leg3["color_check"]["ok"]) and "#3fd83f" in leg3["color_check"]["reason"],
              f"{str(leg3['color_check'])[:200]}")
        check("负例：报告的 fallback 写明「已回退自动配色」，并给出图例色与图内实际主色的对照",
              "已回退自动配色" in leg3["fallback"]
              and "图例给出的颜色" in leg3["fallback"]
              and "图内实际主色" in leg3["fallback"]
              and leg3["fallback_detail"]["region_colors"],
              f"{leg3['fallback'][:200]!r}")
        check("负例：绝不硬用未通过校验的颜色——序列名称回落到自动配色（不含图例名）",
              (not leg3["usable"])
              and all("TD " not in s["name"] and "RH" not in s["name"]
                      for s in d3["series"]) and len(d3["series"]) >= 1,
              f"{[s['name'] for s in d3['series']]}")
    csvs3 = sorted(tmp.glob("leg_bad*.csv"))
    head3 = csvs3[0].read_text(encoding="utf-8-sig") if csvs3 else ""
    check("负例：CSV 头部写明「颜色校验: **未通过**」与回退说明",
          bool(csvs3) and "未通过" in head3 and "已回退自动配色" in head3,
          head3[:160])

    # --- XLSX 元数据表也带图例区块
    p = run(["extract", fig4, *ax4, "--exclude-from-pdf", paper, "--page", "1",
             "--rect", fig4_rect, "--format", "xlsx", "-o", str(tmp / "leg_xlsx")])
    xp2 = tmp / "leg_xlsx.xlsx"
    check("生成带图例区块的 XLSX", p.returncode == 0 and xp2.exists(), f"rc={p.returncode}")
    if xp2.exists():
        try:
            from openpyxl import load_workbook
            wb = load_workbook(xp2, read_only=True)
            joined = " ".join(str(c) for row in wb["Metadata"].iter_rows(values_only=True)
                              for c in row if c is not None)
            check("XLSX 元数据含「图例」区块、hex 与颜色校验结论",
                  "图例" in joined and "#7f7fff" in joined and "颜色校验" in joined,
                  joined[-200:])
        except ImportError:
            print("        （跳过 XLSX 内部校验：未安装 openpyxl）")

    # --- --exclude 与 --exclude-from-pdf 并用（并集）
    p = run(["extract", fig4, *ax4, "--exclude", "10,10,60,60",
             "--exclude-from-pdf", paper, "--page", "1", "--rect", fig4_rect,
             *common, "-o", str(tmp / "ex_both")])
    check("--exclude 与 --exclude-from-pdf 可并用（矩形取并集），退出码 0",
          p.returncode == 0 and (tmp / "ex_both.json").is_file(), f"rc={p.returncode}")

    # --- from-pdf 的下一步提示要串得上 S2 的新开关
    p = run(["from-pdf", paper, "--page", "1", "--rect", fig4_rect,
             "-o", str(tmp / "hint"), "--dpi", "300"])
    check("from-pdf 打印的下一步命令已包含 --exclude-from-pdf 与 --page/--rect"
          "（使「裁图 → 取数 + 图例」可一条链串起来）",
          p.returncode == 0 and "--exclude-from-pdf" in p.stdout
          and "--page 1" in p.stdout and "--rect" in p.stdout,
          p.stdout[-300:])

    print(f"\n{'=' * 60}")
    print(f"共 {checks} 项检查，失败 {len(failures)} 项")
    if failures:
        for f in failures:
            print("  [FAIL] " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
