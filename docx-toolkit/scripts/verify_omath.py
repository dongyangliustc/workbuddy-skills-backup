# -*- coding: utf-8 -*-
"""校验 out.docx：可解析性、drawing 残留、OMML 数量、LaTeX 残留、文本完整性。"""
import zipfile
from lxml import etree

SRC = r"C:\Users\Administrator.DESKTOP-7RU274I\AppData\Local\Temp\pd_v2\out.docx"
ORIG = r"C:\Users\Administrator.DESKTOP-7RU274I\AppData\Local\Temp\pd_v2\main.docx"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"

z = zipfile.ZipFile(SRC)
bad = z.testzip()
print("zip 完整性:", "OK" if bad is None else f"BAD {bad}")
names = z.namelist()
doc = etree.fromstring(z.read("word/document.xml"))

# 1. drawing 残留
blips = list(doc.iter(A + "blip"))
print("a:blip 残留:", len(blips))
# 检查是否还有 w:drawing 残留（除特殊锚定图，如 mermaid 系统框图应保留？）
drawings = list(doc.iter("{%s}drawing" % W))
print("w:drawing 残留:", len(drawings))

# 2. OMML 数量
omaths = list(doc.iter(M + "oMath"))
print("m:oMath 数量:", len(omaths))
omathparas = list(doc.iter(M + "oMathPara"))
print("m:oMathPara 数量:", len(omathparas))

# 3. LaTeX 残留
import re
st = etree.tostring(doc).decode("utf8")
n_latex = st.count(r"\(")
print(r"\( 残留:", n_latex)

# 4. 文本完整性：关键片段抽查
must_have = ["动量转移量", "⑫ 截面前因子与总截面", "径向积分点数", "角度 Lebedev 点数",
             "复平面轮廓偏移参数", "指数稳定性判据", "总网格规模", "光电子动能扫描范围",
             "长度/速度规范比值", "束缚 GTO 指数", "组合高斯指数", "笛卡尔 GTO",
             "第  阶球贝塞尔函数" if False else "阶球贝塞尔函数", "散射中心", "扭曲波网格",
             "库仑部分（局域", "其中  取高斯" if False else "取高斯/洛伦兹轮廓",
             "截面前因子", "动量归一化约定", "类氢原子基线", "双原子分子基准"]
alltext = "".join(t.text or "" for t in doc.iter(W + "t"))
print("全文字符数:", len(alltext))
missing = [s for s in must_have if s not in alltext]
print("缺失关键片段:", missing if missing else "无")

# 5. vertAlign 存疑字符校验（确保没有可显示异常）
subs = sum(1 for va in doc.iter(W + "vertAlign"))
print("vertAlign 数:", subs)

# 6. 与原文总字符比（图像占位扣除后应接近）
zo = zipfile.ZipFile(ORIG)
doc_o = etree.fromstring(zo.read("word/document.xml"))
# 原文本中除去 78 个图像占位（无占位文本，直接数 w:t）
alltext_o = "".join(t.text or "" for t in doc_o.iter(W + "t"))
print("原文 w:t 字符:", len(alltext_o), " -> 新文 w:t 字符:", len(alltext))
# 差异来源：\\(..\\) 6 个残留被替换为 oMath（文本消失 6 个约 xx 字符），图像替换的文本新增
# 统计新增字符
print("字符差异(新-原):", len(alltext) - len(alltext_o))
