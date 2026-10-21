# docx 字体缺失字形（豆腐块）修复

## 何时使用
- Word/WPS 打开 docx 后，部分特殊符号（上标 ⁻、下标 ₂、数学减号 −、圈数字、希腊字母等）显示为方框/问号/空白。
- 常见成因：生成工具把 run 的 `rFonts` 的 `ascii`/`hAnsi`/`eastAsia` 全设为某个中文字体（如宋体 SimSun），而该字体对西文扩展 Unicode 字符无字形。

## 诊断流程（先定位，不盲改）
1. 解压 docx：`unzip -o file.docx -d extract/`，正文在 `extract/word/document.xml`。
2. 找出你怀疑的字符，读取其所在 `<w:r>` 的 `<w:rPr><w:rFonts>`（注意 ascii/hAnsi/eastAsia 三个属性）。
3. **验证字形是否真缺失**：用 python fontTools 读系统字体 cmap，逐个字符比对。
   ```python
   from fontTools.ttLib import TTFont
   f = TTFont(r'C:\Windows\Fonts\simsun.ttc', fontNumber=0)  # .ttc 必须指定 fontNumber
   cmap = f.getBestCmap()
   print(0x207B in cmap)  # ⁻
   ```
   常用字库覆盖结论（Windows）：
   - **宋体 SimSun / 黑体 / 雅黑 / 等线**：常缺 `⁻(U+207B)`、`₂(U+2082)`、`−(U+2212)`、`⑪⑫⑬(U+246A-C)`，却能显示 `± × · → ∥ ⊥ ─ ①~⑩`。
   - **Cambria Math（cambria.ttc）与 Segoe UI Symbol（seguisym.ttf）**：几乎全收录——首选修复字体。
   - Times New Roman 缺 `∥ ⊥ 圈数字`。别用它兜底。
4. 若 OMML 公式（`<m:oMath>`）或图片（`<w:drawing>`）显示正常，问题只在普通文本 run。

## 修复（最小侵入，零丢字）
核心：**只把缺失字符拆分出来成为独立 `<w:r>`，给该 run 的 `ascii/hAnsi` 指定 Cambria Math，eastAsia 保留原中文字体**，其余文本与其他属性（b/i/sz）原样不动。

- 用 lxml 处理（保留命名空间 `w=http://schemas.openxmlformats.org/wordprocessingml/2006/main`）。
- 处理对象：`document.xml` 中所有含目标字符的 `<w:r>`；若 run 内除 `rPr`/`w:t` 外还混有其它子元素（如 drawing、br），跳过该 run 避免破坏结构。
- 保留空格：`<w:t>` 有前后空格时必须设 `xml:space="preserve"`。
- 逐字符 split 文本，缺失字符前/后连续普通文本各成一段 run。

> 语义替代方案（可行但需确认体例）：上标 ⁻² 可改写为普通 "-2" + `vertAlign=superscript` 的 Word 原生上标；但因改变字符内容、拆 run 更多，首选仍是"保留字符 + Cambria Math 字体"。

## 校验（必须做）
1. **零丢字**：对比修复前后 document.xml 的 `w:t` 拼接文本——必须逐字符一致。
2. document.xml 可被 lxml 解析、root 为 `document`。
3. zip 完整性：`zipfile.ZipFile(path).testzip() is None`；`[Content_Types].xml` 位于压缩包首项；必需部件齐全（`[Content_Types].xml / _rels/.rels / word/document.xml / word/styles.xml`）。
4. 全部目标字符所在 run 的 ascii 字体 == Cambria Math，无遗漏。
5. 媒体完好：`word/media/*` 数量与修复前一致（公式图勿动）。

## 重新打包（保持 OOXML 约定）
用 zipfile 重建：`[Content_Types].xml` 必须排最前，其余保字母序，ZIP_DEFLATED。

## 已知环境坑（Windows）
- **PowerShell Add-Type 会被沙箱安全策略拦截**（"compiles and loads .NET code"）。字体字形检查一律用 python fontTools，别用 System.Drawing。
- **路径映射**：Git Bash 的 `/tmp`＝`C:/Users/<user>/AppData/Local/Temp`；但 Write 工具写 `/tmp/...` 会落到 `D:\tmp`（或其他盘根）。工具间传文件、跑脚本的临时目录要统一到一个明确的 Windows 路径，避免教条式引用 `/tmp`。
- 处理完记得删 Word 会话残留锁文件 `~$*.docx`，且修改前先备份原文件（推荐 copy 到 backup/ 目录待确认成功）。
- python 解释器用原生 Windows 路径（`C:\...`），不要用 Git Bash 的 `/c/...`。

## 交付清单
备份原 docx → 修复 → 校验（零丢字 + zip/open 合法）→ 回写正式目录 → 更新工作记忆/日志。
