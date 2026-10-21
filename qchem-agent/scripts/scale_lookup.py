# -*- coding: utf-8 -*-
"""Look up frequency / ZPE scale factors with an auditable nearest-basis fallback.

Rule encoded here (user ruling 2026-10-20):
  1. Exact (method, basis) match wins.
  2. If the exact row exists but the requested COLUMN is empty, fall back to the
     SAME METHOD with a SIMILAR BASIS -- never to a different method.
  3. When several candidates exist, prefer the one whose basis is closest;
     the distance is a documented, inspectable metric (see BASIS DISTANCE below).
  4. If the method has no data at all in the table, refuse: crossing methods
     (e.g. using B3LYP for CAM-B3LYP) is NOT covered by this rule.

BASIS DISTANCE (lower = closer), weights fixed and reproducible:
    d = 5 * family_mismatch
      + 3 * |zeta_level difference|
      + 2 * |diffuse_level difference|
      + 1 * |polarization_level difference|
      + 0.3 * (1 - name similarity)        # tie-breaker only
  Rationale: basis-family identity matters most (cc-pV vs Pople vs TZV are
  different constructions); then cardinal number (zeta); diffuse functions
  matter strongly for anions/frequencies; polarization order is a finer detail.

The tool REPORTS a ranking with per-item differences; it does not silently
authorise a borrowed value. The human still confirms the pick.

Usage
-----
  python scale_lookup.py --method CAM-B3LYP --basis aug-cc-pVTZ --col both
  python scale_lookup.py --method B3LYP --basis 6-311++G(2d,2p) --col zpe
  python scale_lookup.py --method CAM-B3LYP --basis aug-cc-pVTZ --col zpe \
      --reference-other-methods
"""
from __future__ import annotations

import argparse
import difflib
import io
import os
import re
import sys

COLS = {
    "zpe": 2, "h": 3, "s": 4, "fund": 5, "fund_lo": 6, "fund_hi": 7,
}
COL_LABEL = {
    "zpe": "ZPE", "h": "H(T)-H(0)", "s": "S(T)",
    "fund": "Fundamentals (F)", "fund_lo": "Fund <1000", "fund_hi": "Fund >1000",
}

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TABLE = os.path.join(HERE, "..", "references", "scale-factors",
                             "raw", "liyuanhe211_README_2026-10-20.md")


# ---------------------------------------------------------------- table I/O
def load_rows(path: str) -> list[dict]:
    rows = []
    for ln in io.open(path, encoding="utf-8").read().splitlines():
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if not cells or cells[0] == "Method" or len(cells) != 9:
            continue
        if set(ln) <= set("|-: "):
            continue
        if not cells[0] or not cells[1]:
            continue
        rows.append({
            "method": cells[0], "basis": cells[1],
            "zpe": cells[2], "h": cells[3], "s": cells[4],
            "fund": cells[5], "fund_lo": cells[6], "fund_hi": cells[7],
            "source": cells[8],
        })
    return rows


# ------------------------------------------------------------ basis parsing
def norm_basis(name: str) -> str:
    """Strict normalisation for EXACT matching: lower-case, spaces removed.

    Punctuation is deliberately preserved. The table contains misspelled rows
    (`aug-cc-CpVTZ`, `cc-CpVQZ`, ...) whose values DIFFER from the correctly
    spelled rows (e.g. aug-cc-CpVTZ=0.9550 vs aug-cc-pVTZ=0.9540), so a lossy
    normalisation would silently pick the wrong number.
    """
    return name.strip().lower().replace(" ", "")


def norm_basis_loose(name: str) -> str:
    """Typo-tolerant key used only to DETECT intended matches, never to decide."""
    return norm_basis(name).replace("cpv", "pv")


