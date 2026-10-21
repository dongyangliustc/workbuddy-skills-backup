"""connection.py — SSH 连接层。

职责：
1. 基于 ServerProfile 建立/复用 paramiko SSH 连接（仅密钥认证）；
2. 提供 exec_command 与 open_sftp 两个高层接口；
3. 超时与重试策略；
4. 全部能力定义在 SshConnection 协议上，测试可用 Fake 替换。

安全约定：本模块不接收任何口令明文。认证仅走密钥文件。
"""
from __future__ import annotations

import socket
import time
from abc import ABC, abstractmethod
from typing import Optional

from .config import ConfigError, ServerProfile


# ---------------------------------------------------------------------------
# 地址族解析（2026-10-14 引入）
# ---------------------------------------------------------------------------

def resolve_address(host: str, port: int, address_family: str) -> str:
    """按指定地址族解析主机，返回可直连的地址字面量。

    背景（HPCCube 实测）：目标域名同时发布 A 与 AAAA 记录，但本机 IPv6 路由
    不可达 —— 客户端按 getaddrinfo 顺序优先尝试 IPv6，TCP 连接被静默丢弃，
    表现为"DNS 正常、ping 正常、SSH 超时"。强制 IPv4 后连接耗时降至 0.3s。

    - address_family="auto"（默认）：原样返回 host，交由 paramiko/系统解析，
      不改变既有 profile 的行为。
    - "ipv4" / "ipv6"：按该族解析取首个地址；无可用地址时报 ConfigError
      （快速失败，优于长时间超时）。
    """
    family = (address_family or "auto").strip().lower()
    if family in ("", "auto"):
        return host
    want = socket.AF_INET if family == "ipv4" else socket.AF_INET6
    try:
        infos = socket.getaddrinfo(host, port, family=want, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConfigError(
            f"主机 {host} 无 {family} 地址（address_family={family}）: {exc}"
        ) from exc
    if not infos:
        raise ConfigError(f"主机 {host} 无 {family} 地址（address_family={family}）")
    return infos[0][4][0]


# ---------------------------------------------------------------------------
# 抽象协议（便于 mock）
# ---------------------------------------------------------------------------

class SshTransport(ABC):
    """SSH 会话抽象：本地代码只依赖此协议。"""

    @abstractmethod
    def exec_command(self, command: str, timeout: Optional[int] = None) -> tuple[str, str, int]:
        """执行远端命令，返回 (stdout, stderr, exit_code)。"""

    @abstractmethod
    def open_sftp(self):
        """打开 SFTP 会话对象（暴露 paramiko 兼容的 SFTP 接口）。"""

    @abstractmethod
    def close(self) -> None:
        """关闭会话。"""

    @property
    @abstractmethod
    def home_dir(self) -> str:
        """远端 HOME 绝对路径（SFTP 不展开 ~，须先解析）。"""

    def expand_remote(self, path: str) -> str:
        """将 ~/ 前缀展开为远端绝对路径（SFTP 路径必须绝对）。"""
        if path == "~":
            return self.home_dir
        if path.startswith("~/"):
            return self.home_dir + path[1:]
        return path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------------------
# TOTP 生成（RFC 6238，零依赖标准库实现）
# ---------------------------------------------------------------------------

def _totp_code(secret_b32: str, digits: int = 6, interval: int = 30,
               drift: int = 0) -> str:
    """由 Base32 secret 生成当前 TOTP 一次性验证码。

    - drift: 时间窗口偏移（0=当前窗口，-1=上一窗口，1=下一窗口）。
      超算系统常设宽窗口（如 ±8），必要时尝试相邻窗口。
    - 服务器通常要求"密码+验证码"拼接（google-authenticator PAM 模式）。
    """
    import base64
    import hashlib
    import hmac
    import struct
    import time

    # Base32 补齐 padding 后解码
    padded = secret_b32.upper() + "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(padded)

    counter = int(time.time()) // interval + drift
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return f"{code:0{digits}d}"


def _compose_password(profile: ServerProfile) -> str:
    """构造实际发送的密码：口令 + TOTP 验证码（若配置了 totp_secret）。"""
    pw = profile.password or ""
    if profile.totp_secret:
        code = _totp_code(profile.totp_secret)
        pw = f"{pw}{code}"
    return pw


# ---------------------------------------------------------------------------
# paramiko 真实实现
# ---------------------------------------------------------------------------

class ParamikoSsh(SshTransport):
    """基于 paramiko 的真实实现。paramiko 缺失时抛 ConfigError。"""

    def __init__(self, profile: ServerProfile, connect: bool = True):
        self._profile = profile
        self._client = None
        self._home_dir = ""
        if connect:
            self._connect()
            self._resolve_home()

    def _resolve_home(self) -> None:
        """解析远端 HOME（SFTP 不展开 ~，所有路径须用绝对路径）。"""
        try:
            out, _, _ = self.exec_command("echo $HOME")
            self._home_dir = out.strip()
        except Exception:  # noqa: BLE001
            self._home_dir = f"/home/{self._profile.user}"

    def _target_host(self) -> str:
        """按 profile 的 address_family 解析出实际连接目标。"""
        return resolve_address(
            self._profile.host, self._profile.port, self._profile.address_family
        )

    @property
    def home_dir(self) -> str:
        if not self._home_dir:
            self._resolve_home()
        return self._home_dir

    def _connect(self) -> None:
        try:
            import paramiko  # 延迟导入，保持 mock 测试零依赖
        except ImportError as exc:
            raise ConfigError(
                "未安装 paramiko。请执行: pip install paramiko"
            ) from exc

        # 认证参数：优先密钥，其次口令（可含 TOTP 二次验证）
        if self._profile.auth_mode == "key":
            self._connect_key(paramiko)
        elif self._profile.auth_mode == "password":
            if self._profile.totp_secret:
                # 超算类系统：keyboard-interactive 分步问密码与验证码
                self._connect_totp_interactive(paramiko)
            else:
                self._connect_password(paramiko)
        else:
            raise ConfigError(
                f"server '{self._profile.name}' 未配置认证方式。"
                "请配置 key_file（密钥）或 password（口令）字段。"
            )

    # -- 密钥认证 ------------------------------------------------------

    def _connect_key(self, paramiko) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        target = self._target_host()
        connect_kwargs = {
            "hostname": target,
            "port": self._profile.port,
            "username": self._profile.user,
            "key_filename": self._profile.key_file,
            "timeout": self._profile.connect_timeout,
            "banner_timeout": self._profile.connect_timeout,
            "auth_timeout": self._profile.connect_timeout,
            "look_for_keys": False,
            "allow_agent": False,
        }
        last_err: Optional[Exception] = None
        for attempt in range(1, self._profile.max_retries + 1):
            try:
                client.connect(**connect_kwargs)
                self._client = client
                return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt < self._profile.max_retries:
                    time.sleep(2 * attempt)
        raise ConfigError(
            f"连接 {self._profile.host}:{self._profile.port} 失败 "
            f"（实际目标 {target}，地址族 {self._profile.address_family}，"
            f"重试 {self._profile.max_retries} 次）: {last_err}"
        )

    # -- 纯口令认证 ----------------------------------------------------

    def _connect_password(self, paramiko) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        target = self._target_host()
        connect_kwargs = {
            "hostname": target,
            "port": self._profile.port,
            "username": self._profile.user,
            "password": self._profile.password,
            "timeout": self._profile.connect_timeout,
            "banner_timeout": self._profile.connect_timeout,
            "auth_timeout": self._profile.connect_timeout,
            "look_for_keys": False,
            "allow_agent": False,
        }
        last_err: Optional[Exception] = None
        for attempt in range(1, self._profile.max_retries + 1):
            try:
                client.connect(**connect_kwargs)
                self._client = client
                return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt < self._profile.max_retries:
                    time.sleep(2 * attempt)
        raise ConfigError(
            f"连接 {self._profile.host}:{self._profile.port} 失败 "
            f"（实际目标 {target}，地址族 {self._profile.address_family}，"
            f"重试 {self._profile.max_retries} 次）: {last_err}"
        )

    # -- 口令 + TOTP（keyboard-interactive 两步认证） ------------------

    def _connect_totp_interactive(self, paramiko) -> None:
        """USTC 瀚海类超算：SSH 分步询问 Password 与 Verification code。

        每轮尝试生成当前窗口验证码；窗口大小 17（±8）通常无需相邻窗口，
        但重试时顺带尝试相邻窗口提高命中率。
        注意：验证码 30 秒窗口内额度有限（超算限 3 次），连续失败可能
        触发窗口额度耗尽，重试间隔稍长。
        """
        pw = self._profile.password or ""
        target = self._target_host()
        last_err: Optional[Exception] = None
        for attempt in range(1, self._profile.max_retries + 1):
            transport = None
            try:
                code = _totp_code(self._profile.totp_secret, drift=attempt - 1)
                transport = paramiko.Transport(
                    (target, self._profile.port)
                )
                transport.banner_timeout = self._profile.connect_timeout
                transport.auth_timeout = self._profile.connect_timeout
                transport.connect()

                def handler(title, instructions, prompt_list, _code=code):
                    answers = []
                    for p in prompt_list:
                        name = p[0].lower()
                        if "password" in name or "密码" in name or "口令" in name:
                            answers.append(pw)
                        elif "code" in name or "验证" in name or "verification" in name:
                            answers.append(_code)
                        else:
                            answers.append(pw)  # 未知提示按密码处理
                    return answers

                transport.auth_interactive(self._profile.user, handler)
                self._client = paramiko.SSHClient()
                self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self._client._transport = transport  # 复用已认证的 transport
                return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if transport is not None:
                    try:
                        transport.close()
                    except Exception:  # noqa: BLE001
                        pass
                if attempt < self._profile.max_retries:
                    time.sleep(3 * attempt)  # 避开验证码窗口额度竞争

        raise ConfigError(
            f"连接 {self._profile.host}:{self._profile.port} TOTP 认证失败 "
            f"（重试 {self._profile.max_retries} 次）: {last_err}"
        )

    def exec_command(self, command: str, timeout: Optional[int] = None) -> tuple[str, str, int]:
        if self._client is None:
            raise ConfigError("SSH 会话未建立")
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return out, err, code

    def open_sftp(self):
        if self._client is None:
            raise ConfigError("SSH 会话未建立")
        return self._client.open_sftp()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
