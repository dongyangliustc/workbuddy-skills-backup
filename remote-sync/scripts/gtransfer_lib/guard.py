"""guard.py — 合规策略引擎（hard-enforcement harness）。

本模块把「中国科大超算中心对作业提交的规定」固化为可执行策略，在 I/O 之前
拦截越权行为。设计原则：

1. **硬阻断**：任何违规抛 `PolicyViolation`，调用方不得吞掉后继续。
   无参数可绕过；策略判定优先于任何 I/O。
2. **策略来源可追溯**：每条规则标注官方依据条款（依据见 references/ustc-policy.md）。
3. **服务器类型驱动**：不同服务器（受管集群 / 商业云超算 / 课题组工作站）
   适用不同策略档，由 `ServerClass` 决定，而非由调用方临时指定。
   受管集群与商业云的**约束强度一致**（强制 slurm、禁 direct、命令白名单），
   差异在权威依据文案与配额默认值 —— 由 `_POLICY_SOURCES` 与 profile 级配额
   覆盖表达（2026-10-14 引入，接入 HPCCube 时解耦）。
4. **不与传输/执行耦合**：只做判定与查询，实际 I/O 仍在 transport/executor。

官方依据（中国科大超级计算中心，核实日 2026-09-11）：

- 用户手册（2026-05-30版）：登录节点"禁止在此节点上不通过作业调度系统
  直接运行作业"；"所有需要运行的作业均必须通过作业提交命令提交"。
- 用户手册：默认磁盘配额（瀚海22 记 500GB / FAQ 记 50GB，以登录实测为准）；
  "用户帐号的目录文件夹下，不得上传保存任何与科研计算无关的文件"；
  "不提供任何文件的备份或恢复服务"。
- FAQ 基本信息：单用户帐号的磁盘空间、节点类型/数量、队列、运行中作业数、
  排队中作业数均有限制。
- 网络安全说明（2026-03-26）：默认仅校园网访问；默认仅开放 SSH 端口，
  其他端口需发邮件申请。
- 数据安全通知（2026-03-18）：严禁处理/存储国家秘密、工作秘密、商业秘密
  及未公开的敏感科研数据。
"""
from __future__ import annotations

import fnmatch
import math
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .config import ServerProfile, TransferConfig


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class PolicyViolation(Exception):
    """合规策略违规。**调用方不得捕获后继续执行 I/O**。

    attributes:
        rule_id:  规则标识（便于测试与审计）
        authority: 官方依据出处
        detail:   具体说明
        remedy:   合规的替代做法
    """

    def __init__(self, rule_id: str, detail: str, authority: str = "", remedy: str = ""):
        self.rule_id = rule_id
        self.detail = detail
        self.authority = authority
        self.remedy = remedy
        parts = [f"[{rule_id}] {detail}"]
        if authority:
            parts.append(f"依据: {authority}")
        if remedy:
            parts.append(f"合规做法: {remedy}")
        super().__init__("\n  ".join(parts))


# ---------------------------------------------------------------------------
# 服务器分类
# ---------------------------------------------------------------------------

class ServerClass:
    """服务器类别常量。"""
    MANAGED_CLUSTER = "managed_cluster"        # 受管超算集群（如 USTC 瀚海系列）
    COMMERCIAL_CLUSTER = "commercial_cluster"  # 商业云超算平台（如 HPCCube）
    WORKSTATION = "workstation"                # 课题组自有工作站（无调度器）


#: 已知受管超算集群的 host 指纹 → 类别。
#: 用 host 判断而非 profile 名，避免用户改名绕过策略。
_KNOWN_MANAGED_HOSTS: dict[str, str] = {
    "__SERVER_IP__": "USTC 瀚海22（中国科大超级计算中心）",
    "__SERVER_IP__": "USTC 瀚海22（中国科大超级计算中心，备选登录节点）",
}

#: 已知商业云超算平台的 host 指纹 → 类别（2026-10-14 引入）。
#: 商业云与受管集群的**约束强度相同**（强制 slurm、禁 direct、命令白名单），
#: 差异仅在权威依据与配额默认值，故与 _KNOWN_MANAGED_HOSTS 分开登记。
_KNOWN_COMMERCIAL_HOSTS: dict[str, str] = {
    "wuzh02.hpccube.com": "HPCCube 云超算（wuzh02 集群）",
}


def _class_label(profile: ServerProfile) -> str:
    """给出用于提示文案的服务器标识。"""
    return (_KNOWN_MANAGED_HOSTS.get(profile.host)
            or _KNOWN_COMMERCIAL_HOSTS.get(profile.host)
            or profile.host)


