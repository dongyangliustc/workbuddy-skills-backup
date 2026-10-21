---
name: remote-sync
version: 1.3.0
description: "本地与 Linux 服务器之间的数据传输与远端计算执行桥接。当需要把 .gjf/.in 输入文件上传到远程工作站、HPC 集群或商业云超算执行 Gaussian/Q-Chem、等待计算完成、并将结果回传到本地时使用。补齐 gaussian-agent/run-gauss 生态缺失的传输层：上传、远端执行（direct 或 slurm）、状态轮询、结果回传。内置合规 harness 硬阻断越权提交（受管超算/商业云强制 slurm、命令白名单、配额闸门），并支持地址族强制（IPv4）、Q-Chem 求解器通道、平台单核内存上限预检。适用于需要远程计算、集群调度、结果取回的场景。"
agent_created: true
source: |
  作者：DY 课题组本地开发
  迭代：2026-08-01 设计 → 审查修复 → 真机链路验证（ldy@__SERVER_IP__）
        2026-09-11 引入合规 harness（gtransfer_lib/guard.py）硬阻断越权提交
        2026-10-14 接入 HPCCube 商业云 + Q-Chem 通道：地址族强制、商业云策略档、
                   profile 级 Slurm 默认与配额覆盖、单核内存上限预检；
                   修复 sacct 主作业行误判（失败被报 SUCCESS）；许可证 runbook 入库
license: MIT
requirements: |
  - Python 3.10+，pip install paramiko（仅真实连接需要；mock 测试零依赖）
  - 认证：SSH 密钥（key_file）或口令（password=${GTRANSFER_PASSWORD}，经环境变量注入）
  - 远端已安装 Gaussian（g16/g09）或 Q-Chem；slurm 模式需集群有 sbatch/squeue/sacct
  - Q-Chem 需 FlexNet 许可服务可用，见 references/qchem-flexnet-license.md
---

# remote-sync — 本地↔服务器传输与远端 Gaussian 执行

将本地组装好的 .gjf 上传到服务器执行，并把计算结果回传本地。
设计文档见 `docs/plans/2026-08-01-remote-sync-design.md`。

## 触发条件

在以下场景使用本 skill：

- 需要把本地 .gjf 上传到远程工作站或 HPC 集群执行 Gaussian
- 计算完成后需要把 .log / .chk 结果取回本地分析
- 需要远端状态查询（作业是否完成/失败）
- gaussian-agent 在 remote 模式下执行（作为 run-gauss 的传输桥接）

## 快速开始

### 1. 配置服务器（一次性）

```bash
# 复制模板到用户配置
cp ~/.workbuddy/skills/remote-sync/references/transfer.conf.example ~/.workbuddy/transfer.conf
# 编辑 host / user / remote_work_root

# 认证二选一：
# ① 密钥认证（推荐）：
export RSYNC_SSH_KEY=/path/to/id_rsa
# ② 口令认证（信任内网）：
export GTRANSFER_PASSWORD=your_password
```

### 2. 测试连接

```bash
python ~/.workbuddy/skills/remote-sync/scripts/gtransfer.py ping --config ~/.workbuddy/transfer.conf
```

### 3. 提交计算（上传 → 执行 → 回传 全流程）

```bash
python ~/.workbuddy/skills/remote-sync/scripts/gtransfer.py submit \
  --config ~/.workbuddy/transfer.conf \
  --local /path/to/water.gjf \
  --remote ~/gtransfer/water-opt \
  --download-glob "*.log *.chk" \
  --out-dir ./results
```

输出 JSON：`{ok, remote_work, state{status}, files{uploaded, downloaded}}`。
退出码：0=成功，1=作业失败（日志仍回传），2=参数/配置错误，3=传输错误，
4=**合规策略拦截**（越权/违规提交，详见 `policy` 字段与 `remedy`）。

## 合规 harness（硬阻断，2026-09-11 引入）

本 skill 内置 `gtransfer_lib/guard.py` 策略引擎，把**中国科大超算中心等受管
集群的作业提交规定**固化为可执行规则。**任何 I/O 之前先过策略**，违规即抛
`PolicyViolation` 并返回退出码 4 ——不是提示，是硬阻断，无参数可绕过。

### 为什么需要

中心用户手册明确"登录节点禁止不经作业调度系统直接运行作业"，且 FAQ 对磁盘、
节点、队列、运行中/排队中作业数均设限。此前 `--command` 允许任意 shell 命令、
`direct` 模式会在登录节点跑计算、且无配额感知——三处均可越权。harness 消除之。

### 服务器分类

| 类别 | 判定 | 策略档 |
|------|------|--------|
| `managed_cluster` | host 命中已知超算指纹（瀚海22：`__SERVER_IP__` / `.113`），或配置 `server_class = managed_cluster` | 强制 slurm、禁用 direct、命令过白名单+黑名单、配额闸门 |
| `commercial_cluster` | host 命中商业云指纹（`wuzh02.hpccube.com`），或配置 `server_class = commercial_cluster` | 与受管集群**强度相同**；权威依据文案改为「商业算力平台服务规范」 |
| `workstation` | 其余 host，或配置 `server_class = workstation` | 允许 direct、仅危险命令黑名单 |

配置项 `server_class` 可显式覆盖（留空 = 按 host 自动判定）。
CLI `--server-class` 可临时覆盖——**这是逃生舱口，仅用于自建镜像等确需场景**。

> **为什么区分受管集群与商业云（2026-10-14 引入）**：两者约束强度一致（都要经调度
> 系统提交、都不许在登录节点跑计算），但**权威出处不同**。此前策略文案硬编码
> 「中国科大超算中心用户手册」，直接用于商业云会把依据写错、审计链失真。
> 现由 `_POLICY_SOURCES` 按类别给出各自的出处与兜底条款，规则本体七类共用。
> 策略出处可经 `gtransfer check` 的 `policy_source` 字段查看。

### 七类规则

