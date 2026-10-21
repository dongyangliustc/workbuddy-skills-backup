# Q-Chem FlexNet 许可证：架构、排障与启动手册

> 来源：官方 https://www.q-chem.com/install/flexnet/ + 2026-10-14 HPCCube wuzh02
> 真机实测（`/work/share/hjbustc/software/qchem61`）。本文是**可直接照做**的操作手册。

## 1. 架构：两套文件，各司其职

Q-Chem 的 FlexNet 部署分成**服务器端**与**客户端指针**两部分，混用是排障最常见的第一道坎。

| 角色 | 文件 | 位置（HPCCube 实例） | 内容 |
|---|---|---|---|
| **服务器端许可证** | `qchem_<订单号>.lic` | `flexnet/etc/qchem_8791.lic` | `SERVER` + `VENDOR` + `INCREMENT`（含签名与用户授权策略） |
| **客户端指针** | `qchem.lic` | `qcaux/license/qchem.lic` | 仅两/三行：`SERVER <host> <hostid>` + `USE_SERVER`（+ `VENDOR`） |

- `USE_SERVER` 的作用：客户端**不本地解析功能码**，而是去问 lmgrd「vendor daemon 在哪个端口」，
  再向 vendor daemon 申请授权。因此**客户端文件里没有密钥**，改密钥只需动服务器端文件。
- lmgrd 监听 **27000**（`SERVER` 行未写端口时的默认值）；vendor daemon 端口由 lmgrd 动态分配并告知客户端。
  因此**防火墙只需放行 lmgrd 端口**，vendor 端口随握手下发。
- `SERVER <host> <hostid>` 的 hostid 必须**匹配运行 lmgrd 的那台机器**（HPCCube：`login02` = `license02`，
  hostid `e8611f37205a`）。跨机器启动必然 `Invalid hostid`。

## 2. 启动手册（照做即可）

```bash
FN=/work/share/hjbustc/software/qchem61/flexnet      # FlexNet 安装根
LIC=$FN/etc/qchem_8791.lic                            # 服务器端许可证
LOG=$FN/qchemlm.log                                   # 调试日志（可与 lmgrd 共用）

# ① 在许可证服务器主机上（HPCCube 为 login02 / license02）
# ② lmgrd 会在其工作目录创建 .logger 套接字 —— 若安装目录不可写，
#    务必把 cwd 换到可写目录（如 $HOME/.qchemlic）
# ③ 把 vendor daemon 目录加入 PATH，确保 lmgrd 能拉起 qchemlm
export PATH=$FN/bin:$PATH
cd $HOME/.qchemlic
setsid nohup $FN/bin/lmgrd -c $LIC -l +$LOG </dev/null >lmgrd.out 2>&1 &

# ④ 验证（必须看到 license server UP + vendor daemon UP + 功能码计数）
$FN/bin/lmutil lmstat -c $LIC -a
$FN/bin/lmutil lmstat -c $LIC -f QCHEM      # 只看 QCHEM 用量
```

**期望输出**：

```
License server status: 27000@login02
   login02: license server UP (MASTER) v11.19.1
Vendor daemon status (on login02):
   qchemlm: UP v11.19.1
Users of QCHEM:  (Total of 1000 licenses issued;  Total of 0 licenses in use)
```

`setsid` 不可省：lmgrd 会成为会话首进程，脱离 SSH 通道后不被 SIGHUP 杀掉。
`-l +<log>` 的 `+` 表示**追加**，保留历史便于排障。

更新许可证或选项文件后无需重启，`lmutil lmreread -c $LIC` 即可重读。

## 3. 失败模式对照表（每条都有真机实证）

