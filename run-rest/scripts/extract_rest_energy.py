#!/usr/bin/env python3
"""
从 REST 输出文件中提取单点能计算结果。

REST 输出中 R-xDH7 的总能量格式预期形如:
    R-xDH7 energy = -XXX.XXXXXXXX a.u.
或包含分解组分:
    E(DFA)   = -XXX.XXXXXXXX a.u.
    E(opt)   = -XXX.XXXXXXXX a.u.
    E(PT2)   = -XXX.XXXXXXXX a.u.
    E(total) = -XXX.XXXXXXXX a.u.

Usage:
    python extract_rest_energy.py <rest_output.log> [--json]
"""

import argparse
import json
import re
import sys
from pathlib import Path


def extract_energies(log_path: str) -> dict:
    """
    从 REST 输出中提取能量信息。
    返回: {component: value_in_hartree, ...}
    """
    log_path = Path(log_path)
    if not log_path.exists():
        raise FileNotFoundError(f"REST 输出文件未找到: {log_path}")

    text = log_path.read_text(encoding="utf-8", errors="replace")

    energies = {}

    # 模式 1: "R-xDH7 energy = -XXX.XXXXXXXX a.u."
    pat_total = re.compile(
        r"(?:R-xDH7|XYG7|XYG3|sBGE2|total)\s*energy\s*=\s*([+-]?\d+\.\d+)",
        re.IGNORECASE,
    )
    for m in pat_total.finditer(text):
        energies["total"] = float(m.group(1))

    # 模式 2: E(DFA) / E(PT2) / E(opt) / E(corr) 分解组分
    pat_comp = re.compile(r"E\((\w+)\)\s*=\s*([+-]?\d+\.\d+)")
    for m in pat_comp.finditer(text):
        comp = m.group(1).lower()
        val = float(m.group(2))
        key_map = {
            "dfa": "dft_energy",
            "opt": "optimal_energy",
            "pt2": "pt2_energy",
            "total": "total_energy",
            "corr": "correction",
        }
        key = key_map.get(comp, comp)
        # 保留最后出现的值
        energies[key] = val

    if not energies:
        raise ValueError(
            f"在 {log_path} 中未找到任何能量信息。\n"
            f"请检查 REST 输出文件是否包含能量行。"
        )

    return energies


def main():
    parser = argparse.ArgumentParser(
        description="从 REST 输出文件提取单点能结果"
    )
    parser.add_argument("log_file", type=str, help="REST 输出文件路径")
    parser.add_argument(
        "--json", action="store_true",
        help="以 JSON 格式输出（便于下游处理）"
    )
    args = parser.parse_args()

    try:
        energies = extract_energies(args.log_file)
    except Exception as e:
        print(f"❌ 能量提取失败: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(energies, indent=2))
    else:
        print("=" * 48)
        print("  REST R-xDH7 单点能计算结果")
        print("=" * 48)
        for key in ["total", "total_energy", "dft_energy", "optimal_energy",
                     "pt2_energy", "correction"]:
            if key in energies:
                label_map = {
                    "total": "R-xDH7 总能量 (E_total)",
                    "total_energy": "E(total)",
                    "dft_energy": "E(DFA)",
                    "optimal_energy": "E(opt)",
                    "pt2_energy": "E(PT2)",
                    "correction": "E(corr)",
                }
                label = label_map.get(key, key)
                print(f"  {label:30s} = {energies[key]:>16.8f}  a.u.")
        print("=" * 48)


if __name__ == "__main__":
    main()
