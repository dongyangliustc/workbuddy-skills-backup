"""test_hpccube_extension.py — HPCCube 接入扩展的契约测试（2026-10-14）。

覆盖本次为外部商业云服务器（HPCCube wuzh02）与 Q-Chem 引入的四类扩展：

1. **连接层地址族**：`resolve_address` + `ParamikoSsh._target_host`
   —— 目标域名发布 AAAA 但 IPv6 路由不可达，须强制 IPv4。
2. **配置层**：`address_family` / `solver` / `qchem_exec` / profile 级配额覆盖
   的解析与非法值校验。
3. **执行层 Q-Chem 通道**：`.in → .out` 命名、三参数调用、QCSCRATCH 隔离、
   `--mem` 与 `--mem-per-cpu` 互斥、Q-Chem 错误识别。
4. **策略层商业云类别**：`commercial_cluster` 分类、强制 slurm、禁 direct、
   登录节点禁跑 qchem、命令白名单、权威文案与 USTC 解耦、配额按 profile 覆盖。
"""
from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gtransfer_lib.config import (  # noqa: E402
    ADDRESS_FAMILIES,
    SOLVERS,
    ConfigError,
    ServerProfile,
    TransferConfig,
    load_config,
)
from gtransfer_lib.connection import resolve_address  # noqa: E402
from gtransfer_lib.executor import (  # noqa: E402
    DirectExecutor,
    SlurmExecutor,
    create_executor,
    qchem_scratch_name,
    solver_paths,
)
from gtransfer_lib.guard import (  # noqa: E402
    PolicyGuard,
    PolicyViolation,
    QuotaPolicy,
    ServerClass,
    classify_server,
    quota_from_profile,
)
from gtransfer_lib.status import JobStatus, classify_exit, parse_qchem_error  # noqa: E402
from gtransfer_lib.transport import SftpTransfer  # noqa: E402
from fakes import FakeSSH  # noqa: E402


HPCCUBE_HOST = "wuzh02.hpccube.com"

_GIT_BASH = "C:/Program Files/Git/bin/bash.exe"





# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def make_profile(host: str = HPCCUBE_HOST, **kw) -> ServerProfile:
    base = dict(
        name="hpccube", host=host, user="dongyang",
        key_file=None, password=None, totp_secret=None,
        execution_mode="slurm", remote_work_root="~/C2nCN",
        gaussian_exec="g16", command="", env_setup="",
    )
    base.update(kw)
    return ServerProfile(**base)


def make_config(host: str = HPCCUBE_HOST, **kw) -> TransferConfig:
    return TransferConfig(server=make_profile(host, **kw))


# ---------------------------------------------------------------------------
# 1. 连接层：地址族解析
# ---------------------------------------------------------------------------

def test_resolve_address_auto_returns_host_unchanged():
    """auto（默认）不得改变既有行为——host 原样交给 paramiko 解析。"""
    assert resolve_address("example.com", 22, "auto") == "example.com"
    assert resolve_address("example.com", 22, "") == "example.com"


def test_resolve_address_ipv4_picks_a_record(monkeypatch):
    """address_family=ipv4 → 返回 A 记录字面量（HPCCube 实测 __SERVER_IP__）。"""
    seen = {}

    def fake_getaddrinfo(host, port, family=None, type=None):
        seen["family"] = family
        # 模拟同时存在 A 与 AAAA；本函数只应被以 AF_INET 调用
        assert family == socket.AF_INET
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("__SERVER_IP__", 65091))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert resolve_address(HPCCUBE_HOST, 65091, "ipv4") == "__SERVER_IP__"
    assert seen["family"] == socket.AF_INET