| 规则 | 内容 | 规则 ID 前缀 |
|------|------|--------------|
| 执行模式 | 受管集群禁止 direct（登录节点跑计算） | `POLICY-DIRECT-FORBIDDEN` |
| 远端路径 | 必须落在 `remote_work_root` 内（防穿越/同前缀绕过） | `POLICY-PATH-ESCAPE` |
| 命令内容 | 黑名单：`rm -rf`、`sudo`、登录节点跑 g16/orca/vasp 等、嵌套 `sbatch`、`crontab`/`systemctl`、`curl`/`wget` 外联、`chmod 777`、读 `/etc/shadow`；白名单：起首程序须为 python/mpirun/srun/bash/module/formchk/**Multiwfn**/**orca_2mkl** 等 | `POLICY-DESTRUCTIVE-*`、`POLICY-SUDO`、`POLICY-LOGIN-NODE-COMPUTE`、`POLICY-COMMAND-NOT-ALLOWLISTED` 等 |
| 作业资源 | 单作业 ≤64 核、墙钟 ≤72h | `POLICY-RESOURCE-EXCEEDED`、`POLICY-WALLTIME-EXCEEDED` |
| 并发闸门 | 运行中 <8、排队中 <16（提交前实测） | `POLICY-CONCURRENCY-LIMIT`、`POLICY-QUEUE-LIMIT` |
| 磁盘余量 | 提交前远端剩余 ≥5GB | `POLICY-DISK-LOW` |
| 回传/上传 | 回传 glob 不得含越界路径；上传内容不得含 `.git`/`__pycache__`/`.venv` 等非科研文件（告警） | `POLICY-GLOB-ESCAPE` |

阈值定义于 `QuotaPolicy`（保守默认，低于中心上限以留余量）。

### 三层执行

1. **CLI `cmd_submit`**：`PolicyGuard.preflight()` 在任何 I/O 之前完成全量校验。
2. **执行器**：`DirectExecutor`/`SlurmExecutor.submit()` 各自 `_enforce_policy()`
   ——即便绕过 CLI 直接调库，也拦得住。
3. **提交前实测**：连接后跑 `squeue`/`df` 只读探测，过并发与磁盘闸门。

### 用法

```bash
# 查看当前生效的类别、策略与配额闸门（+ --probe 连远端实测实时配额）
python ~/.workbuddy/skills/remote-sync/scripts/gtransfer.py check \
  --config ~/.workbuddy/transfer.conf --server hanhai22 --probe
```

`check` 输出含 `config_violations`——若配置本身违规（如 `execution_mode = direct`
但目标是超算），会直接列出并返回退出码 4。**这是最容易踩的坑**：
`check` 应作为超算配置的常规体检。

违规响应示例（退出码 4）：

```json
{
  "ok": false, "policy": true,
  "rule_id": "POLICY-DIRECT-FORBIDDEN",
  "detail": "USTC 瀚海22 为受管超算集群，禁止 direct 模式",
  "authority": "中心用户手册：登录节点『禁止在此节点上不通过作业调度系统直接运行作业』",
  "remedy": "改用 --mode slurm，由 sbatch 提交至计算节点"
}
```

### 不可自动判定项（强制提醒）

数据性质（严禁国家秘密/工作秘密/商业秘密/未公开敏感科研数据）与数据保全
（中心不提供备份/恢复，须及时回传清理）无法自动判定，故在每次 preflight 与
`check` 输出中作为 `mandatory_notices` / `warnings` 强制呈现，判断责任在用户。

## 命令行契约

```
gtransfer ping   --config <path> [--server <name>] [--dry-run]
gtransfer check  --config <path> [--server <name>] [--probe] [--server-class ...]
gtransfer submit --config <path> --local <path|dir> --remote <path>
                 [--mode direct|slurm] [--solver gaussian|qchem]
                 [--download-glob "*.log *.chk"]
                 [--gaussian-exec g16] [--qchem-exec qchem]
                 [--out-dir <dir>] [--env-setup "..."]
                 [--command "python main.py"]
                 [--partition wzacnormal] [--mem 530G]
                 [--cpus-per-task 16] [--ntasks 1] [--nodes 1] [--walltime 24:00:00]
                 [--timeout 3600] [--dry-run] [--server-class ...]
gtransfer status --config <path> --job <job_id> [--server <name>]
gtransfer fetch  --config <path> --job <job_id> [--glob "..."] [--out <dir>]
```

说明：
- `--remote` 必须落在配置的 `remote_work_root` 内（防目录穿越）
- `--solver` 覆盖 `solver`；未指定时取配置值。`gaussian` → `.gjf`/`.log`，
  `qchem` → `.in`/`.out`（三参数调用，见下节）
- `--gaussian-exec` / `--qchem-exec` / `--env-setup` 未指定时，自动回退到配置的
  `gaussian_exec` / `qchem_exec` / `env_setup` 字段（服务器环境一次配置，永久生效）
- `--download-glob` 覆盖默认回传文件；默认仅 `*.log`
- `--partition` / `--cpus-per-task` / `--ntasks` / `--nodes` / `--mem` /
  `--mem-per-cpu` / `--walltime` 未显式指定时**回退到 profile 配置**，
  再回退到内置默认（`--mem` 指定时取代 `--mem-per-cpu`）
- `--mode` 覆盖配置文件中的 `execution_mode`（direct/slurm）——
  但受管/商业云集群上 `direct` 会被硬阻断
- `--cpus-per-task` / `--ntasks` / `--nodes` / `--walltime` 参与配额闸门校验
- `--server-class` 覆盖服务器类别（逃生舱口，谨慎使用）
- `--dry-run` 仅打印操作序列与策略判定，不产生任何真实 I/O
- `--probe`（仅 check）连远端执行只读配额查询
- 所有子命令输出 JSON 到 stdout，便于智能体解析

## 求解器通道：Gaussian 与 Q-Chem（2026-10-14 新增 Q-Chem）

| | Gaussian | Q-Chem |
|---|---|---|
| `solver` | `gaussian`（默认） | `qchem` |
| 输入 / 输出 | `.gjf` → `.log` | `.in` → `.out` |
| 调用形式 | `g16 < in.gjf > in.log` | `qchem <in> <out> <scratchname>` |
| scratch 变量 | `GAUSS_SCRDIR` | `QCSCRATCH` |
| 可执行名 | `gaussian_exec`（默认 g16） | `qchem_exec`（默认 qchem） |
| 错误识别 | L 码 / `Error termination` | `Q-Chem fatal error` / FlexNet 许可证错误 / 不收敛 |

**共同约定**：

1. **scratch 一律指向任务目录内的 `scratch/`**，不复用平台默认 scratch。
   平台默认常为组共享目录（HPCCube 为 `qchem61/tmp`），并发同名作业会互相覆盖。
2. **`.in` 已纳入 CRLF→LF 转换**（与 `.gjf`/`.inp` 同等待遇）：Windows 端生成的
   Q-Chem 输入若带 CRLF，Linux 端会在 `$molecule`/`$rem` 段尾解析异常。
