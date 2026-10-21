"""gtransfer_lib 包 — remote-sync 核心库。

提供 config / connection / transport / executor / status / guard 六个子模块，
供 CLI 与第三方调用；所有 I/O 抽象为接口，便于 mock 测试。

`guard` 为合规策略引擎（hard-enforcement harness）：把中国科大超算中心等
受管集群的作业提交规定固化为可执行策略，在 I/O 之前硬阻断越权操作。
"""
from .config import ConfigError, ServerProfile, TransferConfig, load_config
from .guard import (
    PolicyDecision,
    PolicyGuard,
    PolicyViolation,
    QuotaPolicy,
    ServerClass,
    asset_sync_hint,
    build_quota_probe_commands,
    classify_server,
    managed_tool_assets,
    resolve_asset,
)

__all__ = [
    "ConfigError",
    "ServerProfile",
    "TransferConfig",
    "load_config",
    "PolicyDecision",
    "PolicyGuard",
    "PolicyViolation",
    "QuotaPolicy",
    "ServerClass",
    "asset_sync_hint",
    "build_quota_probe_commands",
    "classify_server",
    "managed_tool_assets",
    "resolve_asset",
]
