# 受管超算集群作业提交规定（策略依据）

> 本文件是 `gtransfer_lib/guard.py` 中每条规则的官方依据出处。
> 规则实现为硬阻断；本文件供审计与人工复核。
>
> **来源**：中国科学技术大学超级计算中心（scc.ustc.edu.cn）
> **核实日期**：2026-09-11
> **适用对象**：USTC 瀚海22（`__SERVER_IP__` / `__SERVER_IP__`）
> 及其他被 `server_class = managed_cluster` 声明的受管集群。

---

## 1. 登录节点禁止直接运行作业

**原文**（用户手册，2026-05-30 版）：

> 登录节点……**禁止在此节点上不通过作业调度系统直接运行作业**。
> 所有需要运行的作业均必须通过作业提交命令提交。

**对应规则**

| 规则 ID | 实现 |
|---------|------|
| `POLICY-DIRECT-FORBIDDEN` | 受管集群上 `execution_mode = direct` 被硬阻断（该模式本质是登录节点前台跑计算） |
| `POLICY-LOGIN-NODE-COMPUTE` | 远端命令中出现 g16/g09/g03/orca/vasp/lammps/cp2k/pw.x/gmx 等计算程序时拦截 |

**合规做法**：一律使用 `--mode slurm`，由 `sbatch` 提交至计算节点。
Gaussian 场景即"提交脚本 → 调度器分配节点 → 节点上执行 g16"。

---

## 2. 资源配额限制

**原文**（FAQ 基本信息）：

> 单用户帐号的磁盘空间、节点类型/数量、队列、运行中作业数、排队中作业数均有限制。

**对应规则**

| 规则 ID | 自设阈值 | 说明 |
|---------|----------|------|
| `POLICY-RESOURCE-EXCEEDED` | 单作业 ≤64 核 | 防单作业独占 |
| `POLICY-WALLTIME-EXCEEDED` | 单作业 ≤72h | 对齐 QoS `QOSTimeLimit` |
| `POLICY-CONCURRENCY-LIMIT` | 运行中 <8 | 提交前 `squeue -t RUNNING` 实测 |
| `POLICY-QUEUE-LIMIT` | 排队中 <16 | 提交前 `squeue -t PENDING` 实测 |
| `POLICY-DISK-LOW` | 剩余 ≥5GB | 提交前 `df` 实测 |

**关于自设阈值**：中心公布的是上限，harness 取**更保守**的值（如中心可能允许
更多并发，我们只取 8），目的是留余量、避免触顶被拒影响他人。阈值集中定义于
`QuotaPolicy` dataclass，可按实际情况调整。

**磁盘配额口径注意**：用户手册记 500GB、FAQ 记 50GB，存在不一致。
harness 不硬编码该数字，改为提交前实测 `df` 并对照自设下限，规避口径歧义。

---

## 3. 数据内容规定

**原文**（用户手册）：

> 用户帐号的目录文件夹下，**不得上传保存任何与科研计算无关的文件**。

**对应规则**

| 规则 ID | 实现 |
|---------|------|
| （告警）`check_upload_content` | 上传内容含 `.git`/`__pycache__`/`.pyc`/`.venv`/`node_modules`/`.DS_Store`/`*.tmp`/编辑器目录时返回 `content_check: warning` |

**为何只告警不阻断**：存在误伤可能（如某 `.tmp` 确为计算中间产物），
故返回告警列表供调用方/用户判断，而非静默阻断。**Agent 的工作文件
（缓存、会话、仓库元数据）是典型触雷点**，上传前应排除。

---

## 4. 数据保全

**原文**（用户手册）：

> **不提供任何文件的备份或恢复服务**。

**对应规则**：无法自动判定，转为 preflight 与 `check` 输出中的
`mandatory_notices` 强制提醒。

**合规做法**：任务完成后及时回传结果到本地并清理远端数据
（`keep_remote = false` 已是默认，成功后自动 `rm -rf` 工作目录）。

---

## 5. 数据安全红线

**原文**（数据安全通知，2026-03-18）：

> 严禁处理/存储**国家秘密、工作秘密、商业秘密**及未公开的敏感科研数据。