3. **进程输出统一进 `gtransfer.log`**，供提交后启动验证与状态回退使用；
   Q-Chem 的主输出仍在 `<stem>.out`，两者都会参与查询回退判定。
4. 两个求解器都在受管/商业云上受**登录节点禁跑计算程序**黑名单约束
   （`qchem` 与 `g16`/`orca`/`vasp` 同等对待），必须经 slurm 提交。

### Q-Chem 环境准备要点（HPCCube 实测）

Q-Chem 常**不在 module 系统**中，而是装在项目组共享目录并由 `qcenv.sh` 提供环境：

```ini
solver = qchem
qchem_exec = qchem
env_setup = source /work/share/hjbustc/software/qchem61/qcenv.sh
```

`qcenv.sh` 会在变量**未设置时**才填入默认值 —— 因此 gtransfer 在脚本中于
`env_setup` **之后**导出 `QCSCRATCH`，覆盖顺序正确。

⚠️ **许可证**：Q-Chem 依赖 FlexNet 浮动许可。若许可守护进程（`qchemlm`）未运行，
作业会在打印欢迎信息后**立即退出**，输出只有几百字节，退出码不明确 ——
`classify_exit(solver="qchem")` 会识别 `FlexNet Licensing error` /
`The desired vendor daemon is down` 并判为 FAILED。排查要点见文末实测记录。

### Q-Chem 运行注意（HPCCube 实测固化，2026-10-14）

**① `MEM_TOTAL` / `CC_MEMORY` 必须显式设置并与 SLURM 分配对齐**

Q-Chem 默认按**整节点内存**申请堆：实测节点约 1TB 时打印
`Allocating and initializing 359808MB of RAM`，而 SLURM 的 cgroup 只给
`--mem` 那么多 → 被 **OOM 杀掉**（exit 137 / sacct `OUT_OF_MEMORY`）。
站上既有 `C12H_OPT*.in` 里的 `mem_total 500000` 正是为此。建议：

```text
$rem
MEM_TOTAL   28000     ! 略小于 SLURM --mem（本例 --mem 30G）
CC_MEMORY   20000
$end
```

**② `qchem` 的退出码不可尽信（已加容错）**

实测同一 CCSD(T) 算例**输出完整、正常收尾、能量正确，却返回 rc=1**
（加 `MEM_TOTAL` 后该现象消失，触发条件尚不明确）。
若生产脚本直接 `exit $?`，会把已算完的昂贵结果判成 FAILED，
而 `keep_remote=false` 还会**连带删除现场**。

执行器已按"日志优先于退出码"处理：**仅当**输出含
`Thank you very much for using Q-Chem` **且不**含 `Q-Chem fatal error` 时，
才把非 0 退出码降为 0；否则保持非 0。该分支已用真实 shell + 假 qchem 做
参数化行为验证（rc=1+正常收尾→0 / rc=1+致命错误→1 / rc=0→0），
并用 `qchem: command not found`（rc=127）场景确认**不会掩盖真实失败**。

**③ scratch 每轮必须清空并使用唯一名字**

被 kill 的作业会在 scratch 留下**损坏的 `qarchive.h5`**（Q-Chem 6.1 的 HDF5 归档），
下一轮会直接启动失败：

```
Q-Chem fatal error occurred in module progman/main.C, line 178
Problem when trying to open or create .../qarchive.h5 (File accessibility)
The metadata in the file header may be corrupt.
```

对策：作业脚本内先 `rm -rf $QCSCRATCH` 再 `mkdir -p`，且第三参数
（scratch 名）带上 jobid 保持唯一。注意：**`gtransfer` 自动生成的脚本目前只在
任务目录内建 `scratch/`，未做"清空"**；若在**同一远端目录重复提交**，
请显式更换 `--task-name` 或先清空该目录。

## 执行模式

### direct（单台工作站，默认）

`ssh` 过去前台运行 `g16 < in.gjf > in.log`，进程结束即终态。
退出码 0 = 成功；非 0 或日志含 Gaussian 错误码（`Error termination` / `Lxxx`）=
失败。

⚠️ **在受管超算集群上 direct 被 harness 硬阻断**——该模式本质是"登录节点前台
跑计算"，违反中心手册。超算一律用 slurm。

### slurm（HPC 集群）

自动生成 `gtransfer_run.sh` → `sbatch` 提交拿 jobid → `squeue` 轮询 →
作业消失后 `sacct` 查退出码定成败。支持 partition/nodes/cpus/walltime
等参数（通过库 API `SlurmExecutor` 或配置扩展）。

## 配置格式

用户配置 `~/.workbuddy/transfer.conf`（ini），模板见
`references/transfer.conf.example`。支持多 profile：

```ini
[server:cluster1]
host = __SERVER_IP__
user = dy
key_file = ${RSYNC_SSH_KEY}
execution_mode = direct        # direct | slurm
server_class =                 # managed_cluster | commercial_cluster | workstation | 空=按 host 自动判定
address_family = auto          # auto | ipv4 | ipv6（2026-10-14 新增）
solver = gaussian              # gaussian | qchem（2026-10-14 新增）
qchem_exec = qchem             # 仅 solver=qchem 生效
remote_work_root = ~/gtransfer
connect_timeout = 15
poll_interval = 10

; profile 级配额覆盖（0/留空 = 沿用全局默认；2026-10-14 新增）
; min_disk_free_gb = 15
; max_concurrent_jobs = 16
; max_queued_jobs = 32
; max_cpus_per_job = 128
; max_walltime_hours = 336

; 单核内存上限（MB，0 = 不检查；2026-10-14 新增）
; 平台若硬校验「申请内存/申请 CPU ≤ DefMemPerCPU」，本地预检可提前拦截
; max_mem_per_cpu_mb = 3944

; Slurm 作业默认参数（CLI 未显式指定时生效；2026-10-14 新增）
; 把平台固定口径一次配置，避免重复传参、也避免沿用不适用于该平台的内置默认
; partition = wzacnormal
; cpus_per_task = 4
; mem_per_cpu = 3900M       ; 注意：内置默认 4G 在 HPCCube 会被 sbatch 拒绝
; walltime = 24:00:00
; mem =                     ; 指定时取代 mem_per_cpu

[transfer:cluster1]
upload_glob = *.gjf            # Q-Chem 用 *.in
download_glob = *.log *.chk    # Q-Chem 用 *.out *.fchk
keep_remote = false
```