| 现象 | 错误码 / 文本 | 根因 | 处置 |
|---|---|---|---|
| 作业秒退，输出仅几百字节 | FlexNet `-97,121` / `The desired vendor daemon is down` | **vendor daemon 没在跑**（lmgrd 活着，qchemlm 死了） | 按 §2 启动；先用 `ps` 确认 qchemlm 是否存在 |
| lmgrd 起不来，无任何日志落盘 | `Invalid License File`（退出码 11） | **许可证文件是 CRLF 行尾** —— Linux 下 `\r` 被并入 token，hostid 变成 `e8611f37205a\r` | `tr -d '\r'` 生成 LF 副本再启动；**根治**是让许可证出处改为 Unix 行尾 |
| 客户端报用户未授权 | `-39,147` / `User/host not on INCLUDE list` | 许可是 **USER_BASED（SRG）**，而 vendor daemon options 文件未包含该用户 | 在 `qchemlm.opt` 里把用户加入 `GROUP`，并确认 `INCLUDE QCHEM GROUP <组>`（**不要**写 `QCHEM:VERSION=x`） |
| 连不上服务器 | `-15` / `Cannot connect to license server` | lmgrd 未运行或端口被防火墙拦截 | 检查 lmgrd 进程与 27000 端口连通性 |
| 授权与 hostid 不符 | `Invalid hostid` | 许可证在非绑定主机上启动 | 必须运行在 `SERVER` 行指定的主机上 |

### 3.1 本案根因链（2026-10-14，HPCCube 实录）

1. 作业 `FAILED(119:0)`，`.out` 仅 597 字节（欢迎信息后即退出），报 `-97,121`。
2. `login02` 上**只有他厂商**的 lmgrd（`cstd`，属另一账号）占用 `27001`；**Q-Chem 的 qchemlm 缺失**。
3. `qchemlm.log` 尾部是**退出报告块**，最后时间戳 `Sat Oct 03 2026 04:07:47` —— 即停摆于 10-03。
4. 尝试手动启动，lmgrd 报 **`Invalid License File`**。逐字节检查发现许可证为 **CRLF**（8 个 CR）。
5. 用 `tr -d '\r'` 生成 LF 副本 → lmgrd 正常起来 → `qchemlm: UP`，功能码 1000 个可用。
6. Q-Chem 冒烟算例（H₂O / HF / STO-3G）复跑：`Total energy = -74.96259818 hartree`，正常收尾。

### 3.2 权限与持久性（重要）

- HPCCube 上 `flexnet/` 目录**对组用户不可写**（仅 `qchemlm.log` 组可写），
  因此**无法就地修复** CRLF 问题，只能用私有目录放去 CR 副本并以该副本启动。
- 以普通用户启动的守护进程**随节点重启/清理而消失**，且许可证文件副本会与出处脱钩。
- **判断许可证是否可用的最快方法**（不看远端配置）：
  跑一个极小算例，看 `.out` 是否只含几百字节且带 `FlexNet Licensing error`。

## 3.3 持久化部署：让重启后自动拉起（管理员）

**问题**：手工 `setsid nohup lmgrd` 起的进程，节点重启即消失，且没有任何人守护 ——
守护进程死掉时没有告警，只会在跑作业时才暴露。

**正解**：注册为 systemd 系统服务（`Type=forking` + `Restart=always`）。
可直接使用随附资产：

| 文件 | 用途 |
|---|---|
| `references/qchem-flexnet-service/install-qchemlm-service.sh` | 一键安装脚本（root，含 LF 修正、端口钉定、启用、自检） |
| `references/qchem-flexnet-service/qchemlm.service` | systemd 单元（供审查/手工安装） |
| `references/qchem-flexnet-service/lic_service.sh` | 无 root 的降级方案，装在 `~/.qchemlic/lic_service.sh` |

在**许可证主机 login02** 上以 root 执行：

```bash
sudo bash install-qchemlm-service.sh          # 安装 + 启用 + 自检
sudo bash install-qchemlm-service.sh uninstall # 卸载
```

安装脚本做的事（每步都有校验，失败即回退不改原文件）：

1. 备份 `qchem_8791.lic`（带时间戳）；
2. **就地转 LF**（CRLF→LF）并**显式钉住端口 27000**；
3. 建 `flexnet/logs/`（属主 hjbustc）；
4. 写 `/etc/systemd/system/qchemlm.service`；
5. 清掉手工实例后 `systemctl enable --now`；
6. 自检 `lmstat` 并打印日常运维命令。

