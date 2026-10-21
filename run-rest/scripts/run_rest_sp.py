#!/usr/bin/env python3
"""
run_rest_sp — Gaussian 优化完成后，自动执行 REST R-xDH7/def2-QZVPP 高精度单点能计算的端到端脚本。

工作流：
  Step 0: 前置检查（REST 可执行文件、基组池、Gaussian 输出文件）
  Step 1: 从 Gaussian .log 提取优化几何（无波函数需求时）
         或预留接口供 mokit 智能体传递 .chk 波函数（暂未实现）
  Step 2: 组装 REST TOML 输入文件
  Step 3: 执行 REST 计算
  Step 4: 提取最终能量并输出报告

Usage:
    python run_rest_sp.py <gaussian_log> --charge 0 --spin 1 \\
        --basis-pool /path/to/basis-set-pool \\
        --rest-exec /path/to/rest \\
        [--method R-xDH7] [--output-dir ./rest_sp] [--basis def2-QZVPP]

依赖: Python 3.8+ (标准库, 无额外依赖)
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# ── 导入同目录下的模块 ──────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from extract_geom_from_log import extract_final_geometry, to_xyz
from build_rest_input import build_input
from extract_rest_energy import extract_energies


def check_prerequisites(
    gaussian_log: Path,
    rest_exec: Path,
    basis_pool: Path,
    basis_set: str,
):
    """Step 0: 前置环境检查"""
    errors = []

    if not gaussian_log.exists():
        errors.append(f"Gaussian 输出文件不存在: {gaussian_log}")

    if not rest_exec.exists():
        errors.append(f"REST 可执行文件不存在: {rest_exec}")

    basis_path = basis_pool / basis_set
    auxbas_path = basis_pool / f"{basis_set}-JKFIT"
    if not basis_path.exists():
        errors.append(f"基组目录不存在: {basis_path}")
    if not auxbas_path.exists():
        errors.append(f"辅助基组目录不存在: {auxbas_path}")

    if errors:
        for e in errors:
            print(f"❌ {e}")
        sys.exit(1)

    print("✅ 前置检查通过")
    return basis_path, auxbas_path


def extract_geometry(gaussian_log: Path) -> str:
    """Step 1a: 从 Gaussian .log 提取优化几何（无波函数传递）"""
    print(f"📄 从 Gaussian 输出提取几何: {gaussian_log.name}")
    atoms = extract_final_geometry(str(gaussian_log))
    xyz_str = to_xyz(atoms)
    atom_count = len(atoms)
    print(f"   原子数: {atom_count}")
    print(f"   XYZ:\n{xyz_str}")
    return xyz_str


def create_input(
    params: dict,
    xyz_str: str,
) -> str:
    """Step 2: 构建 REST TOML 输入"""
    params["geom_xyz"] = xyz_str
    toml_str = build_input(params)
    print(f"✅ REST 输入文件构建完成")
    return toml_str


def run_rest(toml_str: str, rest_exec: Path, work_dir: Path) -> str:
    """Step 3: 执行 REST 计算"""
    input_path = work_dir / "input.toml"
    out_path = work_dir / "rest_output.log"

    work_dir.mkdir(parents=True, exist_ok=True)
    input_path.write_text(toml_str, encoding="utf-8")

    print(f"🚀 运行 REST: {rest_exec}")
    print(f"   输入: {input_path}")
    print(f"   输出: {out_path}")
    print(f"   工作目录: {work_dir}")

    result = subprocess.run(
        [str(rest_exec)],
        stdin=input_path.open("r"),
        capture_output=True,
        text=True,
        cwd=str(work_dir),
    )

    if result.stdout:
        out_path.write_text(result.stdout, encoding="utf-8")
    if result.stderr:
        err_path = work_dir / "rest_stderr.log"
        err_path.write_text(result.stderr, encoding="utf-8")

    if result.returncode != 0:
        print(f"⚠️  REST 返回非零退出码: {result.returncode}")
        print(f"   标准错误 (前 20 行):\n" + "\n".join(result.stderr.split("\n")[:20]))

    return str(out_path)


def report_energies(out_path: str):
    """Step 4: 提取并报告能量"""
    try:
        energies = extract_energies(out_path)
    except Exception as e:
        print(f"❌ 能量提取失败: {e}")
        return {}

    print("")
    print("=" * 52)
    print("  📊  REST R-xDH7 高精度单点能结果")
    print("=" * 52)
    for key in ["total", "total_energy", "dft_energy", "optimal_energy",
                 "pt2_energy", "correction"]:
        if key in energies:
            label_map = {
                "total": "R-xDH7 总能量",
                "total_energy": "E(total)",
                "dft_energy": "E(DFA)",
                "optimal_energy": "E(opt)",
                "pt2_energy": "E(PT2)",
                "correction": "E(corr)",
            }
            label = label_map.get(key, key)
            print(f"    {label:20s} = {energies[key]:>16.8f}  a.u.")
    print("=" * 52)

    return energies


def main():
    parser = argparse.ArgumentParser(
        description="Gaussian → REST R-xDH7 高精度单点能端到端计算"
    )

    # 必需参数
    parser.add_argument("gaussian_log", type=str,
                        help="Gaussian 优化输出文件 (.log)")

    # 分子信息
    parser.add_argument("--charge", type=int, required=True,
                        help="体系总电荷")
    parser.add_argument("--spin", type=int, required=True,
                        help="自旋多重度")

    # REST 环境
    parser.add_argument("--basis-pool", type=str, required=True,
                        help="REST 基组池根目录（如 /opt/rest_workspace/rest/basis-set-pool）")
    parser.add_argument("--rest-exec", type=str, required=True,
                        help="REST 可执行文件路径")

    # 计算参数
    parser.add_argument("--method", type=str, default="R-xDH7",
                        help="计算方法（默认 R-xDH7，可选 XYG7/XYG3/sBGE2 等）")
    parser.add_argument("--basis", type=str, default="def2-QZVPP",
                        help="基组名称（默认 def2-QZVPP，JKFIT 辅助基组自动拼接）")
    parser.add_argument("--frozen-core", action="store_true", default=True,
                        help="使用冻芯近似（默认启用）")
    parser.add_argument("--no-frozen-core", dest="frozen_core", action="store_false",
                        help="禁用冻芯近似")

    # 输出
    parser.add_argument("--output-dir", type=str, default="./rest_sp",
                        help="REST 工作目录（默认 ./rest_sp）")
    parser.add_argument("--num-threads", type=int, default=10,
                        help="线程数（默认 10，REST 建议 ≥10）")

    # 波函数传递（预留）
    parser.add_argument("--chk", type=str, default=None,
                        help="(预留) Gaussian .chk 文件，用于波函数传递（mokit 智能体功能）")

    args = parser.parse_args()

    # ── Step 0: 前置检查 ──
    gaussian_log = Path(args.gaussian_log)
    rest_exec = Path(args.rest_exec)
    basis_pool = Path(args.basis_pool)
    work_dir = Path(args.output_dir)

    basis_path, auxbas_path = check_prerequisites(
        gaussian_log, rest_exec, basis_pool, args.basis
    )

    # ── 波函数传递判断 ──
    if args.chk:
        print("⚠️   波函数传递（--chk）功能预留，当前使用 mokit 自动化智能体，尚未实现。")
        print("     将使用几何结构模式（从 .log 提取坐标）替代。")

    # ── Step 1: 提取几何 ──
    xyz_str = extract_geometry(gaussian_log)

    # ── Step 2: 构建 REST 输入 ──
    params = {
        "method": args.method,
        "basis_path": str(basis_path),
        "auxbas_path": str(auxbas_path),
        "charge": args.charge,
        "spin": args.spin,
        "num_threads": args.num_threads,
        "frozen_core": args.frozen_core,
    }
    toml_str = create_input(params, xyz_str)

    # ── Step 3: 执行 REST ──
    out_path = run_rest(toml_str, rest_exec, work_dir)

    # ── Step 4: 提取能量 ──
    energies = report_energies(out_path)

    # ── 保存结构化结果 ──
    result_path = work_dir / "result.json"
    result_data = {
        "status": "success",
        "method": args.method,
        "gaussian_source": str(gaussian_log),
        "energies": energies,
        "geometry_source": "log",
        "chk_source": args.chk,
    }
    result_path.write_text(json.dumps(result_data, indent=2), encoding="utf-8")
    print(f"\n📁 结果摘要已保存: {result_path}")


if __name__ == "__main__":
    main()