作业参数解析顺序：**CLI 显式值 > profile 配置 > 内置默认**
（内置默认：`cpus_per_task=16`、`mem_per_cpu=4G`、`walltime=24:00:00`、无分区）。

### 地址族（`address_family`）—— 何时必须设 ipv4

部分域名同时发布 A 与 AAAA 记录，而客户端所在网络 IPv6 路由不可达。
此时 `getaddrinfo` 顺序使客户端优先尝试 IPv6，TCP 连接被静默丢弃，表现为
**「DNS 正常、ping 正常、SSH 超时」**——极易误判为对方防火墙或密钥问题。

设 `address_family = ipv4` 后，连接层按 A 记录解析并以字面量连接；
指定族无记录时**立即报错**（快速失败优于长时间超时）。

HPCCube 实测：`auto` → 多次超时；`ipv4` → 0.34s 建连成功。

### 配额闸门为什么可按 profile 覆盖

不同平台的紧约束不同：超算受 QoS 限制，**商业云受家目录配额限制**
（HPCCube 实测 `/work` 用户配额 50GB）。同一套阈值不适用，故 `min_disk_free_gb`
/ `max_*` 可在 `[server:xxx]` 段覆盖。覆盖值为 0 / 0.0 视为"未覆盖"。

⚠️ `build_quota_probe_commands` 的磁盘探测以 **`$HOME`** 为目标（2026-10-14 修正）。
配额式文件系统（ParaStor 等）在接入点上级目录返回的是**整个文件系统容量**
（HPCCube `/work` 整盘 35PB），只有 `$HOME` 才反映用户可用配额（实测 49GB）。

环境变量引用：仅 `${VAR}` / `%VAR%` 会被展开（本机环境变量）；裸 `$VAR`（如 $PATH）
保留字面供远端 shell 使用（避免 env_setup 中远端变量被误展开为本机值）。
必填字段（host/user）为空则报错。密钥不落盘明文。

⚠️ **超算配置注意**：`server_class` 留空时按 host 指纹自动判定为
`managed_cluster`，此时 `execution_mode = direct` 会被 harness 拦截。
瀚海22 请务必设 `execution_mode = slurm`，并用 `gtransfer check` 体检。

## 随附参考脚本（references/hanhai22-scripts/）

hanhai22 超算上验证过的启动/转换脚本已本地化（防止服务器路径变动导致失效）：

| 脚本 | 用途 | 关键内容 |
|------|------|----------|
| `g16-han22.sh` | g16 slurm 提交模板 | `module load gaussian/16/g16.c02`；`g16 < x.gjf > x.out`；`formchk` |
| `orca22.sh` | orca slurm 提交模板 | orca504 + openmpi411 环境；批量跑 .inp |
| `fchk2inp.sh` | 高斯 .fchk → orca .inp | Multiwfn 3.8 转换（输入取向坐标） |
| `fchk2mkl.sh` | 高斯 .fchk → orca .mkl | Multiwfn 3.8 转换 |

使用时可参考这些模板生成 slurm 作业脚本（需按实际路径调整），
或作为高斯→orca 工作流（run-rest 的衔接参考）的转换依据。

### Gaussian → ORCA 接力流程（组内算便宜 Gaussian，集群算昂贵 ORCA）

```
组内服务器 (__SERVER_IP__, workstation)
  ① g16 优化/收敛波函数 → .fchk        [便宜，随便跑]
  ② fchk2inp.sh → ORCA .inp  ┐         [Multiwfn，已入白名单]
  ③ fchk2mkl.sh → .mkl       ┘
        ↓ 上传
学校集群 (__SERVER_IP__, managed_cluster)
  ④ sbatch: orca_2mkl X -gbw → .gbw    [格式转换]
  ⑤ sbatch: ORCA 主计算                 [昂贵，唯一被允许的执行方式]
```

**关键约束**：

1. **②③ 必须在组内服务器执行**，不要发往受管集群。Multiwfn 是格式转换工具，
   虽已入白名单，但在超算登录节点开长任务仍违背"不在登录节点跑重负载"原则。
   这也正是该分工的本意。
2. **优先使用瀚海22 原位资产**（`guard.managed_tool_assets()`），
   它久经使用、关键词完善、适配大批量计算。组内服务器应把该 Multiwfn +
   `template.inp` 拷贝固定到同一相对路径，保持两侧一致（`asset_sync_hint()`）。
3. **转换后必须复核电荷/自旋多重度**（P0）。Multiwfn 是从源波函数**自动继承**
   这两个值，不判断你的研究意图。开壳层/阴离子体系尤其要查源波函数自旋态
   与 ORCA 侧基组匹配。清单见 `references/ustc-policy.md` §7.3。
4. 模板里的 `%pal nprocs` / `%maxcore` 会原样进入 ORCA 输入，提交前核对；
   但**复核重心在意图（电荷/多重度），不在资源**。


## 与现有生态衔接

```
gjf-flux(组装 .gjf) → remote-sync(本 skill) → dpdata-driver(标注结果)
                          ↕ 知识复用
                    run-gauss(执行环境知识)
```

- **上游**：消费 gjf-flux 产出的 .gjf
- **执行知识**：远端环境/scratch 细节参考 run-gauss SKILL.md
- **下游**：回传的 .log 交 dpdata-driver 标注；.chk 交 Multiwfn 波函数分析
- **调度**：gaussian-agent 在 remote 场景下用本 skill 作为执行桥接
- **振动/热化学后处理**：频率与 ZPE 校正因子见
  `~/.workbuddy/skills/qchem-agent/references/scale-factors/README.md`
  （频率因子与 ZPE 因子是两套独立因子，**不得混用**）。该表适用于远程任何
  量子化学作业的频率/热化学交付物，与求解器无关。

## 库 API（供其他技能/脚本调用）

