"""test_executor.py — direct 与 slurm 执行器的命令构造与状态判定。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeSSH  # noqa: E402
from gtransfer_lib.config import ServerProfile, TransferConfig  # noqa: E402
from gtransfer_lib.executor import (  # noqa: E402
    DirectExecutor,
    SlurmExecutor,
    create_executor,
)
from gtransfer_lib.status import JobStatus  # noqa: E402


def make_cfg(mode="direct") -> TransferConfig:
    profile = ServerProfile(
        name="test", host="h", user="u", key_file="/k",
        execution_mode=mode, remote_work_root="~/gt",
    )
    return TransferConfig(server=profile)


# ---------------------------------------------------------------------------
# direct
# ---------------------------------------------------------------------------

def test_direct_command_construction():
    """g16 命令模板：cd + env + GAUSS_SCRDIR + 重定向 + 退出码回显。"""
    ssh = FakeSSH()
    ex = DirectExecutor(ssh, make_cfg(), gaussian_exec="g16", env_setup="module load gaussian")
    cmd = ex._command("~/gt/t1", "water.gjf")
    # ~ 前缀转 "$HOME"（shlex.quote 会锁死波浪号导致 cd 失败）
    assert 'cd "$HOME"/gt/t1' in cmd
    assert "module load gaussian" in cmd
    assert 'GAUSS_SCRDIR="$HOME"/gt/t1/scratch' in cmd
    assert "g16 < water.gjf > water.log 2>&1" in cmd
    assert "echo __GT_EXIT=$?" in cmd


def test_direct_command_escapes_special_chars():
    """[回归] 含特殊字符的 workdir/input 被安全引用（防注入）。"""
    ssh = FakeSSH()
    ex = DirectExecutor(ssh, make_cfg())
    cmd = ex._command("~/gt/t1; rm -rf /", "x.gjf; rm -rf /")
    # 引号包裹后，; 不再作为命令分隔符
    assert '"$HOME"/gt/t1; rm -rf /' not in cmd
    assert "'x.gjf; rm -rf /'" in cmd


def test_slurm_script_escapes_special_chars():
    """[回归] slurm 脚本中路径被安全引用。"""
    ssh = FakeSSH()
    ex = SlurmExecutor(ssh, make_cfg("slurm"))
    script = ex.build_script("~/gt/t1; rm -rf /", "x.gjf; rm -rf /")
    assert '"$HOME"/gt/t1; rm -rf /' not in script
    assert "g16 < 'x.gjf; rm -rf /' > 'x.gjf; rm -rf /'.log 2>&1" in script


def test_direct_success():
    """退出码 0 → SUCCESS。"""
    ssh = FakeSSH()
    ssh.register(r"g16", lambda cmd: ("__GT_EXIT=0", "", 0))
    ex = DirectExecutor(ssh, make_cfg())
    state = ex.submit("~/gt/t1", "water.gjf")
    assert state.status == JobStatus.SUCCESS
    assert state.exit_code == 0


def test_direct_failed_by_exit_code():
    """退出码非 0 → FAILED 并带线索。"""
    ssh = FakeSSH()
    ssh.register(r"g16", lambda cmd: ("some output\n__GT_EXIT=1", "error", 1))
    ex = DirectExecutor(ssh, make_cfg())
    state = ex.submit("~/gt/t1", "water.gjf")
    assert state.status == JobStatus.FAILED
    assert state.error_hint and "退出码" in state.error_hint


def test_direct_failed_by_gaussian_error_in_log():
    """退出码 0 但日志含 Gaussian 错误码 → FAILED。"""
    log = (
        "Normal termination of Gaussian 16\n"
        "Error termination\n"
        "__GT_EXIT=0\n"
    )
    ssh = FakeSSH()
    ssh.register(r"g16", lambda cmd: (log, "", 0))
    ex = DirectExecutor(ssh, make_cfg())
    state = ex.submit("~/gt/t1", "water.gjf")
    assert state.status == JobStatus.FAILED
    assert "Gaussian 错误终止" in (state.error_hint or "")


def test_direct_l_code_detection():
    """日志含 L502 等错误码 → FAILED 且 hint 含 L 码。"""
    log = (
        "Error termination request processed by link 9999.\n"
        "L502 in opt1 on PE 0\n"
        "__GT_EXIT=0\n"
    )
    ssh = FakeSSH()
    ssh.register(r"g16", lambda cmd: (log, "", 0))
    ex = DirectExecutor(ssh, make_cfg())
    state = ex.submit("~/gt/t1", "water.gjf")
    assert state.status == JobStatus.FAILED
    assert "L502" in (state.error_hint or "")


def test_direct_query_not_supported():
    """direct 模式 query 应明确不支持（异步接口预留说明）。"""
    ex = DirectExecutor(FakeSSH(), make_cfg())
    with pytest.raises(NotImplementedError):
        ex.query("anything")


# ---------------------------------------------------------------------------
# slurm
# ---------------------------------------------------------------------------

def test_slurm_script_template():
    """作业脚本包含 SBATCH 指令与 g16 执行。"""
    ssh = FakeSSH()
    ex = SlurmExecutor(ssh, make_cfg("slurm"), cpus_per_task=32, walltime="48:00:00",
                       partition="gpu")
    script = ex.build_script("~/gt/t1", "water.gjf")
    assert "#SBATCH --cpus-per-task=32" in script
    assert "#SBATCH --time=48:00:00" in script
    assert "#SBATCH --partition=gpu" in script
    assert "g16 < water.gjf > water.log 2>&1" in script
    assert "#!/bin/bash" in script


def test_slurm_submit_parses_jobid():
    """sbatch 输出 'Submitted batch job 12345' → jobid 解析。"""
    ssh = FakeSSH()
    ssh.register(r"sbatch", lambda cmd: ("Submitted batch job 12345\n", "", 0))
    ex = SlurmExecutor(ssh, make_cfg("slurm"))
    state = ex.submit("~/gt/t1", "water.gjf")
    assert state.job_id == "12345"
    assert state.status == JobStatus.PENDING
    # 作业脚本已写入远端
    assert ssh.exists("~/gt/t1/gtransfer_run.sh")


def test_slurm_submit_failure():
    """sbatch 返回非 0 → FAILED。"""
    ssh = FakeSSH()
    ssh.register(r"sbatch", lambda cmd: ("", "sbatch: error: invalid partition", 1))
    ex = SlurmExecutor(ssh, make_cfg("slurm"))
    state = ex.submit("~/gt/t1", "water.gjf")
    assert state.status == JobStatus.FAILED
    assert "invalid partition" in (state.error_hint or "")


def test_slurm_query_running_then_completed():
    """squeue 有记录 → RUNNING；消失后 sacct 主作业 COMPLETED → SUCCESS。"""
    ssh = FakeSSH()
    ex = SlurmExecutor(ssh, make_cfg("slurm"))

    ssh.register(r"squeue", lambda cmd: ("RUNNING", "", 0))
    state = ex.query("12345")
    assert state.status == JobStatus.RUNNING

    # 换行为：squeue 空、sacct 主作业行 COMPLETED(0:0)
    ssh.commands = [st for st in ssh.commands if "squeue" not in st.pattern]
    ssh.register(r"squeue", lambda cmd: ("", "", 0))
    ssh.register(r"sacct", lambda cmd: ("12345|0:0|COMPLETED", "", 0))
    state2 = ex.query("12345")
    assert state2.status == JobStatus.SUCCESS


def test_slurm_query_failed_state():
    """sacct 主作业非 COMPLETED → FAILED 并带 Slurm 状态。"""
    ssh = FakeSSH()
    ssh.register(r"squeue", lambda cmd: ("", "", 0))
    ssh.register(r"sacct", lambda cmd: ("12345|1:0|CANCELLED", "", 0))
    ex = SlurmExecutor(ssh, make_cfg("slurm"))
    state = ex.query("12345")
    assert state.status == JobStatus.FAILED
    assert "CANCELLED" in (state.error_hint or "")


def test_slurm_query_extern_step_must_not_mask_failure():
    """[回归·误报成功] sacct 中 .extern 步骤为 COMPLETED，但主作业 FAILED。

    早期实现取 `tail -1` 恰好命中 `.extern` 步骤行 → 把失败作业误判为 SUCCESS。
    2026-10-14 在 HPCCube 真机复现（主作业 FAILED 119:0 / .extern COMPLETED，
    因 Q-Chem 许可证守护进程停止）。
    """
    ssh = FakeSSH()
    ssh.register(r"squeue", lambda cmd: ("", "", 0))
    ssh.register(r"sacct", lambda cmd: (
        "46528774|119:0|FAILED\n"
        "46528774.batch|119:0|FAILED\n"
        "46528774.extern|0:0|COMPLETED\n", "", 0))
    ex = SlurmExecutor(ssh, make_cfg("slurm"))
    state = ex.query("46528774")
    assert state.status == JobStatus.FAILED
    assert "FAILED" in (state.error_hint or "")
    assert state.exit_code == 119


def test_slurm_query_completed_but_nonzero_exit_is_failed():
    """COMPLETED 但退出码非 0 亦不算成功。"""
    ssh = FakeSSH()
    ssh.register(r"squeue", lambda cmd: ("", "", 0))
    ssh.register(r"sacct", lambda cmd: ("999|127:0|COMPLETED", "", 0))
    ex = SlurmExecutor(ssh, make_cfg("slurm"))
    assert ex.query("999").status == JobStatus.FAILED


def test_slurm_query_without_accounting_rechecks_squeue_before_giving_up():
    """accounting 未启用时：sacct 无主作业行，但作业仍在队列 → 不判 FAILED。

    防「作业 PENDING、sacct 尚未落记录」被误判失败的竞态（同时避免
    cleanup_remote 删除在途作业目录）。
    """
    ssh = FakeSSH()
    calls = {"n": 0}

    def squeue(cmd):
        calls["n"] += 1
        return ("", "", 0) if calls["n"] == 1 else ("PENDING", "", 0)

    ssh.register(r"squeue", squeue)
    ssh.register(r"sacct", lambda cmd: ("", "", 0))
    ex = SlurmExecutor(ssh, make_cfg("slurm"))
    state = ex.query("777")
    assert state.status == JobStatus.RUNNING


def test_parse_sacct_main_job_ignores_step_rows():
    exit_code, state = SlurmExecutor._parse_sacct_main_job(
        "1|0:0|COMPLETED\n1.batch|0:0|COMPLETED\n1.extern|0:0|COMPLETED\n",
        "1",
    )
    assert (exit_code, state) == ("0:0", "COMPLETED")


def test_parse_sacct_main_job_returns_none_without_main_row():
    exit_code, state = SlurmExecutor._parse_sacct_main_job(
        "1.batch|0:0|COMPLETED\n1.extern|0:0|COMPLETED\n", "1")
    assert (exit_code, state) == (None, None)


def test_slurm_wait_blocks_until_terminal(monkeypatch):
    """wait 轮询直至终态（模拟 2 次 RUNNING 后 SUCCESS）。"""
    ssh = FakeSSH()
    ex = SlurmExecutor(ssh, make_cfg("slurm"))
    calls = {"n": 0}

    def fake_query(job_id):
        calls["n"] += 1
        from gtransfer_lib.status import JobState
        if calls["n"] < 3:
            return JobState(job_id=job_id, status=JobStatus.RUNNING)
        return JobState(job_id=job_id, status=JobStatus.SUCCESS)

    monkeypatch.setattr(ex, "query", fake_query)
    state = ex.wait("12345")
    assert state.status == JobStatus.SUCCESS
    assert calls["n"] == 3


# ---------------------------------------------------------------------------
# 通用命令模式（command 参数取代 g16 流程）
# ---------------------------------------------------------------------------

def test_direct_command_construction_generic():
    """通用命令模式：cd + env + command > gtransfer.log + 退出码回显。"""
    ssh = FakeSSH()
    ex = DirectExecutor(ssh, make_cfg(), env_setup="conda activate pycs-sw",
                        command="python main.py")
    cmd = ex._command("~/gt/t1", "water.gjf")
    assert 'cd "$HOME"/gt/t1' in cmd
    assert "conda activate pycs-sw" in cmd
    assert "python main.py > gtransfer.log 2>&1" in cmd
    assert "echo __GT_EXIT=$?" in cmd
    # 通用命令模式不应包含 g16 流程相关片段
    assert "GAUSS_SCRDIR" not in cmd
    assert "g16 <" not in cmd


def test_direct_command_generic_no_env():
    """通用命令模式未配 env_setup 时 cd 直接接 command。"""
    ssh = FakeSSH()
    ex = DirectExecutor(ssh, make_cfg(), command="python run.py --fast")
    cmd = ex._command("~/gt/t1", "x.gjf")
    assert 'cd "$HOME"/gt/t1 && python run.py --fast' in cmd


def test_direct_command_generic_success():
    """通用命令退出码 0 → SUCCESS。"""
    ssh = FakeSSH()
    ssh.register(r"python main.py", lambda cmd: ("__GT_EXIT=0", "", 0))
    ex = DirectExecutor(ssh, make_cfg(), command="python main.py")
    state = ex.submit("~/gt/t1", "water.gjf")
    assert state.status == JobStatus.SUCCESS
    assert state.exit_code == 0


def test_direct_command_generic_failed():
    """通用命令退出码非 0 → FAILED。"""
    ssh = FakeSSH()
    ssh.register(r"python main.py", lambda cmd: ("traceback\n__GT_EXIT=1", "", 1))
    ex = DirectExecutor(ssh, make_cfg(), command="python main.py")
    state = ex.submit("~/gt/t1", "water.gjf")
    assert state.status == JobStatus.FAILED
    assert state.error_hint and "退出码" in state.error_hint


def test_slurm_script_template_generic():
    """slurm 通用命令模式：脚本含 command 而非 g16。"""
    ssh = FakeSSH()
    ex = SlurmExecutor(ssh, make_cfg("slurm"), cpus_per_task=16,
                       command="python main.py")
    script = ex.build_script("~/gt/t1", "water.gjf")
    assert "#SBATCH --cpus-per-task=16" in script
    assert "python main.py > gtransfer.log 2>&1" in script
    assert "g16 <" not in script
    assert "GAUSS_SCRDIR" not in script


def test_create_executor_dispatches_generic_command():
    """create_executor 将 command 透传给 direct/slurm。"""
    ssh = FakeSSH()
    d = create_executor(ssh, make_cfg("direct"), command="python main.py")
    assert isinstance(d, DirectExecutor)
    assert d._command_template == "python main.py"
    s = create_executor(ssh, make_cfg("slurm"), command="python main.py")
    assert isinstance(s, SlurmExecutor)
    assert s._command_template == "python main.py"


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def test_create_executor_dispatch():
    """create_executor 按 execution_mode 分发。"""
    ssh = FakeSSH()
    assert isinstance(create_executor(ssh, make_cfg("direct")), DirectExecutor)
    assert isinstance(create_executor(ssh, make_cfg("slurm")), SlurmExecutor)