def test_resolve_address_ipv6_uses_aaaa(monkeypatch):
    def fake_getaddrinfo(host, port, family=None, type=None):
        assert family == socket.AF_INET6
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "",
                 ("2409:8728:4eff:2001::100", 65091, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert resolve_address(HPCCUBE_HOST, 65091, "ipv6") == "2409:8728:4eff:2001::100"


def test_resolve_address_fails_fast_when_family_unavailable(monkeypatch):
    """指定族无记录时立即报错，而不是退化成长时间超时。"""

    def fail(*a, **kw):
        raise socket.gaierror("no address for family")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(ConfigError, match="ipv4"):
        resolve_address(HPCCUBE_HOST, 65091, "ipv4")


# ---------------------------------------------------------------------------
# 2. 配置层：新字段解析与校验
# ---------------------------------------------------------------------------

def _write_cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "transfer.conf"
    p.write_text(body, encoding="utf-8")
    return p


BASE_HPCCUBE_CFG = f"""
[server:hpccube]
host = {HPCCUBE_HOST}
user = dongyang
port = 65091
password = pw
execution_mode = slurm
server_class = commercial_cluster
remote_work_root = ~/C2nCN
solver = qchem
qchem_exec = qchem
address_family = ipv4
min_disk_free_gb = 15
max_concurrent_jobs = 16
max_queued_jobs = 32

[transfer:hpccube]
upload_glob = *.in
download_glob = *.out *.fchk
keep_remote = true
"""


def test_load_hpccube_profile_new_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("gtransfer_lib.config._template_path",
                        lambda: tmp_path / "nonexistent.template")
    cfg = load_config(str(_write_cfg(tmp_path, BASE_HPCCUBE_CFG)), "hpccube")
    s = cfg.server
    assert s.address_family == "ipv4"
    assert s.solver == "qchem"
    assert s.qchem_exec == "qchem"
    assert s.extra_class == "commercial_cluster"
    assert s.min_disk_free_gb == 15.0
    assert s.max_concurrent_jobs == 16
    assert s.max_queued_jobs == 32
    assert cfg.download_glob == "*.out *.fchk"


def test_new_fields_have_safe_defaults(tmp_path, monkeypatch):
    """老配置（无新字段）必须继续可加载，且默认不改变既有行为。"""
    monkeypatch.setattr("gtransfer_lib.config._template_path",
                        lambda: tmp_path / "nonexistent.template")
    body = "[server:old]\nhost = h\nuser = u\npassword = pw\nexecution_mode = direct\n"
    cfg = load_config(str(_write_cfg(tmp_path, body)), "old")
    s = cfg.server
    assert s.address_family == "auto"
    assert s.solver == "gaussian"
    assert s.qchem_exec == "qchem"
    assert s.min_disk_free_gb == 0.0
    assert s.max_concurrent_jobs == 0


@pytest.mark.parametrize("bad", ["ipv5", "v4", "IPv4x"])
def test_invalid_address_family_rejected(tmp_path, monkeypatch, bad):
    monkeypatch.setattr("gtransfer_lib.config._template_path",
                        lambda: tmp_path / "nonexistent.template")
    body = (f"[server:x]\nhost = h\nuser = u\npassword = pw\n"
            f"execution_mode = slurm\naddress_family = {bad}\n")
    with pytest.raises(ConfigError, match="address_family"):
        load_config(str(_write_cfg(tmp_path, body)), "x")


def test_invalid_solver_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("gtransfer_lib.config._template_path",
                        lambda: tmp_path / "nonexistent.template")
    body = ("[server:x]\nhost = h\nuser = u\npassword = pw\n"
            "execution_mode = slurm\nsolver = nwchem\n")
    with pytest.raises(ConfigError, match="solver"):
        load_config(str(_write_cfg(tmp_path, body)), "x")


def test_non_numeric_quota_override_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("gtransfer_lib.config._template_path",
                        lambda: tmp_path / "nonexistent.template")
    body = ("[server:x]\nhost = h\nuser = u\npassword = pw\n"
            "execution_mode = slurm\nmin_disk_free_gb = abc\n")
    with pytest.raises(ConfigError, match="min_disk_free_gb"):
        load_config(str(_write_cfg(tmp_path, body)), "x")


def test_allowed_value_tables_are_declared():
    assert set(ADDRESS_FAMILIES) == {"auto", "ipv4", "ipv6"}
    assert set(SOLVERS) == {"gaussian", "qchem"}


# ---------------------------------------------------------------------------
# 3. 执行层：Q-Chem 通道
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expect", [
    ("C6N.in", ("C6N", "C6N.out")),
    ("C12H_OPT4.IN", ("C12H_OPT4", "C12H_OPT4.out")),
    ("run.inp", ("run", "run.out")),
])
def test_solver_paths_qchem(name, expect):
    assert solver_paths(name, "qchem") == expect


