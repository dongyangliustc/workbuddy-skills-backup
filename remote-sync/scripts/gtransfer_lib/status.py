"""status.py — 作业状态机与结果解析。

状态流转：
    PENDING → RUNNING → SUCCESS
                  ↓
                FAILED

direct 模式：进程退出码 0 = SUCCESS，非 0 = FAILED。
slurm 模式：sbatch 提交得 jobid → PENDING；squeue 中存在 = RUNNING；
          消失后查 sacct 退出码定 SUCCESS/FAILED。

本模块独立于传输/执行层，供同步等待与未来异步查询共用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass
class JobState:
    """单个远端作业的完整状态。"""
    job_id: str                 # 远端任务目录名 或 slurm jobid
    status: JobStatus
    exit_code: Optional[int] = None
    error_hint: Optional[str] = None    # 从日志/退出信息提取的失败线索
    remote_dir: Optional[str] = None
    duration_sec: Optional[float] = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "error_hint": self.error_hint,
            "remote_dir": self.remote_dir,
            "duration_sec": self.duration_sec,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Gaussian 日志错误识别
# ---------------------------------------------------------------------------

# 典型 Gaussian 错误码（思想家公社常见问题中高频出现的 L 码，3-4 位）
_ERROR_PATTERNS: list[tuple[str, object]] = [
    (r"Error termination\s*$", "Gaussian 错误终止"),
    (r"L(\d{3,4})(?!\d)", lambda m: f"Gaussian 错误码 L{m.group(1)}"),
    (r"Error\s*:\s*(\w+.*)", lambda m: m.group(1).strip()),
    (r"\*\*\*\*\*\s*PROGRAM\s*TERMINATED", "程序被外部终止"),
]


def parse_gaussian_error(log_tail: str) -> Optional[str]:
    """从日志尾部文本提取失败线索，无匹配返回 None。"""
    for pattern, handler in _ERROR_PATTERNS:
        m = re.search(pattern, log_tail, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            return handler(m) if callable(handler) else handler
    return None


# ---------------------------------------------------------------------------
# Q-Chem 日志错误识别（2026-10-14 引入）
# ---------------------------------------------------------------------------
#
# Q-Chem 与 Gaussian 的错误呈现方式不同：Gaussian 用 L 码，Q-Chem 用
# "Q-Chem fatal error occurred in module XXX" 段落 + 尾部 "Thank you very much
# for running Q-Chem" 收尾。**关键陷阱**：Q-Chem 即使致命报错也常以正常文案
# 收尾、退出码仍为 0 或非 0 不定，不能只看退出码。
#
# 顺序敏感：致命错误需先于通用 Error: 匹配。

_QCHEM_ERROR_PATTERNS: list[tuple[str, object]] = [
    # 许可证类最先匹配：Q-Chem 无可用 license 时会在欢迎信息后直接退出，
    # 输出极短（仅数百字节），若不做识别很容易被误当作"正常完成"。
    # 实测样例（HPCCube 2026-10-14）：
    #   FlexNet Licensing error:-97,121 / The desired vendor daemon is down
    (r"FlexNet Licensing error:\s*(-?\d+)",
     lambda m: f"Q-Chem 许可证错误（FlexNet {m.group(1)}，许可证服务不可用）"),
    (r"The desired vendor daemon is down", "Q-Chem 许可证守护进程未运行"),
    (r"Cannot connect to license server|License server machine is down",
     "Q-Chem 无法连接许可证服务器"),
    (r"Q-Chem fatal error occurred in module\s+(\S+)",
     lambda m: f"Q-Chem 致命错误（模块 {m.group(1).strip()}）"),
    (r"Q-Chem was unable to", "Q-Chem 无法完成计算"),
    (r"(?:^|\n)\s*Error:\s*(.+)",
     lambda m: f"Q-Chem 报错: {m.group(1).strip()[:120]}"),
    (r"The (?:SCF|CCSD|CCSD\(T\)|MP2)\s+failed to converge",
     lambda m: f"{m.group(0).strip()}（未收敛）"),
    (r"RESTART.*failed|SCF convergence failure", "SCF 收敛失败"),
    (r"malloc.*failed|out of memory|Out of memory",
     "内存不足（请调高 --mem 或减小 mem_total）"),
    (r"Cannot open.*\.in file|Input file does not exist", "输入文件缺失/不可读"),
]

#: 求解器名 → 错误解析函数（classify_exit 的 solver 参数查表用）
_ERROR_PARSERS = {
    "gaussian": parse_gaussian_error,
    "qchem": lambda tail: _parse_by_patterns(tail, _QCHEM_ERROR_PATTERNS),
}


def _parse_by_patterns(
    log_tail: str, patterns: list[tuple[str, object]]
) -> Optional[str]:
    for pattern, handler in patterns:
        m = re.search(pattern, log_tail, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            return handler(m) if callable(handler) else handler
    return None


def parse_qchem_error(log_tail: str) -> Optional[str]:
    """从 Q-Chem 输出尾部提取失败线索，无匹配返回 None。"""
    return _parse_by_patterns(log_tail, _QCHEM_ERROR_PATTERNS)


def classify_exit(
    exit_code: Optional[int],
    log_tail: str = "",
    solver: str = "gaussian",
) -> tuple[JobStatus, Optional[str]]:
    """根据退出码 + 日志尾部判定最终状态。

    返回 (status, error_hint)。

    - Gaussian 常以 exit 0 结束但日志含错误（如 L 码终止）；
    - Q-Chem 致命错误亦常伴随正常收尾文案。
    两者均以**日志优先于退出码**判定 FAILED。

    solver: "gaussian"（默认，保持既有行为）| "qchem"。
    """
    parser = _ERROR_PARSERS.get((solver or "gaussian").strip().lower(),
                                parse_gaussian_error)
    hint = parser(log_tail)
    if hint:
        return JobStatus.FAILED, hint
    if exit_code == 0:
        return JobStatus.SUCCESS, None
    return JobStatus.FAILED, f"退出码 {exit_code}"


# ---------------------------------------------------------------------------
# 轮询
# ---------------------------------------------------------------------------

def poll_until_terminal(
    state_fn,
    poll_interval: float,
    timeout: Optional[float] = None,
    on_poll=None,
) -> JobState:
    """阻塞轮询直到状态进入终态（SUCCESS/FAILED）。

    - state_fn() → JobState：每次轮询调用，返回当前状态
    - poll_interval：轮询间隔秒数
    - timeout：可选超时（秒），超时抛 TimeoutError
    - on_poll：可选回调，收到 RUNNING 状态时调用（供日志/进度输出）
    """
    import time

    start = time.monotonic()
    while True:
        state = state_fn()
        if state.status in (JobStatus.SUCCESS, JobStatus.FAILED):
            if state.duration_sec is None:
                state.duration_sec = time.monotonic() - start
            return state
        if on_poll is not None:
            on_poll(state)
        if timeout is not None and (time.monotonic() - start) > timeout:
            raise TimeoutError(
                f"作业 {state.job_id} 在 {timeout}s 内未进入终态（当前 {state.status.value}）"
            )
        time.sleep(poll_interval)
