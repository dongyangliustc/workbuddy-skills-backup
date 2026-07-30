---
name: workbuddy-skin
description: WorkBuddy 桌面端换肤管理。当用户说"应用皮肤""换肤""切换主题""暂停皮肤""查看皮肤状态""对话框背景色"或提及 WorkBuddy 界面主题/壁纸时触发。通过 CDP 注入 CSS+菜单，支持自动重启 WorkBuddy 并重新注入皮肤。包含对话框半透明背景功能。
triggers:
  - 应用皮肤
  - 换肤
  - 切换主题
  - 皮肤
  - skin
  - 暂停皮肤
  - 皮肤状态
  - 应用昔涟
  - workbuddy skin
---

# WorkBuddy Skin Manager Skill

## 概述

管理 WorkBuddy 桌面端皮肤。通过 Chrome DevTools Protocol (CDP) 向 WorkBuddy renderer 注入 CSS + 🎨 主题菜单。
皮肤不修改 app.asar，重启后需重新注入。

## 关键路径

| 资产 | 路径 |
|------|------|
| Skin Studio 安装目录 | `C:\Users\Administrator.DESKTOP-7RU274I\.workbuddy\workbuddy-skin-studio` |
| 统一管理脚本 | `<Studio>\skin-manager.mjs` |
| CLI 入口 | `<Studio>\src\cli.mjs` |
| 用户主题目录 | `%LOCALAPPDATA%\WorkBuddySkinStudio\themes\` |
| 日志 | `<Studio>\skin-manager.log` |
| Node.js | `C:\Users\Administrator.DESKTOP-7RU274I\.workbuddy\binaries\node\versions\22.22.2\node.exe` |
| WorkBuddy.exe | `C:\Users\Administrator.DESKTOP-7RU274I\AppData\Local\Programs\workbuddy\WorkBuddy.exe` |

## 可用主题

当前用户主题（`%LOCALAPPDATA%\WorkBuddySkinStudio\themes\`）：

| ID | 名称 | 说明 |
|----|------|------|
| `xilian-dark-old` | 昔涟-深色-old | 自定义壁纸，底边色 #352d56，contain + center top |

内置主题已移至 `~\.workbuddy\themes-backup\` 备份。

## 皮肤包含的视觉效果

1. **背景图**：`#root` 层使用 `background-size: contain` 完整显示，`background-position: center top`，留白填充底边色
2. **侧边栏/面板磨砂玻璃**：`backdrop-filter: blur()` + 半透明 surface 色
3. **对话框半透明背景**：`[class*="_cbChat_"]` 使用 72% 不透明度 surface 色 + 16px 模糊，输入框 85% 不透明度，覆盖在皮肤背景图上
4. **全局 CSS 变量覆盖**：60+ 个 `--cb-*` 变量

## 操作指南

### 1. 应用皮肤（apply）

用户说"应用皮肤"或"应用昔涟"时执行：

```bash
# 通过 Bash 工具运行（不能通过 PowerShell 工具，会因进程树依赖被杀）
"C:\Users\Administrator.DESKTOP-7RU274I\.workbuddy\binaries\node\versions\22.22.2\node.exe" "C:\Users\Administrator.DESKTOP-7RU274I\.workbuddy\workbuddy-skin-studio\skin-manager.mjs" apply --theme xilian-dark-old --port 9223
```

**脚本逻辑**：
- 若 CDP 端口 9223 已就绪 → 直接 apply（无需重启 WorkBuddy）
- 若 CDP 端口未就绪 → 通过 WMI 启动独立进程执行 kill→重启→apply 全流程
  - WMI 启动的进程脱离 WorkBuddy 进程树，taskkill 不会误杀

**注意事项**：
- 必须用 **Bash 工具** 执行，不要用 PowerShell 工具（WorkBuddy 是 PowerShell 的父进程，taskkill 会连带杀死 shell）
- 若脚本返回 "WMI relaunch dispatched"，说明已派发后台进程，需等待 5-10s 后检查日志
- 查看日志确认结果：`cat skin-manager.log`（最后几行）

### 2. 指定不同主题

```bash
node skin-manager.mjs apply --theme <theme-id> --port 9223
```

用 `list` 命令查看所有可用主题 ID：
```bash
node skin-manager.mjs list
```

### 3. 暂停皮肤（恢复原生界面）

```bash
node skin-manager.mjs pause --port 9223
```

清除 CSS 注入和背景图，保留 🎨 菜单（可随时重新选择主题）。

### 4. 查看状态

```bash
node skin-manager.mjs status --port 9223
```

检查 CDP 端口是否就绪、当前皮肤注入状态。

### 5. 列出主题

```bash
node skin-manager.mjs list
```

## 沙箱限制与规避

1. **禁止用 PowerShell 工具执行**：`taskkill /IM WorkBuddy.exe` 会杀死 WorkBuddy 的所有子进程，包括 Bash/PowerShell tool 的 shell。必须用 Bash 工具。

2. **WMI 进程脱离**：当 CDP 未就绪需要重启 WorkBuddy 时，`skin-manager.mjs` 通过 `wmic.exe process call create` 启动自身的独立副本，完全脱离 WorkBuddy 进程树。

3. **CDP 端口**：固定使用 9223。WorkBuddy 必须以 `--remote-debugging-port=9223` 参数启动才能接受注入。正常启动（无此参数）的 WorkBuddy 无法注入皮肤。

4. **手动重启后**：用户手动关闭再打开 WorkBuddy 时，不会带 CDP 参数，因此皮肤和🎨菜单都会丢失。需要重新运行 `skin-manager.mjs apply`。

## 故障排查

| 症状 | 原因 | 解决 |
|------|------|------|
| "CDP not up after 50s" | WorkBuddy 启动失败或端口被占 | 检查 9223 端口占用：`netstat -ano | findstr 9223` |
| "找不到主题" | theme.json 丢失或 ID 拼写错误 | 检查 `%LOCALAPPDATA%\WorkBuddySkinStudio\themes\` 目录 |
| WMI relaunch 失败 | wmic.exe 路径或权限问题 | 尝试手动运行：先 taskkill，再 start WorkBuddy.exe --remote-debugging-port=9223，再 node skin-manager.mjs apply |
| 重启后皮肤丢失 | WorkBuddy 未带 CDP 参数启动 | 正常行为，重新运行 apply 即可 |
| 🎨 菜单不显示 | 注入未成功或被页面刷新覆盖 | 重新运行 apply |

## 一键启动器（备选方案）

`Launch-WorkBuddy.bat` 可双击运行，功能与 `skin-manager.mjs apply` 类似但更简单（无 WMI 脱离逻辑）：

```
<Studio>\Launch-WorkBuddy.bat
```

适合在 WorkBuddy 完全关闭后手动双击使用。如果在 WorkBuddy 内部通过工具调用，优先用 `skin-manager.mjs`。