```python
from gtransfer_lib.config import load_config
from gtransfer_lib.connection import ParamikoSsh
from gtransfer_lib.transport import SftpTransfer
from gtransfer_lib.executor import create_executor
from gtransfer_lib.guard import (PolicyGuard, PolicyViolation,
                                 managed_tool_assets, resolve_asset)

cfg = load_config("~/.workbuddy/transfer.conf", server_name="default")

# 合规预检（强烈建议；CLI 已内置，库调用需自行执行）
guard = PolicyGuard(cfg)
decision = guard.preflight(
    mode=cfg.server.execution_mode,
    remote="~/gtransfer/t1",
    command="python main.py",
    cpus_per_task=16, walltime="24:00:00", download_glob="*.log",
)   # 违规抛 PolicyViolation，调用方不得吞掉后继续 I/O

# Gaussian→ORCA 接力：取瀚海22 原位首选资产（Multiwfn + 久经验证的模板）
mw = managed_tool_assets()["multiwfn"]
tpl, source = resolve_asset("orca_template")   # source == "managed_default"
if source == "override":
    pass   # 调用方须留痕告警：模板已被定制，结果与他人不可直接比较

with ParamikoSsh(cfg.server) as ssh:
    tr = SftpTransfer(ssh, cfg)
    tr.upload("water.gjf", "~/gtransfer/t1")
    ex = create_executor(ssh, cfg)   # submit 内部再校验一次（第二道防线）
    state = ex.submit("~/gtransfer/t1", "water.gjf")   # direct: 同步终态
    tr.download("~/gtransfer/t1", "results", "*.log *.chk")
```

`PolicyViolation` 携带 `rule_id` / `authority` / `remedy` 属性，便于程序化
提示用户合规做法与官方依据。

## 测试

238 个 mock 测试（含 RFC 6238 TOTP 官方向量、74+ 合规策略测试、76 项
HPCCube/Q-Chem 扩展测试、真实 shell 行为验证）+ 真机链路验证（2026-08-01 课题组服务器真实 g16 计算闭环；
2026-08-05 hanhai22 超算 TOTP 认证 + 真实 g16 计算闭环；
2026-10-14 HPCCube 商业云 Q-Chem 真实计算闭环 —— H₂O/HF/STO-3G，
`Total energy = -74.96259818 hartree`）：


```bash
cd ~/.workbuddy/skills/remote-sync
python -m pytest tests/ -v
```

覆盖：配置模板+用户覆盖、上传路径映射/原子写/校验/rename 回退、下载 glob 与恶意文件名过滤、
direct 退出码判定、Gaussian L 码识别、slurm jobid 解析/squeue 轮询、CLI 契约与 --dry-run、
路径穿越拒绝、shell 注入转义（~ 前缀特判）、~ 展开为绝对路径、TOTP 生成与口令拼接、
上传 CRLF→LF 自动转换（.gjf/.com/.inp/.xyz）。

**合规策略测试（`tests/test_guard.py`，60 项）**：服务器分类（host 指纹/显式声明/优先级）、
direct 模式阻断、路径边界（含同前缀与 `../` 两种绕过尝试）、命令黑名单 11 类 + 白名单、
工作站豁免规则、CPU/墙钟上限（含边界值）、并发闸门、回传 glob 越界、上传内容告警、
preflight 汇总与强制提醒、配额探测命令只读性与 shell 引用、
CLI 与执行器**两层**阻断验证（含"拦截发生在任何远端命令之前"断言）。

真机验证结论（两台服务器）：
- 课题组服务器（__SERVER_IP__）：口令认证 + 真实 g16 计算闭环
- hanhai22 超算（__SERVER_IP__）：TOTP keyboard-interactive 认证 + 真实 g16 计算闭环
- Windows 本机 .gjf（CRLF）上传后自动转 LF，Gaussian 不再报 "End of file in ZSymb"
- 合规 harness 真机验证（2026-09-11）：`check` 正确识别 hanhai22 为 managed_cluster
  并报出配置中 `execution_mode = direct` 违规；submit 在发出任何 SSH 之前拦截
  direct 模式与 `rm -rf` 命令（退出码 4）；合规 slurm 路径正常放行

## 安全注意事项

1. 支持三种认证：SSH 密钥（推荐，`key_file`）、口令（`password = ${GTRANSFER_PASSWORD}`）、
   口令 + **TOTP 二次验证**（`totp_secret = ${TOTP_SECRET}`，如 USTC 瀚海超算，
   SSH 分步询问 Password 与 Verification code，remote-sync 自动应答）
2. `--remote` 强制落在 `remote_work_root` 内，拒绝目录穿越
3. 远端命令为固定模板，路径经 shlex 安全引用，不拼接用户原始输入
4. 上传/下载均做字节数校验，失败即报错（连接层自动重试 3 次）
5. `keep_remote=false` 时成功后自动清理远端工作目录
6. **合规 harness（硬阻断）**：受管超算集群强制 slurm、禁用 direct、
   命令过白名单+黑名单、作业规模与并发受配额闸门约束。违规返回退出码 4，
   且拦截发生在任何 I/O 之前。CLI 与执行器两层独立校验，绕过其一仍被另一拦截

### 超算 TOTP 认证注意事项（2026-08-04 真机验证）

- USTC 瀚海22：`keyboard-interactive` 两步认证（Password → Verification code），
  验证码 6 位，窗口大小 17（±8），每 30 秒限 3 次，禁止复用
- remote-sync 已内置 RFC 6238 TOTP 生成（零依赖标准库实现），自动应答两步提示
- ⚠️ 超算对高频连接有限制：短时间多次认证/探测会触发 banner 空响应（临时限制），
  验证码窗口额度也会因连续失败耗尽。正常使用间隔应 > 30 秒，避免高频重试
- 双登录节点（如 .113/.115）可能有节点下线，连接失败时可切换 host

## 已知限制与后续迭代

- direct 模式不支持异步查询（query 明确 NotImplemented，接口已预留）
- 调度器暂支持 slurm；PBS/LSF 可复用 RemoteExecutor 接口扩展
- 大文件增量传输（rsync）尚未实现
- paramiko 未安装时真实连接报清晰错误（mock 测试不受影响）

## 稳定远程提交流程 (2026-08-23 固化)

> 本节由 Run #130 故障经验总结。以下流程确保远程计算提交的可靠性，
> 避免因上传错误、路径问题或环境缺失导致数小时白跑。

### 通用命令模式（非 Gaussian 计算）

```bash
python ~/.workbuddy/skills/remote-sync/scripts/gtransfer.py submit \
  --config ~/.workbuddy/transfer.conf \
  --local /path/to/local_dir/ \
  --remote ~/gtransfer-test/job_name \
  --mode slurm \
  --command "python main.py" \
  --env-setup "export PATH=/home/ldy/miniconda3/envs/pycs-sw/bin:$PATH" \
  --download-glob "*.log *.json *.txt" \
  --out-dir ./results/job_name \
  --timeout 21600
```

### 内置安全验证（gtransfer CLI 自动执行）