def parse_basis(name: str) -> dict:
    s = norm_basis(name)
    plain = s.replace("-", "").replace("(", "").replace(")", "").replace(",", "")

    # family
    if s.startswith("aug-cc-pv") or s.startswith("aug-cc-pv"):
        family = "dunning-aug"
    elif s.startswith("cc-pv") or "cc-pv" in s:
        family = "dunning"
    elif s.startswith("ma-") or s.startswith("maug-"):
        family = "ma"
    elif re.search(r"6-3|3-21|sto-", s):
        family = "pople"
    elif "def2" in s:
        family = "def2"
    elif s.startswith("pc-"):
        family = "pc"
    elif "tzv" in s or s in ("tzvp", "tzvpp", "tz2p", "tzp") or s.startswith("tzv"):
        family = "tzv"
    elif s.startswith("spk-"):
        family = "spk"
    elif "sadlej" in s:
        family = "sadlej"
    elif s.startswith("cep-"):
        family = "cep"
    else:
        family = "other"

    # zeta cardinal number
    zeta = 0
    if "5z" in plain or s.startswith("pc-4"):
        zeta = 5
    elif "qz" in plain or s.startswith("pc-3"):
        zeta = 4
    elif "tz" in plain or s.startswith("pc-2"):
        zeta = 3
    elif "dz" in plain or s.startswith("pc-1"):
        zeta = 2
    if family == "pople":
        if s.startswith("6-311"):
            zeta = 3
        elif s.startswith("6-31") or s.startswith("3-21"):
            zeta = 2
        elif s.startswith("sto-"):
            zeta = 1
    if zeta == 0:                      # e.g. MG3S, CEP-31G
        zeta = 3 if "mg3" in s else 2

    # diffuse level: 0 none / 1 single or minimally augmented / 2 augmented
    diffuse = 0
    if "aug-" in s or "++" in s:
        diffuse = 2
    elif "+" in s or s.startswith("ma-") or s.startswith("maug-"):
        diffuse = 1

    # polarization level
    pol = 0
    m = re.search(r"\(([^)]*)\)", s)
    if m:
        grp = m.group(1)
        d = re.findall(r"(\d*)d", grp)
        p = re.findall(r"(\d*)p", grp)
        # Sum the multiplicities: (2d,2p) -> 4, (3df,3pd) -> 6. Do NOT cap at a
        # small number, otherwise different polarization orders tie spuriously.
        pol = (sum(int(x) if x else 1 for x in d)
               + sum(int(x) if x else 1 for x in p))
        pol = min(pol, 8)
    elif "*" in s:
        pol = 1
    elif family.startswith("dunning") or family in ("tzv", "def2", "pc", "ma"):
        pol = 1

    return {"family": family, "zeta": zeta, "diffuse": diffuse, "pol": pol,
            "norm": plain}


def basis_distance(a: dict, b: dict) -> tuple[float, list[str]]:
    why = []
    d = 0.0
    if a["family"] != b["family"]:
        d += 5.0
        why.append(f"family {a['family']} vs {b['family']} (+5)")
    dz = abs(a["zeta"] - b["zeta"])
    if dz:
        d += 3.0 * dz
        why.append(f"zeta {a['zeta']} vs {b['zeta']} (+{3.0*dz:g})")
    dd = abs(a["diffuse"] - b["diffuse"])
    if dd:
        d += 2.0 * dd
        why.append(f"diffuse {a['diffuse']} vs {b['diffuse']} (+{2.0*dd:g})")
    dp = abs(a["pol"] - b["pol"])
    if dp:
        d += 1.0 * dp
        why.append(f"polarization {a['pol']} vs {b['pol']} (+{1.0*dp:g})")
    sim = difflib.SequenceMatcher(None, a["norm"], b["norm"]).ratio()
    d += 0.3 * (1 - sim)
    return round(d, 2), why


# ------------------------------------------------------------------- lookup
def lookup(rows: list[dict], method: str, basis: str, col: str) -> dict:
    key = method.strip().lower()
    same_method = [r for r in rows if r["method"].strip().lower() == key]
    target = parse_basis(basis)
    ci = COLS[col]
    req_strict = norm_basis(basis)
    req_loose = norm_basis_loose(basis)

    exact = [r for r in same_method if norm_basis(r["basis"]) == req_strict]
    exact_ok = [r for r in exact if r[col]]
    if exact_ok:
        return {"status": "EXACT", "value": exact_ok[0][col], "row": exact_ok[0],
                "candidates": []}

    # Same basis after typo repair -- report but never confuse with the strict row.
    typo = [r for r in same_method
            if norm_basis(r["basis"]) != req_strict
            and norm_basis_loose(r["basis"]) == req_loose and r[col]]
    if typo and not exact:
        r = typo[0]
        return {"status": "TYPO_MATCH", "value": r[col], "row": r, "candidates": [],
                "note": (f"表中该基组拼写异常（{r['basis']}，疑为录入错误），"
                         f"按修复后匹配取 {r[col]}；引用前请核对原文。")}

    if not same_method:
        return {"status": "NO_METHOD", "value": None, "row": None, "candidates": [],
                "note": f"表内无 {method} 的任何数据；本规则不允许跨方法外推。"}

    cands = []
    for r in same_method:
        if not r[col]:
            continue
        d, why = basis_distance(target, parse_basis(r["basis"]))
        cands.append({"row": r, "dist": d, "why": why})
    cands.sort(key=lambda c: (c["dist"], c["row"]["basis"]))

    return {
        "status": "EXACT_ROW_EMPTY_OR_MISSING" if exact else "BASIS_FALLBACK",
        "value": cands[0]["row"][col] if cands else None,
        "row": cands[0]["row"] if cands else None,
        "candidates": cands,
        "note": ("该方法的精确行存在但目标列为空" if exact
                 else "表内无该基组行") if cands else f"同方法 {method} 在该列无任何数据。",
    }


