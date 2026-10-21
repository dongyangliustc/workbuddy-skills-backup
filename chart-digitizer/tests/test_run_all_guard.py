"""``run_all.py`` 检查数护栏的自检（对应自审发现 A2）。

**为什么需要这个文件**：``run_all.py`` 原先只凭退出码判定阶段通过，于是"断言
全被删光"的阶段会打印 ``共 0 项检查，失败 0`` 并 ``exit=0``，照样被报"通过"。
新增的护栏本身也是代码，**同样可能失效**——最隐蔽的失效方式是：把
``BASELINE`` 里的键名改掉（或把阶段改名），于是 ``BASELINE.get(name)`` 恒为
``None``，护栏**每次都"通过"**，却什么都不查。

本文件用**纯函数级**的对抗用例把护栏钉住，不重跑任何测试阶段（无递归）。

    python test_run_all_guard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_all  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, bool(ok), detail))


def synth(n: int | None, n_fail: int = 0) -> str:
    """造一段像真实阶段的输出。``n=None`` 表示不报项数。"""
    body = "  [OK]   某条断言\n"
    if n is None:
        return body + "全部通过\n"
    tail = f"失败 {n_fail} 项" if n_fail else "失败 0"
    return body + f"\n{'=' * 60}\n共 {n} 项检查，{tail}\n全部通过\n"


def main() -> int:
    # --- 1. 基本口径 ---------------------------------------------------
    ans = run_all.guard("test_cli_smoke.py", synth(80))
    check("项数等于基线 -> 护栏放行", ans is None, f"得 {ans!r}")

    ans = run_all.guard("test_cli_smoke.py", synth(95))
    check("项数高于基线 -> 放行（视为新增断言）", ans is None, f"得 {ans!r}")

    ans = run_all.guard("test_cli_smoke.py", synth(0))
    check("项数 0（断言全删）-> 拦截", ans is not None, f"得 {ans!r}")
    check("项数 0 的拦截信息指出低于基线",
          ans is not None and f"< 基线 {run_all.BASELINE['test_cli_smoke.py']}" in ans,
          f"得 {ans!r}")

    ans = run_all.guard("test_cli_smoke.py", synth(79))
    check("项数 79 < 基线 80 -> 拦截", ans is not None, f"得 {ans!r}")

    ans = run_all.guard("test_cli_smoke.py", synth(3, n_fail=3))
    check("拦截信息带上自报失败数", ans is not None and "失败 3" in ans,
          f"得 {ans!r}")

    # --- 2. 报数行整体消失（最隐蔽的失效） -------------------------------
    ans = run_all.guard("test_cli_smoke.py", synth(None))
    check("基线阶段不报项数 -> 拦截", ans is not None, f"得 {ans!r}")
    check("拦截信息含『未报出』", ans is not None and "未报出" in ans,
          f"得 {ans!r}")

    # --- 3. 非报数阶段不应被误拦（否则每次跑都假红） ---------------------
    ans = run_all.guard("make_testcases.py", synth(None))
    check("非基线阶段不报项数 -> 放行（避免假红）", ans is None, f"得 {ans!r}")
    ans = run_all.guard("__不存在__.py", synth(None))
    check("未知阶段 -> 放行（由退出码把关）", ans is None, f"得 {ans!r}")

    # --- 4. 解析健壮性 --------------------------------------------------
    check("能解析『失败 0』（无『项』字）", run_all.CHECK_RE.search("共 24 项检查，失败 0") is not None)
    check("能解析『失败 0 项』", run_all.CHECK_RE.search("共 24 项检查，失败 0 项") is not None)
    check("不把『项』字前的数字吃错", run_all.CHECK_RE.search("共 81 项检查").group(1) == "81")
    check("失败数正则独立可用", run_all.FAIL_RE.search("共 81 项检查，失败 7 项").group(1) == "7")

    # --- 5. 防静默失效：基线表的每条都必须真在 STAGES 里 -------------------
    stage_names = [n for _, n in run_all.STAGES]
    check("STAGES 无重名", len(stage_names) == len(set(stage_names)))
    missing = [k for k in run_all.BASELINE if k not in stage_names]
    check("BASELINE 每个键都对应一个真实阶段", not missing, f"悬空键 {missing}")

    # 基线表的键必须指向磁盘上真实存在的文件（阶段被删/改名即报错）
    absent = [k for k in run_all.BASELINE if not (HERE / k).exists()]
    check("BASELINE 的每个键都是磁盘上的真实文件", not absent, f"缺失 {absent}")

    # --- 6. 总口径：406 必须能被复算出来 --------------------------------
    guard7 = {k: v for k, v in run_all.BASELINE.items()
              if k != "test_run_all_guard.py"}
    check("护栏覆盖的 7 个阶段合计 406",
          sum(guard7.values()) == 406,
          f"得 {sum(guard7.values())}")
    check("这 7 个阶段就是 MEMORY 记录的那 7 个",
          sorted(guard7) == sorted([
              "test_extract_units.py", "test_audit_units.py",
              "test_bwline_units.py", "test_pdf_units.py",
              "verify_bw_accuracy.py", "test_cli_smoke.py",
              "test_pdf_smoke.py"]),
          f"得 {sorted(guard7)}")
    check("本自检阶段自己也进了基线表", "test_run_all_guard.py" in run_all.BASELINE)

    # --- 汇总 -----------------------------------------------------------
    print("=" * 66)
    print("run_all.py 检查数护栏 —— 自检")
    print("=" * 66)
    n_fail = 0
    for label, ok, detail in RESULTS:
        tag = "[OK]  " if ok else "[FAIL]"
        print(f"  {tag} {label}" + (f"  <- {detail}" if detail and not ok else ""))
        if not ok:
            n_fail += 1
    print(f"共 {len(RESULTS)} 项检查，失败 {n_fail}")
    if n_fail:
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
