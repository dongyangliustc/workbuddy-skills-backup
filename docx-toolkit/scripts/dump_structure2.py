# -*- coding: utf-8 -*-
"""全量结构导出 v2：覆盖表格内段落；文档顺序嵌入索引；输出 JSON + 摘要。
每个节点记录：全局 seq、类型(para/table/row/cell)、style/outline、drawing rIds、math、text。
同时解析 rels 得到 rId→media 映射。"""
import zipfile, re, json
from lxml import etree

SRC = r"C:\Users\Administrator.DESKTOP-7RU274I\AppData\Local\Temp\pd_v2\main.docx"
OUT = r"C:\Users\Administrator.DESKTOP-7RU274I\AppData\Local\Temp\pd_v2\structure_dump2.json"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"

NS = {"w": W, "m": M, "r": R, "a": A}

z = zipfile.ZipFile(SRC)
doc = etree.fromstring(z.read("word/document.xml"))

# rels: rId -> target
rels = {}
for r in doc.findall(".//r:Relationship", NS):
    # word/_rels/document.xml.rels 需单独读，但 w:document 无内嵌 rels。改用 zip 读 rels 文件
    pass
import zipfile as zf2
with zf2.ZipFile(SRC) as zz:
    relxml = etree.fromstring(zz.read("word/_rels/document.xml.rels"))
    for r in relxml:
        rels[r.get("Id")] = r.get("Target")

rid2media = {}
for rid, tgt in rels.items():
    if tgt.startswith("media/"):
        rid2media[rid] = tgt

def para_text(p):
    return "".join(t.text or "" for t in p.findall(".//w:t", NS))

def para_drawings(p):
    RID = "{%s}embed" % "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    out = []
    for el in p.iter():
        if el.tag == "{%s}blip" % A:
            rid = el.get(RID)
            if rid:
                out.append(rid)
    return out

def para_math(p): return p.find(".//m:oMath", NS) is not None

nodes = []
def handle_para(p, ctx):
    txt = para_text(p)
    dr = [x for x in para_drawings(p) if x]
    math = para_math(p)
    pPr = p.find("w:pPr", NS)
    style = None; outline = None
    if pPr is not None:
        s = pPr.find("w:pStyle", NS)
        if s is not None: style = s.get("{%s}val" % W)
        ol = pPr.find("w:outlineLvl", NS)
        if ol is not None: outline = ol.get("{%s}val" % W)
    nodes.append({
        "seq": len(nodes), "type": "para", "ctx": ctx,
        "style": style, "outline": outline,
        "drawings": dr, "math": math, "text": txt[:160],
    })

body = doc.find("w:body", NS)
order = 0
for child in body:
    tag = etree.QName(child).localname
    if tag == "p":
        handle_para(child, None)
    elif tag == "tbl":
        tctx = "tbl%d" % order
        for ri, tr in enumerate(child.findall("w:tr", NS)):
            for ci, tc in enumerate(tr.findall("w:tc", NS)):
                for p in tc.findall(".//w:p", NS):
                    handle_para(p, f"{tctx}:r{ri}:c{ci}")
    order += 1

with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"nodes": nodes, "rid2media": rid2media}, f, ensure_ascii=False, indent=1)

print(f"节点总数: {len(nodes)}; rId->media 映射 {len(rid2media)} 个")
tot_dr = sum(len(n["drawings"]) for n in nodes)
print(f"drawing 总数(含表内): {tot_dr}")
print("=== media 映射 ===")
for rid, m in sorted(rid2media.items(), key=lambda x:int(x[0][3:])):
    print(f"{rid} -> {m}")

print("\n=== 含 drawing 或 math 或 outline 的段落 ===")
for n in nodes:
    if n["drawings"] or n["math"] or n["outline"] or (n["text"].strip().startswith("5.") and len(n["text"])<20):
        print(f"seq[{n['seq']}] ctx={n['ctx']} style={n['style']} ol={n['outline']} math={n['math']} dr={n['drawings']} text={n['text']!r}")