def classify_server(profile: ServerProfile) -> tuple[str, str]:
    """判定服务器类别，返回 (类别, 说明)。

    判定顺序：
    1. 配置显式声明 `server_class`（受管/商业云仍需满足 direct 禁用等硬约束）；
    2. host 命中已知受管超算指纹 → MANAGED_CLUSTER；
    3. host 命中已知商业云指纹 → COMMERCIAL_CLUSTER；
    4. 否则视为课题组工作站。
    """
    declared = (profile.extra_class or "").strip().lower()
    if declared in (ServerClass.MANAGED_CLUSTER, ServerClass.COMMERCIAL_CLUSTER,
                    ServerClass.WORKSTATION):
        label = _class_label(profile)
        if declared == ServerClass.MANAGED_CLUSTER:
            return ServerClass.MANAGED_CLUSTER, f"配置声明为受管集群（{label}）"
        if declared == ServerClass.COMMERCIAL_CLUSTER:
            return ServerClass.COMMERCIAL_CLUSTER, f"配置声明为商业云超算（{label}）"
        return ServerClass.WORKSTATION, f"配置声明为工作站（{profile.host}）"

    if profile.host in _KNOWN_MANAGED_HOSTS:
        return ServerClass.MANAGED_CLUSTER, _KNOWN_MANAGED_HOSTS[profile.host]

    if profile.host in _KNOWN_COMMERCIAL_HOSTS:
        return ServerClass.COMMERCIAL_CLUSTER, _KNOWN_COMMERCIAL_HOSTS[profile.host]

    return ServerClass.WORKSTATION, f"未识别为受管集群（{profile.host}）"


# ---------------------------------------------------------------------------
# 策略依据来源（按服务器类别区分权威出处）
# ---------------------------------------------------------------------------
#
# 设计动因（2026-10-14）：商业云超算与高校超算的合规约束强度一致，但**权威
# 出处不同**。此前策略文案硬编码"中心用户手册"，直接用于商业云会把依据写错，
# 审计链失真。此处按类别给出各自的出处与兜底条款，规则本体（七类）共用。

_POLICY_SOURCES: dict[str, dict[str, str]] = {
    ServerClass.MANAGED_CLUSTER: {
        "name": "中国科大超算中心用户手册",
        "login_node": "中心用户手册：登录节点『禁止在此节点上不通过作业调度系统直接运行作业』",
        "disk": "中心规定：磁盘配额有限，且不提供备份/恢复服务",
        "workdir": "中心规定：用户账号目录不得上传与科研计算无关的文件",
        "command": "中心规定：作业须经调度系统提交，禁止在受管集群执行任意程序",
        "concurrency": "中心 FAQ：运行中/排队中作业数量均有限制",
        "walltime": "中心 FAQ：QoS 对单作业时长（QOSTimeLimit）有限制",
        "cleanup": "中心规定：数据须及时回传本地并清理",
    },
    ServerClass.COMMERCIAL_CLUSTER: {
        "name": "商业算力平台服务规范",
        "login_node": "商业算力平台规范：登录节点仅供作业提交与轻量操作，"
                      "计算负载须经调度系统提交至计算节点",
        "disk": "商业算力平台规范：家目录配额有限，超配额将导致作业写入失败或中断",
        "workdir": "商业算力平台规范：工作目录仅用于本课题计算数据",
        "command": "商业算力平台规范：计算作业须经调度系统提交，"
                   "禁止在登录节点执行计算程序或任意二进制",
        "concurrency": "商业算力平台规范：并发作业数与排队数由套餐配额约束",
        "walltime": "商业算力平台规范：单作业墙钟受队列上限约束",
        "cleanup": "商业算力平台规范：数据须及时回传本地并清理，避免占用配额",
    },
}


def _policy_source(server_class: str) -> dict[str, str]:
    """取该类别的策略出处表；未登记类别返回空表（不阻断判定）。"""
    return _POLICY_SOURCES.get(server_class, {})