@pytest.mark.parametrize("name,expect", [
    ("water.gjf", ("water", "water.log")),
    ("noext", ("noext", "noext.log")),
])
def test_solver_paths_gaussian(name, expect):
    assert solver_paths(name, "gaussian") == expect


def test_qchem_scratch_name_sanitized():
    """Q-Chem 以该名在 $QCSCRATCH 下建目录，非法字符须归一化。"""
    assert qchem_scratch_name("C12H-OPT4_1+") == "C12H-OPT4_1_"
    assert qchem_scratch_name("a b/c") == "a_b_c"
    assert qchem_scratch_name("") == "qchem_job"


def test_slurm_script_qchem_uses_three_arg_call_and_private_scratch():
    ex = SlurmExecutor(FakeSSH(), make_config(), solver="qchem",
                       partition="wzacnormal", mem="530G", cpus_per_task=4)
    script = ex.build_script("~/C2nCN/c6n", "C6N.in")
    assert "#SBATCH --partition=wzacnormal" in script
    assert "#SBATCH --mem=530G" in script
    # --mem 与 --mem-per-cpu 不可同时出现（Slurm 会报冲突）
    assert "--mem-per-cpu" not in script
    # QCSCRATCH 必须指向本任务目录（站上默认是组共享 tmp，会互相覆盖）
    assert "export QCSCRATCH=" in script and "/scratch" in script
    # 三参数调用：qchem <in> <out> <scratchname>
    assert "qchem C6N.in C6N.out C6N" in script
    # Q-Chem 主输出应进入 gtransfer.log 供 startup_check / query 回退使用
    assert "gtransfer.log" in script


def test_slurm_script_gaussian_unchanged_shape():
    ex = SlurmExecutor(FakeSSH(), make_config(), solver="gaussian")
    script = ex.build_script("~/gt/t1", "water.gjf")
    assert "g16 < water.gjf > water.log" in script
    assert "#SBATCH --mem-per-cpu=4G" in script
    assert "GAUSS_SCRDIR" in script


def test_direct_qchem_command_shape():
    ex = DirectExecutor(FakeSSH(), make_config(execution_mode="direct"),
                        solver="qchem", env_setup="source /p/qcenv.sh")
    cmd = ex._command("~/C2nCN/j1", "C6N.in")
    assert "source /p/qcenv.sh" in cmd
    assert "qchem C6N.in C6N.out C6N" in cmd
    assert "export QCSCRATCH=" in cmd
    # 退出码经"正常收尾"校验后才回传（见 rc 容错测试）
    assert "__gt_rc=$?" in cmd
    assert "echo __GT_EXIT=$__gt_rc" in cmd


def test_create_executor_falls_back_to_profile_solver():
    """solver 未显式传入时取 profile 配置。"""
    cfg = make_config(solver="qchem", qchem_exec="/opt/qc/qchem")
    ex = create_executor(FakeSSH(), cfg)
    assert isinstance(ex, SlurmExecutor)
    assert ex._solver == "qchem"
    assert ex._qchem_exec == "/opt/qc/qchem"


def test_direct_executor_ignores_slurm_only_kwargs():
    """direct 模式不得因 CLI 统一传入 partition/mem 而 TypeError。"""
    ex = create_executor(FakeSSH(), make_config(execution_mode="direct"),
                         partition="p", mem="10G")
    assert isinstance(ex, DirectExecutor)


# -- Q-Chem 错误识别 --------------------------------------------------------

@pytest.mark.parametrize("tail,expect_kw", [
    ("Q-Chem fatal error occurred in module scfman", "致命错误"),
    ("Q-Chem was unable to converge", "无法完成计算"),
    ("     The SCF failed to converge after 100 cycles", "failed to converge"),
    ("malloc failed in subroutine", "内存不足"),
    ("FlexNet Licensing error:-97,121", "许可证错误"),
    ("The desired vendor daemon is down.", "许可证守护进程未运行"),
])
def test_parse_qchem_error_detects_failures(tail, expect_kw):
    hint = parse_qchem_error(tail)
    assert hint and expect_kw in hint


