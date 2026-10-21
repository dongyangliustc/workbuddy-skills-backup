# -*- coding: utf-8 -*-
"""Test 6: set <plot-sname> -om/-op with range variables (avoid %C switching).
A2: plain plotxy, attach minus & plus via sname target -> expect 8..15
D2: symmetric plotxy(Em), override plus via sname target -> expect 8..15
"""
import os
import originpro as op

BASE = "D:/WORK/workbuddy/2026-09-17-14-45-47"
op.set_show(True)
op.new()

wb = op.new_book("w", "ErrT6")
wks = wb.add_sheet("T1")
wks.from_list(0, [1.0, 2.0, 3.0], "X")
wks.from_list(1, [10.0, 10.0, 10.0], "Y")
wks.from_list(2, [2.0, 2.0, 2.0], "Em")
wks.from_list(3, [5.0, 5.0, 5.0], "Ep")
bk = wb.name

def new_graph(tag):
    op.new_graph(lname=tag)
    op.lt_exec(f"win -a {tag}")
    op.lt_exec("layer.x.from=0; layer.x.to=4")

def export(tag):
    op.lt_exec(f"win -a {tag}")
    op.lt_exec("layer.y.rescale=1;doc -uw")
    op.lt_exec(f'expGraph type:=png path:="{BASE}" filename:="err6_{tag}"')
    print(tag, "png:", os.path.exists(f"{BASE}/err6_{tag}.png"))

op.lt_exec(f"range rEm = [{bk}]T1!3; range rEp = [{bk}]T1!4;")

# A2: plain plot, sname target, attach both
new_graph("A2")
op.lt_exec(f"plotxy iy:=[{bk}]T1!(1,2) plot:=201 ogl:=[A2]1!")
sn = op.find_graph("A2")[0].obj.GetStrProp("plot1.name")
print("A2 sname:", sn)
print("A2 om:", op.lt_exec(f"set {sn} -om rEm;"))
print("A2 op:", op.lt_exec(f"set {sn} -op rEp;"))

# D2: symmetric plot, sname target, override plus
new_graph("D2")
op.lt_exec(f"plotxy iy:=[{bk}]T1!(1,2,3) plot:=201 ogl:=[D2]1!")
sn2 = op.find_graph("D2")[0].obj.GetStrProp("plot1.name")
print("D2 sname:", sn2)
print("D2 op:", op.lt_exec(f"set {sn2} -op rEp;"))

for t in ("A2", "D2"):
    export(t)
