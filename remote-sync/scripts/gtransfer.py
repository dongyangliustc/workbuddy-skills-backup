#!/usr/bin/env python3
"""gtransfer.py — remote-sync 命令行入口。

人类与智能体共用。所有子命令输出 JSON 到 stdout，便于程序解析。

用法：
  gtransfer ping   --config <path> [--server <name>]
  gtransfer check  --config <path> [--server <name>] [--probe]
  gtransfer submit --config <path> --local <path|dir> --remote <path>
                   [--mode direct|slurm] [--solver gaussian|qchem]
                   [--download-glob "*.log *.chk"]
                   [--gaussian-exec g16] [--qchem-exec qchem]
                   [--command "python main.py"]
                   [--env-setup "..."] [--timeout 3600]
                   [--partition wzacnormal] [--mem 530G]
                   [--cpus-per-task 16] [--ntasks 1] [--nodes 1]
                   [--walltime 24:00:00]
                   [--dry-run]
  gtransfer status --config <path> --job <job_id> [--server <name>]
  gtransfer fetch  --config <path> --job <job_id> [--glob "..."] [--out <dir>]

退出码：0 = 成功 / SUCCESS；1 = 作业失败；2 = 参数/配置错误；3 = 传输错误；
        4 = 合规策略拦截（越权/违规提交，详见 policy 字段）。

求解器：solver=gaussian（.gjf → .log）| solver=qchem（.in → .out，三参数调用）。

合规 harness：所有提交在 I/O 之前经过 PolicyGuard 硬校验。受管超算集群
（如 USTC 瀚海22）与商业云超算（如 HPCCube）均强制 slurm 模式、禁用 direct、
命令须过白/黑名单、作业规模与并发受配额闸门约束。配置项 `server_class` 可显式
声明服务器类别；`address_family`（auto/ipv4/ipv6）应对 IPv6 路由不可达场景。
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import shlex
import sys
import time
from pathlib import Path

# 允许以源码方式运行（scripts/ 下直接 python gtransfer.py）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gtransfer_lib.config import ConfigError, TransferConfig, load_config  # noqa: E402
from gtransfer_lib.connection import ParamikoSsh  # noqa: E402
from gtransfer_lib.executor import create_executor  # noqa: E402
from gtransfer_lib.guard import (  # noqa: E402
    PolicyGuard,
    PolicyViolation,
    ServerClass,
    build_quota_probe_commands,
)
from gtransfer_lib.transport import SftpTransfer, TransportError  # noqa: E402
from gtransfer_lib.status import JobStatus  # noqa: E402

EXIT_OK = 0
EXIT_JOB_FAILED = 1
EXIT_USAGE = 2
EXIT_TRANSPORT = 3
EXIT_POLICY = 4


def _emit(payload: dict, exit_code: int = EXIT_OK) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


def _emit_policy_violation(exc: PolicyViolation, **extra) -> int:
    """统一输出策略违规（硬阻断）。exit code 恒为 EXIT_POLICY。"""
    payload = {
        "ok": False,
        "policy": True,
        "error": str(exc),
        "rule_id": exc.rule_id,
        "detail": exc.detail,
        "authority": exc.authority,
        "remedy": exc.remedy,
    }
    payload.update(extra)
    return _emit(payload, EXIT_POLICY)


def _probe_quota(ssh, user: str) -> dict:
    """在真实连接上执行配额探测，返回 {running_jobs, queued_jobs, disk_free_gb, raw}。"""
    cmds = build_quota_probe_commands(user)
    out: dict = {"raw": {}}
    for key, cmd in cmds.items():
        try:
            sout, _, _ = ssh.exec_command(cmd)
            out["raw"][key] = sout.strip()
        except Exception as exc:  # noqa: BLE001
            out["raw"][key] = f"<probe failed: {exc}>"

    def _as_int(val: str) -> int | None:
        v = re.sub(r"\D", "", val or "")
        return int(v) if v else None

    out["running_jobs"] = _as_int(out["raw"].get("running_jobs", ""))
    out["queued_jobs"] = _as_int(out["raw"].get("queued_jobs", ""))
    out["disk_free_gb"] = _as_int(out["raw"].get("disk_usage", ""))
    return out


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------

def cmd_ping(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config, args.server)
        if args.port:
            cfg = _override_port(cfg, args.port)
    except ConfigError as exc:
        return _emit({"ok": False, "error": str(exc)}, EXIT_USAGE)

    if args.dry_run:
        return _emit({
            "ok": True,
            "dry_run": True,
            "would_connect": {
                "host": cfg.server.host,
                "port": cfg.server.port,
                "user": cfg.server.user,
                "key_file": cfg.server.key_file,
                "address_family": cfg.server.address_family,
                "execution_mode": cfg.server.execution_mode,
                "solver": cfg.server.solver,
            },
        })

    try:
        with ParamikoSsh(cfg.server) as ssh:
            out, _, code = ssh.exec_command("echo ok && hostname && date +%s")
        return _emit({
            "ok": code == 0,
            "host": cfg.server.host,
            "address_family": cfg.server.address_family,
            "remote": out.strip(),
            "execution_mode": cfg.server.execution_mode,
            "solver": cfg.server.solver,
        })
    except (ConfigError, Exception) as exc:  # noqa: BLE001
        return _emit({"ok": False, "error": str(exc)}, EXIT_USAGE)


# ---------------------------------------------------------------------------
# check — 合规自检
# ---------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    """合规自检：报告服务器类别、适用策略、配额闸门与（可选）远端实时配额。

    只读操作：不产生任何 I/O 副作用（--probe 仅执行查询类命令）。
    """
    try:
        cfg = load_config(args.config, args.server)
        if args.port:
            cfg = _override_port(cfg, args.port)
        if args.server_class:
            cfg = _override_server_class(cfg, args.server_class)
    except ConfigError as exc:
        return _emit({"ok": False, "error": str(exc)}, EXIT_USAGE)

    guard = PolicyGuard(cfg)
    q = guard.quota
    payload: dict = {
        "ok": True,
        "server": cfg.server.name,
        "host": cfg.server.host,
        "user": cfg.server.user,
        "address_family": cfg.server.address_family,
        "execution_mode": cfg.server.execution_mode,
        "solver": cfg.server.solver,
        "qchem_exec": cfg.server.qchem_exec,
        "server_class": guard.server_class,
        "server_label": guard.server_label,
        "policy_source": guard.policy_source_name,
        "managed_cluster": guard.is_managed_cluster(),
        "commercial_cluster": guard.is_commercial_cluster(),
        "compute_restricted": guard.is_compute_restricted(),
        "enforced": {
            "direct_mode": (
                "FORBIDDEN" if guard.is_compute_restricted() else "allowed"
            ),
            "command_policy": (
                "whitelist+blacklist" if guard.is_compute_restricted()
                else "blacklist"
            ),
            "remote_work_root": cfg.server.remote_work_root,
        },
        "quota_gates": {
            "max_concurrent_jobs": q.max_concurrent_jobs,
            "max_queued_jobs": q.max_queued_jobs,
            "max_cpus_per_job": q.max_cpus_per_job,
            "max_walltime_hours": q.max_walltime_hours,
            "min_disk_free_gb": q.min_disk_free_gb,
            "max_mem_per_cpu_mb": cfg.server.max_mem_per_cpu_mb or None,
        },
        "slurm_defaults": {
            "partition": cfg.server.partition or None,
            "cpus_per_task": cfg.server.cpus_per_task or None,
            "ntasks": cfg.server.ntasks or None,
            "nodes": cfg.server.nodes or None,
            "mem": cfg.server.mem or None,
            "mem_per_cpu": cfg.server.mem_per_cpu or None,
            "walltime": cfg.server.walltime or None,
        },
    }

    # 当前配置的合法性自检（不含远端查询）
    config_violations: list[dict] = []
    try:
        guard.check_execution_mode(cfg.server.execution_mode)
    except PolicyViolation as exc:
        config_violations.append({
            "rule_id": exc.rule_id, "detail": exc.detail, "remedy": exc.remedy,
        })
    try:
        guard.check_remote_path(cfg.server.remote_work_root)
    except PolicyViolation as exc:
        config_violations.append({
            "rule_id": exc.rule_id, "detail": exc.detail, "remedy": exc.remedy,
        })
    try:
        guard.check_command(cfg.server.command or None)
    except PolicyViolation as exc:
        config_violations.append({
            "rule_id": exc.rule_id, "detail": exc.detail, "remedy": exc.remedy,
        })
    payload["config_violations"] = config_violations
    if config_violations:
        payload["ok"] = False

    if args.probe:
        if args.dry_run:
            payload["probe"] = {"skipped": "dry-run"}
        else:
            try:
                with ParamikoSsh(cfg.server) as ssh:
                    probe = _probe_quota(ssh, cfg.server.user)
                payload["probe"] = probe
                # 实时配额闸门判定
                if probe.get("running_jobs") is not None or \
                        probe.get("queued_jobs") is not None:
                    try:
                        guard.check_concurrency(
                            probe.get("running_jobs") or 0,
                            probe.get("queued_jobs") or 0,
                        )
                        payload["probe"]["concurrency_gate"] = "ok"
                    except PolicyViolation as exc:
                        payload["probe"]["concurrency_gate"] = "blocked"
                        payload["probe"]["gate_violation"] = {
                            "rule_id": exc.rule_id,
                            "detail": exc.detail,
                            "remedy": exc.remedy,
                        }
                        payload["ok"] = False
                dg = probe.get("disk_free_gb")
                if dg is not None:
                    if dg < q.min_disk_free_gb:
                        payload["ok"] = False
                        payload["probe"]["disk_gate"] = (
                            f"blocked: 剩余 {dg}GB < 阈值 {q.min_disk_free_gb}GB"
                        )
                    else:
                        payload["probe"]["disk_gate"] = f"ok ({dg}GB)"
            except Exception as exc:  # noqa: BLE001
                payload["probe"] = {"error": str(exc)}

    # 数据性质 / 数据保全强制提醒（按类别给出对应平台的出处）
    src = guard.policy_source_name or "平台使用规范"
    payload["mandatory_notices"] = [
        "严禁处理/存储国家秘密、工作秘密、商业秘密及未公开的敏感科研数据。",
        f"{src}：平台不提供备份或恢复服务，任务完成后须及时回传并清理远端数据。",
    ]
    return _emit(payload, EXIT_OK if payload["ok"] else EXIT_POLICY)


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------

def _expand_tilde(path: str, username: str = "") -> str:
    """展开路径中的波浪号 (~) 为远端 home 目录的合理猜测。

    在 Windows 上 posixpath.expanduser 无效（展开为 Windows 用户目录），
    所以手动替换 ~ 前缀为 /home/<user>。
    """
    if path.startswith("~/"):
        user_home = f"/home/{username}" if username else "/home"
        return f"{user_home}/{path[2:]}"
    if path == "~":
        return f"/home/{username}" if username else "/home"
    return path


def _resolve_slurm_opts(cfg: TransferConfig, args: argparse.Namespace) -> dict:
    """解析 Slurm 作业参数：CLI 显式值 > profile 配置 > 内置默认。

    动机（2026-10-14）：平台口径相对固定（分区名、单核内存上限、墙钟），
    每次提交都手传既繁琐又易错 —— 例如内置默认 `--mem-per-cpu=4G` 在
    HPCCube 会因 4096MB > DefMemPerCPU(3944MB) 被 sbatch 直接拒绝。
    允许 profile 固化这些默认值后，同一条提交命令可在不同平台直接复用。
    """
    p = cfg.server
    return {
        "partition": args.partition or p.partition or None,
        "cpus_per_task": args.cpus_per_task or p.cpus_per_task or 16,
        "ntasks": args.ntasks or p.ntasks or 1,
        "nodes": args.nodes or p.nodes or 1,
        "walltime": args.walltime or p.walltime or "24:00:00",
        "mem": args.mem or p.mem or None,
        "mem_per_cpu": args.mem_per_cpu or p.mem_per_cpu or "4G",
    }


def cmd_submit(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config, args.server)
        if args.mode:
            cfg = _override_mode(cfg, args.mode)
        if args.port:
            cfg = _override_port(cfg, args.port)
        if args.server_class:
            cfg = _override_server_class(cfg, args.server_class)
    except ConfigError as exc:
        return _emit({"ok": False, "error": str(exc)}, EXIT_USAGE)

    local = Path(args.local).expanduser()
    if not local.exists():
        return _emit({"ok": False, "error": f"本地路径不存在: {local}"}, EXIT_USAGE)

    slurm = _resolve_slurm_opts(cfg, args)

    remote_root = cfg.server.remote_work_root
    if args.remote:
        # 显式远端目录：规范化后必须等于 root 或位于 root 下（防 `~/gt2` 前缀绕过）
        rp = posixpath.normpath(args.remote.replace("\\", "/"))
        remote_work = rp
    else:
        # 远端任务目录：任务名 = 本地名（去扩展名）或用户指定
        task_name = args.task_name or (local.stem if local.is_file() else local.name)
        remote_work = f"{remote_root.rstrip('/')}/{task_name}"

    # --- 合规硬校验（第一道防线，任何 I/O 之前）---------------------------
    guard = PolicyGuard(cfg)
    try:
        decision = guard.preflight(
            mode=cfg.server.execution_mode,
            remote=remote_work,
            command=args.command or cfg.server.command or None,
            cpus_per_task=slurm["cpus_per_task"],
            walltime=slurm["walltime"],
            nodes=slurm["nodes"],
            ntasks=slurm["ntasks"],
            download_glob=args.download_glob or cfg.download_glob,
            mem=slurm["mem"],
        )
    except PolicyViolation as exc:
        return _emit_policy_violation(exc, remote_work=remote_work,
                                      server=cfg.server.name,
                                      server_class=guard.server_class)

    ops: list[dict] = [
        {"op": "upload", "local": str(local), "remote": remote_work},
        {"op": "exec", "mode": cfg.server.execution_mode,
         "solver": args.solver or cfg.server.solver,
         "workdir": remote_work, "input": local.name,
         "slurm": {k: slurm[k] for k in
                   ("partition", "cpus_per_task", "ntasks", "nodes",
                    "walltime", "mem", "mem_per_cpu")}},
        {"op": "download", "glob": args.download_glob or cfg.download_glob,
         "remote_dir": remote_work},
        {"op": "cleanup_remote", "keep": cfg.keep_remote},
    ]

    if args.dry_run:
        payload = {
            "ok": True,
            "dry_run": True,
            "server": cfg.server.name,
            "server_class": guard.server_class,
            "server_label": guard.server_label,
            "solver": args.solver or cfg.server.solver,
            "remote_work": remote_work,
            "operations": ops,
            "policy": decision.to_dict(),
        }
        return _emit(payload)

    t0 = time.monotonic()
    result = {"ok": True, "server": cfg.server.name, "remote_work": remote_work,
              "server_class": guard.server_class,
              "operations": [], "state": None,
              "files": {"uploaded": [], "downloaded": []}}

    try:
        with ParamikoSsh(cfg.server) as ssh:
            # 0. 提交前配额自检（受管集群强制；探测失败不阻断但记录）
            try:
                probe = _probe_quota(ssh, cfg.server.user)
                result["operations"].append({"op": "quota_probe", **{
                    k: probe.get(k) for k in
                    ("running_jobs", "queued_jobs", "disk_free_gb")
                }})
                if probe.get("running_jobs") is not None or \
                        probe.get("queued_jobs") is not None:
                    guard.check_concurrency(
                        probe.get("running_jobs") or 0,
                        probe.get("queued_jobs") or 0,
                    )
                dg = probe.get("disk_free_gb")
                if dg is not None and dg < guard.quota.min_disk_free_gb:
                    raise PolicyViolation(
                        "POLICY-DISK-LOW",
                        f"远端剩余磁盘 {dg}GB，低于自设下限 "
                        f"{guard.quota.min_disk_free_gb}GB",
                        authority="中心规定：磁盘配额有限，且不提供备份/恢复服务",
                        remedy="清理远端旧任务目录后重新提交",
                    )
            except PolicyViolation:
                raise
            except Exception as exc:  # noqa: BLE001
                result["operations"].append({
                    "op": "quota_probe", "status": "skipped",
                    "reason": str(exc)[:200],
                })

            # 1. 上传
            transfer = SftpTransfer(ssh, cfg)
            uploaded = transfer.upload(local, remote_work)
            result["operations"].append({"op": "upload", "files": uploaded})
            result["files"]["uploaded"] = uploaded

            # 1b. 上传内容合规检查（与科研计算无关的文件 → 告警）
            irrelevant = guard.check_upload_content(uploaded)
            if irrelevant:
                result["operations"].append({
                    "op": "content_check",
                    "status": "warning",
                    "irrelevant_files": irrelevant,
                    "notice": "中心规定：用户账号目录不得上传保存与科研计算无关的文件",
                })

            # 1b. 上传后验证：检查远端文件确实是文件（非目录），且数量匹配
            remote_abs = ssh.expand_remote(remote_work)
            verify_cmd = (
                f"find {shlex.quote(remote_abs)} -maxdepth 1 -type f | wc -l"
            )
            vout, _, _ = ssh.exec_command(verify_cmd)
            vout_clean = vout.strip()
            remote_file_count = int(vout_clean) if vout_clean.isdigit() else -1
            expected_count = len(uploaded)
            if remote_file_count == -1:
                # 远端 find 不可用或返回非数字（mock 环境等），跳过验证
                result["operations"].append({
                    "op": "verify_upload",
                    "remote_files": "unknown",
                    "expected": expected_count,
                    "status": "skipped",
                })
            elif remote_file_count == 0:
                return _emit({
                    "ok": False,
                    "error": f"上传后验证失败：远端 {remote_abs} 无文件 "
                             f"(find 返回 '{vout_clean}')。"
                             f"可能 SftpTransfer 误将文件上传为目录。",
                    "remote_work": remote_work,
                    "uploaded_count": expected_count,
                    "remote_file_count": remote_file_count,
                }, EXIT_TRANSPORT)
            else:
                result["operations"].append({
                    "op": "verify_upload",
                    "remote_files": remote_file_count,
                    "expected": expected_count,
                    "status": "ok",
                })

            # 2. 执行（CLI 参数优先，未指定时回退到 profile 配置的求解器/环境/作业参数）
            executor = create_executor(
                ssh, cfg,
                gaussian_exec=args.gaussian_exec or cfg.server.gaussian_exec,
                qchem_exec=args.qchem_exec or cfg.server.qchem_exec,
                solver=args.solver or cfg.server.solver,
                command=args.command or cfg.server.command or None,
                env_setup=args.env_setup or cfg.server.env_setup,
                timeout=args.timeout,
                partition=slurm["partition"],
                mem=slurm["mem"],
                mem_per_cpu=slurm["mem_per_cpu"],
                nodes=slurm["nodes"],
                ntasks=slurm["ntasks"],
                cpus_per_task=slurm["cpus_per_task"],
                walltime=slurm["walltime"],
            )
            state = executor.submit(remote_work, local.name)
            result["state"] = state.to_dict()
            result["operations"].append({"op": "exec", "state": state.status.value})

            # 2b. 提交后启动验证（仅真实 SLURM 模式）：等待 10 秒后检查作业状态 + 日志
            # 跳过条件：mock 环境（job_id 非数字）、direct 模式、已终态
            if args.mode == "slurm" and state.job_id and \
                    state.job_id.isdigit() and \
                    state.status not in (JobStatus.SUCCESS, JobStatus.FAILED):
                import time as _time
                _time.sleep(10)
                # 检查 squeue
                sout, _, _ = ssh.exec_command(
                    f"squeue -j {state.job_id} -h -o %T 2>/dev/null"
                )
                squeue_state = sout.strip()
                # 检查 gtransfer.log 是否已有内容且无错误
                log_check_cmd = (
                    f"test -s {shlex.quote(remote_abs)}/gtransfer.log && "
                    f"head -5 {shlex.quote(remote_abs)}/gtransfer.log || "
                    f"echo '__NO_LOG_YET__'"
                )
                lout, _, _ = ssh.exec_command(log_check_cmd)
                log_head = lout.strip()
                # 检测常见启动错误
                error_markers = [
                    "can't find '__main__'",
                    "Traceback (most recent call last)",
                    "ImportError",
                    "ModuleNotFoundError",
                    "No such file or directory",
                    "SyntaxError",
                ]
                startup_errors = [
                    m for m in error_markers if m in log_head
                ]
                result["operations"].append({
                    "op": "startup_check",
                    "squeue_state": squeue_state or "unknown",
                    "log_head": log_head[:200] if log_head else "",
                    "startup_errors": startup_errors,
                })
                if startup_errors:
                    # 立即报告启动错误，不等超时
                    return _emit({
                        "ok": False,
                        "error": f"作业启动失败，检测到错误标记: {startup_errors}",
                        "remote_work": remote_work,
                        "job_id": state.job_id,
                        "log_head": log_head[:500],
                        "hint": "检查上传的文件是否正确（可能是目录而非文件），"
                                "或远端 Python 环境是否正确。",
                    }, EXIT_JOB_FAILED)

            # 3. 同步等待终态（direct 已阻塞返回；slurm 在此轮询）
            if state.status not in (JobStatus.SUCCESS, JobStatus.FAILED):
                state = executor.wait(state.job_id, timeout=args.timeout)
                result["state"] = state.to_dict()
                result["operations"].append({"op": "wait", "state": state.status.value})

            if state.status == JobStatus.FAILED:
                result["ok"] = False
                result["error"] = state.error_hint

            # 4. 回传（即使失败也抓回日志便于排查）
            downloaded = transfer.download(
                remote_work, args.out_dir,
                glob_pattern=args.download_glob or cfg.download_glob,
            )
            result["files"]["downloaded"] = downloaded
            result["operations"].append({"op": "download", "files": downloaded})

            # 5. 清理远端（路径须展开 ~ 为绝对路径，否则单引号锁死波浪号）
            if not cfg.keep_remote:
                remote_abs = ssh.expand_remote(remote_work)
                ssh.exec_command(f"rm -rf {shlex.quote(remote_abs)}")
                result["operations"].append({"op": "cleanup_remote", "kept": False})

        result["duration_sec"] = round(time.monotonic() - t0, 2)
        code = EXIT_OK if result["ok"] else EXIT_JOB_FAILED
        return _emit(result, code)
    except PolicyViolation as exc:
        return _emit_policy_violation(exc, remote_work=remote_work,
                                      server=cfg.server.name,
                                      server_class=guard.server_class)
    except TransportError as exc:
        return _emit({"ok": False, "error": f"传输错误: {exc}",
                      "remote_work": remote_work}, EXIT_TRANSPORT)
    except (ConfigError, Exception) as exc:  # noqa: BLE001
        return _emit({"ok": False, "error": str(exc), "remote_work": remote_work}, EXIT_USAGE)


def _override_mode(cfg: TransferConfig, mode: str) -> TransferConfig:
    """构造 execution_mode 被 CLI 覆盖的副本。"""
    from dataclasses import replace
    return replace(cfg, server=replace(cfg.server, execution_mode=mode))


def _override_port(cfg: TransferConfig, port: int) -> TransferConfig:
    """构造 port 被 CLI 覆盖的副本。"""
    from dataclasses import replace
    return replace(cfg, server=replace(cfg.server, port=port))


def _override_server_class(cfg: TransferConfig, server_class: str) -> TransferConfig:
    """构造 server_class 被 CLI 覆盖的副本。"""
    from dataclasses import replace
    return replace(cfg, server=replace(cfg.server, extra_class=server_class.strip().lower()))


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config, args.server)
        if args.port:
            cfg = _override_port(cfg, args.port)
    except ConfigError as exc:
        return _emit({"ok": False, "error": str(exc)}, EXIT_USAGE)

    if args.dry_run:
        return _emit({"ok": True, "dry_run": True, "would_query": args.job})

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.job):
        return _emit({"ok": False, "error": f"非法 job 名: {args.job!r}"}, EXIT_USAGE)

    try:
        with ParamikoSsh(cfg.server) as ssh:
            executor = create_executor(ssh, cfg)
            state = executor.query(args.job)
        return _emit({"ok": True, **state.to_dict()},
                     EXIT_OK if state.status != JobStatus.FAILED else EXIT_JOB_FAILED)
    except Exception as exc:  # noqa: BLE001
        return _emit({"ok": False, "error": str(exc)}, EXIT_USAGE)


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def cmd_fetch(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config, args.server)
        if args.port:
            cfg = _override_port(cfg, args.port)
    except ConfigError as exc:
        return _emit({"ok": False, "error": str(exc)}, EXIT_USAGE)

    remote_root = cfg.server.remote_work_root
    # job 名只允许安全字符集，且拼接后必须在 root 内
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.job):
        return _emit({"ok": False, "error": f"非法 job 名: {args.job!r}"}, EXIT_USAGE)
    remote_dir = f"{remote_root.rstrip('/')}/{args.job}"
    root_norm = posixpath.normpath(remote_root)
    if remote_dir != root_norm and not remote_dir.startswith(root_norm + "/"):
        return _emit({"ok": False, "error": f"job 越界: {remote_dir}"}, EXIT_USAGE)

    if args.dry_run:
        return _emit({"ok": True, "dry_run": True,
                      "would_fetch_from": remote_dir,
                      "glob": args.glob or cfg.download_glob,
                      "out": args.out})

    try:
        with ParamikoSsh(cfg.server) as ssh:
            transfer = SftpTransfer(ssh, cfg)
            files = transfer.download(remote_dir, args.out,
                                      glob_pattern=args.glob or cfg.download_glob)
        return _emit({"ok": True, "job": args.job, "files": files})
    except TransportError as exc:
        return _emit({"ok": False, "error": f"传输错误: {exc}"}, EXIT_TRANSPORT)
    except Exception as exc:  # noqa: BLE001
        return _emit({"ok": False, "error": str(exc)}, EXIT_USAGE)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gtransfer",
        description="remote-sync: 本地↔服务器数据传输与远端 Gaussian 执行",
    )
    # 全局参数：同时挂到每个子命令（parents），
    # 允许 --config 出现在子命令前或后（argparse 主解析器限制）
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=None,
                        help="用户配置文件路径（默认 ~/.workbuddy/transfer.conf）")
    common.add_argument("--server", default="default", help="server profile 名")
    common.add_argument("--port", type=int, default=None,
                        help="覆盖 transfer.conf 中的 SSH 端口")
    common.add_argument("--dry-run", action="store_true",
                        help="只打印操作序列，不产生真实 I/O")
    common.add_argument("--server-class", default=None,
                        choices=[ServerClass.MANAGED_CLUSTER,
                                 ServerClass.COMMERCIAL_CLUSTER,
                                 ServerClass.WORKSTATION],
                        dest="server_class",
                        help="覆盖服务器类别（受管集群/商业云强制 slurm 并启用命令白名单）")
    parser.add_argument("--config", default=None, dest="config")
    parser.add_argument("--server", default="default", dest="server")
    parser.add_argument("--port", type=int, default=None, dest="port")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    parser.add_argument("--server-class", default=None, dest="server_class",
                        choices=[ServerClass.MANAGED_CLUSTER,
                                 ServerClass.COMMERCIAL_CLUSTER,
                                 ServerClass.WORKSTATION])

    sub = parser.add_subparsers(dest="command", required=True)

    p_ping = sub.add_parser("ping", parents=[common], help="测试 SSH 连接")
    p_ping.set_defaults(func=cmd_ping)

    p_check = sub.add_parser("check", parents=[common],
                             help="合规自检：报告服务器类别/策略/配额闸门")
    p_check.add_argument("--probe", action="store_true",
                         help="连远端查询实时配额（squeue/df/sacctmgr，只读）")
    p_check.set_defaults(func=cmd_check)

    p_submit = sub.add_parser("submit", parents=[common],
                              help="上传 → 执行 → 回传 全流程")
    p_submit.add_argument("--local", required=True, help="本地 .gjf/.in 文件或目录")
    p_submit.add_argument("--remote", default=None,
                          help="远端工作目录（默认 remote_work_root/任务名）")
    p_submit.add_argument("--task-name", default=None, help="远端任务目录名")
    p_submit.add_argument("--mode", choices=["direct", "slurm"], default=None,
                          help="覆盖 execution_mode")
    p_submit.add_argument("--download-glob", default=None,
                          help="回传文件 glob（如 '*.log *.chk' / '*.out *.fchk'）")
    p_submit.add_argument("--out-dir", default="results",
                          help="回传文件的本地存放目录")
    p_submit.add_argument("--solver", choices=["gaussian", "qchem"], default=None,
                          help="求解器类型（默认取配置 solver；qchem 走 .in → .out）")
    p_submit.add_argument("--gaussian-exec", default=None,
                          help="Gaussian 可执行名/路径（默认取配置 gaussian_exec）")
    p_submit.add_argument("--qchem-exec", default=None,
                          help="Q-Chem 可执行名/路径（默认取配置 qchem_exec）")
    p_submit.add_argument("--command", default=None,
                          help="通用命令模式：执行任意远端命令（如 'python main.py'），"
                               "取代默认求解器流程")
    p_submit.add_argument("--env-setup", default="", help="远端环境设置命令")
    p_submit.add_argument("--timeout", type=float, default=None, help="轮询超时秒数")
    # 作业资源参数（参与合规配额闸门校验）
    # 默认值留空 → 依次回退到 profile 配置、内置默认（见 _resolve_slurm_opts）。
    # 这样平台固定口径（分区、单核内存上限、墙钟）可一次配置、处处生效。
    p_submit.add_argument("--partition", default=None,
                          help="Slurm 分区（如 HPCCube 的 wzacnormal；默认取配置 partition）")
    p_submit.add_argument("--cpus-per-task", type=int, default=None,
                          help="Slurm 每任务 CPU 核数（默认取配置 cpus_per_task，否则 16）")
    p_submit.add_argument("--ntasks", type=int, default=None,
                          help="Slurm 任务数（默认取配置 ntasks，否则 1）")
    p_submit.add_argument("--nodes", type=int, default=None,
                          help="Slurm 节点数（默认取配置 nodes，否则 1）")
    p_submit.add_argument("--mem", default=None,
                          help="Slurm 整节点内存声明（如 7G）；指定时取代 --mem-per-cpu")
    p_submit.add_argument("--mem-per-cpu", default=None,
                          help="Slurm 单核内存（默认取配置 mem_per_cpu，否则 4G）")
    p_submit.add_argument("--walltime", default=None,
                          help="Slurm 墙钟时间，格式 [D-]HH:MM:SS（默认取配置 walltime，否则 24:00:00）")
    p_submit.set_defaults(func=cmd_submit)

    p_status = sub.add_parser("status", parents=[common], help="查询作业状态")
    p_status.add_argument("--job", required=True, help="作业 id（任务名或 slurm jobid）")
    p_status.set_defaults(func=cmd_status)

    p_fetch = sub.add_parser("fetch", parents=[common], help="回传远端结果到本地")
    p_fetch.add_argument("--job", required=True, help="远端任务目录名")
    p_fetch.add_argument("--glob", default=None, help="回传 glob（默认 *.log）")
    p_fetch.add_argument("--out", default="results", help="本地存放目录")
    p_fetch.set_defaults(func=cmd_fetch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