def test_qchem_license_outage_is_recognized_as_failure():
    """实测场景（HPCCube 2026-10-14）：许可证服务停摆 → 输出仅数百字节。

    这种"看起来只是没跑完"的短输出必须被判为失败，否则会被当成正常完成。
    """
    tail = ("                  Welcome to Q-Chem\n"
            " Q-Chem 6.1, Q-Chem, Inc., Pleasanton, CA (2023)\n"
            " The desired vendor daemon is down.\n"
            "FlexNet Licensing error:-97,121\n")
    status, hint = classify_exit(0, tail, solver="qchem")
    assert status == JobStatus.FAILED
    assert "许可证" in hint


def test_qchem_exit_zero_with_fatal_error_is_failed():
    """Q-Chem 致命错误常伴随正常收尾文案，不得只看退出码。"""
    status, hint = classify_exit(
        0, "Q-Chem fatal error occurred in module ccman2\n"
           "Thank you very much for running Q-Chem",
        solver="qchem",
    )
    assert status == JobStatus.FAILED
    assert "致命错误" in hint


def test_qchem_clean_run_is_success():
    status, hint = classify_exit(0, "Total energy = -100.0\n"
                                    "Thank you very much for running Q-Chem",
                                 solver="qchem")
    assert status == JobStatus.SUCCESS and hint is None


def test_gaussian_parser_unchanged_by_solver_default():
    """默认 solver 仍走 Gaussian 解析（向后兼容）。"""
    status, hint = classify_exit(0, "Error termination\n")
    assert status == JobStatus.FAILED and "Gaussian" in hint


# -- 上传换行归一化 ---------------------------------------------------------

def test_upload_converts_crlf_for_qchem_input(tmp_path):
    """.in（Q-Chem 输入）上传时 CRLF→LF —— 与 .gjf 同等待遇。"""
    ssh = FakeSSH(home_dir="/home/dongyang")
    tr = SftpTransfer(ssh, make_config())
    local = tmp_path / "C6N.in"
    local.write_bytes(b"$molecule\r\n0 2\r\n$end\r\n")
    uploaded = tr.upload(str(local), "~/C2nCN/j1")
    assert len(uploaded) == 1
    data = ssh.read_file(uploaded[0])
    assert b"\r\n" not in data
    assert data.startswith(b"$molecule\n0 2\n$end\n")


# ---------------------------------------------------------------------------
# 4. 策略层：商业云超算类别
# ---------------------------------------------------------------------------

def test_classify_hpccube_host_as_commercial_cluster():
    cls, label = classify_server(make_profile())
    assert cls == ServerClass.COMMERCIAL_CLUSTER
    assert "HPCCube" in label


def test_explicit_commercial_declaration_on_unknown_host():
    cls, label = classify_server(
        make_profile("__SERVER_IP__", extra_class="commercial_cluster"))
    assert cls == ServerClass.COMMERCIAL_CLUSTER
    assert "商业云" in label


def test_commercial_cluster_forbids_direct_mode():
    guard = PolicyGuard(make_config())
    with pytest.raises(PolicyViolation) as ei:
        guard.check_execution_mode("direct")
    assert ei.value.rule_id == "POLICY-DIRECT-FORBIDDEN"
    assert "slurm" in ei.value.remedy


def test_commercial_cluster_allows_slurm():
    guard = PolicyGuard(make_config())
    assert guard.check_execution_mode("slurm") == "slurm"


def test_commercial_cluster_flags():
    guard = PolicyGuard(make_config())
    assert guard.is_commercial_cluster() is True
    assert guard.is_managed_cluster() is False
    assert guard.is_compute_restricted() is True


def test_commercial_authority_is_not_ustc_manual():
    """权威依据必须与 USTC 手册解耦，否则审计链失真。"""
    guard = PolicyGuard(make_config())
    assert guard.policy_source_name == "商业算力平台服务规范"
    with pytest.raises(PolicyViolation) as ei:
        guard.check_execution_mode("direct")
    assert "中国科大" not in ei.value.authority
    assert "商业算力平台规范" in ei.value.authority


def test_ustc_authority_untouched():
    guard = PolicyGuard(make_config("__SERVER_IP__"))
    with pytest.raises(PolicyViolation) as ei:
        guard.check_execution_mode("direct")
    assert "中心用户手册" in ei.value.authority