# ---------------------------------------------------------------------------
# 受管工具资产登记表（Managed Tool Assets）
# ---------------------------------------------------------------------------
#
# 背景（DY 指示，2026-09-11）：
# 瀚海22 上已部署 Multiwfn 与一份**久经使用、关键词完善、适配大批量计算**的
# ORCA 输入模板。该组合是 Gaussian→ORCA 接力流程的**首选资产**，原因：
#   1) 模板经过大量实际计算验证，关键词组合（级别/收敛/资源/输出控制）已成体系；
#   2) 与集群 ORCA 版本、模块环境、调度配置相容；
#   3) 统一使用可避免"每人一份模板"导致的不可复现与方法学漂移。
#
# 因此策略为：**优先直接使用瀚海22 上的原位资产**；仅当确有定制需求时，
# 才以 `--multiwfn` / `--orca-template` 显式覆盖（覆盖须留痕）。
# 若需在组内服务器上跑同一流程，应把该资产**拷贝固定**到组内机同一相对路径，
# 以保持两侧行为一致（见 `asset_sync_hint()`）。
#
# 注意：路径以 `~` 相对家目录书写，由远端 shell 展开，避免硬编码用户名。

#: 受管集群上的首选工具链资产（键为资产名，值为默认远端路径）。
_MANAGED_TOOL_ASSETS: dict[str, str] = {
    "multiwfn": "~/software/Multiwfn_3.8_dev_bin_Linux_noGUI/Multiwfn",
    "orca_template": "~/software/Multiwfn_3.8_dev_bin_Linux_noGUI/template.inp",
}


def managed_tool_assets() -> dict[str, str]:
    """返回受管集群首选工具链资产的 {资产名: 默认远端路径} 副本。"""
    return dict(_MANAGED_TOOL_ASSETS)


def resolve_asset(name: str, override: str | None = None) -> tuple[str, str]:
    """解析资产路径，返回 (路径, 来源说明)。

    `override` 非空时使用覆盖值，来源标记为 `override`（调用方应留痕告警）；
    否则回落到登记表默认值，来源标记为 `managed_default`。
    """
    key = name.strip().lower()
    if key not in _MANAGED_TOOL_ASSETS:
        raise KeyError(f"未登记的受管工具资产: {name!r}；"
                       f"可选: {sorted(_MANAGED_TOOL_ASSETS)}")
    if override:
        return override, "override"
    return _MANAGED_TOOL_ASSETS[key], "managed_default"


def asset_sync_hint(name: str, dest_host: str) -> str:
    """给出去往非受管主机时"拷贝固定资产"的操作提示（仅提示，不自动执行）。"""
    path, _ = resolve_asset(name)
    return (f"将 {dest_host} 上的流程与瀚海22 对齐，建议把 {path} "
            f"拷贝到 {dest_host} 的同一相对路径并固定，避免两侧模板漂移")


# ---------------------------------------------------------------------------
# 命令白名单 / 黑名单
# ---------------------------------------------------------------------------

#: 禁止出现在远端命令中的模式 → (规则id, 说明, 依据, 合规做法)
#: 这些模式一旦执行即构成越权或破坏性操作。
_FORBIDDEN_COMMAND_PATTERNS: list[tuple[str, str, str, str, str]] = [
    (
        "POLICY-DESTRUCTIVE-RM",
        r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*[rR])\b",
        "禁止递归强制删除（rm -rf），该操作可不可逆破坏服务器文件",
        "远端工作目录由 gtransfer 按 keep_remote 配置自行清理",
        "",
    ),
    (
        "POLICY-DESTRUCTIVE-RM-HOME",
        r"\brm\s+[^\n]*(\$HOME|~|/home/\S*)\s*($|;)",
        "禁止删除 HOME 或家目录内容",
        "远端工作目录由 gtransfer 按 keep_remote 配置自行清理",
        "",
    ),
    (
        "POLICY-SUDO",
        r"\bsudo\b|\bsu\s+-",
        "禁止提权操作（超算用户无 root 权限，且属安全违规）",
        "如需软件环境，使用 module load 或 conda activate",
        "",
    ),
    (
        "POLICY-LOGIN-NODE-COMPUTE",
        r"(^|[\s;&|])(g16|g09|g03)\s*<",
        "禁止在登录节点直接运行 Gaussian（不走调度器占用登录节点 CPU）",
        "do not run compute on login node",
        "使用 --mode slurm 经 sbatch 提交，由调度器分配计算节点",
    ),
    (
        "POLICY-LOGIN-NODE-COMPUTE",
        r"(^|[\s;&|])(orca|qchem|vasp_std|vasp_gam|vasp_ncl|lammps|cp2k\.popt|pw\.x|gmx)\b",
        "禁止在登录节点直接运行计算程序",
        "do not run compute on login node",
        "使用 --mode slurm 经 sbatch 提交",
    ),
    (
        "POLICY-NESTED-SBATCH",
        r"\bsbatch\b",
        "禁止在命令中嵌套 sbatch（会导致作业套作业，绕过调度器资源管理）",
        "gtransfer 自身负责 sbatch 提交，无需在 command 中再提交",
        "",
    ),
    (
        "POLICY-CRON-SYSTEMD",
        r"\bcrontab\b|\bsystemctl\b|\bservice\s+\w+\s+(start|stop|restart)\b",
        "禁止注册计划任务或操作系统服务（属未申请的服务端变更）",
        "本地侧用 WorkBuddy automation 调度，远端每次显式提交",
        "",
    ),
    (
        "POLICY-NETWORK-EGRESS",
        r"\b(curl|wget|nc|ncat|ssh\s+\S+@)\b",
        "禁止从登录节点主动外联（网段默认仅校园网访问，外联属未授权行为）",
        "需要的输入数据请先上传到工作目录",
        "",
    ),
    (
        "POLICY-PERM-ESCALATION",
        r"\bchmod\s+777\b|\bchmod\s+-R\b",
        "禁止放开权限或递归改权（影响其他用户与数据安全）",
        "文件权限保持默认即可",
        "",
    ),
    (
        "POLICY-SECRETS-EXFIL",
        r"(/etc/passwd|/etc/shadow|\.ssh/id_|authorized_keys)",
        "禁止读取系统凭据文件",
        "",
        "",
    ),
]

