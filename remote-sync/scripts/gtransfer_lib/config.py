"""config.py — remote-sync 配置解析层。

设计：模板(技能内置) + 用户配置(~/.workbuddy/transfer.conf) 双层，
用户配置优先级更高，支持 ini 覆盖与 %%ENV_VAR%% 环境变量展开。
"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServerProfile:
    """单个服务器连接配置。"""
    name: str
    host: str
    user: str
    key_file: Optional[str] = None
    password: Optional[str] = None
    totp_secret: Optional[str] = None   # TOTP 二次验证 secret（Base32），如超算系统登录
    execution_mode: str = "direct"       # "direct" | "slurm"
    remote_work_root: str = "~/gtransfer"
    gaussian_exec: str = "g16"           # 远端 Gaussian 可执行文件（可含路径，如 /opt/g16/g16）
    command: str = ""                    # 通用命令模式：非空则取代 g16 流程（如 "python main.py"）
    env_setup: str = ""                  # 远端环境设置命令（如 export GAUSS_EXEDIR=... / conda activate）
    connect_timeout: int = 15
    poll_interval: int = 10
    max_retries: int = 3
    port: int = 22
    # 服务器类别声明（compliance harness）：
    #   managed_cluster | commercial_cluster | workstation | 空=按 host 自动判定
    # 受管超算集群 / 商业云超算均强制 slurm、禁用 direct，且适用命令白名单等硬约束。
    extra_class: str = ""
    # 地址族（2026-10-14 引入，HPCCube 实测）：
    #   部分域名同时发布 AAAA 记录但 IPv6 路由不可达，SSH 客户端优先 IPv6 会
    #   直接超时（TCP 黑盒丢包）。设 ipv4 时按 A 记录解析后以字面量连接。
    #   auto（默认）= 交给系统解析，不改变既有行为。
    address_family: str = "auto"          # auto | ipv4 | ipv6
    # 求解器类型（2026-10-14 引入）：
    #   gaussian → .gjf 输入，gaussian_exec 前台/调度器运行，日志 .log
    #   qchem    → .in 输入，qchem_exec 以 `qchem in out scratch` 三参数运行，日志 .out
    solver: str = "gaussian"              # gaussian | qchem
    qchem_exec: str = "qchem"             # 远端 Q-Chem 可执行文件（可含路径）
    # profile 级配额覆盖（0 = 沿用 QuotaPolicy 全局默认）。
    # 商业云配额（如 50GB 家目录）与超算不同，需按 profile 收窄/放宽闸门。
    min_disk_free_gb: float = 0.0
    max_concurrent_jobs: int = 0
    max_queued_jobs: int = 0
    max_cpus_per_job: int = 0
    max_walltime_hours: float = 0.0
    # 单核内存上限（MB，0 = 不检查）。2026-10-14 引入。
    # 部分平台强制「申请内存 / 申请 CPU ≤ DefMemPerCPU」，超限直接被 sbatch 拒绝
    # （HPCCube 实测 DefMemPerCPU=3944MB）。本地预检可把远端拒绝变成即时可操作报错。
    max_mem_per_cpu_mb: float = 0.0
    # Slurm 作业默认参数（2026-10-14 引入；CLI 未显式指定时生效）。
    # 把平台固定口径（分区名、单核内存上限、墙钟）一次配置，避免每次重复传参、
    # 也避免沿用不适用的内置默认（如内置 --mem-per-cpu=4G 在 HPCCube 必然被拒）。
    partition: str = ""
    cpus_per_task: int = 0
    nodes: int = 0
    ntasks: int = 0
    mem: str = ""
    mem_per_cpu: str = ""
    walltime: str = ""

    @property
    def auth_mode(self) -> str:
        """认证方式：key（密钥）或 password（口令，可选 TOTP 二次验证）。

        NOTE: 属性名契约以 tests/test_config.py 与 connection.py 为准（auth_mode）。
        2026-09-11 曾短暂误改名为 auth_method 导致 SSH 连接层断裂，已回退。
        """
        if self.key_file:
            return "key"
        if self.password is not None:
            return "password"
        return "none"


@dataclass(frozen=True)
class TransferConfig:
    """一次传输任务的完整配置。"""
    server: ServerProfile
    upload_glob: str = "*.gjf"
    download_glob: str = "*.log"
    keep_remote: bool = False
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 环境变量展开
# ---------------------------------------------------------------------------

def _expand_env(value: str) -> str:
    """展开 ${VAR} / %VAR% 形式的环境变量引用（仅显式花括号/百分号形式）。

    注意：**不**支持裸 `$VAR` 形式——它易与 shell 变量（如 $PATH、$HOME）
    冲突，导致 env_setup 等字段中的远端变量被误展开为本机值。
    需在远端展开的变量请使用 `${VAR}` 或 `%VAR%` 显式标记。
    """
    import re
    pattern = re.compile(r"\$\{(\w+)\}|%(\w+)%")

    def _sub(match: re.Match) -> str:
        var = match.group(1) or match.group(2)
        return os.environ.get(var, "")

    return pattern.sub(_sub, value)


def _require_nonempty(value: str, context: str) -> str:
    """必填字段：展开后为空则报错（覆盖"未定义环境变量"与"缺字段"两类）。"""
    if not value:
        raise ConfigError(f"{context} 为必填项，且展开后不能为空")
    return value


class ConfigError(Exception):
    """配置错误（缺失字段 / 非法值 / 未解析的环境变量）。"""


# ---------------------------------------------------------------------------
# 加载逻辑
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, str] = {
    "connect_timeout": "15",
    "poll_interval": "10",
    "max_retries": "3",
    "port": "22",
    "upload_glob": "*.gjf",
    "download_glob": "*.log",
    "keep_remote": "false",
}

#: 合法地址族（auto = 交由系统解析，保持既有行为）
ADDRESS_FAMILIES: tuple[str, ...] = ("auto", "ipv4", "ipv6")

#: 合法求解器类型
SOLVERS: tuple[str, ...] = ("gaussian", "qchem")


def _resolve_config_path(explicit: Optional[str]) -> Path:
    """确定用户配置文件路径：显式 > 环境变量 > 默认。"""
    if explicit:
        return Path(explicit)
    env_path = os.environ.get("GTRANSFER_CONFIG")
    if env_path:
        return Path(env_path)
    return Path.home() / ".workbuddy" / "transfer.conf"


def _template_path() -> Path:
    """技能内置模板路径（测试可 monkeypatch）。

    本文件位于 scripts/gtransfer_lib/ 下，模板在 skill 根 references/，
    故需上三级。
    """
    return (
        Path(__file__).resolve().parent.parent.parent
        / "references" / "transfer.conf.example"
    )


def load_config(
    config_path: Optional[str] = None,
    server_name: str = "default",
) -> TransferConfig:
    """加载配置。

    规则：
    1. 若用户配置文件存在，则读取之；
    2. 不存在时回退到技能内置模板（references/transfer.conf.example）；
    3. 模板字段为默认值，用户配置覆盖同名字段。
    """
    parser = configparser.ConfigParser()
    user_file = _resolve_config_path(config_path)
    template_file = _template_path()

    # 模板先行
    if template_file.exists():
        parser.read(template_file, encoding="utf-8")
    # 用户配置覆盖
    if user_file.exists():
        parser.read(user_file, encoding="utf-8")

    server_section = f"server:{server_name}"
    transfer_section = f"transfer:{server_name}"

    if server_section not in parser:
        raise ConfigError(
            f"配置中缺少 [server:{server_name}] 段。"
            f"请检查 {user_file} 或模板 {template_file}"
        )

    def _get(section: str, key: str, default: Optional[str] = None) -> str:
        if parser.has_option(section, key):
            return _expand_env(parser.get(section, key).strip())
        if default is not None:
            return _expand_env(default)
        raise ConfigError(f"[{section}] 缺少必填字段 '{key}'")

    host = _require_nonempty(_get(server_section, "host"), f"server:{server_name}.host")
    user = _require_nonempty(_get(server_section, "user"), f"server:{server_name}.user")
    key_file_raw = _get(server_section, "key_file", default="")
    execution_mode = _get(server_section, "execution_mode", default="direct")
    remote_root = _get(server_section, "remote_work_root", default="~/gtransfer")

    if execution_mode not in ("direct", "slurm"):
        raise ConfigError(f"execution_mode 必须为 direct 或 slurm，当前: {execution_mode}")

    address_family = _get(server_section, "address_family", default="auto").strip().lower()
    if address_family not in ADDRESS_FAMILIES:
        raise ConfigError(
            f"address_family 必须为 {'/'.join(ADDRESS_FAMILIES)}，当前: {address_family}"
        )

    solver = _get(server_section, "solver", default="gaussian").strip().lower()
    if solver not in SOLVERS:
        raise ConfigError(f"solver 必须为 {'/'.join(SOLVERS)}，当前: {solver}")

    key_file: Optional[str] = None
    if key_file_raw:
        key_path = Path(key_file_raw).expanduser()
        if not key_path.exists():
            raise ConfigError(f"密钥文件不存在: {key_path}")
        key_file = str(key_path)

    # 口令认证（可选）：password = ${GTRANSFER_PASSWORD} 经环境变量注入，
    # 不强制落盘明文。未配置时使用密钥或无认证。
    password_raw = _get(server_section, "password", default="")
    password: Optional[str] = password_raw if password_raw else None

    # TOTP 二次验证（可选）：totp_secret = ${TOTP_SECRET} 为 Base32 secret。
    # 若配置，口令认证时自动拼接当前一次性验证码（password + 6位code）。
    totp_raw = _get(server_section, "totp_secret", default="").strip()
    totp_secret: Optional[str] = totp_raw if totp_raw else None

    def _get_int(section: str, key: str, default_key: str) -> int:
        raw = _get(section, key, default=_DEFAULTS[default_key])
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"[{section}].{key} 需要整数，当前: {raw}")

    def _get_bool(section: str, key: str) -> bool:
        raw = _get(section, key, default=_DEFAULTS[key]).strip().lower()
        if raw in ("true", "1", "yes", "on"):
            return True
        if raw in ("false", "0", "no", "off"):
            return False
        raise ConfigError(f"[{section}].{key} 需要布尔值，当前: {raw}")

    def _get_float_default(section: str, key: str, default: float = 0.0) -> float:
        raw = _get(section, key, default=str(default)).strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            raise ConfigError(f"[{section}].{key} 需要数值，当前: {raw}")

    profile = ServerProfile(
        name=server_name,
        host=host,
        user=user,
        key_file=key_file,
        password=password,
        totp_secret=totp_secret,
        execution_mode=execution_mode,
        remote_work_root=remote_root,
        gaussian_exec=_get(server_section, "gaussian_exec", default="g16").strip() or "g16",
        command=_get(server_section, "command", default="").strip(),
        env_setup=_get(server_section, "env_setup", default="").strip(),
        connect_timeout=_get_int(server_section, "connect_timeout", "connect_timeout"),
        poll_interval=_get_int(server_section, "poll_interval", "poll_interval"),
        max_retries=_get_int(server_section, "max_retries", "max_retries"),
        port=_get_int(server_section, "port", "port"),
        extra_class=_get(server_section, "server_class", default="").strip().lower(),
        address_family=address_family,
        solver=solver,
        qchem_exec=_get(server_section, "qchem_exec", default="qchem").strip() or "qchem",
        min_disk_free_gb=_get_float_default(server_section, "min_disk_free_gb"),
        max_concurrent_jobs=int(_get_float_default(server_section, "max_concurrent_jobs")),
        max_queued_jobs=int(_get_float_default(server_section, "max_queued_jobs")),
        max_cpus_per_job=int(_get_float_default(server_section, "max_cpus_per_job")),
        max_walltime_hours=_get_float_default(server_section, "max_walltime_hours"),
        max_mem_per_cpu_mb=_get_float_default(server_section, "max_mem_per_cpu_mb"),
        partition=_get(server_section, "partition", default="").strip(),
        cpus_per_task=int(_get_float_default(server_section, "cpus_per_task")),
        nodes=int(_get_float_default(server_section, "nodes")),
        ntasks=int(_get_float_default(server_section, "ntasks")),
        mem=_get(server_section, "mem", default="").strip(),
        mem_per_cpu=_get(server_section, "mem_per_cpu", default="").strip(),
        walltime=_get(server_section, "walltime", default="").strip(),
    )

    extra = {}
    if parser.has_section(server_section):
        for key, val in parser.items(server_section):
            if key not in ServerProfile.__dataclass_fields__ and key not in _DEFAULTS:
                extra[key] = val

    return TransferConfig(
        server=profile,
        upload_glob=_get(transfer_section, "upload_glob", default=_DEFAULTS["upload_glob"]),
        download_glob=_get(transfer_section, "download_glob", default=_DEFAULTS["download_glob"]),
        keep_remote=_get_bool(transfer_section, "keep_remote") if parser.has_section(transfer_section) else False,
        extra=extra,
    )