def test_qchem_blocked_on_login_node_of_commercial_cluster():
    """裸 qchem 出现在登录节点命令中必须被拦截。"""
    guard = PolicyGuard(make_config())
    with pytest.raises(PolicyViolation) as ei:
        guard.check_command("qchem C6N.in C6N.out C6N")
    assert ei.value.rule_id == "POLICY-LOGIN-NODE-COMPUTE"


def test_command_allowlist_enforced_on_commercial_cluster():
    """非白名单程序不得作为 command 起首（商业云同样强制白名单）。"""
    guard = PolicyGuard(make_config())
    with pytest.raises(PolicyViolation) as ei:
        guard.check_command("nwchem input.nw")
    assert ei.value.rule_id == "POLICY-COMMAND-NOT-ALLOWLISTED"
    assert "商业算力平台规范" in ei.value.authority


def test_allowlisted_leading_command_passes_on_commercial_cluster():
    guard = PolicyGuard(make_config())
    assert guard.check_command("python main.py") == "python main.py"


def test_workstation_still_exempt_from_login_node_rule():
    """回归：工作站豁免逻辑不得被商业云类别引入而破坏。"""
    guard = PolicyGuard(make_config("__SERVER_IP__"))
    assert guard.is_compute_restricted() is False
    assert guard.check_command("qchem in out name") is not None


def test_preflight_blocks_direct_on_commercial_cluster():
    guard = PolicyGuard(make_config(execution_mode="direct"))
    with pytest.raises(PolicyViolation) as ei:
        guard.preflight(mode="direct", remote="~/C2nCN/j1")
    assert ei.value.rule_id == "POLICY-DIRECT-FORBIDDEN"


def test_preflight_passes_for_compliant_commercial_submission():
    """HPCCube 实际 profile 口径下的合规提交应放行（含长墙钟与配额覆盖）。"""
    guard = PolicyGuard(make_config(
        max_walltime_hours=336.0, min_disk_free_gb=15.0,
        max_cpus_per_job=32, max_concurrent_jobs=16,
    ))
    decision = guard.preflight(
        mode="slurm", remote="~/C2nCN/c6n", command=None,
        cpus_per_task=4, walltime="14-00:00:00", download_glob="*.out *.fchk",
    )
    assert decision.server_class == ServerClass.COMMERCIAL_CLUSTER
    assert decision.policy_source == "商业算力平台服务规范"
    assert "execution_mode" in decision.checks_passed
    assert guard.quota.max_walltime_hours == 336.0
    assert guard.quota.min_disk_free_gb == 15.0
    # 数据性质/保全提醒须保留（无法自动判定项）
    assert any("数据性质" in w for w in decision.warnings)
    assert any("数据保全" in w for w in decision.warnings)


# -- 配额覆盖 ---------------------------------------------------------------

def test_quota_from_profile_overrides_and_defaults():
    p = make_profile(min_disk_free_gb=15.0, max_concurrent_jobs=16)
    q = quota_from_profile(p)
    assert q.min_disk_free_gb == 15.0
    assert q.max_concurrent_jobs == 16
    # 未覆盖字段沿用全局默认
    assert q.max_queued_jobs == QuotaPolicy().max_queued_jobs
    assert q.max_walltime_hours == QuotaPolicy().max_walltime_hours


def test_guard_uses_profile_quota_override():
    guard = PolicyGuard(make_config(min_disk_free_gb=15.0))
    assert guard.quota.min_disk_free_gb == 15.0


def test_explicit_quota_argument_beats_profile():
    guard = PolicyGuard(make_config(min_disk_free_gb=15.0),
                        quota=QuotaPolicy(min_disk_free_gb=3.0))
    assert guard.quota.min_disk_free_gb == 3.0


def test_concurrency_gate_uses_profile_limit():
    guard = PolicyGuard(make_config(max_concurrent_jobs=2))
    with pytest.raises(PolicyViolation) as ei:
        guard.check_concurrency(running_jobs=2, queued_jobs=0)
    assert ei.value.rule_id == "POLICY-CONCURRENCY-LIMIT"
    assert "商业算力平台规范" in ei.value.authority