#: 允许作为 command 起首的可执行程序白名单（正则，匹配命令第一个 token）。
#: 白名单与黑名单同时生效，两者命中其一即拦截。
#: 仅在 compute-restricted 类别（受管集群/商业云）上强制。
#:
#: 关于 Multiwfn / orca_2mkl 的说明（2026-09-11 加入，依据 DY 明确指示）：
#: 二者是**格式转换工具**，不产生计算负载，且是 Gaussian→ORCA 接力流程的必需环节。
#: 仍受黑名单约束——若与 POLICY-LOGIN-NODE-COMPUTE 模式冲突（如裸 `Multiwfn` 在
#: 受管集群登录节点被识别为交互式重负载），黑名单优先。
#: 实践中在受管集群上应经 `bash xxx.sh` / `srun` 调用（见 references/ustc-policy.md）。
#:
#: 关于 qchem（2026-10-14 加入）：Q-Chem 可执行名本身在白名单内，便于经
#: `srun`/作业脚本调用；但**裸 qchem 出现在登录节点命令中仍被黑名单拦截**
#: （POLICY-LOGIN-NODE-COMPUTE），二者协同而非冲突。
_ALLOWED_LEADING_COMMANDS = re.compile(
    r"^(python[0-9.]*|mpirun|mpiexec|srun|bash|sh|\./[\w./-]+|/[\w./-]+|"
    r"module|source|export|conda|mkdir|cp|mv|ls|cat|echo|test|dirname|basename|"
    r"find|grep|awk|sed|formchk|unfchk|"
    r"Multiwfn|multiwfn|orca_2mkl|orca_2mkl_\w+|qchem|qchem_\w+)\b"
)


# ---------------------------------------------------------------------------
# 策略配置
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QuotaPolicy:
    """配额与并发闸门阈值（保守默认，低于平台上限以留余量）。"""
    max_concurrent_jobs: int = 8      # 运行中作业数上限（自行收窄）
    max_queued_jobs: int = 16         # 排队中作业数上限
    max_cpus_per_job: int = 64        # 单作业 CPU 核数上限
    max_walltime_hours: float = 72.0  # 单作业最长墙钟时间
    min_disk_free_gb: float = 5.0     # 提交前要求的最小磁盘余量


def quota_from_profile(profile: ServerProfile,
                       base: Optional[QuotaPolicy] = None) -> QuotaPolicy:
    """按 profile 级覆盖构造 QuotaPolicy（未覆盖字段沿用 base 默认）。

    动因（2026-10-14）：不同平台的紧约束不同 —— 超算受 QoS 限制，
    商业云受**家目录配额**限制（HPCCube 实测 50GB），同一套阈值不适用。
    覆盖值为 0 / 0.0 时视为"未覆盖"。
    """
    q = base or QuotaPolicy()
    return QuotaPolicy(
        max_concurrent_jobs=(profile.max_concurrent_jobs
                             or q.max_concurrent_jobs),
        max_queued_jobs=(profile.max_queued_jobs or q.max_queued_jobs),
        max_cpus_per_job=(profile.max_cpus_per_job or q.max_cpus_per_job),
        max_walltime_hours=(profile.max_walltime_hours
                            or q.max_walltime_hours),
        min_disk_free_gb=(profile.min_disk_free_gb or q.min_disk_free_gb),
    )