1. **上传后验证** (`verify_upload` 操作): 上传完成后在远端执行
   `find <remote> -maxdepth 1 -type f | wc -l`，确认文件以文件形式存在
   （非目录）。若返回 0，立即报错终止，防止 "文件上传为目录" 导致的
   `can't find '__main__' module` 错误。

2. **提交后启动验证** (`startup_check` 操作, 仅 SLURM 模式):
   提交后等待 10 秒，检查 squeue 状态 + gtransfer.log 前 5 行。
   若日志含 `can't find '__main__'`、`Traceback`、`ImportError`、
   `ModuleNotFoundError`、`No such file or directory`、`SyntaxError`
   等错误标记，立即报错终止，不等超时。

3. **路径验证**: `--remote` 参数支持 `~/` 前缀，自动展开为
   `/home/<user>/...` 后与 `remote_work_root` 比较。

### 库 API 调用注意事项

使用库 API 直接提交时，**务必使用 `tr.upload(local_dir, remote_dir)`
上传整个目录**，而非逐文件上传：

```python
# ✅ 正确：上传目录（SftpTransfer 递归上传所有文件）
tr = SftpTransfer(ssh, cfg)
uploaded = tr.upload("/path/to/local_dir", "/home/ldy/gtransfer-test/job")

# ❌ 错误：逐文件上传时 remote 参数被视为目标目录，文件会上传为子目录
for f in os.listdir(local_dir):
    tr.upload(os.path.join(local_dir, f), f"{remote_dir}/{f}")  # BUG!
```

**根因**: `SftpTransfer.upload(local, remote)` 的 `remote` 参数始终是
**目标目录**，不是文件路径。当 `local` 是文件时，上传为 `remote/<文件名>`。
若 `remote` 已含文件名（如 `remote_dir/phase_k.py`），则文件被上传到
`remote_dir/phase_k.py/phase_k.py`（子目录内）。

### 常见故障排查

| 症状 | 根因 | 修复 |
|------|------|------|
| `can't find '__main__' module` | .py 文件被上传为目录 | 用 `tr.upload(dir, remote)` 上传整个目录 |
| `--remote 必须位于 ~/gtransfer 内` | `~` 未展开 | 已修复 (2026-08-23): CLI 自动展开 `~/` |
| SLURM job 立即失败 | 远端 Python 环境缺失 | `--env-setup "export PATH=..."` 指定环境 |
| 作业跑完但无输出 | gtransfer.log 重定向问题 | 启动验证已检测日志前 5 行 |
| `sacct 无记录` | 服务器 accounting storage 未启用 | gtransfer 回退检查输出文件是否存在 |
| **失败作业被报 SUCCESS** | 🔴 **sacct 取 `tail -1` 命中 `.extern` 步骤行（2026-10-14 实测）** | 见下方 `🔴 sacct 主作业行` 专节 |
| Q-Chem 输出仅数百字节、无能量行 | Q-Chem 许可证守护进程未运行（FlexNet `-97,121`） | 联系课题组/平台重启 `qchemlm`，见文末实测记录 |
| **`sacct` 无记录 + 日志未生成 → 报 FAILED 并静默删除远端目录** | 🔴 **sacct 竞态缺陷（2026-09-12 实测）** | 见下方专节 |

### 🔴 sacct 主作业行：`tail -1` 会把失败作业误报为 SUCCESS（2026-10-14 修复）

**适用条件**：目标集群**启用了 accounting**（`sacct` 有记录），且作业为多步骤。

**缺陷机理**：
`sacct -j <id>` 会返回**主作业行 + 各步骤行**：

```
46528774|119:0|FAILED            ← 主作业（真实结果）
46528774.batch|119:0|FAILED
46528774.extern|0:0|COMPLETED    ← .extern（探针步骤，失败作业也常为 COMPLETED）
```

早期实现 `sacct ... | tail -1` **恰好命中 `.extern` 步骤行** → 判为 SUCCESS。

**为何长期未暴露**：该模式在 accounting 关闭的集群（USTC 瀚海22）上不可见 ——
`sacct` 无记录即走输出文件回退，根本不进入这段解析。**只有接了商业云
（HPCCube accounting 启用）才会必然暴露。**

**实测证据（2026-10-14，HPCCube wuzh02）**：Q-Chem 作业因许可证守护进程停止而
`FAILED(119:0)`，gtransfer 却返回 `SUCCESS`、退出码 0，且 `keep_remote=false`
会顺手删掉现场 —— **失败被静默吞掉**。

**修复**：新增 `_parse_sacct_main_job()`，只承认 `JobID` 与查询 id **完全一致**的
主作业行，忽略 `.batch` / `.extern` / `.0` 等步骤行；且要求
`State=COMPLETED` **且** `ExitCode=0:0` 才算成功。无主作业行时才走回退，
并**先复查一次 `squeue`**（防"作业仍在排队、sacct 尚未落记录"被误判失败）。

**判别方法**：怀疑结果异常时，用 `gtransfer status --job <id>` 复核，
或直接 `sacct -j <id> -o JobID,State,ExitCode --parsable2` 看**第一行**。

### 🔴 sacct 竞态：`submit` 会静默删除已提交的作业目录

**适用条件**：目标集群 **accounting storage 未启用**（`sacct` 恒无记录），
且作业从 `sbatch` 到写出首行日志有可感延迟（队列繁忙、PENDING、环境初始化慢）。

**故障链**：
1. CLI `submit` 上传 bundle → `sbatch` 拿 jobid；
2. 立即调用 `SlurmExecutor.query()` 判断状态；
3. 集群 `sacct` 无记录 → query() 回退为 `test -s gtransfer.log`；
4. 此刻作业多为 **PENDING**，日志尚未生成 → query() 判定 **FAILED**；
5. CLI 认为作业失败 → 执行 `cleanup_remote` → **删除刚上传的远端运行目录**；
6. 结果：`{"error": "sacct 无记录且输出文件不存在，作业可能被清理"}`，
   而作业其实正在正常排队/运行 —— 但工作目录已被删除，作业注定失败。

**实测证据（2026-09-12，__SERVER_IP__）**：首次提交 46.99 s 后即报上述错误；
SSH 复核确认 `~/gtransfer-test/<jobname>/` 已不存在、`squeue` 为空。

**判别方法**：看到「sacct 无记录且输出文件不存在」**先别相信作业真失败** ——
立即 SSH 查 `squeue -u <user>` 是否仍有该 jobid；若在跑，说明命中了本缺陷。