# ---------------------------------------------------------------------------
# 5. 平台内存:CPU 比闸门与 Slurm 默认参数（2026-10-14 实测引入）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expect_mb", [
    ("7G", 7168.0),
    ("530G", 542720.0),
    ("3900M", 3900.0),
    ("4g", 4096.0),
    ("1024", 1024.0),
    ("1T", 1048576.0),
])
def test_parse_mem_mb(text, expect_mb):
    from gtransfer_lib.guard import _parse_mem_mb
    assert _parse_mem_mb(text) == pytest.approx(expect_mb)


@pytest.mark.parametrize("bad", ["", "abc", "G", "4GB extra", None])
def test_parse_mem_mb_returns_none_on_garbage(bad):
    from gtransfer_lib.guard import _parse_mem_mb
    assert _parse_mem_mb(bad) is None


def test_mem_per_cpu_gate_blocks_over_limit():
    """HPCCube 实测：--mem 4G + 1 CPU = 4096MB/核 > DefMemPerCPU 3944MB。"""
    guard = PolicyGuard(make_config(max_mem_per_cpu_mb=3944.0))
    with pytest.raises(PolicyViolation) as ei:
        guard.check_mem_per_cpu("4G", cpus_per_task=1)
    assert ei.value.rule_id == "POLICY-MEM-PER-CPU-EXCEEDED"
    # 定量修正建议：4096MB / 3944MB → 需 2 核
    assert "2" in ei.value.remedy and "cpus-per-task" in ei.value.remedy


def test_mem_per_cpu_gate_passes_at_boundary():
    guard = PolicyGuard(make_config(max_mem_per_cpu_mb=3944.0))
    guard.check_mem_per_cpu("7G", cpus_per_task=2)        # 3584MB/核
    guard.check_mem_per_cpu("480G", cpus_per_task=128)    # 3840MB/核


def test_mem_per_cpu_gate_rejects_at_platform_observed_failures():
    """真机观测到的拒绝组合：4G/1CPU、8G/2CPU、500G/128CPU、mem-per-cpu=4G。"""
    guard = PolicyGuard(make_config(max_mem_per_cpu_mb=3944.0))
    for mem, cpus in (("4G", 1), ("8G", 2), ("500G", 128)):
        with pytest.raises(PolicyViolation):
            guard.check_mem_per_cpu(mem, cpus_per_task=cpus)


def test_mem_per_cpu_gate_disabled_when_unconfigured():
    """未配置阈值时必须保持既有行为（不阻断）。"""
    guard = PolicyGuard(make_config())
    guard.check_mem_per_cpu("530G", cpus_per_task=1)


def test_mem_per_cpu_gate_skips_when_mem_absent_or_unparseable():
    guard = PolicyGuard(make_config(max_mem_per_cpu_mb=3944.0))
    guard.check_mem_per_cpu(None, cpus_per_task=1)
    guard.check_mem_per_cpu("lots", cpus_per_task=1)


def test_preflight_includes_mem_ratio_check():
    guard = PolicyGuard(make_config(max_mem_per_cpu_mb=3944.0))
    with pytest.raises(PolicyViolation) as ei:
        guard.preflight(mode="slurm", remote="~/C2nCN/x",
                        cpus_per_task=1, mem="100G")
    assert ei.value.rule_id == "POLICY-MEM-PER-CPU-EXCEEDED"

    decision = guard.preflight(mode="slurm", remote="~/C2nCN/x",
                               cpus_per_task=28, mem="100G",
                               walltime="24:00:00")
    assert "mem_per_cpu" in decision.checks_passed


def test_profile_slurm_defaults_parsed(tmp_path, monkeypatch):
    monkeypatch.setattr("gtransfer_lib.config._template_path",
                        lambda: tmp_path / "nonexistent.template")
    body = ("[server:hpccube]\nhost = h\nuser = u\npassword = pw\n"
            "execution_mode = slurm\nserver_class = commercial_cluster\n"
            "partition = wzacnormal\ncpus_per_task = 4\nnodes = 1\nntasks = 1\n"
            "mem_per_cpu = 3900M\nwalltime = 7-00:00:00\n"
            "max_mem_per_cpu_mb = 3944\n")
    s = load_config(str(_write_cfg(tmp_path, body)), "hpccube").server
    assert s.partition == "wzacnormal"
    assert s.cpus_per_task == 4
    assert s.mem_per_cpu == "3900M"
    assert s.walltime == "7-00:00:00"
    assert s.max_mem_per_cpu_mb == 3944.0


