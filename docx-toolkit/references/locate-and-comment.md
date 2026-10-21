# docx 按位置定位 + 批注锚定审计

## 适用场景

- 用户给的是**页码/行号**或**段落号**（如 `P14 L248-272`、`P35-37`），需要落到具体文字；
- 需要把 docx 里**所有批注**连同**锚定的原文片段**列出来（Word 的批注面板看不到锚点范围）；
- 用户说"我之前的修改忘开修订模式了"——意味着**正文已含未标记的改动**，不能靠 `w:ins/w:del` 还原历史。

## 第 0 步：确认"最新母本"

```bash
ls -la --time-style=full-iso *.docx && md5sum *.docx
```
同名/近名文件（`xxx.docx` vs `xxx-0914.docx`）**先比 md5**：若 `word/document.xml` 的 md5 相同，
两份内容等价（差异只在批注 XML 的 run 切分），不要纠结选哪份。

## 第 1 步：解包取批注 + 锚定原文（纯 Python，最可靠）

```bash
unzip -o -q 初稿.docx "word/comments.xml" "word/document.xml" -d _work/
```

用 `xml.etree` 遍历 `body.findall(W+'p')`，在**每段内**按出现顺序跟踪
`commentRangeStart` / `commentRangeEnd` / `commentReference`，即可得到
**每条批注锚定的那一小段原文**（这是判断"用户到底在说什么"的唯一硬证据）。

要点：
- `findall(W+'p')` 只取**顶层段落**；表格单元格里的段落会被漏掉或错位。
  `body.iter(W+'p')` 会把表格单元格也算进来，**两种口径的段号不同**——报段号时必须声明用哪种。
- 批注 id 不连续（如 0,1,3,9,10…）说明中间有批注被删过，属正常。
- **行内中文遗留批注**（如 `这里补充一个由于劈裂能导致的误差带`）不在 comments.xml 里，
  要对目标段做 `re.findall(r'[\u4e00-\u9fff][^\x00-\x7f]{0,40}', 段文本)` 才捞得出来。
  **用户口中的"批注"常常把这种行内提示也算进去**。

## 第 2 步（可选）：用 Word COM 核页码 / 行号

沙箱内 Word COM **可用**，但必须按下面的姿势（三个都是实测踩过的坑）：

1. **PowerShell 工具的 stdout 不回流** —— `Write-Output` 什么都看不到。
   一律 `[System.IO.File]::WriteAllText(out, $sb.ToString(), UTF8)` 落盘，再用 Read/Bash 回读。
2. **`powershell -File "含中文的路径"` 静默失败**（exit 1、连日志都不生成）。
   脚本与 docx 副本一律放**纯 ASCII 路径**（例：`D:\LDY\_wbprobe\`）。
   `Write` 工具**不能写盘符根目录**（`D:\x.ps1` 会 EPERM），所以建一个 ASCII 子目录。
3. **Bash 里调 `powershell.exe` 被安全策略拦截**，必须走 PowerShell 工具（内联命令即可）。

内联骨架（只读打开副本，绝不碰母本）：

```powershell
$w = New-Object -ComObject Word.Application; $w.Visible=$false; $w.DisplayAlerts=0
$d = $w.Documents.Open('D:\LDY\_wbprobe\probe.docx', $false, $true, $false)  # ReadOnly
# 段首折叠 range 才拿得到"段首"的页码
$cs = $d.Range($p.Range.Start, $p.Range.Start); $pg = $cs.Information(3); $ln = $cs.Information(10)
$d.Close($false); $w.Quit()
```

**行号语义（关键）**：`Information(10)` 返回**页内相对行号**（正文双倍行距约 19 行/页，
单倍行距的参考文献页约 44 行/页），**不能跨页累加**；把 `sectPr` 的
`<w:lnNumType .../>` 改成 `restart="continuous"` 也**不改变**该返回值。
`Information(3)` 对**非折叠** range 返回的是 range **结束处**所在页——段落跨页时页码会错位，
所以必须用折叠 range 取段首。

## 第 3 步：用户给的"P## L###-###"到底是什么

实测（本环境 DY 的约定）：
- `P` = **页码**，`L` = **连续行号**（不是段落号，也不是页内行号）；
- 用户文档若在 `sectPr` 里开着 `lnNumType`，Word 状态栏/打印边距就会显示连续行号，
  用 `总行数 / 总页数` 可反推每页行数并交叉验证区间归属。

**结论：别硬猜。** 反推 10 分钟不如直接回一句"请把这段的首尾原句发我"——
用户贴两句话即可 100% 定死区间（本项目就是这么解决的）。若确实无法询问，
则把候选区间连同各自的首句列出来让用户挑。

## 第 4 步：审阅产出建议格式

用户（科研作者）要的是**可决策**的东西，按此顺序给：

1. **批注逐条解读表**：原文批注 → 拆成硬约束 → 现状逐条核对（✅/⚠️/❌）；
2. **准确性反查**：批注里的量化断言是否在正文/表格里站得住（例："八个量级"只对部分物种成立）；
3. **逐句问题表**：编号 / 原文 / 问题 / 建议；
4. **可直接替换的改写稿**（分段给出，并标注每处改动对应哪条批注）；
5. **配套改动清单**（图、表注、SI、引用写法）；
6. **待决策项**（含"是否以修订模式落盘"）。

## 第 5 步：正文 ↔ SI 交叉引用覆盖核对（图表编号）

**为什么必做**：审阅/编辑段落时只盯被点名的位置，会漏掉**别的段落**里的编号缺陷；
而"SI 有图、正文无引用"和"编号被顶替"是编辑与审稿人必抓的硬伤。**逐条比对集合**，
不要只 grep 单点。

```python
import re
pat = re.compile(r"Figure\s+S(\d+)")          # 正文侧：引用
# SI 侧：caption 行锚定 ^Figure\s+S\d+ 才是"图存在"的证据，正文提及不算
```

判定矩阵（**两类缺陷都要报**）：

| 现象 | 判据 | 后果 |
|---|---|---|
| **孤儿 (orphan)** | SI 有 caption，正文引用集合中缺失 | 编辑必问；补引用 |
| **顶替 (substitution)** | 正文某处引用 Sn，但其内容与 SI `Sn` 的 caption **不符** | 更隐蔽：只数个数会漏，须**读 caption 语义**逐条对 |

> 实测例（本项目 2026-09-16）：SI 有 S1–S4 四张，正文只引 3 处 ⇒
> `S4`（REA 截面）**孤儿**；`S1`（Franck–Condon 包络）**从未被引用**，而正文把 FC 包络标成了
> `Figure S2`（SI 的 S2 实为 Dyson 轨道）⇒ **顶替**。若只 grep "Figure S4 存在吗"，
> 顶替永远查不出来。

顺带固定体例项（同一遍可完成）：`en dash` vs 连字符（`1–5` / `1-5`）、
复合定语连字符（`phase-space-theory`）、`斜杠后空格`（`A-/ B` → `A-/B`）。

## 写回文档时的纪律

- 未经确认**不落盘**；用户提到"忘开修订模式"时，落盘一律用 `w:ins`/`w:del`
  （`<w:ins w:id w:author w:date>` 包裹新 run，删除用 `<w:del><w:delText>`），
  并保留 `rPr`，否则会丢上下标与字体。
- 落盘前先 `cp` 一份带时间戳的备份到 `初稿存档/`。