**规避（推荐）**：把「上传 → 写 sbatch 脚本 → `sbatch` → 返回」与「轮询/取数」
**拆成两步，各自都不走 CLI 的 submit/fetch 一次式接口**：

```python
# 第一步：只提交，不轮询（ParamikoSsh 自动连接）
from gtransfer_lib.config import load_config
from gtransfer_lib.connection import ParamikoSsh
cfg = load_config(None, "default"); ssh = ParamikoSsh(cfg.server)
sftp = ssh.open_sftp(); sftp.put("local.py", f"{ssh.home_dir}/gtransfer-test/j/run.py"); sftp.close()
out, _, _ = ssh.exec_command(f"cd {ssh.home_dir}/gtransfer-test/j && sbatch run.sh")
# 到此为止即返回；绝不调用 query()/cleanup_remote

# 第二步：用 squeue + 日志尾部 + SFTP 自行取数，绕过 CLI
out, _, _ = ssh.exec_command('squeue -u ldy -o "%.8i %.14j %.8T %.10M %R"')
```

**项目内实现范例**：`_submit_k3_manual.py`（提交）+ `_poll_k3_results.py`（取数）。
**通用原则**：凡「sacct 不可用 + 作业启动延迟」的组合，都不要用 CLI 的
`submit`/`fetch` 一步式接口。

**若要根治**：在 `SlurmExecutor.query()` 中，把「作业不在 squeue 且无日志」
与「作业在 squeue」区分处理 —— 前者才可能是失败，后者应继续等待；
且 `cleanup_remote` 绝不应在「状态未知」时触发。

> 2026-10-14 部分落实：`query()` 在 sacct 无主作业行时会**先复查 `squeue`**，
> 仍在队列则返回 RUNNING 而非 FAILED，已消除"在途作业被误删"这一半风险。
> `cleanup_remote` 的时序问题仍存，批量长任务建议仍按上面的两步式提交。


---

## HPCCube 商业云超算接入实测记录（2026-10-14）

**目标**：星际化学 C2nCN 自由基第一激发能计算，使用外部商业云 Q-Chem。
配置见 `~/.workbuddy/transfer.conf` 的 `[server:hpccube]`（模板已同步新字段文档）。

### 平台实测参数

| 项目 | 实测值 | 对策略的影响 |
|------|--------|--------------|
| 接入 | `wuzh02.hpccube.com:65091`，SSH 密钥（RSA 2048，无口令），到期 2027-01-12 | 密钥置于 `~/.workbuddy/keys/`，不落项目目录 |
| **地址族** | 域名同时有 A `__SERVER_IP__` 与 AAAA `2409:8728:4eff:2001::100`；**IPv6 不可达** | 必须 `address_family = ipv4` |
| 登录节点 | `login03`/`login05`，CentOS 7 内核 3.10，OpenSSH 7.4 | — |
| 调度器 | SLURM（`/opt/gridview/slurm`），唯一可用分区 `wzacnormal` | `--partition wzacnormal` |
| 分区规模 | 336 节点 × 128 核，RealMemory ~1TB/节点，MaxTime 333 天，DefMemPerCPU 3944MB | `--mem` 声明整节点内存 |
| **家目录配额** | `/work` ParaStor，用户配额 **50GB**（整盘 35PB，勿按整盘判断） | `min_disk_free_gb = 15`；scratch 必须及时清理 |
| **Q-Chem** | 6.1.1，组共享 `/work/share/hjbustc/software/qchem61`，**不在 module 系统** | `env_setup = source .../qcenv.sh` |
| **Q-Chem 并行能力** | `QCMPI = seq`、binary 无 `-fopenmp` → **纯串行，无 MPI/OpenMP** | `-np`/`-nt` 无加速；并行只能靠**多作业并发** |
| 组共享 scratch | 默认 `qchem61/tmp`，且已残留历史任务目录 | 必须按任务覆盖 `QCSCRATCH` |
| 可复用资产 | `~/C12H-/CCSDT/wz_qchem_single.sh`、`C12H_OPT*.in` | 输入格式与提交口径的参考基准 |

### 已验证通过的链路（真机）

| 环节 | 结果 |
|------|------|
| `ping` | ✅ `login03`，IPv4 强制后 0.34s 建连 |
| `check --probe` | ✅ `$HOME` 配额探测返回 49GB（非整盘 35PB）；并发/磁盘闸门 ok |
| `upload` + 校验 | ✅ CRLF→LF 转换生效，远端文件计数校验通过 |
| `sbatch` 提交 + 启动验证 | ✅ 捕获 jobid、读到 Q-Chem 6.1.1 banner、无启动错误标记 |
| 状态判定（成功） | ✅ 通用命令作业 `python3 --version` → `sacct` 主作业 `COMPLETED 0:0`，判 SUCCESS |
| 状态判定（失败） | ✅ 修复后对许可证失败作业正确报 `FAILED`（ExitCode 119） |
| 回传 + 远端清理 | ✅ 结果回传本地；`keep_remote=false` 后 `~/C2nCN` 已清空 |

### ✅ 已解除：Q-Chem 许可证服务停摆（2026-10-14 现场修复）

**⭐ 关键结论（受控实验实测）：正在运行的作业不受守护进程消失影响。**

用单个长算例（H₂O CCSD(T)/aug-cc-pVQZ，串行，总 311 s）做受控中断：
在作业进入 CCSD 关联计算后杀掉 lmgrd+qchemlm 并**中断 45 秒**，观测到

| 时刻 | 观测 |
|---|---|
| 中断前 | 输出 9,525 B（SCF 迭代中） |
| 杀后 +9 s | 输出 **+205 B**（仍在推进） |
| 杀后 +18 s | 输出 **+1,335 B** |
| 杀后 +27 s | 输出 **+7,345 B**（进入 CCSD 迭代） |
| 中断期结束 | 输出 18,410 B（累计 +8,885 B），作业状态始终 RUNNING |
| 恢复许可后 | 作业继续跑完 → `COMPLETED 0:0`，Wall 311 s |
| 最终结果 | `CCSD(T) total energy = -76.36355029`，正常收尾，**全程无任何许可错误** |

**含义**：FlexNet 在作业启动时一次性签出授权，运行期间不再依赖守护进程。
因此**已启动的长作业不会因许可服务短暂中断而夭折** ——
受影响的只是"中断期间新提交的作业"（它们会即刻失败）。

