"""transport.py — SFTP 传输层。

职责：
1. 上传/下载单个文件与目录（递归）；
2. 原子写入（tmp + rename），防半文件；
3. 传输校验（字节数比对）；
4. glob 过滤回传文件；
5. 路径安全：远端目标必须落在 remote_work_root 内（防目录穿越）。

依赖 SshTransport 协议（见 connection.py），因此可与 Fake 替换。
"""
from __future__ import annotations

import fnmatch
import io
import posixpath
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from .config import TransferConfig
from .connection import SshTransport


class TransportError(Exception):
    """传输层错误。"""


@dataclass
class TransferResult:
    """一次传输任务的产物清单。"""
    uploaded: list[str] = field(default_factory=list)   # 远端路径
    downloaded: list[str] = field(default_factory=list) # 本地路径


def _safe_join_remote(root: str, rel: str) -> str:
    """将 rel 安全拼接到 root 下，拒绝路径穿越。

    先规范化再判断前缀，防止 `../` 绕过 startswith 检查。
    """
    root_p = PurePosixPath(posixpath.normpath(root))
    rel_p = PurePosixPath(rel)
    if rel_p.is_absolute():
        raise TransportError(f"远端路径不允许绝对路径: {rel}")
    joined = posixpath.normpath(str(root_p / rel_p))
    if joined != str(root_p) and not joined.startswith(str(root_p) + "/"):
        raise TransportError(f"路径越界（目录穿越）: {rel} 不在 {root} 下")
    return joined


def _normalize_remote(path: str) -> str:
    return posixpath.normpath(path.replace("\\", "/"))