def test_cli_slurm_opts_fallback_order(tmp_path, monkeypatch):
    """CLI 显式值 > profile 默认 > 内置默认。"""
    import gtransfer
    prof = make_profile(partition="wzacnormal", cpus_per_task=4,
                        mem_per_cpu="3900M", walltime="7-00:00:00")
    cfg = TransferConfig(server=prof)

    # 全部留空 → 取 profile 默认
    empty = argparse.Namespace(partition=None, cpus_per_task=None, ntasks=None,
                               nodes=None, walltime=None, mem=None,
                               mem_per_cpu=None)
    got = gtransfer._resolve_slurm_opts(cfg, empty)
    assert got == {"partition": "wzacnormal", "cpus_per_task": 4, "ntasks": 1,
                   "nodes": 1, "walltime": "7-00:00:00", "mem": None,
                   "mem_per_cpu": "3900M"}

    # CLI 显式值覆盖 profile
    given = argparse.Namespace(partition="other", cpus_per_task=32, ntasks=2,
                               nodes=1, walltime="1-00:00:00", mem="100G",
                               mem_per_cpu="1G")
    got2 = gtransfer._resolve_slurm_opts(cfg, given)
    assert got2["partition"] == "other" and got2["cpus_per_task"] == 32
    assert got2["mem"] == "100G"

    # profile 未设 → 内置默认
    bare = TransferConfig(server=make_profile())
    got3 = gtransfer._resolve_slurm_opts(bare, empty)
    assert got3["cpus_per_task"] == 16 and got3["mem_per_cpu"] == "4G"
    assert got3["walltime"] == "24:00:00" and got3["partition"] is None


def test_slurm_executor_accepts_mem_per_cpu_override():
    ex = SlurmExecutor(FakeSSH(), make_config(), solver="qchem",
                       partition="wzacnormal", mem_per_cpu="3900M",
                       cpus_per_task=4)
    script = ex.build_script("~/C2nCN/j", "x.in")
    assert "#SBATCH --mem-per-cpu=3900M" in script
    assert "#SBATCH --mem=" not in script


@pytest.mark.skipif(not Path(_GIT_BASH).exists(),
                    reason="未找到 Git Bash，跳过 shell 行为验证")
@pytest.mark.parametrize("fake_body,expected", [
    # 返回 1 但输出有正常收尾标记 → 应被降为 0（不误判已算完的作业）
    ('printf "Thank you very much for using Q-Chem\\n" > "$2"\nexit 1\n', 0),
    # 返回 1 且无正常收尾标记 → 必须保持非 0（不得掩盖真实失败）
    ('printf "Q-Chem fatal error occurred in module scfman\\n" > "$2"\nexit 1\n', 1),
    # 正常返回 0 → 原样透传
    ('printf "Thank you very much for using Q-Chem\\n" > "$2"\nexit 0\n', 0),
])
def test_qchem_rc_guard_behavior_under_real_shell(tmp_path, fake_body, expected):
    """用真实 shell + 假 qchem 确定性验证退出码容错分支的**行为**（非文本）。"""
    import subprocess

    wd = tmp_path / "cc"
    wd.mkdir()
    (wd / "x.in").write_text("$molecule\n$end\n", encoding="utf-8")

    fake = tmp_path / "qchem_fake.sh"
    fake.write_bytes(("#!/bin/sh\n" + fake_body).replace("\r\n", "\n").encode())

    ex = DirectExecutor(FakeSSH(), make_config(execution_mode="direct"),
                        solver="qchem",
                        qchem_exec=str(fake).replace("\\", "/"))
    cmd = ex._command(str(wd).replace("\\", "/"), "x.in")
    proc = subprocess.run([_GIT_BASH, "-c", cmd], capture_output=True,
                          text=True, cwd=str(tmp_path))
    assert f"__GT_EXIT={expected}" in proc.stdout, (
        f"期望 __GT_EXIT={expected}，实际输出:\n{proc.stdout}\n{proc.stderr}")


