# -*- coding: utf-8 -*-
"""主替换脚本：将 5.4/5.5/5.6 区域全部公式/符号图片替换为 OMML 与文本（含上下标）。
依据 structure_dump2 + dump_tables 建立的精确 rId->内容 映射。
输出 pd_v2/out.docx（未清理 rels，视 debug 需要）。
"""
import zipfile, re, glob, os
from lxml import etree

BASE = r"C:\Users\Administrator.DESKTOP-7RU274I\AppData\Local\Temp\pd_v2"
SRC = os.path.join(BASE, "main.docx")
OUT = os.path.join(BASE, "out.docx")
OMML_CACHE = os.path.join(BASE, "omml_cache")
INLINE_CACHE = os.path.join(BASE, "inline_cache")

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
XML = "http://www.w3.org/XML/1998/namespace"

WQ = "{%s}" % W
MQ = "{%s}" % M
AQ = "{%s}" % A
RIDQ = "{%s}embed" % R

# ---------- 内容模板：rId -> 描述 ----------
# ('text', s)                      简单文本
# ('sub', base, sub)               基础 + 下标文本（base 可空）
# ('sup', base, sup)               基础 + 上标文本
# ('omath', cachekey)              从 inline_cache 读 OMML
# ('eq', cachekey, num)            展示公式：OMML + 编号 (num)
RID_MAP = {
    # 符号表 / 参数表 / 正文单字符
    "rId9": ("text", "p"), "rId10": ("text", "k"), "rId11": ("text", "s"),
    "rId13": ("text", "C"), "rId16": ("text", "R"), "rId27": ("text", "T"),
    "rId28": ("text", "W"), "rId29": ("text", "η"), "rId31": ("text", "ω"),
    "rId32": ("text", "α"), "rId38": ("text", "M"), "rId39": ("text", "D"),
    "rId45": ("text", "ε"), "rId18": ("text", "ζ"), "rId19": ("text", "γ"),
    "rId43": ("text", "β"), "rId40": ("text", "σ∥"), "rId41": ("text", "σ⊥"),
    "rId30": ("text", "η>0"), "rId44": ("text", "−1≤β≤2"),
    "rId35": ("text", "l=0,1,2,…"), "rId71": ("text", "exp_arg<10"),
    "rId68": ("text", "p=k"),
    # 上下标
    "rId14": ("sub", "R", "A"), "rId15": ("sub", "", "A"),
    "rId20": ("sub", "n", "x"), "rId21": ("sub", "", "x"),
    "rId22": ("sub", "Z", "A"), "rId23": ("sub", "P", "μν"),
    "rId25": ("sub", "V", "eff"), "rId26": ("sub", "V", "Coul"),
    "rId33": ("sub", "j", "l"), "rId34": ("sub", "", "l"),
    "rId36": ("sub", "Y", "lm"), "rId42": ("sub", "σ", "tot"),
    "rId63": ("sub", "N", "r"), "rId64": ("sub", "N", "Ω"),
    "rId66": ("sub", "E", "kin"), "rId67": ("sub", "R", "L/V"),
    # OMML 行内
    "rId24": ("omath", "f_G0"), "rId37": ("omath", "f_dhat"),
    "rId12": ("omath", "f_s_eq"), "rId17": ("omath", "f_R_eq"),
    "rId59": ("omath", "f_4pi2aok"), "rId60": ("omath", "f_4pi2akw"),
    "rId65": ("omath", "f_NrNo"), "rId69": ("omath", "f_Nr20"),
    "rId70": ("omath", "f_No14"), "rId72": ("omath", "f_Nr16"),
    "rId73": ("omath", "f_No6"), "rId74": ("omath", "f_Nr20xNo14"),
}
# 展示公式 rId46..62 -> eq01..15
EQ_RIDS = {}
eq_nums = {"rId46":1,"rId47":2,"rId48":3,"rId49":4,"rId50":5,"rId51":6,
           "rId52":7,"rId53":8,"rId54":9,"rId55":10,"rId56":11,"rId57":12,
           "rId58":13,"rId61":14,"rId62":15}
for rid, n in eq_nums.items():
    RID_MAP[rid] = ("eq", "eq%02d" % n, n)

# LaTeX 残留（单个 w:t 完整内容）-> inline cache key
LATEX_MAP = {
    r"\(P(p)\)": "f_Pp", r"\(Q(p)\)": "f_Qp",
    r"\(\tilde{\phi}_A(p)\)": "f_phitA", r"\(\tilde{\rho}(s)\)": "f_rhot",
    r"\(G(p;k)\)": "f_Gpk", r"\(K(p,q)\)": "f_Kpq",
}

