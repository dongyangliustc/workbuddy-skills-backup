# -*- coding: utf-8 -*-
"""清理不再引用的 image rels/media，并按 OOXML 约定重打包为 final.docx。"""
import zipfile, os
from lxml import etree

BASE = r"C:\Users\Administrator.DESKTOP-7RU274I\AppData\Local\Temp\pd_v2"
SRC = os.path.join(BASE, "out.docx")
FINAL = os.path.join(BASE, "final.docx")

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RID = "{%s}embed" % R

z = zipfile.ZipFile(SRC)
doc = etree.fromstring(z.read("word/document.xml"))

# 1) 统计 document.xml 中仍被引用的所有 r:id / r:embed（含 textbox、其它属性）
used_rids = set()
for el in doc.iter():
    for attr, val in el.attrib.items():
        if attr == RID:
            used_rids.add(val)
# 其他部件（若有 header/footer）也纳入
all_used = set(used_rids)

# 2) document.xml.rels 中删除未引用的 image rel
rels_path = "word/_rels/document.xml.rels"
rels = etree.fromstring(z.read(rels_path))
RNS = "http://schemas.openxmlformats.org/package/2006/relationships"
r_root = rels
removed = []
kept = []
for rel in list(r_root):
    rid = rel.get("Id")
    tgt = rel.get("Target") or ""
    typ = rel.get("Type") or ""
    if "image" in typ.lower() and rid not in all_used:
        removed.append((rid, tgt))
        r_root.remove(rel)
    else:
        kept.append((rid, tgt, typ))
print(f"rels 保留 {len(kept)} 条, 删除 image rel {len(removed)} 条")

# 3) 决定删除的 media 文件（zip 内路径带 word/ 前缀）
del_media = set(("word/" + tgt) for _, tgt in removed if tgt.startswith("media/"))
print("将删除 media 文件:", len(del_media))

# 4) 重打包
new_parts = {}
new_parts["word/document.xml"] = etree.tostring(doc, xml_declaration=True, encoding="UTF-8", standalone=True)
new_parts[rels_path] = etree.tostring(r_root, xml_declaration=True, encoding="UTF-8", standalone=True)

names = z.namelist()
ct = "[Content_Types].xml"
ordered = [ct] + sorted(n for n in names if n != ct)
with zipfile.ZipFile(FINAL, "w", zipfile.ZIP_DEFLATED) as zo:
    for n in ordered:
        if n in del_media:
            continue
        data = new_parts.get(n, z.read(n))
        zo.writestr(n, data)

print("写出:", FINAL)
print("media 保留:", len([n for n in z.namelist() if n.startswith("word/media/")]) - len(del_media))