⚠️ **安装前必须先停掉手工起的实例**（否则新 `qchemlm` 会因"同厂商重复注册"
`EXITING DUE TO SIGNAL 41` 并使 lmgrd 一并退出 —— 实测踩过）。
脚本已自动处理，但**清理必须精确**：login02 是**共享许可节点**，
另一租户的 `lmgrd`（cmdline 为 `./license.dat`）与厂商守护进程 `cstd` **必须保留**。
故判据只取两条：

| 判据 | 说明 |
|---|---|
| 可执行文件名 == `qchemlm` | 我们的厂商守护进程（另一租户是 `cstd`，不会误伤） |
| 可执行文件名 == `lmgrd` **且** cmdline 提及 `qchem_8791.lic` 或 `qchem61/flexnet` | 我们的服务器管理器（含手工起的私有副本实例） |

**只看可执行名、不看整行文本** —— 否则会把仅*提及* `qchemlm` 字样的无关进程
（`tail .../qchemlm.log`、`grep qchemlm`、脚本自身）一并杀掉。
脚本在清理后会**交叉校验另一租户的 lmgrd 仍在存活**（计数应为 ≥1）作为安全网。

**单元文件的关键设计（每条都有实测依据，勿随意改）**：

| 配置 | 依据 |
|---|---|
| `Type=forking` | lmgrd **会自行 daemonize**（实测：前台调用 0 秒返回 0，留下 PPID=1 的后台进程）。用 `simple` 会被 systemd 判为已退出 |
| `User=hjbustc` | `flexnet/` 属主为 hjbustc 且**组无写权限**；以属主运行才能就地写 `.logger` 与日志 |
| `WorkingDirectory=<FN>` | lmgrd 在 cwd 创建 `.logger` 套接字；目录不可写会**静默失败** |
| `Environment=PATH=<FN>/bin:...` | 许可证 VENDOR 行未写守护进程绝对路径，lmgrd 靠 PATH 找 `qchemlm` |
| `Restart=always` | 这是"进程死后无人管"的根治手段 |
| `SERVER ... 27000` 显式端口 | 见 §3.4：不写端口时 lmgrd 会静默自增 |

**验证安装成功**：

```bash
systemctl is-active qchemlm          # active
lmutil lmstat -c <LIC> -a | grep qchemlm   # qchemlm: UP
systemctl restart qchemlm && sleep 5 && lmutil lmstat -c <LIC> -f QCHEM
```

**推荐附加项（可选）**：

- 把 `VENDOR` 行补上守护进程绝对路径，彻底摆脱 PATH 依赖
  （官方 `README.FlexNet` 的写法）：
  `VENDOR qchemlm <FN>/bin/qchemlm OPTIONS=<FN>/etc/qchemlm.opt`
- 加一个 systemd timer 每 10 分钟执行 `lmutil lmstat -f QCHEM`，
  覆盖"进程活着但未正常服务"的场景（`Restart=always` 只覆盖进程死亡）。
- 若希望端口非默认，必须**同时**改服务端 `SERVER` 行与客户端
  `qcaux/license/qchem.lic` 的 `SERVER` 行，并确认计算节点到该端口可达。

### 3.4 端口必须显式钉定（2026-10-14 实测）

`SERVER` 行**不写端口**时，客户端按默认 **27000** 连接；而 lmgrd 在 27000 被占
（例如同主机其它租户的 lmgrd、或刚被 kill 的旧实例仍占 socket）时会
**静默自增到 27001/27002** 并照常启动，日志里只有一行
`Listening port: 27002` —— 结果：**服务"看着在跑"（进程在、lmstat 在部分场景也能连），
但所有客户端连不上**。本案就踩到过这个状态。

因此：**许可证 `SERVER` 行显式写 `27000`**。这样端口冲突会变成
**显式启动失败**（噪音优于静默错配）。

