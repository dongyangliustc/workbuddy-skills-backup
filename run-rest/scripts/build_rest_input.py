#!/usr/bin/env python3
"""
构建 REST TOML 格式输入文件，用于高精度单点能计算。

核心方法：R-xDH7（重整化双杂化泛函）
推荐基组：def2-QZVPP / def2-QZVPP-JKFIT

Usage:
    python build_rest_input.py <params.json> [--output input.toml]
"""

import argparse
import json
import sys
from pathlib import Path


TEMPLATE = """\
[ctrl]
# --- 任务控制 ---
job_type = "energy"
print_level = {print_level}
num_threads = {num_threads}

# --- 分子信息 ---
charge = {charge}
spin = {spin}
spin_polarization = {spin_polarization}

# --- 计算方法 ---
xc = "{method}"
basis_path = "{basis_path}"
auxbas_path = "{auxbas_path}"
basis_type = "gaussian"
eri_type = "ri-v"

# --- SCF 控制 ---
initial_guess = "{initial_guess}"
mixer = "{mixer}"
max_scf_cycle = {max_scf_cycle}

# --- Post-SCF ---
frozen_core_postscf = {frozen_core}
"""


def build_input(params: dict) -> str:
    """
    根据参数字典组装 REST TOML 输入文件字符串。
    """
    default = {
        "print_level": 1,
        "num_threads": 10,
        "charge": 0,
        "spin": 1,
        "spin_polarization": False,
        "method": "R-xDH7",
        "basis_path": "",
        "auxbas_path": "",
        "initial_guess": "sad",
        "mixer": "pulay-diis",
        "max_scf_cycle": 100,
        "frozen_core": True,
        "geom_unit": "angstrom",
        "geom_comment": "Optimized geometry from Gaussian",
    }

    p = {**default, **params}

    # 参数校验
    if not p["basis_path"]:
        raise ValueError("basis_path 不能为空，必须指定基组路径。")
    if not p["auxbas_path"]:
        raise ValueError("auxbas_path 不能为空，必须指定辅助基组（JKFIT）路径。")
    if p["num_threads"] < 10:
        p["num_threads"] = 10  # REST 建议强制至少 10 线程

    # 组装 [ctrl]
    ctrl = TEMPLATE.format(
        print_level=p["print_level"],
        num_threads=p["num_threads"],
        charge=p["charge"],
        spin=p["spin"],
        spin_polarization=str(p["spin_polarization"]).lower(),
        method=p["method"],
        basis_path=p["basis_path"],
        auxbas_path=p["auxbas_path"],
        initial_guess=p["initial_guess"],
        mixer=p["mixer"],
        max_scf_cycle=p["max_scf_cycle"],
        frozen_core=str(p["frozen_core"]).lower(),
    )

    # 组装 [geom]
    geom = (
        f'\n[geom]\n'
        f'unit = "{p["geom_unit"]}"\n'
        f'# {p["geom_comment"]}\n'
        f'position = """\n'
        f'{p["geom_xyz"].strip()}\n'
        f'"""\n'
    )

    return ctrl + geom


def main():
    parser = argparse.ArgumentParser(
        description="构建 REST TOML 格式输入文件"
    )
    parser.add_argument("params", type=str, help="参数字典 JSON 文件路径")
    parser.add_argument(
        "--output", "-o", type=str, default="input.toml",
        help="输出 TOML 文件路径（默认 input.toml）"
    )
    args = parser.parse_args()

    try:
        params = json.loads(Path(args.params).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ 参数文件读取失败: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        toml_str = build_input(params)
        Path(args.output).write_text(toml_str, encoding="utf-8")
        print(f"✅ REST 输入文件已生成: {args.output}")
    except Exception as e:
        print(f"❌ 生成失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