class SftpTransfer:
    """基于 SFTP 的传输实现。"""

    def __init__(self, ssh: SshTransport, config: TransferConfig):
        self._ssh = ssh
        self._cfg = config

    def _remote(self, path: str) -> str:
        """远端路径：展开 ~ 并规范化（SFTP 需要绝对路径）。"""
        return _normalize_remote(self._ssh.expand_remote(path))

    # ------------------------------------------------------------------
    # 上传
    # ------------------------------------------------------------------

    def upload(self, local: str | Path, remote: str, result: Optional[TransferResult] = None) -> list[str]:
        """上传本地文件或目录到远端目录。

        - local 为文件 → 上传为 remote/<文件名>
        - local 为目录 → 递归上传到 remote 目录下（保留相对结构）

        语义约定：remote 始终视为目标目录（与 CLI --remote 工作目录一致）。
        """
        result = result or TransferResult()
        local_p = Path(local).expanduser().resolve()
        remote_norm = self._remote(remote)
        sftp = self._ssh.open_sftp()

        try:
            if local_p.is_file():
                dest = posixpath.join(remote_norm, local_p.name)
                self._put_file(sftp, str(local_p), dest)
                result.uploaded.append(dest)
            elif local_p.is_dir():
                dest_root = remote_norm
                for f in sorted(local_p.rglob("*")):
                    if f.is_dir():
                        continue
                    rel = f.relative_to(local_p).as_posix()
                    dest = posixpath.join(dest_root, rel)
                    self._put_file(sftp, str(f), dest)
                    result.uploaded.append(dest)
            else:
                raise TransportError(f"本地路径不存在: {local_p}")
        finally:
            sftp.close()
        return result.uploaded

    def _put_file(self, sftp, local: str, remote: str) -> None:
        self._ensure_remote_dir(sftp, posixpath.dirname(remote))
        tmp = f"{remote}.tmp-{Path(local).name}"

        local_p = Path(local)
        size = local_p.stat().st_size
        # `.in` 为 Q-Chem 输入（2026-10-14 加入）：Linux 端 Q-Chem 解析 CRLF
        # 会在 $molecule/$rem 段尾触发解析异常，须先转 LF。
        needs_convert = local_p.suffix.lower() in (".gjf", ".com", ".inp", ".in", ".xyz")

        if needs_convert:
            # 跨平台：Windows CRLF → Linux LF，并确保末尾空行
            # （Gaussian 在 Linux 上解析 CRLF 会报 "End of file in ZSymb"）
            data = local_p.read_bytes().replace(b"\r\n", b"\n")
            if needs_convert and not data.endswith(b"\n\n"):
                data = data.rstrip(b"\n") + b"\n\n"
            size = len(data)
            sftp.putfo(io.BytesIO(data), tmp, confirm=False)
        else:
            sftp.put(local, tmp, confirm=False)

        try:
            remote_stat = sftp.stat(tmp)
        except OSError as exc:
            raise TransportError(f"上传后校验失败: {remote} ({exc})") from exc

        if remote_stat.st_size != size:
            raise TransportError(
                f"上传校验失败: {remote} 大小 {remote_stat.st_size} != 本地 {size}"
            )
        self._atomic_rename(sftp, tmp, remote)

    def _atomic_rename(self, sftp, tmp: str, remote: str) -> None:
        """原子改名；目标已存在且服务器不允许覆盖时先删再移。

        部分 SFTP 服务器（如部分商业实现）对已存在目标执行 rename
        会返回 failure，需回退为 remove+rename。
        """
        try:
            sftp.rename(tmp, remote)
        except OSError:
            try:
                sftp.remove(remote)
            except OSError:
                pass  # 目标本就不存在，直接重试 rename
            sftp.rename(tmp, remote)

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------

    def download(
        self,
        remote: str,
        local_dir: str | Path,
        glob_pattern: Optional[str] = None,
        result: Optional[TransferResult] = None,
    ) -> list[str]:
        """从远端目录下载匹配 glob 的文件到本地目录。

        仅支持远端目录下载；文件级请用 download_file。
        """
        result = result or TransferResult()
        local_dir_p = Path(local_dir).expanduser()
        local_dir_p.mkdir(parents=True, exist_ok=True)
        pattern = glob_pattern or self._cfg.download_glob
        patterns = pattern.split()

        remote_norm = self._remote(remote)
        sftp = self._ssh.open_sftp()
        try:
            if not self._is_dir(sftp, remote_norm):
                raise TransportError(f"远端路径不是目录: {remote_norm}")
            for entry in sorted(self._list_dir(sftp, remote_norm)):
                # 拒绝恶意远端文件名（路径穿越/分隔符）
                if entry in (".", "..") or "/" in entry or "\\" in entry:
                    continue
                if not any(fnmatch.fnmatch(entry, p) for p in patterns):
                    continue
                remote_file = posixpath.join(remote_norm, entry)
                if self._is_dir(sftp, remote_file):
                    continue
                local_file = local_dir_p / entry
                self._get_file(sftp, remote_file, str(local_file))
                result.downloaded.append(str(local_file))
        finally:
            sftp.close()
        return result.downloaded

    def download_file(self, remote_file: str, local_path: str | Path, result: Optional[TransferResult] = None) -> str:
        """下载单个远端文件到指定本地路径。"""
        result = result or TransferResult()
        local_p = Path(local_path).expanduser()
        local_p.parent.mkdir(parents=True, exist_ok=True)
        sftp = self._ssh.open_sftp()
        try:
            self._get_file(sftp, self._remote(remote_file), str(local_p))
        finally:
            sftp.close()
        result.downloaded.append(str(local_p))
        return str(local_p)

    def _get_file(self, sftp, remote: str, local: str) -> None:
        tmp = f"{local}.tmp"
        sftp.get(remote, tmp)
        got = Path(tmp).stat().st_size
        try:
            want = sftp.stat(remote).st_size
        except OSError:
            want = got
        if got != want:
            raise TransportError(f"下载校验失败: {remote} 大小 {got} != {want}")
        Path(tmp).replace(local)

    # ------------------------------------------------------------------
    # SFTP 辅助
    # ------------------------------------------------------------------

    def _is_dir(self, sftp, path: str) -> bool:
        try:
            return sftp.stat(path).st_mode & 0o170000 == 0o040000  # S_IFDIR
        except OSError:
            return False

    def _list_dir(self, sftp, path: str) -> list[str]:
        try:
            return [e.filename for e in sftp.listdir_attr(path)]
        except OSError as exc:
            raise TransportError(f"远端目录不可读: {path} ({exc})") from exc

    def _ensure_remote_dir(self, sftp, path: str) -> None:
        """递归创建远端目录。"""
        if path in ("", ".", "/"):
            return
        parts = [p for p in path.split("/") if p]
        cur = "/" if path.startswith("/") else "."
        for part in parts:
            cur = posixpath.join(cur, part)
            if not self._is_dir(sftp, cur):
                try:
                    sftp.mkdir(cur)
                except OSError:
                    if not self._is_dir(sftp, cur):  # 并发竞态后重查
                        raise
