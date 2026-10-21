"""一键运行 chart-digitizer 的全部测试。

流程：重新生成测试图与真值 -> 单元/缺陷锁定 -> API 精度验证 -> CLI 冒烟 -> 汇总退出码。

**必须用受管 venv 解释器运行**（含 pymupdf / openpyxl）：

    C:/Users/Administrator.DESKTOP-7RU274I/.workbuddy/binaries/python/envs/default/Scripts/python.exe

裸解释器会让 PDF 单元测试、精度验证、CLI 冒烟、PDF 冒烟、夹具生成 5 个阶段同时
报错，**看起来像代码回归，实际是解释器选错**。

用法::

    python run_all.py

退出码 0 表示全部通过；非 0 表示存在失败阶段。

**检查数护栏（2026-09-17 自审后新增，P0）**
------------------------------------------------

本脚本原先**只凭子进程退出码**判定阶段通过。这有一个真实缺口：一个断言被
全部删除（或断言块整体被注释掉）的阶段，会打印 ``共 0 项检查，失败 0`` 并以
``exit=0`` 结束 —— 于是它**照样被报"通过"**，而总项数 406 是**人工求和、无保护**。

现已加上护栏：捕获每个阶段的输出，解析 ``共 N 项检查``，与 :data:`BASELINE`
逐阶段比对。判失败的三种情形：

1. 子进程退出码非 0（原有行为，保留）；
2. **基线阶段未能报出项数** —— 说明报数代码被移除/该阶段整体跳过；
3. **项数低于基线** —— 说明断言被删改。

项数**高于**基线视为正常新增（本项目要求"只追加、不改写既有断言"），
但会在汇总里单独标出增量，便于核对并非误伤。

> 与"标签集合比对"（交付目录 ``s4_assertion_label_diff.txt``）的关系：
> 两者互补。标签比对证明**既有断言没被改写**（语义层），护栏证明**断言没被
> 删除**（计数层）。缺任何一层都留有盲区。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

STAGES = [
    ("生成测试用例", "make_testcases.py"),          # 含 make_pdf_cases()
    ("生成黑白线画用例", "make_bw_cases.py"),        # 零彩色图夹具 + truth_bw.json
    ("单元测试（缺陷锁定）", "test_extract_units.py"),
    ("标定审核单元测试", "test_audit_units.py"),
    ("黑白线画分离单元", "test_bwline_units.py"),    # 线宽×划线分离的机制锁定
    ("回归护栏自检", "test_run_all_guard.py"),       # run_all.py 的检查数护栏本身
    ("PDF 单元测试", "test_pdf_units.py"),          # 文献 PDF 定位/裁剪/图例/排除掩码
    ("API 精度验证", "verify_accuracy.py"),
    ("对数坐标验证", "verify_log_axes.py"),
    ("黑白线画精度验证", "verify_bw_accuracy.py"),   # 黑白引擎逐点精度（生成级真值）
    ("CLI 冒烟测试", "test_cli_smoke.py"),
    ("PDF 真实文献冒烟", "test_pdf_smoke.py"),      # 文献不存在时跳过并计为通过
]

#: 各阶段的「检查数」基线（2026-09-17，S4 末 + 自审修复后）。
#: 只有会打印 ``共 N 项检查`` 的阶段才在表内；其余阶段（两个夹具生成器、
#: ``verify_accuracy``、``verify_log_axes``）不报项数，故不设护栏，
#: 仅由退出码把关。本表 **7 项** 合计 **406**（= 自审前 5 阶段 275 + 黑白引擎 131），
#: 其中 ``test_run_all_guard.py`` 是护栏自检（20 项，不属于 406 的口径，
#: 复核 406 时须先排除它）。追加断言时把新数字写进这里；**不得调低任何一项**。
BASELINE: dict[str, int] = {
    "test_extract_units.py": 37,
    "test_audit_units.py": 53,
    "test_bwline_units.py": 52,
    "test_run_all_guard.py": 20,
    "test_pdf_units.py": 81,
    "verify_bw_accuracy.py": 79,
    "test_cli_smoke.py": 80,
    "test_pdf_smoke.py": 24,
}

#: 匹配 ``共 52 项检查，失败 0`` / ``共 79 项检查，失败 0 项``。
CHECK_RE = re.compile(r"共\s*(\d+)\s*项检查")
#: 匹配 ``失败 3 项``。
FAIL_RE = re.compile(r"失败\s*(\d+)\s*项")


def run_script(name: str) -> tuple[int, str]:
    """跑一个阶段，返回 (退出码, 完整输出)。

    输出被捕获后**原样转发**到本进程 stdout，既保留人工可读性，又供护栏解析。
    子进程强制 UTF-8（否则 Windows 下中文会以 cp936 写出，与解码方式不匹配）。
    """
    path = HERE / name
    print(f"\n{'=' * 70}\n>> {name}\n{'=' * 70}", flush=True)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, str(path)], cwd=str(HERE), env=env,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()
    return p.returncode, out


def guard(name: str, out: str) -> str | None:
    """检查数护栏。返回问题描述；``None`` 表示通过。"""
    base = BASELINE.get(name)
    if base is None:
        return None                       # 该阶段本来就不报项数，退出码即全部依据
    m = CHECK_RE.search(out)
    if m is None:
        return (f"未报出项数（基线 {base} 项）—— 报数代码可能被移除，"
                f"或该阶段被整体跳过")
    got = int(m.group(1))
    if got < base:
        n_fail = FAIL_RE.search(out)
        extra = f"，自报失败 {n_fail.group(1)} 项" if n_fail else ""
        return f"项数 {got} < 基线 {base} —— 断言被删改{extra}"
    return None


def main() -> int:
    results: list[tuple[str, str, int, str | None, int | None]] = []
    for label, name in STAGES:
        rc, out = run_script(name)
        problem = guard(name, out)
        m = CHECK_RE.search(out)
        got = int(m.group(1)) if m else None
        results.append((label, name, rc, problem, got))

    print(f"\n{'=' * 70}\n汇总\n{'=' * 70}")
    failed = 0
    total_obs = 0
    total_base = 0
    for label, name, rc, problem, got in results:
        base = BASELINE.get(name)
        if rc != 0:
            status = f"失败 (rc={rc})"
        elif problem:
            status = f"护栏失败：{problem}"
        else:
            status = "通过"
        if rc != 0 or problem:
            failed += 1
        if base is not None:
            total_base += base
            total_obs += got or 0
            count_txt = f"{got}/{base}" if got is not None else f"缺失/{base}"
            if got is not None and got > base:
                count_txt += f" (+{got - base})"
            status = f"{status}  [检查 {count_txt}]"
        print(f"  {label:14s} {name:22s} {status}")

    if total_base:
        delta = total_obs - total_base
        sign = "+" if delta > 0 else ""
        n_cov = len(BASELINE)
        note = ""
        if "test_run_all_guard.py" in BASELINE:
            note = ("（含护栏自检 20 项；**不含**它的 %d 项即一期自审口径的 406）"
                    % (total_base - BASELINE["test_run_all_guard.py"]))
        print(f"\n  护栏覆盖的 {n_cov} 个报数阶段：实得 {total_obs} 项 / 基线 {total_base} 项"
              f"（{sign}{delta}）{note}")
        print(f"  另有 {len(STAGES) - n_cov} 个阶段不报项数，仅由退出码把关")
    if failed:
        print(f"\n{failed} 个阶段失败")
        return 1
    print("\n全部阶段通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
