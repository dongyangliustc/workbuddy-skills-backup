#!/usr/bin/env python3
"""
从 Gaussian 输出文件 (.log/.out) 中提取末态优化几何结构。

提取策略：
1. 若存在 "Standard orientation" 段 → 取最后一组的 Cartesian 坐标
2. 若无优化步骤（单点能任务）→ 取 "Input orientation" 段
3. 输出格式：XYZ 坐标（Angstrom 为单位）

Usage:
    python extract_geom_from_log.py <gaussian.log> [--output <geom.xyz>]
"""

import argparse
import re
import sys
from pathlib import Path


def extract_final_geometry(log_path: str) -> list[dict]:
    """
    从 Gaussian 输出文件中提取末态几何。
    返回: [{"element": str, "x": float, "y": float, "z": float}, ...]
    """
    log_path = Path(log_path)
    if not log_path.exists():
        raise FileNotFoundError(f"Gaussian 输出文件未找到: {log_path}")

    text = log_path.read_text(encoding="utf-8", errors="replace")

    # 优先找 Standard orientation（优化任务）
    sections = list(
        re.finditer(
            r"Standard orientation:.*?Coordinates \(Angstroms\).*?\n"
            r"-{3,}\n(.*?)\n\s*-{3,}",
            text,
            re.DOTALL,
        )
    )

    if not sections:
        # 退化到 Input orientation（单点能任务）
        sections = list(
            re.finditer(
                r"Input orientation:.*?Coordinates \(Angstroms\).*?\n"
                r"-{3,}\n(.*?)\n\s*-{3,}",
                text,
                re.DOTALL,
            )
        )

    if not sections:
        raise ValueError(
            f"在 {log_path} 中未找到任何几何坐标段。"
            "请确认该文件是有效的 Gaussian 输出文件。"
        )

    # 取最后一组坐标
    last_section = sections[-1].group(1)

    atoms = []
    # 解析坐标行:  行号  原子序数  原子类型    x       y       z
    #            6       8        0    0.000  0.000  0.117
    for line in last_section.strip().split("\n"):
        parts = line.strip().split()
        if len(parts) < 6:
            continue
        try:
            atomic_number = int(parts[1])
            x, y, z = float(parts[3]), float(parts[4]), float(parts[5])
        except (ValueError, IndexError):
            continue
        element = _atomic_number_to_symbol(atomic_number)
        atoms.append({"element": element, "x": x, "y": y, "z": z})

    if not atoms:
        raise ValueError("解析坐标失败，请检查 Gaussian 输出文件格式。")

    return atoms


def _atomic_number_to_symbol(z: int) -> str:
    """质子数 → 元素符号"""
    elements = [
        "X", "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
        "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
        "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
        "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
        "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
        "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
        "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
        "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
        "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
        "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
        "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds",
        "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
    ]
    if 0 <= z < len(elements):
        return elements[z]
    return f"E{z:03d}"


def to_xyz(atoms: list[dict]) -> str:
    """原子列表 → XYZ 格式字符串"""
    lines = [f"{len(atoms)}", "Extracted from Gaussian output"]
    for a in atoms:
        lines.append(f"{a['element']:>3s}  {a['x']:>12.8f}  {a['y']:>12.8f}  {a['z']:>12.8f}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="从 Gaussian 输出文件提取末态优化几何（XYZ 格式）"
    )
    parser.add_argument("log_file", type=str, help="Gaussian 输出文件路径 (.log/.out)")
    parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="输出 XYZ 文件路径（默认 stdout）"
    )
    args = parser.parse_args()

    try:
        atoms = extract_final_geometry(args.log_file)
        xyz_str = to_xyz(atoms)

        if args.output:
            Path(args.output).write_text(xyz_str, encoding="utf-8")
            print(f"✅ 几何写入: {args.output}")
            print(f"   原子数: {len(atoms)}")
        else:
            print(xyz_str)
    except Exception as e:
        print(f"❌ 错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
