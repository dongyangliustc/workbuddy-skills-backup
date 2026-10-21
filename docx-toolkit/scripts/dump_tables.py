# -*- coding: utf-8 -*-
"""精确 dump body 中所有表格每行每列：文本 + 图像占位 + math 占位。"""
import zipfile
from lxml import etree

SRC = r"C:\Users\Administrator.DESKTOP-7RU274I\AppData\Local\Temp\pd_v2\main.docx"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RID = "{%s}embed" % RNS
NS = {"w": W, "m": M, "a": A, "r": RNS}

z = zipfile.ZipFile(SRC)
doc = etree.fromstring(z.read("word/document.xml"))
with zipfile.ZipFile(SRC) as zz:
    rels = etree.fromstring(zz.read("word/_rels/document.xml.rels"))
rid2media = {}
for r in rels:
    rid2media[r.get("Id")] = r.get("Target")

def cell_content(tc):
    parts = []
    for el in tc.iter():
        if el.tag == "{%s}t" % W and el.text:
            parts.append(el.text)
        elif el.tag == "{%s}blip" % A:
            rid = el.get(RID)
            parts.append("[IMG:%s=%s]" % (rid, rid2media.get(rid)))
        elif el.tag == "{%s}oMath" % M:
            parts.append("[MATH]")
    return "".join(parts)

def dump_table(tbl, label):
    print(f"\n===== {label} =====")
    for ri, tr in enumerate(tbl.findall("w:tr", NS)):
        cells = [cell_content(tc) for tc in tr.findall("w:tc", NS)]
        print(f"r{ri:02d}: " + " || ".join(cells))

body = doc.find("w:body", NS)
tbls = body.findall("w:tbl", NS)
print(f"body 中表格数: {len(tbls)}")
for idx, tbl in enumerate(tbls):
    dump_table(tbl, f"tbl[{idx}]")