@dataclass(frozen=True)
class PolicyDecision:
    """一次策略评估的结果（用于审计与输出）。"""
    server_class: str
    server_label: str
    checks_passed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    policy_source: str = ""      # 生效策略的权威出处名称（按类别区分）

    def to_dict(self) -> dict:
        return {
            "server_class": self.server_class,
            "server_label": self.server_label,
            "policy_source": self.policy_source,
            "checks_passed": self.checks_passed,
            "warnings": self.warnings,
        }

# ---------------------------------------------------------------------------
# 策略引擎
# ---------------------------------------------------------------------------

class PolicyGuard:
    """合规策略引擎。所有方法为判定、无副作用、无 I/O。"""

    def __init__(self, cfg: TransferConfig, quota: Optional[QuotaPolicy] = None):
        self._cfg = cfg
        self._profile = cfg.server
        self._server_class, self._server_label = classify_server(self._profile)
        # 配额：显式传入 > profile 级覆盖 > 全局默认
        self._quota = quota or quota_from_profile(self._profile)
        self._source = _policy_source(self._server_class)

    # -- 属性 -----------------------------------------------------------

    @property
    def server_class(self) -> str:
        return self._server_class

    @property
    def server_label(self) -> str:
        return self._server_label

    @property
    def quota(self) -> QuotaPolicy:
        return self._quota

    @property
    def policy_source_name(self) -> str:
        """当前生效策略的权威出处名称（未登记类别返回空串）。"""
        return self._source.get("name", "")

    def _authority(self, key: str, fallback: str = "") -> str:
        return self._source.get(key, fallback)

    def is_managed_cluster(self) -> bool:
        return self._server_class == ServerClass.MANAGED_CLUSTER

    def is_commercial_cluster(self) -> bool:
        return self._server_class == ServerClass.COMMERCIAL_CLUSTER

    def is_compute_restricted(self) -> bool:
        """是否为"需经调度系统提交、禁登录节点计算"的类别。

        受管超算与商业云超算约束强度一致（强制 slurm、禁 direct、命令白名单），
        仅权威文案与配额默认不同，故以本属性统一驱动规则判定。
        """
        return self._server_class in (ServerClass.MANAGED_CLUSTER,
                                      ServerClass.COMMERCIAL_CLUSTER)

    # -- 规则 1：执行模式 -----------------------------------------------

    def check_execution_mode(self, mode: str) -> str:
        """校验执行模式合法性。

        compute-restricted 类别**强制 slurm**：登录节点直接运行作业为规范所禁，
        direct 模式在架构上就是"登录节点前台跑计算"，故硬阻断。
        """
        if mode not in ("direct", "slurm"):
            raise PolicyViolation(
                "POLICY-MODE-INVALID",
                f"未知执行模式: {mode!r}",
                remedy="使用 direct 或 slurm",
            )
        if self.is_compute_restricted() and mode == "direct":
            raise PolicyViolation(
                "POLICY-DIRECT-FORBIDDEN",
                f"{self._server_label} 为 {self._server_class}，禁止 direct 模式"
                f"（该模式会在登录节点直接运行计算程序）",
                authority=self._authority("login_node"),
                remedy="改用 --mode slurm，由 sbatch 提交至计算节点",
            )
        return mode

    # -- 规则 2：远端路径 -----------------------------------------------

    def check_remote_path(self, remote: str) -> str:
        """校验远端路径落在 remote_work_root 内（防目录穿越）。"""
        root = self._profile.remote_work_root
        rp = posixpath.normpath(remote.replace("\\", "/"))
        root_norm = posixpath.normpath(root)

        def _expand(p: str) -> str:
            if p == "~":
                return f"/home/{self._profile.user}"
            if p.startswith("~/"):
                return f"/home/{self._profile.user}/{p[2:]}"
            return p

        rp_x, root_x = _expand(rp), _expand(root_norm)
        inside = (
            rp == root_norm
            or rp_x == root_x
            or rp_x.startswith(root_x + "/")
            or rp.startswith(root_norm + "/")
        )
        if not inside:
            raise PolicyViolation(
                "POLICY-PATH-ESCAPE",
                f"远端路径越界: {rp} 不在 {root} 内",
                authority=self._authority("workdir"),
                remedy=f"将 --remote 置于 {root} 之下",
            )
        return rp

    # -- 规则 3：命令内容 -----------------------------------------------

    def check_command(self, command: Optional[str]) -> Optional[str]:
        """校验通用命令模式的内容。空命令返回 None（表示走求解器默认流程）。

        双重防线：黑名单（明令禁止的破坏性/越权操作）+ 白名单（起首程序须可识别）。
        白名单仅在 compute-restricted 类别上强制。
        """
        if not command or not command.strip():
            return None
        cmd = command.strip()

        # 仅在 compute-restricted 类别上强制执行的规则（工作站豁免）
        restricted_only = {"POLICY-LOGIN-NODE-COMPUTE", "POLICY-NETWORK-EGRESS"}

        for rule_id, pattern, detail, _authority, remedy in _FORBIDDEN_COMMAND_PATTERNS:
            if re.search(pattern, cmd, flags=re.IGNORECASE):
                if rule_id in restricted_only and not self.is_compute_restricted():
                    continue
                author = self._authority(
                    "command" if rule_id != "POLICY-LOGIN-NODE-COMPUTE"
                    else "login_node"
                )
                raise PolicyViolation(
                    rule_id,
                    f"{detail}。命中片段: {cmd[:120]}",
                    authority=author or "平台使用规范 / 数据安全通知 / 网络安全说明",
                    remedy=remedy or "移除该操作后重新提交",
                )

        # 白名单：起首 token 必须可识别，防止任意二进制执行
        if self.is_compute_restricted():
            lead = cmd.split("&&")[0].split(";")[0].strip()
            if not _ALLOWED_LEADING_COMMANDS.match(lead):
                first = lead.split()[0] if lead.split() else ""
                raise PolicyViolation(
                    "POLICY-COMMAND-NOT-ALLOWLISTED",
                    f"命令起首程序不在白名单内: {first!r}",
                    authority=self._authority("command"),
                    remedy="使用 python / mpirun / srun 等白名单程序，"
                           "或申请开通对应权限",
                )
        return cmd

    # -- 规则 4：作业参数 -----------------------------------------------

    def check_job_resources(
        self,
        cpus_per_task: int,
        walltime: str,
        nodes: int = 1,
        ntasks: int = 1,
    ) -> None:
        """校验单作业资源规模不超过自设上限（避免触碰中心 QoS 硬限）。"""
        total_cpus = max(1, cpus_per_task) * max(1, ntasks) * max(1, nodes)
        if total_cpus > self._quota.max_cpus_per_job:
            raise PolicyViolation(
                "POLICY-RESOURCE-EXCEEDED",
                f"单作业申请 {total_cpus} 核，超过自设上限 {self._quota.max_cpus_per_job} 核",
                authority=self._authority("concurrency"),
                remedy="降低 --cpus-per-task/--ntasks，或将任务拆分为作业阵列",
            )
        hours = _parse_walltime_hours(walltime)
        if hours is not None and hours > self._quota.max_walltime_hours:
            raise PolicyViolation(
                "POLICY-WALLTIME-EXCEEDED",
                f"作业墙钟 {walltime}（{hours}h）超过自设上限 {self._quota.max_walltime_hours}h",
                authority=self._authority("walltime"),
                remedy="缩短 --walltime，或申请独占队列",
            )

    # -- 规则 4b：单核内存上限（2026-10-14 引入）--------------------------

    def check_mem_per_cpu(
        self,
        mem: Optional[str] = None,
        cpus_per_task: int = 1,
        nodes: int = 1,
        ntasks: int = 1,
    ) -> None:
        """校验「申请内存 / 申请 CPU」不超过平台单核内存上限。

        动因：部分平台（HPCCube 实测 `DefMemPerCPU=3944MB`）在 sbatch 侧硬校验
        该比值，超限直接拒绝提交并返回
        "too much memory was requested relative to the number of CPUs"。
        该拒绝发生在**远端**，用户需往返一次才知道原因；本地预检可把远端拒绝
        变成即时、带定量修正建议的报错。

        阈值由 profile 的 `max_mem_per_cpu_mb` 提供（0 = 不检查，保持既有行为）。
        """
        limit = self._profile.max_mem_per_cpu_mb
        if not limit or not mem:
            return
        mem_mb = _parse_mem_mb(mem)
        if mem_mb is None:
            return  # 无法解析则不阻断（交由 Slurm 自行判断）
        total_cpus = max(1, cpus_per_task) * max(1, ntasks) * max(1, nodes)
        ratio = mem_mb / total_cpus
        if ratio <= limit:
            return

        need_cpus = math.ceil(mem_mb / limit)
        raise PolicyViolation(
            "POLICY-MEM-PER-CPU-EXCEEDED",
            f"申请内存 {mem} 配 {total_cpus} 核，单核 {ratio:.0f}MB 超过平台上限 "
            f"{limit:.0f}MB（内存:CPU 比越界，sbatch 将直接拒绝）",
            authority=self._authority("concurrency"),
            remedy=(f"把 --cpus-per-task 提到 {need_cpus}（或降低 --mem），"
                    f"使 内存/CPU ≤ {limit:.0f}MB；"
                    f"若 {need_cpus} 核超过单节点核数，需同时增加 --nodes"),
        )

    # -- 规则 5：并发闸门 -----------------------------------------------

    def check_concurrency(self, running_jobs: int, queued_jobs: int) -> None:
        """提交前并发闸门：运行中/排队中作业数必须留有余量。

        数值由调用方通过远端查询获得（见 build_quota_probe_commands）。
        """
        if running_jobs >= self._quota.max_concurrent_jobs:
            raise PolicyViolation(
                "POLICY-CONCURRENCY-LIMIT",
                f"当前运行中作业 {running_jobs} 个，已达自设上限 "
                f"{self._quota.max_concurrent_jobs} 个",
                authority=self._authority("concurrency"),
                remedy="等待在途作业完成后提交，或将批量任务合并为作业阵列",
            )
        if queued_jobs >= self._quota.max_queued_jobs:
            raise PolicyViolation(
                "POLICY-QUEUE-LIMIT",
                f"当前排队中作业 {queued_jobs} 个，已达自设上限 "
                f"{self._quota.max_queued_jobs} 个",
                authority=self._authority("concurrency"),
                remedy="减少批量提交规模，避免作业积压触顶被拒",
            )

    # -- 规则 6：回传文件 -----------------------------------------------

    def check_download_glob(self, glob_pattern: str) -> str:
        """校验回传 glob：拒绝可能拉取系统文件或越界目录的模式。"""
        if not glob_pattern or not glob_pattern.strip():
            raise PolicyViolation(
                "POLICY-GLOB-EMPTY",
                "回传 glob 为空",
                remedy="指定如 '*.log *.chk' 的模式",
            )
        for p in glob_pattern.split():
            if p.startswith("/") or ".." in p or "/" in p:
                raise PolicyViolation(
                    "POLICY-GLOB-ESCAPE",
                    f"回传 glob 含越界路径: {p!r}",
                    authority=self._authority("cleanup"),
                    remedy="仅使用文件名模式（如 *.log），目录限定在工作目录内",
                )
        return glob_pattern

    # -- 规则 7：上传内容 -----------------------------------------------
    #: 与科研计算无关的文件模式（中心/平台规范均禁止在工作目录堆积）
    _IRRELEVANT_PATTERNS: tuple[str, ...] = (
        ".git*", "*.tmp", "*.log.old", "__pycache__*", "*.pyc",
        ".DS_Store", "Thumbs.db", "*~", "*.swp", ".vscode", ".idea",
        "node_modules", ".venv", "venv", ".pytest_cache", ".mypy_cache",
    )

    def check_upload_content(self, local_names: Iterable[str]) -> list[str]:
        """校验上传内容不含与科研计算无关的文件（返回需告警的文件名）。

        平台规定"用户账号目录不得上传保存与科研计算无关的文件"，发现即警告。
        Agent 的工作文件（缓存/会话/仓库）是典型触雷点。
        此处不阻断（可能误伤合法文件），但返回告警列表供调用方拒绝或提示。
        """
        irrelevant: list[str] = []
        for name in local_names:
            base = posixpath.basename(name.replace("\\", "/"))
            for pat in self._IRRELEVANT_PATTERNS:
                if fnmatch.fnmatch(base, pat):
                    irrelevant.append(name)
                    break
        return irrelevant

    # -- 汇总：提交前完整校验 -------------------------------------------

    def preflight(
        self,
        mode: str,
        remote: str,
        command: Optional[str] = None,
        cpus_per_task: int = 16,
        walltime: str = "24:00:00",
        nodes: int = 1,
        ntasks: int = 1,
        download_glob: Optional[str] = None,
        mem: Optional[str] = None,
    ) -> PolicyDecision:
        """提交前完整策略校验。任一违规即抛 PolicyViolation（硬阻断）。"""
        decision = PolicyDecision(
            server_class=self._server_class,
            server_label=self._server_label,
            policy_source=self.policy_source_name,
        )

        self.check_execution_mode(mode)
        decision.checks_passed.append("execution_mode")

        self.check_remote_path(remote)
        decision.checks_passed.append("remote_path")

        self.check_command(command)
        decision.checks_passed.append("command_allowlist")

        self.check_job_resources(cpus_per_task, walltime, nodes, ntasks)
        decision.checks_passed.append("job_resources")

        self.check_mem_per_cpu(mem, cpus_per_task, nodes, ntasks)
        decision.checks_passed.append("mem_per_cpu")

        if download_glob:
            self.check_download_glob(download_glob)
            decision.checks_passed.append("download_glob")

        # 数据性质与数据保全：无法自动判定，转为强制提醒（写入 warnings 供输出）
        decision.warnings.append(
            "数据性质自检：严禁处理/存储国家秘密、工作秘密、商业秘密及未公开的敏感科研数据。"
            "判断责任在用户。"
        )
        decision.warnings.append(
            "数据保全：" + (
                self._authority("disk")
                or "平台不提供备份或恢复服务"
            ) + "，任务完成后请及时回传并清理远端数据。"
        )
        return decision


