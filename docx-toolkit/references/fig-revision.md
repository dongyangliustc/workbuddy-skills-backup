# SKILL: 专利交底书附图重绘 + docx 图文同步修订

> 适用：交底书/专利文档中重绘示意图（系统框图、流程图）并同步修订 docx 图片与文字的场景。
> 首次验证：2026-09-07 交底书图1/图2 扭曲波层移出修订（产出 ..._20260907220512.docx）。

## 工作流（5 步）

### 1. 重绘前：找到旧图生成脚本或视觉基准
- 在项目内 grep 图名（如 `图1_系统框图`）找生成脚本；找不到则按旧 PNG 目测风格重建。
- **先备份旧 PNG** 到 `backup/`（带日期后缀），新图直接覆盖原文件名（docx 引用同名文件）。

### 2. matplotlib 重绘（模板：`outputs/patent_disclosure/_gen_fig1_fig2_v2.py`）
- 配色规范（与 9-1 版交底书附图一致）：层带 #EAF1F9/#B8CFE8、层标签 #2E75B6、模块框 #DCE9F7/#2E5F9E、箭头 #3A3A3A、可选元素 #FDF0DC/#D98E32。
- 元件：`FancyBboxPatch(boxstyle="round,pad=0,rounding_size=10")` + `FancyArrowPatch(arrowstyle="-|>", mutation_scale=17, lw=1.9)`；层带用 `plt.Rectangle` zorder=0。
- **禁用手估字宽**。中文字体实际宽度约为理论值的 1.3 倍（YaHei 13pt ≈ 46.4 绘图单位/字 @dpi=200；两行 11.5pt/ls1.25 文本 bbox 高 ≈111 单位）。
- **层间连接用正交折线（elbow：垂直-水平-垂直，转角 90°），禁用斜线**——斜线会穿过层标签/其他框且角度杂乱（DY 22:19 反馈教训）。层标签放**层带外左侧**（带外区域划给标签，带内全留给箭头通道），从布局上根除重叠。elbow 实现：前 n-2 段 `ax.plot`，末段 FancyArrowPatch。
- **elbow 末段（最后转角→箭头尖端）长度 ≥60 绘图单位**（mutation_scale=17 的箭头头约占 20 单位）——末段过短时转角贴住箭头，视觉拥挤（DY 22:31 反馈教训）。做法：层内水平走廊取"框顶上方 65、带顶下方 ~25"。
- **editor_sdk 会话中断后 pool 清空、未保存编辑全部丢失**——每完成一组修改立即 `save_file` 落盘；若 pool 已空，从原稿重放全部编辑（图片替换+文字替换均可脚本化重放）。

### 3. 程序化校验（模板：`_check_fig_text_overflow.py`）
- ① 文本 `get_window_extent(renderer)` → `transData.inverted()` 反变换 → 与框 bbox 比 margin（阈值 ≥5 单位）。
- ② **线段-矩形相交测试**（采样法）：每条箭头折线段 vs 全部层标签 bbox + 全部"非端点"框 bbox，问题数必须为 0。先跑校验（`ALL OK`）再目检 PNG，避免多轮目检循环。

### 4. docx 编辑（editor_sdk，强制 schema 先查）
```bash
cd <skill-dir>/../..../skill-tencent-local-office-edit/0.0.1
python3 edsdk.py call open_file file_path=<docx>          # 无头打开
python3 edsdk.py call get_pool_status                     # 取真实 file_id
python3 edsdk.py call doc_get_images file_id=...          # 拿 index + image_url
python3 edsdk.py call doc_replace_image --json '{"file_id":...,"idx":N,"old_image_url":"http://127.0.0.1:39099/...","new_image_path":"C:/abs/新图.png"}'
python3 edsdk.py call doc_find file_id=... text=<锚点片段>  # 确认 docx 实际文字（勿信转换稿）
python3 edsdk.py call doc_find_and_replace --json '{"file_id":...,"old_text":"<完整旧句>","new_text":"<新句>"}'
python3 edsdk.py call save_file file_id=... file_path=<新路径>  # 另存时间戳新稿，保留原稿
```

### 5. zip 层核验（doc_to_image 常因非 ASCII 路径/外部 API 失败，勿依赖）
```python
import zipfile, hashlib, re
z = zipfile.ZipFile(new_docx)
media = {n: hashlib.md5(z.read(n)).hexdigest()[:12] for n in z.namelist() if n.startswith('word/media/')}
# ① 新图 MD5 与磁盘 PNG 一致；② m:oMath 计数与原稿一致（公式未损）；③ 文字断言列表 10+ 项；④ 原稿 media/文字未变
```

## 环境坑（本项目 Windows 沙箱）
- **patent venv**（`~/.workbuddy/binaries/python/envs/patent`）python.exe 会被沙箱掏空；`cp` base python.exe 回去会因 pyvenv 解析错乱失败。**可靠做法**：`PYTHONPATH="<envs/patent>/Lib/site-packages" <versions/3.13.12>/python.exe script.py`。
- editor_sdk `save_file` 的 file_path 用反斜杠转义在 --json 里易错；输出路径可能出现 `//` 混合分隔符，属正常。
- 交底书目录存在**并行修订线**产物（时间戳文件名），动手前 `ls -la` 确认最新版本与线归属，修订后在工作日志记录线号，避免版本混淆。