### 3.5 同一主机不得有两个同厂商 vendor daemon

残留旧实例（哪怕端口不同）会让新起的 `qchemlm` 立刻
`EXITING DUE TO SIGNAL 41 / Exit reason 9`，随后 lmgrd 也退出。
**判据与处置**：先 `pkill -u <user> -x qchemlm; pkill -u <user> -x lmgrd`，
确认清零后再单实例启动；且**停止与启动之间存在注册释放竞态**，
单次启动可能失败 → 随附的 `lic_service.sh` 内置 3 次重试。

### 3.6 非交互 SSH shell 的 PATH 陷阱

`ssh host 'cmd'` 与脚本内 shell 的 PATH **不完整**（实测缺 `ss`），
且把带引号的命令塞进 shell 变量再 `eval` 会被反斜杠转义搞坏。
**可靠写法**：脚本开头显式 `export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`；
工具用绝对路径；`export PATH=<dir>:$PATH` **务必带 `:$PATH`**（漏写会覆盖整个 PATH，
后续 `date`/`ps`/`grep` 全部 command not found，症状极具误导性）。

## 3.7 受控实验：许可中断时"在跑的作业"会怎样？（2026-10-14 实测）

用单个长算例（H₂O CCSD(T)/aug-cc-pVQZ，串行，总 311 s）做受控中断：
作业进入 CCSD 关联计算后杀掉 lmgrd+qchemlm，**中断 45 秒**后重启。

| 时刻 | 观测 |
|---|---|
| 中断前 | 输出 9,525 B（SCF 迭代中） |
| 杀后 +9 s | +205 B（仍在推进） |
| +18 s | +1,335 B |
| +27 s | +7,345 B（进入 CCSD 迭代） |
| 中断期结束 | 18,410 B（累计 +8,885 B），状态始终 RUNNING |
| 恢复许可后 | 跑完 → `COMPLETED 0:0`，Wall 311 s |
| 结果 | `CCSD(T) total energy = -76.36355029`，正常收尾，**无任何许可错误** |

**结论**：FlexNet 在作业启动时一次性签出授权，运行期间不再依赖守护进程。

- ✅ **已启动的作业不会因许可中断而夭折** —— 长作业是安全的。
- ❌ **中断期间新提交的作业会即刻失败**（`-97,121` / `-15`），排队中的作业
  若在启动时撞上停机窗口同样失败。

**对"要不要做持久化"的影响**：代价模型从"在跑作业全军覆没"降级为
"停机窗口内提交失败 + 需人工拉起"，紧迫性下降；但人工恢复仍有遗漏风险，
且批量提交/长排队场景会撞窗口，故仍建议部署 systemd（§3.3）。

⚠️ 实验须在 **QCHEM 用量为 0** 时进行（会中断全组授权），
且实验本身踩过三个坑，复现时注意：重复 `$molecule/$rem` 块不被该版本支持
（被当作 1 个作业并段错误）、快算例批处理时长不可控、被 kill 的作业会留下
损坏的 `qarchive.h5` 导致下一轮启动失败。

## 4. USER_BASED（SRG）许可的额外要求

本案许可证带 `USER_BASED` 与 `NOTICE="SRG <PI 名>, <单位>"`，属**按用户计数**的站点许可：

- vendor daemon **必须**通过 options 文件下发授权名单，否则合法用户也会被拒（`-39,147`）。
- 实测可用的 options 文件格式（`flexnet/etc/qchemlm.opt`）：

```
GROUP qchemusers login02 hjbustc feng2011 luoguangda mengqiwu smartmonkey dongyang
INCLUDE QCHEM GROUP qchemusers
```

- `SERVER` 行的 hostid 与 `lmutil lmhostid` 输出中**任一** ID 匹配即可
  （该机返回 10 个候选 ID，许可证取第一个）。
- **新增组内用户需同步加入 `qchemlm.opt`**，改后 `lmutil lmreread` 生效，无需停服。