# ---------------------------------------------------------------------------
# 远端配额探测命令（供 executor/CLI 在真实连接上执行）
# ---------------------------------------------------------------------------

def build_quota_probe_commands(user: str) -> dict[str, str]:
    """生成提交前自检所需的远端查询命令（全部只读）。

    命令集对齐 Slurm 标准查询：squeue（并发/排队）、df（余量）、
    sacctmgr（账户级 QoS 限制，accounting 未启用时静默失败）。

    `disk_usage` 以 **$HOME** 为目标（2026-10-14 修正）：商业云多为配额式
    文件系统，接入点目录返回的是整个文件系统容量，$HOME 才反映用户可用配额
    （HPCCube 实测：/work 整盘 35PB，而用户配额 50GB）。
    """
    q_user = shlex.quote(user)
    return {
        "running_jobs": f"squeue -u {q_user} -h -t RUNNING -o '%i' 2>/dev/null | wc -l",
        "queued_jobs": f"squeue -u {q_user} -h -t PENDING -o '%i' 2>/dev/null | wc -l",
        "disk_usage": 'df -BG --output=avail "$HOME" 2>/dev/null | tail -1 | tr -dc 0-9',
        "qos_limits": (
            f"sacctmgr -n -P show assoc user={q_user} "
            "format=QOS,MaxJobs,MaxSubmitJobs,GrpTRES 2>/dev/null | head -5"
        ),
    }


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_WALLTIME_RE = re.compile(
    r"^(?:(\d+)-)?(\d{1,2}):(\d{2}):(\d{2})$"
)


