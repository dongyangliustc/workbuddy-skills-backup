---
name: origin-opju-parity-build
description: 用 originpro/COM 从 Python 数据构建与 matplotlib 图完全一致的 Origin 工程图（OPJU），供用户在 Origin 内继续微调。覆盖：单层多 plot 精确样式化、非对称误差棒（手工线段法）、fill-to-next 填充带、双 log 轴、图例定位、PNG 自检闭环。触发词：OPJU、Origin 工程文件、把 Python/matplotlib 图搬进 Origin、Origin COM 自动化、originpro、误差棒图 Origin 版。
---

# Origin OPJU 等价构建（matplotlib → Origin 全自动）

把 Python 侧已定稿的图在 Origin 里 1:1 重建，用户拿到可编辑工程文件自行微调字体/结构。

## 七步流程（全部经 OriginPro 2023 教育版 + originpro 1.1.15 实测）

1. **Preflight**：杀掉已有 Origin64 进程（`Stop-Process -Name Origin64`）——每个 Python 进程 `OriginExt.Application()` 新开实例，多实例挤爆单席教育许可且静默禁用保存。全流程必须单进程完成。
2. **数据入表**：`op.new_book` + `wks.from_list`；book 短名不带空格（便于拼 `[Book]Sheet!(cols)` 范围）。
3. **绘图**：一律 `plotxy iy:=... plot:=... ogl:=[Graph]1!`——**不带 ogl 每次调用新建一层**（曾堆到 13 层导致纵轴重叠）。绘制顺序 = 叠放顺序：拟合线 → 填充带边线 → 误差棒线段 → 符号（最上层）。
4. **逐 plot 样式化**：用 `oh.layer_info(gl)` 解析每个 plot 的 `(book, col, sheet)` 后分派；颜色 `p.color=(r,g,b)`、符号 `p.symbol_kind/p.symbol_size`、线宽 `p.set_cmd("-w N")`。
5. **轴/图例**：`oh.set_log_axes` / `oh.set_axis_titles` / `oh.update_legend`。
6. **自检闭环**：`oh.export_preview` 导出 PNG → Read 工具看图 → 对照 matplotlib 原图逐项核对（轴、颜色、符号、误差棒方向幅度、带、图例）→ 不符则改脚本重跑（每次重跑前先杀 Origin）。
7. **交付**：COM 侧保存被禁（`op.save`/`doc -s` 静默返回 False）→ 用户在 Origin 窗口 **文件→另存为** 覆盖目标 .opju。

## 硬坑清单（违反任何一条都会静默出错）

- **`Plot.lname` 返回空串**：不能按长名匹配样式。plot 来源用 `gl.obj.GetStrProp("plotN.name")`，格式 `Book_列短名@Sheet序号`（`oh.parse_sname` 解析）。原生 COM 属性全在 `gl.obj`（PyOrigin GraphLayer）上，GLayer 包装类没有。
- **LabTalk 组合语句静默失败**：每条语句单独 `lt()` 并检查返回值。
- **`set %C` 落在当前激活图上**：逐 plot 定向必须 `p.set_cmd("-w ...")`（内部 `range rr=[层]!N; set rr ...`）。
- **误差棒是重灾区**（受控实验结论，Y=10/E1=2/E2=5 验证）：
  - plotxy 4 列 `iy=(X,Y,E1,E2)` 只画**对称 ±E1**，E2 被静默忽略 → 低 T 端 `TD−ErrUp<0` 被裁剪到轴底，视觉上"误差棒远远超出"；
  - 列 designation EE + `plotxy (?,2:4)` → 完全不画；
  - `set <plot> -om <range变量>` 有效，**`-op` 无效**（返回 True 但图不变）；
  - → **唯一可靠方案：手工线段**，用 `oh.err_segments()`（NaN 分隔折线：垂直段+上下端帽，log 轴端帽等比因子 1.07；NaN 经 `from_list` 正常断线）。相对误差 <0.5% 的序列直接跳过。
- **`set -w` 数值 ≈ pt×1000**：1600↔1.6pt、1100↔1.1pt、100↔0.1pt（发丝线）均已视觉验证。
- **填充带**：相邻两条边线（下边先、上边后），下边 plot 上 `p.set_fill_area(2, 9)`（9=fill to next）+ `p.transparency=80`；边线设 -w 100 发丝线让带读作纯填充。
- **图例**：`legendupdate mode:=lname` 按列长名生成；`legend.x/y` 用**轴值坐标**（log 轴直接给数值），锚点≈图例中心，条目多时先 `legend.fsize` 缩字号（14 条目 fsize=11）否则溢出页面。
- **expGraph** 只认 `type:/path:/filename:`，带 `igp:/tr1:` 会失败；导当前激活图前先 `win -a`。
- **轴标题上标转义**：`cm\+(-3)`（`\+()=上标`，注意 Python 字符串里写 `\\+`）。
- **同文件多处 Edit 不要并行发**（会静默丢失部分修改）；逐条串行。

## scripts/

- `origin_helpers.py` —— 通用函数库：`lt` / `parse_sname` / `layer_info` / `err_segments` / `write_err_sheet` / `set_log_axes` / `set_axis_titles` / `update_legend` / `export_preview`。
- `build_rea_opju.py` —— 参考实现（REA TD 误差棒图全流程，最终版已验证）。
- `test_err_semantics.py` —— 误差棒语义受控探针；换 Origin 版本后先跑一遍复核 plotxy/set 语义是否变化。

## 运行环境（本机）

venv `~/.workbuddy/binaries/python/envs/default` 的 python.exe 可能丢失，用 base 解释器 + PYTHONPATH 指向 venv site-packages：

```bash
PYTHONPATH="C:/Users/Administrator.DESKTOP-7RU274I/.workbuddy/binaries/python/envs/default/Lib/site-packages" \
"C:/Users/Administrator.DESKTOP-7RU274I/.workbuddy/binaries/python/versions/3.13.12/python.exe" build_rea_opju.py
```

依赖：originpro、OriginExt、numpy、scipy（PCHIP 带边插值时）。

## 验收清单

- [ ] `page.nlayers == 1`
- [ ] 每个 plot 颜色/符号/线宽与源图一致（layer_info 打印核对）
- [ ] 误差棒上下端与 ±误差情景值逐点吻合（高 T 端下长上短等特征）
- [ ] 填充带仅着色于两边线之间、无描边感
- [ ] 图例完整在页面内、条目名正确
- [ ] PNG 预览与 matplotlib 原图并排比对通过
- [ ] 提醒用户 文件→另存为 落盘 .opju
