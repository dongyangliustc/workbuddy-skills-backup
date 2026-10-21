# -*- coding: utf-8 -*-
"""REA TD 误差棒图 OPJU 构建 —— 参考实现（最终版 v5.1，已验证）。

与 matplotlib 版 REA_TD_errorbar.png 结构完全一致：
5 拟合线 + C4H 填充带（fill-to-next）+ 2 条手工误差棒线段组 + 5 组符号图，
单层、双 log 轴、图例入层内。

数据依赖：BASE/summary.csv（xlsx 导出）与 BASE/fit_curves.csv（拟合曲线）。
复用到其他图时的改法：换数据解析段 + COL/SHAPES + plotxy 列表 + 样式分派，
其余（layer_info / err_segments / set_log_axes / export_preview）直接用
origin_helpers.py。

注意：每次运行会新开 Origin 实例——先杀掉已有 Origin64 进程；构建完成后
由用户在 Origin 窗口 文件→另存为（COM save 在教育版被静默禁用）。
"""
import csv
import json
import os

import numpy as np
import originpro as op
from scipy.interpolate import PchipInterpolator

BASE = r"D:/WORK/workbuddy/2026-09-17-14-45-47"
PREVIEW_DIR = BASE
species_order = ["C2H", "C4H", "C6H", "C8H", "C10H"]

# RGB colors matching the matplotlib version
COL = {
    "C2H": (63, 63, 63),
    "C4H": (230, 51, 35),
    "C6H": (26, 86, 184),
    "C8H": (0, 158, 74),
    "C10H": (155, 48, 201),
}
# symbol kinds per LabTalk -k: 1=square 2=circle 3=up-tri 4=down-tri 5=diamond
SHAPES = {"C2H": 1, "C4H": 2, "C6H": 3, "C8H": 4, "C10H": 5}

def lt(cmd, critical=False):
    r = op.lt_exec(cmd)
    if not r:
        print(f"[FAIL] {cmd}")
        if critical:
            raise RuntimeError(cmd)
    return r

# ---------- data ----------
with open(f"{BASE}/summary.csv", encoding="utf-8") as f:
    payload = json.load(f)
rows = [r.split(",") for r in payload["csv_data"].strip().splitlines()][1:]

D = {sp: {"T": [], "td": [], "p043": [], "m043": []} for sp in species_order}
for r in rows:
    sp, T, sc, rate = r[0], float(r[2]), r[3], float(r[5])
    d = D[sp]
    if sc == "TD":
        d["T"].append(T); d["td"].append(rate)
    elif sc == "TD+043":
        d["p043"].append(rate)
    elif sc == "TD-043":
        d["m043"].append(rate)
for sp in species_order:
    d = D[sp]
    o = np.argsort(np.array(d["T"]))
    for k in ("T", "td", "p043", "m043"):
        d[k] = np.array(d[k])[o]
    d["up"] = np.clip(d["m043"] - d["td"], 0, None)
    d["dn"] = np.clip(d["td"] - d["p043"], 0, None)

with open(f"{BASE}/fit_curves.csv", encoding="utf-8") as f:
    rd = csv.reader(f)
    header = next(rd)
    cols = {name: [] for name in header}
    for row in rd:
        for name, val in zip(header, row):
            cols[name].append(float(val))
fit_T = np.array(cols["T"])

d4 = D["C4H"]
tt4 = np.log10(d4["T"])
band_lower = 10 ** PchipInterpolator(tt4, np.log10(d4["p043"]))(np.log10(fit_T))
band_upper = 10 ** PchipInterpolator(tt4, np.log10(d4["m043"]))(np.log10(fit_T))

# ---------- build ----------
op.set_show(True)
op.new()

wb = op.new_book("w", "READata")
for sp in species_order:
    d = D[sp]
    wks = wb.add_sheet(sp)
    wks.from_list(0, d["T"].tolist(), "T (K)")
    wks.from_list(1, d["td"].tolist(), f"{sp} TD")
    wks.from_list(2, d["p043"].tolist(), f"{sp} TD+43meV")
    wks.from_list(3, d["m043"].tolist(), f"{sp} TD-43meV")
    wks.from_list(4, d["up"].tolist(), f"{sp} ErrUp")
    wks.from_list(5, d["dn"].tolist(), f"{sp} ErrDn")

# error-bar segments: vertical line + end caps, NaN-separated polyline.
# Only species with visible relative errors (>0.5%) get segments.
CAP_F = 1.07  # cap half-width factor (log-spaced)
err_species = []
for sp in species_order:
    d = D[sp]
    rel = max(
        max((u / t for u, t in zip(d["up"], d["td"]) if t > 0), default=0),
        max((v / t for v, t in zip(d["dn"], d["td"]) if t > 0), default=0),
    )
    if rel < 0.005:
        continue
    xs, ys = [], []
    for T, td, up, dn in zip(d["T"], d["td"], d["up"], d["dn"]):
        if up <= 0 and dn <= 0:
            continue
        ylo, yhi = td - dn, td + up
        x1, x2 = T / CAP_F, T * CAP_F
        xs += [T, T, np.nan]; ys += [ylo, yhi, np.nan]      # vertical bar
        xs += [x1, x2, np.nan]; ys += [ylo, ylo, np.nan]    # lower cap
        xs += [x1, x2, np.nan]; ys += [yhi, yhi, np.nan]    # upper cap
    wke = wb.add_sheet(f"{sp}_Err")
    wke.from_list(0, xs, "T (K)")
    wke.from_list(1, ys, f"{sp} ±43meV")
    err_species.append(sp)