⇒ 这**降低了持久化的紧迫性**：守护进程消失的代价是"停机窗口内提交失败 + 需人工拉起"，
而不是"在跑的长作业全军覆没"。但**仍建议部署持久化**，
因为人工恢复仍有遗漏风险，且长作业的**排队期**若撞上停机窗口同样会失败。

**现象**：Q-Chem 作业 `FAILED(119:0)`，输出仅 597 字节（欢迎信息后即退出）：
`FlexNet Licensing error:-97,121` / `The desired vendor daemon is down`。

**定位与修复（两层根因，均可复现）**：

1. `login02` 上**没有 Q-Chem 的 vendor daemon `qchemlm`**（只有他厂商的 `cstd` 占着 27001），
   `qchemlm.log` 尾部退出报告时间戳为 **2026-10-03 04:07:47** —— 服务已停摆 11 天。
2. 手动拉起 lmgrd 时暴露第二层根因：**服务器端许可证文件是 CRLF 行尾**
   （`SERVER login02 e8611f37205a\r`）→ lmgrd 报 `Invalid License File`（退出码 11）。
   去 CR 副本启动后 lmgrd 与 qchemlm 均 `UP`，`QCHEM` 功能码 1000 个可用，
   冒烟算例 `Total energy = -74.96259818 hartree` 正常收尾。

**完整操作手册**（启动命令、失败模式对照表、USER_BASED/SRG 授权要求、持久化部署）：
见 `references/qchem-flexnet-license.md`；**重启后自动拉起**用
`references/qchem-flexnet-service/` 下的 systemd 资产（管理员执行）：
`install-qchemlm-service.sh`（一键安装，含 LF 修正 + 端口钉定 + 自检），
无 root 时用 `lic_service.sh`（start/stop/status/restart，带重试自检）。

⚠️ **持久性缺口**：`flexnet/` 目录对组用户不可写，普通用户只能用
「去 CR 私有副本 + 后台进程」临时激活，**节点重启后失效**；须由管理员按上面的
systemd 资产部署才能根治。

⚠️ **两个易踩的坑（真机实测）**：
① **端口必须显式钉定** —— `SERVER` 行不写端口时，lmgrd 在 27000 被占时会
**静默自增到 27001/27002** 并照常启动，而客户端按默认 27000 连接 →
服务"看着在跑"但客户端全连不上（本案踩过）。
② **同一主机不得有两个同厂商 vendor daemon** —— 残留旧实例会让新 qchemlm 立即
`EXITING DUE TO SIGNAL 41`，lmgrd 随之退出；且停止→启动存在注册释放竞态，
单次启动可能失败，需重试（`lic_service.sh` 内置 3 次）。

⚠️ **许可证判活的最快方法**（不必查远端配置）：跑一个极小算例，看 `.out` 是否只有
几百字节且含 `FlexNet Licensing error` —— 是则该守护进程又停了。

**副产品（已修复）**：该失败最初被 gtransfer **误报为 SUCCESS**，根因是 sacct 取
`tail -1` 命中 `.extern` 步骤行 —— 见 `🔴 sacct 主作业行` 专节。修复后正确报 FAILED。

### ⚠️ 平台硬约束：单核内存上限（内存:CPU 比）

HPCCube 的 sbatch **硬校验** `申请内存 / 申请 CPU ≤ DefMemPerCPU`（该分区 = **3944 MB**），
超限**直接拒绝提交**：

```
sbatch: error: Job submission failed because too much memory was requested
relative to the number of CPUs requested.
```

`sbatch --test-only` 实测边界（2026-10-14，无消耗）：

| 请求 | 结果 |
|---|---|
| 1 CPU / 3G、2 CPU / 7G、128 CPU / 480G、`--mem-per-cpu=3900M` | ✅ 通过 |
| 1 CPU / 4G、2 CPU / 8G、128 CPU / 500G、`--mem-per-cpu=4G` | ❌ 拒绝 |
| 20 CPU / 530G（= 站上既有 `wz_qchem_single.sh` 的口径） | ❌ **现已会被拒** |

**两个直接后果**：

1. **内置默认 `--mem-per-cpu=4G` 在该平台必然被拒** → hpccube profile 固化为 `3900M`。
2. **内存只能靠核数兑换**：要 N GB 内存须申请 `ceil(N*1024/3944)` 核。
   单节点 128 核 ⇒ 单节点内存上限约 **493 GB**；更大内存须增加 `--nodes`。
   由于该 Q-Chem 是**串行版**，这些核大部分闲置 ——
   **内存受限的 CCSD(T)/EOM-CCSD 作业在此平台按核计费，实际成本高于直觉估算。**

**本地预检**：profile 设 `max_mem_per_cpu_mb = 3944` 后，guard 会在**任何 I/O 之前**
拦截越界请求并给出定量修正建议：

```json
{"rule_id": "POLICY-MEM-PER-CPU-EXCEEDED",
 "remedy": "把 --cpus-per-task 提到 26（或降低 --mem），使 内存/CPU ≤ 3944MB；..."}
```

### 本项目推荐提交口径

hpccube profile 已固化平台口径（`partition=wzacnormal`、`mem_per_cpu=3900M`、
`cpus_per_task=4`、`walltime=24:00:00`、`max_mem_per_cpu_mb=3944`），
**常规提交无需重复传平台参数**（实测：零平台参数提交已跑通闭环）。

```bash
# 小体系：零平台参数，全走 profile 默认
python ~/.workbuddy/skills/remote-sync/scripts/gtransfer.py submit \
  --server hpccube --local ./C6N_SP.in --remote "~/C2nCN/c6n_sp" \
  --download-glob "*.out *.fchk" --out-dir ./results/c6n_sp --timeout 3600

# 大内存作业：按内存反推核数（100GB ⇒ 26 核；200GB ⇒ 52 核）
python ~/.workbuddy/skills/remote-sync/scripts/gtransfer.py submit \
  --server hpccube --local ./C6N_EOM.in --remote "~/C2nCN/c6n_eom" \
  --mem 100G --cpus-per-task 26 --walltime 7-00:00:00 \
  --download-glob "*.out *.fchk" --out-dir ./results/c6n_eom --timeout 604800
```

要点：**`--cpus-per-task` 由内存需求反推，而非由并行需求决定**（Q-Chem 串行为主）；
并行度靠**并发多个作业**，`max_concurrent_jobs = 16`，但受 50GB 家目录配额与
scratch 占用约束；`max_cpus_per_job = 128`（单节点核数上限）。