# ---------- 缓存读取 ----------
_omml_cache = {}
def load_omath(key):
    if key not in _omml_cache:
        for d in (OMML_CACHE, INLINE_CACHE):
            p = os.path.join(d, f"{key}_omml.xml")
            if os.path.exists(p):
                root = etree.parse(p).getroot()
                _omml_cache[key] = root
                break
        else:
            raise RuntimeError(f"缺失 OMML 缓存: {key}")
    return _omml_cache[key]

# ---------- run / oMath 构造 ----------
def ensure_center(p):
    """确保段落 pPr 有 jc=center（公式段居中）。"""
    ppr = p.find(WQ + "pPr")
    if ppr is None:
        ppr = etree.Element(WQ + "pPr")
        p.insert(0, ppr)
    jc = ppr.find(WQ + "jc")
    if jc is None:
        jc = etree.SubElement(ppr, WQ + "jc")
    jc.set(WQ + "val", "center")

def make_run(text, vert=None):
    r = etree.Element(WQ + "r")
    rpr = etree.SubElement(r, WQ + "rPr")
    rf = etree.SubElement(rpr, WQ + "rFonts")
    rf.set(WQ + "ascii", "Cambria Math")
    rf.set(WQ + "hAnsi", "Cambria Math")
    rf.set(WQ + "eastAsia", "宋体")
    if vert:
        va = etree.SubElement(rpr, WQ + "vertAlign")
        va.set(WQ + "val", vert)
    t = etree.SubElement(r, WQ + "t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    return r

def build_nodes(tpl):
    """模板 -> 节点列表。传入含命名空间的孤立元素即可（插入时 lxml 自动绑定）。"""
    kind = tpl[0]
    if kind == "text":
        return [make_run(tpl[1])]
    if kind == "sub":
        base, sub = tpl[1], tpl[2]
        nodes = []
        if base:
            nodes.append(make_run(base))
        nodes.append(make_run(sub, "subscript"))
        return nodes
    if kind == "sup":
        base, sup = tpl[1], tpl[2]
        nodes = []
        if base:
            nodes.append(make_run(base))
        nodes.append(make_run(sup, "superscript"))
        return nodes
    if kind == "omath":
        return [load_omath(tpl[1])]
    if kind == "eq":
        o = load_omath(tpl[1])
        # 加编号 run
        num = make_run(" (%d)" % tpl[2])
        # oMath 包装在 oMathPara 内不强制；直接 oMath + 编号即可
        return [o, num]
    raise ValueError(kind)

# ---------- 主流程 ----------
z = zipfile.ZipFile(SRC)
doc = etree.fromstring(z.read("word/document.xml"))

replaced_drawings = []
# 快照全部 w:r 与其段（避免遍历时修改报错）
all_runs = [r for r in doc.iter(WQ + "r")]
for r in all_runs:
    parent = r.getparent()
    if parent is None:
        continue
    # 该 run 内是否有 blip
    target_rid = None
    for b in r.iter():
        if b.tag == AQ + "blip":
            rid = b.get(RIDQ)
            if rid:
                target_rid = rid
                break
    if target_rid is None:
        continue
    if target_rid not in RID_MAP:
        continue
    tpl = RID_MAP[target_rid]
    nodes = build_nodes(tpl)
    # 公式段：确保 pPr jc=center
    if tpl[0] == "eq":
        p = r
        while p is not None and p.tag != WQ + "p":
            p = p.getparent()
        if p is not None:
            ensure_center(p)
    idx = list(parent).index(r)
    for off, node in enumerate(nodes):
        parent.insert(idx + off, node)
    parent.remove(r)
    replaced_drawings.append((target_rid, tpl[0]))

# LaTeX 残留替换（单个 w:t 完全匹配）
all_t = [t for t in doc.iter(WQ + "t")]
latex_count = 0
for t in all_t:
    if not t.text:
        continue
    full = t.text.strip()
    if full in LATEX_MAP:
        key = LATEX_MAP[full]
        rr = t.getparent()          # w:r
        parent = rr.getparent()
        o = load_omath(key)
        idx = list(parent).index(rr)
        parent.insert(idx, o)
        parent.remove(rr)
        latex_count += 1

print(f"替换 drawing runs: {len(replaced_drawings)}")
print(f"替换 LaTeX 残留: {latex_count}")
from collections import Counter
print("按类型统计:", dict(Counter(k for _, k in replaced_drawings)))

# 写回 XML
new_doc = etree.tostring(doc, xml_declaration=True, encoding="UTF-8", standalone=True)

# 重打包
names = z.namelist()
ct = "[Content_Types].xml"
ordered = [ct] + sorted(n for n in names if n != ct)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zo:
    for n in ordered:
        if n == "word/document.xml":
            data = new_doc
        else:
            data = z.read(n)
        zo.writestr(n, data)
print("写出:", OUT)