def print_result(method: str, basis: str, col: str, res: dict, other_refs=None) -> None:
    print(f"\n=== {method} / {basis}  ->  {COL_LABEL[col]} ===")
    if res["status"] == "EXACT":
        r = res["row"]
        print(f"  ✓ 精确匹配: {r['method']}/{r['basis']} = {res['value']}   [source: {r['source']}]")
        return
    if res["status"] == "TYPO_MATCH":
        r = res["row"]
        print(f"  ⚠ {res['note']}")
        print(f"  → {r['method']}/{r['basis']} = {res['value']}   [source: {r['source']}]")
        return
    if res["status"] == "NO_METHOD":
        print(f"  ✗ {res['note']}")
        return

    print(f"  · {res['note']}")
    if not res["candidates"]:
        print(f"  ✗ {res['note']}")
        return
    print(f"  推荐（距离最小）: {res['row']['basis']} = {res['value']}   [source: {res['row']['source']}]")
    print(f"  {'rank':>4s} {'basis':22s} {'value':8s} {'dist':>6s}  diff")
    for i, c in enumerate(res["candidates"][:6], 1):
        why = "; ".join(c["why"]) if c["why"] else "identical construction"
        print(f"  {i:4d} {c['row']['basis']:22s} {c['row'][col]:8s} {c['dist']:6.2f}  {why}")
    if len(res["candidates"]) > 6:
        print(f"  ... {len(res['candidates'])-6} more same-method candidates")
    if len(res["candidates"]) == 1:
        print("  ⚠ 同方法仅此一条数据，无法做候选间比较；借用须在结果中标注基组差异。")

    # Diffuse functions matter disproportionately for anions and low-frequency modes.
    tb = parse_basis(basis)
    pb = parse_basis(res["row"]["basis"])
    if tb["diffuse"] != pb["diffuse"]:
        lvl = {0: "none", 1: "single/ma", 2: "aug/++"}
        print(f"  ⚠ 弥散级别不一致：请求 {lvl[tb['diffuse']]} vs 候选 {lvl[pb['diffuse']]}；"
              f"阴离子与低频模式对该项敏感，须复核。")

    if other_refs:
        print(f"\n  [仅旁证，不得作为主值] 其他方法在 {basis} 上的同类数据:")
        for r in other_refs:
            print(f"    {r['method']:12s} {r['basis']:16s} {col.upper()}={r[col]:8s}  [{r['source']}]")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", required=True)
    ap.add_argument("--basis", required=True)
    ap.add_argument("--col", default="both", choices=list(COLS) + ["both"])
    ap.add_argument("--table", default=os.path.normpath(DEFAULT_TABLE))
    ap.add_argument("--reference-other-methods", action="store_true",
                    help="列出其他方法在同一基组上的值（仅旁证）")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.table):
        print(f"table not found: {args.table}", file=sys.stderr)
        return 2
    rows = load_rows(args.table)
    if not rows:
        print("no rows parsed", file=sys.stderr)
        return 2

    cols = ["zpe", "fund"] if args.col == "both" else [args.col]
    for col in cols:
        res = lookup(rows, args.method, args.basis, col)
        refs = None
        if args.reference_other_methods and res["status"] != "EXACT":
            refs = [r for r in rows if norm_basis(r["basis"]) == norm_basis(args.basis) and r[col]]
        print_result(args.method, args.basis, col, res, refs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