**对应规则**：无法自动判定（按内容性质，非技术特征），故作为
`mandatory_notices` 强制呈现，**判断责任在用户**。

---

## 6. 网络安全约束

**原文**（网络安全说明，2026-03-26）：

> 默认仅校园网访问；默认仅开放 SSH 端口，其他端口需发邮件申请。

**对应规则**

| 规则 ID | 实现 |
|---------|------|
| `POLICY-NETWORK-EGRESS` | 远端命令含 `curl`/`wget`/`nc`/`ssh <user>@` 时拦截（登录节点主动外联属未授权行为） |

**合规做法**：所需输入数据先本地准备后经 SFTP 上传到工作目录，
不要在登录节点拉取外部资源。

**另注**：因仅开放 SSH 端口，**不存在 HTTP API 提交途径**——
一切自动化必须走 SSH + Slurm CLI。这也是 harness 只能建在 remote-sync
（SSH 层）而非某个 REST 客户端的原因。

---

## 7. 一般性越权与破坏性操作

以下规则无单一官方条款对应，但综合"禁止未经申请的服务端变更""用户无
root 权限""不得影响其他用户"等原则确定：

| 规则 ID | 拦截内容 | 理由 |
|---------|----------|------|
| `POLICY-DESTRUCTIVE-RM` | `rm -rf` | 不可逆破坏；远端目录清理由 gtransfer 自身按 `keep_remote` 处理 |
| `POLICY-DESTRUCTIVE-RM-HOME` | 删除 `$HOME`/`~`/`/home/*` | 同上 |
| `POLICY-SUDO` | `sudo` / `su -` | 用户无 root 权限，提权属安全违规 |
| `POLICY-NESTED-SBATCH` | 命令中嵌套 `sbatch` | 作业套作业，绕过调度器资源管理 |
| `POLICY-CRON-SYSTEMD` | `crontab` / `systemctl` / `service ... start` | 未申请的服务端变更；周期性调度应在本地侧做 |
| `POLICY-PERM-ESCALATION` | `chmod 777` / `chmod -R` | 影响其他用户与数据安全 |
| `POLICY-SECRETS-EXFIL` | 读 `/etc/passwd`、`/etc/shadow`、`.ssh/id_*`、`authorized_keys` | 凭据窃取 |
| `POLICY-COMMAND-NOT-ALLOWLISTED` | 起首程序不在白名单 | 防止任意二进制执行；白名单含 python/mpirun/srun/bash/module/**Multiwfn**/**orca_2mkl** 等 |

### 7.1 白名单中的格式转换工具（2026-09-11 新增）

`Multiwfn` 与 `orca_2mkl` 已加入白名单。判定理由：

- 二者是**格式转换工具**，不产生计算负载，不占用计算资源；
- 是 Gaussian→ORCA 接力流程（`.fchk → .inp` / `.mkl → .gbw`）的必需环节；
- 单机绿色版 Multiwfn 无需安装、无需 root，符合"不申请服务端变更"原则。

**边界（重要）**：

- 白名单只放行**起首程序名**，不放行任意参数——黑名单始终优先。
  例如 `rm -rf` 即使跟在 `Multiwfn ... &&` 后面仍被 `POLICY-DESTRUCTIVE-RM` 拦截。
- 近似名不放行：`MultiwfnX`、`orca_2mklfake` 一律命中 `POLICY-COMMAND-NOT-ALLOWLISTED`
  （测试 `test_lookalike_converter_name_still_blocked` 锁定该行为）。
- 在**受管集群**上，`Multiwfn` 作为**交互式重负载**仍受 `POLICY-LOGIN-NODE-COMPUTE`
  精神约束：应经 `bash convert.sh`（脚本封装）或 `srun` 提交，不要在登录节点开长任务。

---

## 7.2 受管工具资产登记表（Managed Tool Assets）

瀚海22 上已部署 Multiwfn 与一份**久经使用、关键词完善、适配大批量计算**的
ORCA 输入模板。该组合是接力流程的**首选资产**，理由：

1. 模板经大量实际计算验证，关键词体系（级别/收敛/资源/输出控制）成熟；
2. 与集群 ORCA 版本、module 环境、调度配置相容；
3. 统一使用可避免"每人一份模板"导致的不可复现与方法学漂移。