def test_slurm_qchem_script_tolerates_nonzero_rc_with_normal_output():
    """[回归] qchem 退出码不可尽信（2026-10-14 HPCCube 实测：CCSD(T) 完整正常
    收尾却返回 rc=1）。

    生产路径若直接 `exit $?` 会把已算完的昂贵结果判成 FAILED，
    而 keep_remote=false 会连带删除现场。故脚本必须：
      仅当输出含正常收尾标记且无致命错误时，才把非 0 退出码降为 0。
    """
    ex = SlurmExecutor(FakeSSH(), make_config(), solver="qchem",
                       partition="wzacnormal", mem="30G", cpus_per_task=8)
    script = ex.build_script("~/C2nCN/cc", "heavy.in")
    assert "__gt_rc=$?" in script
    assert "Thank you very much for using Q-Chem" in script
    assert "Q-Chem fatal error" in script
    assert "exit $__gt_rc" in script
    # 不得无条件透传 qchem 的退出码
    assert "\nexit $?\n" not in script


def test_direct_qchem_command_tolerates_nonzero_rc_with_normal_output():
    ex = DirectExecutor(FakeSSH(), make_config(execution_mode="direct"),
                        solver="qchem")
    cmd = ex._command("~/C2nCN/cc", "heavy.in")
    assert "__gt_rc=$?" in cmd
    assert "Thank you very much for using Q-Chem" in cmd
    assert "echo __GT_EXIT=$__gt_rc" in cmd


#: 显式使用 Git Bash 做语法校验。**不要用 shutil.which("bash")** ——
#: Windows 下它先命中 System32\bash.exe（WSL 启动器），在沙箱中会被安全策略拦截
#: 并连带中断整个测试进程。


@pytest.mark.skipif(not Path(_GIT_BASH).exists(),
                    reason="未找到 Git Bash，跳过 shell 语法校验")
def test_generated_qchem_scripts_are_valid_bash():
    """生成的 slurm/direct 脚本必须能通过 bash 语法检查（防拼错引号/换行）。"""
    import subprocess
    ex = SlurmExecutor(FakeSSH(), make_config(), solver="qchem",
                       partition="wzacnormal", mem="30G", cpus_per_task=8)
    script = ex.build_script("~/C2nCN/cc", "heavy.in")
    proc = subprocess.run([_GIT_BASH, "-n"], input=script.encode(),
                          capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode()

    cmd = DirectExecutor(FakeSSH(), make_config(execution_mode="direct"),
                         solver="qchem")._command("~/C2nCN/cc", "heavy.in")
    proc2 = subprocess.run([_GIT_BASH, "-n"], input=cmd.encode(), capture_output=True)
    assert proc2.returncode == 0, proc2.stderr.decode()


def test_gaussian_script_still_uses_plain_exit_status():
    """Gaussian 路径不受影响（仍按退出码判定）。"""
    ex = SlurmExecutor(FakeSSH(), make_config(), solver="gaussian")
    script = ex.build_script("~/gt/t1", "water.gjf")
    assert "exit $?" in script and "__gt_rc" not in script


# ---------------------------------------------------------------------------
# 6. 端到端：Q-Chem 作业经 FakeSSH 走完 submit 契约
# ---------------------------------------------------------------------------

def test_slurm_qchem_submit_parses_jobid_and_queries_output_file():
    """提交后 query 回退须认 Q-Chem 的 .out（而非只看 gtransfer.log）。"""
    ssh = FakeSSH(home_dir="/work/home/dongyang")
    ssh.register(r"sbatch", lambda c: ("Submitted batch job 424242\n", "", 0))
    ssh.register(r"squeue", lambda c: ("", "", 0))
    ssh.register(r"sacct", lambda c: ("", "", 0))
    ssh.register(r"test -s .*C6N\.out",
                 lambda c: ("EXISTS\n", "", 0))
    ex = SlurmExecutor(ssh, make_config(), solver="qchem")
    state = ex.submit("/work/home/dongyang/C2nCN/c6n", "C6N.in")
    assert state.status == JobStatus.PENDING
    assert state.job_id == "424242"
    q = ex.query("424242")
    assert q.status == JobStatus.SUCCESS
