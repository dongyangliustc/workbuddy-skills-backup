"""executor.py — 远端执行层。

按 execution_mode 分发：
- direct：前台 `g16 < in.gjf > in.log`，进程结束即终态；
- slurm： 生成作业脚本 → `sbatch` 提交拿 jobid → `squeue` 轮询
          → 消失后 `sacct` 查退出码。

通用命令模式：传 command 参数（如 `python main.py`）可执行任意远端命令，
输出重定向到 gtransfer.log；未传时保持默认 g16 流程（向后兼容）。

职责边界：只负责"把作业跑起来并给出状态"，不做文件传输
（传输在 transport.py）。全部 I/O 走 SshTransport 协议，可 mock。
"""
from __future__ import annotations

import posixpath
import re
import shlex
import time
from abc import ABC, abstractmethod
from typing import Optional

from .config import ConfigError, TransferConfig
from .connection import SshTransport
from .guard import PolicyGuard, PolicyViolation
from .status import JobState, JobStatus, classify_exit, poll_until_terminal


class RemoteExecutor(ABC):
    """执行器抽象：新增模式（如 pbs/lsf）只需实现此接口。

    所有实现必须在提交前调用 `PolicyGuard.check_command()` 校验 `command`
    内容、在提交时调用 `check_execution_mode()` 校验自身模式。这是第二道防线
    ——CLI 已做过 preflight，但执行器作为独立可调用对象不应假设调用方已校验。
    """

    @abstractmethod
    def submit(self, workdir: str, input_name: str) -> JobState:
        """在远端 workdir 提交作业，返回初始状态。"""

    @abstractmethod
    def query(self, job_id: str) -> JobState:
        """查询作业当前状态（供轮询与 status 子命令）。"""

    # -- 合规自检（供子类复用） -------------------------------------------

    def _enforce_policy(self) -> PolicyGuard:
        """构造策略引擎并校验执行模式与命令内容。

        返回 guard 供子类追加校验（如远端路径）。违规抛 PolicyViolation。
        """
        guard = PolicyGuard(self._cfg)
        guard.check_execution_mode(self._mode_name())
        guard.check_command(getattr(self, "_command_template", None))
        return guard

    def _mode_name(self) -> str:
        """执行模式名，用于策略校验。"""
        raise NotImplementedError


def _shell_safe_path(path: str) -> str:
    """将远端路径转为可安全插入 shell 命令的形式。

    要点：
    - 含 `~` 前缀时转成 "$HOME"（shlex.quote 会把 `~` 加引号，
      导致远端 bash 不再展开波浪号 → cd 失败）
    - 其余部分 shlex.quote 防注入
    """
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"' + shlex.quote(path[1:])
    return shlex.quote(path)


# ---------------------------------------------------------------------------
# 求解器路径约定（2026-10-14 引入，支持 Q-Chem）
# ---------------------------------------------------------------------------

#: 输入文件扩展名 → 作业名去后缀
_INPUT_SUFFIXES: tuple[str, ...] = (".gjf", ".com", ".inp", ".in")


def solver_paths(input_name: str, solver: str = "gaussian") -> tuple[str, str]:
    """按求解器约定返回 (作业名, 输出文件名)。

    - gaussian：`x.gjf` → 作业名 `x`，输出 `x.log`
    - qchem   ：`x.in`  → 作业名 `x`，输出 `x.out`
      （Q-Chem 的三参数调用形式为 `qchem in out scratchname`）
    """
    stem = input_name
    for ext in _INPUT_SUFFIXES:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    if (solver or "gaussian").strip().lower() == "qchem":
        return stem, f"{stem}.out"
    return stem, f"{stem}.log"