**策略**：优先使用瀚海22 上的**原位资产**；确需定制时经 `resolve_asset(override=...)`
显式覆盖（来源标记为 `override`，调用方须留痕）。若要在组内服务器
（`__SERVER_IP__`）跑同一流程，应把该资产**拷贝固定**到同一相对路径，
以保持两侧行为一致——见 `asset_sync_hint()`。

| 资产名 | 默认远端路径 |
|--------|--------------|
| `multiwfn` | `~/software/Multiwfn_3.8_dev_bin_Linux_noGUI/Multiwfn` |
| `orca_template` | `~/software/Multiwfn_3.8_dev_bin_Linux_noGUI/template.inp` |

> 注：路径以 `~` 书写由远端 shell 展开，避免硬编码用户名。
> 若集群实际路径不同，改 `_MANAGED_TOOL_ASSETS` 并补测试，不要在脚本里硬写。

---

## 7.3 转换后强制复核（P0）

Multiwfn 的电荷/自旋多重度是**从源波函数自动继承**的（作者原文见 sobereva.com/490：
"原本波函数对应的电荷和自旋多重度是多少，则产生的ORCA输入文件里的值也是多少"）。

**继承正确 ≠ 研究意图正确。** Multiwfn 只做忠实传递，无法判断源 Gaussian 是否
收敛到期望的电子态。因此**每次转换后必须由 agent 复核**，逐条留痕：

| # | 检查项 | 判据 |
|---|--------|------|
| 1 | `* xyz` 后两数顺序是否为 `<charge> <mult>` | 顺序正确 |
| 2 | 与源 fchk 的 `Charge`/`Multiplicity` 是否一致 | 数值相同 |
| 3 | **与本次研究意图是否一致** | 人工判断（P0） |
| 4 | 多重度与电子数奇偶性是否自洽 | 奇偶匹配 |
| 5 | 开壳层体系：源波函数是否为期望自旋态（查 ⟨S²⟩） | 人工判断 |
| 6 | 阴离子/激发态：ORCA 侧基组（弥散/辅助基组）是否匹配 | 方法学一致 |

**尤其对开壳层与阴离子体系（如 CnH⁻ 系列）第 3、5、6 项不可省。**
`template.inp` 中的资源行（nprocs/maxcore）也须与目标集群核对，但该问题
优先级低于电荷/多重度意图问题——**复核的重心在意图，不在资源**。


---

## 8. 关于"Agent 提交"的说明

中心**没有专门针对 agent/自动化的条款**。这意味着：

1. **不存在豁免**：agent 提交与人工提交适用同一套规定，不因"是程序在跑"而放宽。
2. **归责主体是人**：账号所有者对账号下的一切操作负责。Agent 的越权行为
   在管理层面等同于用户的越权行为。
3. **故 harness 是必需而非可选**：既然没有针对自动化的特别条款，防止 agent
   越权的唯一可靠手段就是把规定**固化为代码级硬约束**——即本目录对应的
   `guard.py`。把规定写成文档并寄望模型"读懂并遵守"不构成保证。

---

## 9. 逃生舱口与例外

以下情形需**人工显式**操作，不保留隐式绕过路径：

- **自建镜像/课题组内部节点**：若某 host 实为无调度器的自建机，配置
  `server_class = workstation`，或用 `--server-class workstation` 临时覆盖。
  这是有意设计的、需要用户明示的动作。
- **需要新增白名单程序**（如某自研计算程序）：修改 `guard.py` 的
  `_ALLOWED_LEADING_COMMANDS` 并补测试，而非在命令里绕。
- **需要放宽配额阈值**：修改 `QuotaPolicy`，并记录理由。

---

## 10. 维护提示

- 中心规定更新时，同步修订本文件与 `guard.py` 中对应规则的 `authority` 字段。
- 新增受管超算：在 `guard.py` 的 `_KNOWN_MANAGED_HOSTS` 登记 host 指纹，
  并在此文件补充其条款出处。
- 规则变更必须补 `tests/test_guard.py` 用例——**无测试的策略等于无策略**。
