# 场景：图片公式 → Word 原生 OMML

把 docx 中公式/符号**图片**替换为可编辑的 Word 原生数学公式（OMML `<m:oMath>`）与**文本+上下标**，并清理残留 `\(...\)` LaTeX 文本与孤儿图片。适合专利交底书、论文中大量数学公式图无法选中/编辑的场景。

## 何时使用
- docx 段落/表格里有大量 `w:drawing`/`a:blip` 图片表达公式或符号
- 出现字面 `\(...\)` LaTeX 残留（转换器未渲染，显示为原始文本）
- 用户要求"公式图片换成可编辑公式/其他格式"

## 方案决策（先问用户）
| 方案 | 适用 |
|---|---|
| **Word 原生公式(OMML) + 上下标文本（推荐）** | 公式图多、需可编辑 |
| 纯文本 Unicode | 符号极简单、无分式 |
- 原则：能文本+`w:vertAlign` 可靠表达的（单符号/简单下标）用文本 run；含分式/hat/tilde/乘积/求和等复杂结构用 OMML。

## 0. 环境准备
- Python venv（需 `lxml` + `latex2mathml`）：项目已有 `C:\Users\Administrator.DESKTOP-7RU274I\.workbuddy\binaries\python\envs\latexenv`（若缺 lxml 则 `pip install lxml`）
- 官方转换器（Windows + MS Office）：`C:\Program Files\Microsoft Office\root\Office16\MML2OMML.XSL`
- 工作目录统一放 `%TEMP%\<task>`（注意：Git Bash `/tmp` 与 Write 工具写的 `/tmp` 不是同一目录！一律用 `C:\Users\...\AppData\Local\Temp\...` 绝对路径）

## 1. 结构分析（精确映射的地基）
导出 `word/document.xml` 结构，必须**覆盖表格内段落**（`w:body` 直子 `w:p` 只含 30/78，表内 `w:tbl>>w:tr>>w:tc>>w:p` 另含大部分）：
- 遍历 body 直子，`w:p` 直接处理；`w:tbl` 递归 `w:tc/w:p` 处理，单元格带 `(tbl{i}:r{r}:c{c})` 上下文
- 每段记录：style/outline、drawing rIds、math、text
- 解析 `word/_rels/document.xml.rels` → `rId→media` 映射
- 读每个 `word/media/*.png` 的**PNG 尺寸**（`struct.unpack(">II", data[16:24])`），宽高比区分「单符号」(ratio≈0.5–1.5) 与「长公式图」(ratio>3)：这是无法视觉看图时判断图片语义的关键手段
- 若有 md/源码源文件，将其符号表/公式表**按行序对齐**单元格——是最权威的内容来源
- ⚠️ `p.findall(".//a:blip")` 必须传命名空间 `namespaces` 参数（lxml 的 elementpath 陷阱），最稳做法是 `for el in p.iter(): if el.tag == AQ+'blip'`

## 2. 生成 OMML 缓存
```python
import latex2mathml.converter
from lxml import etree
xslt = etree.XSLT(etree.parse(XSL))
mml = etree.fromstring(latex2mathml.converter.convert(latex_str).encode())
omml = xslt(mml)  # 根元素即 <m:oMath>
```
- 展示公式（独立段大图）与行内公式分开缓存：`omml_cache/eqNN_omml.xml`、`inline_cache/f_XX_omml.xml`
- latex2mathml 支持 `\tilde/\hat/\times/_{}/^{}`，够用（`\tilde{\phi}` → `<m:acc>`/mover）

## 3. 替换（骨架见 references/apply_omath.py）
建立 `rId → 内容模板` 全量映射（**复用 rId** 跨多处出现是正常的，同一符号用同一模板）：
- `('text', s)` → Cambria Math 字体 run
- `('sub', base, sub)` / `('sup', base, sup)` → base run + vertAlign run
- `('omath', key)` → 从缓存 clone `<m:oMath>`
- `('eq', key, num)` → oMath + 编号 run `(n)`，且段 pPr 设 `jc=center`
- LaTeX 残留：若某 `w:t` 全文恰为 `\(...\)`，整体替换该 run 为 oMath（用 LATEX_MAP 按内容匹配）

写保护：
- 用 `list(doc.iter(WQ+'r'))` 快照后遍历，先 `parent.insert(idx+off, node)` 再 `parent.remove(r)`
- 编号 run：`make_run(" (%d)" % n)`，中文文档公式编号加全角括号可改 `(n)` 为 `（n）`
- 字体：数学 run 设 `rFonts ascii/hAnsi="Cambria Math"`、`eastAsia="宋体"`；`w:t` 设 `xml:space="preserve"`

## 4. rels/media 清理 + 重打包（见 references/apply_omath.py）
- 统计替换后 doc 中所有 `r:embed` 引用 rId
- `document.xml.rels` 删 `Type 含 image 且未引用` 的 Relationship
- ⚠️ **删除 media 文件名必须带 `word/` 前缀**（rels Target 是相对路径 `media/x.png`，zip 路径是 `word/media/x.png`）——之前因漏 `word/` 前缀导致 66 个文件残留
- 重打包顺序：`[Content_Types].xml` 首位 + 其余字母序 + `ZIP_DEFLATED`

## 5. 三层校验（每次替换后必跑）
1. `zipfile.testzip()` 完整性
2. `a:blip` 残留应为 0；`m:oMath` 数 = 预期（eq数 + latex数 + inline omath 数）
3. 文本：`\\(` 残留 0、关键片段不含缺失、字符数合理（替换后应约等于 原字符数 + 新增符号文本）

## 6. 交付
- 先备份正式目录当前 docx 到 `backup/`（带时间戳），再覆盖回写
- 校验回写后的正式文件
- 记入当日 memory 日志

## 踩坑清单
- `xml:space` 属性名必须写完整 URI `{http://www.w3.org/XML/1998/namespace}space`（`"namespace"+"space"` 拼接会得错名）
- lxml elementpath 命名空间必须显式传入；`.iter()` 按 QName 判断最稳
- Git Bash `/tmp` ≠ Write 的 `/tmp`（后者落 `D:\tmp`），工作目录用 Windows 绝对路径
- Python 不认 Git Bash 路径（`/c/...`），一律 `C:\...`
- 展示公式段落若原本无 `jc`，加 `w:jc val=center` 使公式居中
- 删除图片后 docx 体积大幅缩小（本例 236KB→51.5KB），属正常
- 多个结构 dump 脚本：先 dump「透视图」(全部段+表内段) 再做「行内容」dump，两者配合才不易错位

## 参考
- `references/apply_omath.py`：可运行的替换/清理脚本模板（需按文档具体 rId 映射改 RID_MAP）
- `references/dump_tables.py`：表格行内容精确导出模板