def qchem_scratch_name(stem: str) -> str:
    """Q-Chem scratch 目录名（第三参数）。

    Q-Chem 会在 $QCSCRATCH 下按此名建目录，故限定安全字符集；
    含 `+`/`-`/空格 等字符的作业名会被归一化，避免与既存 scratch 冲突或
    因非法字符导致建目录失败。
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", stem or "")
    return safe or "qchem_job"


# ---------------------------------------------------------------------------
# direct 模式
# ---------------------------------------------------------------------------

class DirectExecutor(RemoteExecutor):
    """前台执行：阻塞等待 g16/qchem 进程结束（或通用命令执行结束）。"""

    def __init__(
        self,
        ssh: SshTransport,
        cfg: TransferConfig,
        gaussian_exec: str = "g16",
        env_setup: str = "",
        timeout: Optional[float] = None,
        command: Optional[str] = None,
        solver: str = "gaussian",
        qchem_exec: str = "qchem",
    ):
        self._ssh = ssh
        self._cfg = cfg
        self._gaussian_exec = gaussian_exec
        self._env_setup = env_setup
        self._timeout = timeout
        self._command_template = command  # 通用命令模式；None 时保持求解器默认
        self._solver = (solver or "gaussian").strip().lower()
        self._qchem_exec = qchem_exec

    def _mode_name(self) -> str:
        return "direct"

    def _command(self, workdir: str, input_name: str) -> str:
        # 路径安全引用：~ 前缀 → "$HOME"；其余 shlex.quote 防注入
        wd = _shell_safe_path(workdir)
        prefix = f"cd {wd}"
        if self._env_setup:
            prefix = f"cd {wd} && {self._env_setup}"

        if self._command_template is not None:
            # 通用命令模式：执行用户给出的任意命令，输出统一进 gtransfer.log
            cmd = shlex.quote("gtransfer.log")
            return (
                prefix + " && "
                f"{self._command_template} > {cmd} 2>&1; "
                f"echo __GT_EXIT=$?"
            )

        stem, out_name = solver_paths(input_name, self._solver)
        inp = shlex.quote(input_name)
        out = shlex.quote(out_name)

        if self._solver == "qchem":
            # Q-Chem：三参数调用 + QCSCRATCH 按任务目录隔离
            # （站上默认 QCSCRATCH 为组共享目录，并发同名作业会互相覆盖）
            log = shlex.quote("gtransfer.log")
            scratch = shlex.quote(qchem_scratch_name(stem))
            qexec = shlex.quote(self._qchem_exec)
            return (
                prefix + " && "
                f"export QCSCRATCH={wd}/scratch && mkdir -p $QCSCRATCH && "
                f"{qexec} {inp} {out} {scratch} > {log} 2>&1; "
                # 退出码不可尽信：实测（2026-10-14，HPCCube）CCSD(T) 作业完整
                # 正常收尾却返回 rc=1。仅当输出**证明**正常结束时才把非 0 降为 0，
                # 避免把已算完的结果判成失败（进而被 keep_remote=false 删掉）。
                "__gt_rc=$?; "
                "if [ $__gt_rc -ne 0 ] && "
                'grep -q "Thank you very much for using Q-Chem" ' + out + " && "
                '! grep -q "Q-Chem fatal error" ' + out + "; "
                "then __gt_rc=0; fi; "
                "echo __GT_EXIT=$__gt_rc"
            )

        # Gaussian：`g16 < in.gjf > in.log` + 私有 GAUSS_SCRDIR
        gexec = shlex.quote(self._gaussian_exec)
        log = shlex.quote(f"{stem}.log")
        cmd = (
            prefix + " && "
            f"GAUSS_SCRDIR={wd}/scratch && mkdir -p $GAUSS_SCRDIR && "
            f"{gexec} < {inp} > {log} 2>&1; "
            f"echo __GT_EXIT=$?; rm -rf $GAUSS_SCRDIR"
        )
        return cmd

    def submit(self, workdir: str, input_name: str) -> JobState:
        # 合规自检（第二道防线）：受管/商业云集群禁止 direct 模式，命令须过白/黑名单
        self._enforce_policy()
        out, err, code = self._ssh.exec_command(
            self._command(workdir, input_name),
            timeout=self._timeout,
        )
        m = re.search(r"__GT_EXIT=(\d+)", out)
        exit_code = int(m.group(1)) if m else code
        state = classify_exit(exit_code, log_tail=out[-4000:], solver=self._solver)
        return JobState(
            job_id=input_name,
            status=state[0],
            exit_code=exit_code,
            error_hint=state[1],
            remote_dir=workdir,
        )

    def query(self, job_id: str) -> JobState:
        # direct 模式为一次性同步调用，query 语义为"已完成"
        raise NotImplementedError(
            "direct 模式不支持异步查询；请使用 submit 同步等待结果"
        )


# ---------------------------------------------------------------------------
# slurm 模式
# ---------------------------------------------------------------------------

class SlurmExecutor(RemoteExecutor):
    """Slurm 调度器：sbatch 提交 + squeue/sacct 轮询。"""

    def __init__(
        self,
        ssh: SshTransport,
        cfg: TransferConfig,
        gaussian_exec: str = "g16",
        env_setup: str = "",
        partition: Optional[str] = None,
        nodes: int = 1,
        ntasks: int = 1,
        cpus_per_task: int = 16,
        mem_per_cpu: str = "4G",
        mem: Optional[str] = None,
        walltime: str = "24:00:00",
        command: Optional[str] = None,
        solver: str = "gaussian",
        qchem_exec: str = "qchem",
    ):
        self._ssh = ssh
        self._cfg = cfg
        self._gaussian_exec = gaussian_exec
        self._env_setup = env_setup
        self._partition = partition
        self._nodes = nodes
        self._ntasks = ntasks
        self._cpus_per_task = cpus_per_task
        self._mem_per_cpu = mem_per_cpu
        self._mem = mem            # 整节点内存声明（--mem），优先于 --mem-per-cpu
        self._walltime = walltime
        self._command_template = command  # 通用命令模式；None 时保持求解器默认
        self._solver = (solver or "gaussian").strip().lower()
        self._qchem_exec = qchem_exec
        self._last_remote_dir: Optional[str] = None  # 供 query 回退使用
        self._output_name: Optional[str] = None      # 期望输出文件（供 query 回退判定）

    def _mode_name(self) -> str:
        return "slurm"

    def build_script(self, workdir: str, input_name: str) -> str:
        stem, out_name = solver_paths(input_name, self._solver)
        # 路径安全引用：~ 前缀 → "$HOME"（#SBATCH --chdir 与脚本内 cd 均可展开）
        wd = _shell_safe_path(workdir)
        chdir = wd
        inp = shlex.quote(input_name)
        base_q = shlex.quote(stem)
        lines = [
            "#!/bin/bash",
            "#SBATCH --job-name=gtransfer",
            f"#SBATCH --cpus-per-task={self._cpus_per_task}",
            f"#SBATCH --time={self._walltime}",
            f"#SBATCH --nodes={self._nodes}",
            f"#SBATCH --ntasks={self._ntasks}",
            "#SBATCH --output=gtransfer-%j.log",
            "#SBATCH --error=gtransfer-%j.err",
            f"#SBATCH --chdir={chdir}",
        ]
        # 内存声明：--mem（整节点，Q-Chem 大内存体系常用）优先于 --mem-per-cpu
        if self._mem:
            lines.append(f"#SBATCH --mem={self._mem}")
        else:
            lines.insert(3, f"#SBATCH --mem-per-cpu={self._mem_per_cpu}")
        if self._partition:
            lines.insert(1, f"#SBATCH --partition={shlex.quote(self._partition)}")
        if self._env_setup:
            lines.append(self._env_setup)
        if self._command_template is not None:
            # 通用命令模式：执行用户给出的任意命令，输出统一进 gtransfer.log
            log = shlex.quote("gtransfer.log")
            lines += [
                f"cd {wd}",
                f"{self._command_template} > {log} 2>&1",
                "exit $?",
                f"# end gtransfer script for {input_name}",
            ]
        elif self._solver == "qchem":
            # Q-Chem：QCSCRATCH 必须在本任务目录内
            # （站上 shellvar 默认指向组共享 tmp，并发同名作业会互相覆盖 scratch）
            log = shlex.quote("gtransfer.log")
            out_q = shlex.quote(out_name)
            scratch = shlex.quote(qchem_scratch_name(stem))
            lines += [
                f"cd {wd}",
                f"export QCSCRATCH={wd}/scratch",
                "mkdir -p $QCSCRATCH",
                f"{shlex.quote(self._qchem_exec)} {inp} {out_q} {scratch} "
                f"> {log} 2>&1",
                # 退出码不可尽信（2026-10-14 HPCCube 实测：CCSD(T) 完整正常收尾
                # 却返回 rc=1）。只有输出**证明**正常结束且无致命错误时才把非 0
                # 降为 0 —— 否则已算完的昂贵结果会被判失败并被 cleanup 删除。
                "__gt_rc=$?",
                'if [ $__gt_rc -ne 0 ] && '
                'grep -q "Thank you very much for using Q-Chem" ' + out_q + " && "
                '! grep -q "Q-Chem fatal error" ' + out_q + "; then",
                '  echo "[gtransfer] qchem rc=$__gt_rc 但输出显示正常收尾，按成功处理" >> '
                + log,
                "  __gt_rc=0",
                "fi",
                "exit $__gt_rc",
                f"# end gtransfer script for {input_name}",
            ]
        else:
            lines += [
                f"export GAUSS_SCRDIR={wd}/scratch",
                "mkdir -p $GAUSS_SCRDIR",
                f"{shlex.quote(self._gaussian_exec)} < {inp} > {base_q}.log 2>&1",
                "exit $?",
                f"# end gtransfer script for {input_name}",
            ]
        self._output_name = out_name
        return "\n".join(lines) + "\n"

    # -- 提交与轮询 -----------------------------------------------------

    def submit(self, workdir: str, input_name: str) -> JobState:
        # 合规自检（第二道防线）：命令内容须过白/黑名单，防脚本注入越权操作
        self._enforce_policy()
        # workdir 经 ~ 展开为绝对路径（SFTP 写脚本与 sbatch 均需绝对路径）
        workdir = self._ssh.expand_remote(workdir)
        script = self.build_script(workdir, input_name)
        script_path = posixpath.join(workdir, "gtransfer_run.sh")
        sftp = self._ssh.open_sftp()
        try:
            with sftp.open(script_path, "w") as f:
                f.write(script)
        finally:
            sftp.close()

        out, err, code = self._ssh.exec_command(f"cd {workdir} && sbatch {script_path}")
        if code != 0:
            return JobState(
                job_id=input_name,
                status=JobStatus.FAILED,
                exit_code=code,
                error_hint=f"sbatch 提交失败: {err.strip() or out.strip()}",
                remote_dir=workdir,
            )
        m = re.search(r"Submitted batch job (\d+)", out)
        if not m:
            return JobState(
                job_id=input_name,
                status=JobStatus.FAILED,
                error_hint=f"无法从 sbatch 输出解析 jobid: {out.strip()}",
                remote_dir=workdir,
            )
        self._last_remote_dir = workdir  # 供 query 回退使用
        return JobState(
            job_id=m.group(1),
            status=JobStatus.PENDING,
            remote_dir=workdir,
        )

    def query(self, job_id: str) -> JobState:
        # squeue 中 → RUNNING（或 PENDING）
        out, _, _ = self._ssh.exec_command(
            f"squeue -j {job_id} -h -o %T 2>/dev/null"
        )
        state_line = out.strip()
        if state_line:
            running = state_line.upper() in ("RUNNING", "R", "COMPLETING", "CG")
            status = JobStatus.RUNNING if running else JobStatus.PENDING
            return JobState(job_id=job_id, status=status)

        # 不在队列 → 查 sacct 定终态
        #
        # 🔴 关键：必须取**主作业行**，不能用 `tail -1`。
        # sacct -j <id> 会返回主作业行 + 各步骤行（.batch / .extern / .0 ...），
        # 其中 `.extern` 步骤在作业失败时仍为 COMPLETED。早期实现取 `tail -1`
        # 恰好命中 .extern → 把失败作业误判为 SUCCESS。
        # 该缺陷在 accounting 关闭的集群上不可见（sacct 无记录即走回退），
        # 在 accounting 启用的商业云（HPCCube）上必然暴露 —— 2026-10-14 实测：
        # 主作业 FAILED(119:0) / .extern COMPLETED，被误报 SUCCESS。
        out, _, _ = self._ssh.exec_command(
            f"sacct -j {job_id} -n -o JobID,ExitCode,State --parsable2 2>/dev/null"
        )
        main_exit, main_state = self._parse_sacct_main_job(out, job_id)

        if main_state is None:
            # 无主作业行 → accounting 未启用或无记录。
            # 先复查 squeue（防"作业仍排队但 sacct 尚未落记录"的竞态误判），
            # 再回退查输出文件是否存在且非空。
            squeue_recheck, _, _ = self._ssh.exec_command(
                f"squeue -j {job_id} -h -o %T 2>/dev/null"
            )
            if squeue_recheck.strip():
                return JobState(job_id=job_id, status=JobStatus.RUNNING)

            wd = self._last_remote_dir or self._cfg.server.remote_work_root
            candidates = [c for c in (self._output_name, "gtransfer.log") if c]
            for cand in candidates:
                check_cmd = (
                    f"test -s {shlex.quote(wd)}/{shlex.quote(cand)} "
                    f"&& echo EXISTS || echo MISSING"
                )
                out2, _, _ = self._ssh.exec_command(check_cmd)
                if out2.strip() == "EXISTS":
                    return JobState(job_id=job_id, status=JobStatus.SUCCESS)
            return JobState(job_id=job_id, status=JobStatus.FAILED,
                            error_hint="sacct 无记录且输出文件不存在，作业可能被清理")

        state_tag = main_state.upper()
        # Slurm 终态：COMPLETED 且退出码 0:0 才算成功
        exit_ok = (main_exit or "").split(":")[0].strip() in ("", "0")
        if state_tag.startswith("COMPLETED") and exit_ok:
            return JobState(job_id=job_id, status=JobStatus.SUCCESS,
                            exit_code=int((main_exit or "0").split(":")[0] or 0))
        return JobState(
            job_id=job_id, status=JobStatus.FAILED,
            exit_code=(int(main_exit.split(":")[0])
                       if main_exit and main_exit.split(":")[0].isdigit() else None),
            error_hint=f"Slurm 状态 {state_tag}"
                        + (f"（ExitCode {main_exit}）" if main_exit else ""),
        )

    @staticmethod
    def _parse_sacct_main_job(out: str, job_id: str) -> tuple[Optional[str], Optional[str]]:
        """从 sacct --parsable2 输出中提取**主作业行**的 (ExitCode, State)。

        只认 `JobID` 与查询 id 完全一致的行；步骤行形如 `<id>.batch` /
        `<id>.extern` / `<id>.0`，一律忽略 —— 它们的状态不代表作业整体成败。
        无主作业行时返回 (None, None)。
        """
        target = str(job_id).strip()
        for raw in (out or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            if parts[0] == target:
                return parts[1], parts[2]
        return None, None

    def wait(self, job_id: str, timeout: Optional[float] = None) -> JobState:
        return poll_until_terminal(
            lambda: self.query(job_id),
            poll_interval=self._cfg.server.poll_interval,
            timeout=timeout,
        )


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def create_executor(ssh: SshTransport, cfg: TransferConfig, **kwargs) -> RemoteExecutor:
    """按 execution_mode 与 solver 创建执行器。

    求解器（gaussian/qchem）与 Q-Chem 可执行文件未显式传入时，
    回退到 profile 配置（cfg.server.solver / cfg.server.qchem_exec）。
    """
    mode = cfg.server.execution_mode
    kwargs.setdefault("solver", cfg.server.solver)
    kwargs.setdefault("qchem_exec", cfg.server.qchem_exec)

    if mode == "direct":
        # direct 支持 gaussian_exec / env_setup / timeout / solver / qchem_exec
        direct_kw = {k: v for k, v in kwargs.items()
                     if k in ("gaussian_exec", "env_setup", "timeout",
                              "command", "solver", "qchem_exec")}
        return DirectExecutor(ssh, cfg, **direct_kw)
    if mode == "slurm":
        # slurm 不支持 timeout 构造参数（轮询超时由 wait() 接收）
        slurm_kw = {k: v for k, v in kwargs.items()
                    if k in ("gaussian_exec", "env_setup", "partition",
                             "nodes", "ntasks", "cpus_per_task",
                             "mem_per_cpu", "mem", "walltime", "command",
                             "solver", "qchem_exec")}
        return SlurmExecutor(ssh, cfg, **slurm_kw)
    raise ConfigError(f"未知 execution_mode: {mode}")