print("errseg species:", err_species)

wbf = op.new_book("w", "FitCurves")
wfit = wbf.add_sheet("FitCurves")
wfit.from_list(0, fit_T.tolist(), "T (K)")
for i, sp in enumerate(species_order):
    wfit.from_list(i + 1, cols[f"{sp}_fit"], f"{sp} Fit")
wband = wbf.add_sheet("C4HBand")
wband.from_list(0, fit_T.tolist(), "T (K)")
wband.from_list(1, band_lower.tolist(), "C4H Band Lower (TD+43meV)")
wband.from_list(2, band_upper.tolist(), "C4H Band Upper (TD-43meV)")

bk1, bk2 = wb.name, wbf.name
gp = op.new_graph()
gname = gp.name
print("graph:", gname)
lt(f"win -a {gname}", critical=True)

# 1) fit lines
for i, sp in enumerate(species_order):
    lt(f"plotxy iy:=[{bk2}]FitCurves!(1,{i + 2}) plot:=200 ogl:=[{gname}]1!")
# 2) C4H band edges (adjacent pair)
lt(f"plotxy iy:=[{bk2}]C4HBand!(1,2) plot:=200 ogl:=[{gname}]1!")
lt(f"plotxy iy:=[{bk2}]C4HBand!(1,3) plot:=200 ogl:=[{gname}]1!")
# 3) error-bar segments (under the symbols)
for sp in err_species:
    lt(f"plotxy iy:=[{bk1}]{sp}_Err!(1,2) plot:=200 ogl:=[{gname}]1!")
# 4) data symbols (topmost)
for sp in species_order:
    lt(f"plotxy iy:=[{bk1}]{sp}!(1,2) plot:=201 ogl:=[{gname}]1!")

# ---------- style per plot ----------
gl = gp[0]
plots = gl.plot_list()
print("n plots:", len(plots))

def sname_of(idx):
    """dataset name of plot idx (0-based), via raw layer string property."""
    try:
        return gl.obj.GetStrProp(f"plot{idx + 1}.name") or ""
    except Exception:
        return ""

def col_token_of(sname):
    return sname.split("!", 1)[1] if "!" in sname else ""

def species_from_sheet(sname):
    for sp in species_order:
        if f"]{sp}!" in sname or f"]{sp}\"" in sname:
            return sp
    return None

info = []
for idx, p in enumerate(plots):
    sn = sname_of(idx)
    sym = gl.obj.GetNumProp(f"plot{idx + 1}.symbol.kind")
    info.append((idx, p, sn, sym))
    print(f"plot {idx}: name={sn!r} symbol.kind={sym}")

def parse_sname(sn):
    """'FitCurves_B@3' -> ('FitCurves', 'B', 3)."""
    stem, _, sheet = sn.partition("@")
    book, _, col = stem.partition("_")
    return book, col, int(sheet) if sheet.isdigit() else 0

for idx, p, sn, sym in info:
    book, col, sheet = parse_sname(sn)
    if book == "FitCurves" and sheet == 2:
        # fit lines: col B..F -> C2H..C10H
        ci = ord(col) - ord("A")
        sp = species_order[ci - 1] if 1 <= ci <= 5 else None
        if sp:
            p.color = COL[sp]
            p.set_cmd("-w 1600")   # PNG fit lw=1.6pt
            print(f"styled fit {sp}")
    elif book == "FitCurves" and sheet == 3:
        # C4H band edges: B=lower, C=upper; hairline so band reads as pure fill
        p.color = COL["C4H"]
        p.set_cmd("-w 100")
        print(f"styled band edge {col}")
    elif book == "READata" and col == "B":
        if 2 <= sheet <= 6:   # symbol plot (TD data)
            sp = species_order[sheet - 2]
            p.color = COL[sp]
            p.symbol_kind = SHAPES[sp]
            p.symbol_size = 9
            print(f"styled symbol {sp}")
        elif 7 <= sheet <= 6 + len(err_species):   # error-bar segment lines
            sp = err_species[sheet - 7]
            p.color = COL[sp]
            p.set_cmd("-w 1100")   # PNG elinewidth=1.1pt
            print(f"styled errseg {sp}")

# band fill: on the LOWER edge plot (FitCurves sheet3 col B), fill to next
for idx, p, sn, sym in info:
    book, col, sheet = parse_sname(sn)
    if book == "FitCurves" and sheet == 3 and col == "B":
        p.set_fill_area(2, 9)
        p.transparency = 80
        print("fill area set on Band Lower (idx", idx, ")")

# ---------- axes ----------
lt("layer.x.type=2")
lt("layer.y.type=2")
gl.set_xlim(0.75, 620)
gl.set_ylim(5e-19, 2.5e-12)
lt("layer.x.inc=1")
lt("layer.y.inc=1")
lt('xb.text$="T (K)"')
lt('yl.text$="REA rate coefficients cm\\+(-3) s\\+(-1)"')
lt("legendupdate mode:=lname")
# move legend inside the layer, upper-left free zone (axis-value coordinates)
lt("legend.fsize=11")
lt("legend.x=1.6")
lt("legend.y=2e-13")
print("nlayers =", op.lt_float("page.nlayers"))

# ---------- self preview ----------
if os.path.exists(f"{PREVIEW_DIR}/origin_preview.png"):
    os.remove(f"{PREVIEW_DIR}/origin_preview.png")
lt(f'expGraph type:=png path:="{PREVIEW_DIR}" filename:="origin_preview"')
print("preview exists:", os.path.exists(f"{PREVIEW_DIR}/origin_preview.png"))
print("BUILD_DONE")