def _parse_walltime_hours(walltime: str) -> Optional[float]:
    """解析 Slurm 墙钟格式（[D-]HH:MM:SS 或 MM:SS）为小时数。解析失败返回 None。"""
    wt = (walltime or "").strip()
    if not wt:
        return None
    m = _WALLTIME_RE.match(wt)
    if m:
        days = int(m.group(1) or 0)
        return days * 24 + int(m.group(2)) + int(m.group(3)) / 60 + int(m.group(4)) / 3600
    # MM:SS / 纯分钟形式
    parts = wt.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return nums[0] / 60 + nums[1] / 3600
    if len(nums) == 1:
        return nums[0] / 60
    return None


# ---------------------------------------------------------------------------
# Slurm 内存量的解析（2026-10-14 引入）
# ---------------------------------------------------------------------------

_MEM_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGTP]?)(?:B)?\s*$", re.IGNORECASE)

#: 单位 → MB 系数
_MEM_UNIT_MB: dict[str, float] = {
    "": 1.0,
    "K": 1.0 / 1024,
    "M": 1.0,
    "G": 1024.0,
    "T": 1024.0 ** 2,
    "P": 1024.0 ** 3,
}


def _parse_mem_mb(text: str) -> Optional[float]:
    """把 Slurm 内存写法（如 `7G` / `530G` / `3900M` / 纯数字=MB）解析为 MB。

    解析失败返回 None（调用方据此跳过判定，交由 Slurm 自行报错）。
    """
    m = _MEM_RE.match(text or "")
    if not m:
        return None
    return float(m.group(1)) * _MEM_UNIT_MB[m.group(2).upper()]
