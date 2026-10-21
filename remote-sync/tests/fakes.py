"""fakes.py — mock harness 基座。

提供内存文件系统 + 假命令执行器的 FakeSSH/FakeSFTP，模拟 paramiko 行为，
使全部核心逻辑可在不上真机的情况下测试。

用法：测试中构造 FakeSshTransport，替换 ParamikoSsh 传入 SftpTransfer /
Executor 即可。
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from gtransfer_lib.connection import SshTransport


# ---------------------------------------------------------------------------
# 内存文件系统
# ---------------------------------------------------------------------------

@dataclass
class FakeFile:
    content: bytes = b""
    mode: int = 0o100644  # 普通文件
    rename_overwrite_ok: bool = True  # 模拟部分服务器不允许覆盖 rename


class FakeFS:
    """极简内存文件系统：目录与文件，支持 SFTP 常用操作。"""

    def __init__(self):
        self._files: dict[str, FakeFile] = {}
        self._dirs: set[str] = {"/", "."}

    # -- 路径工具 --------------------------------------------------------

    @staticmethod
    def _norm(path: str) -> str:
        p = posixpath.normpath(path.replace("\\", "/"))
        return p

    def _parent_dir(self, path: str) -> str:
        return posixpath.dirname(self._norm(path))

    # -- 目录 ------------------------------------------------------------

    def mkdir(self, path: str) -> None:
        p = self._norm(path)
        self._dirs.add(p)
        # 隐式创建父目录，模拟 sftp.mkdir 前置目录已存在
        parent = posixpath.dirname(p)
        if parent not in ("", "/", "."):
            self._dirs.add(parent)

    def is_dir(self, path: str) -> bool:
        p = self._norm(path)
        return p in self._dirs

    def listdir_attr(self, path: str):
        p = self._norm(path)
        parent = "" if p in ("/", ".") else p
        entries = []
        for d in self._dirs:
            if d == p:
                continue
            if posixpath.dirname(d) == p and d != "/":
                entries.append(FakeSFTPAttributes(posixpath.basename(d), 0o040755))
        for f, fa in self._files.items():
            if posixpath.dirname(f) == p:
                entries.append(FakeSFTPAttributes(posixpath.basename(f), fa.mode))
        return entries

    # -- 文件 ------------------------------------------------------------

    def put_bytes(self, path: str, data: bytes) -> None:
        p = self._norm(path)
        self._files[p] = FakeFile(content=data)
        self._dirs.add(self._parent_dir(p))

    def get_bytes(self, path: str) -> bytes:
        p = self._norm(path)
        if p not in self._files:
            raise FileNotFoundError(f"远端文件不存在: {p}")
        return self._files[p].content

    def stat(self, path: str):
        p = self._norm(path)
        if p in self._files:
            return FakeSFTPAttributes(
                posixpath.basename(p),
                self._files[p].mode,
                size=len(self._files[p].content),
            )
        if p in self._dirs:
            return FakeSFTPAttributes(posixpath.basename(p) or p, 0o040755)
        raise FileNotFoundError(f"远端路径不存在: {p}")

    def rename(self, old: str, new: str) -> None:
        o, n = self._norm(old), self._norm(new)
        # 模拟部分 SFTP 服务器：目标存在时 rename 直接失败
        if n in self._files and not self._files[n].rename_overwrite_ok:
            raise OSError("rename: 目标已存在（服务器策略拒绝覆盖）")
        if o in self._files:
            self._files[n] = self._files.pop(o)
        elif o in self._dirs:
            self._dirs.discard(o)
            self._dirs.add(n)
            # 目录内文件迁移
            moved = {}
            for f, fa in self._files.items():
                if f.startswith(o + "/"):
                    moved[n + f[len(o):]] = fa
                else:
                    moved[f] = fa
            self._files = moved
        else:
            raise FileNotFoundError(f"重命名源不存在: {o}")

    def remove(self, path: str) -> None:
        p = self._norm(path)
        if p in self._files:
            del self._files[p]
        elif p in self._dirs:
            self._dirs.discard(p)

    def rmtree(self, path: str) -> None:
        p = self._norm(path)
        self._dirs = {d for d in self._dirs if not d.startswith(p + "/")}
        self._dirs.discard(p)
        self._files = {f: fa for f, fa in self._files.items()
                       if not f.startswith(p + "/")}


class FakeSFTPAttributes:
    """模拟 paramiko SFTPAttributes 的 filename/st_mode/st_size 访问。"""

    def __init__(self, filename: str, st_mode: int, size: int = 0):
        self.filename = filename
        self.st_mode = st_mode
        self.st_size = size


# ---------------------------------------------------------------------------
# FakeSFTP
# ---------------------------------------------------------------------------

class FakeSFTP:
    """模拟 paramiko SFTPClient 常用接口（供 transport.py 使用）。"""

    def __init__(self, fs: FakeFS):
        self.fs = fs

    def put(self, localpath, remotepath, confirm=True):
        with open(localpath, "rb") as f:
            data = f.read()
        self.fs.put_bytes(remotepath, data)
        return self.fs.stat(remotepath)

    def putfo(self, fl, remotepath, confirm=True):
        data = fl.read()
        self.fs.put_bytes(remotepath, data)
        return self.fs.stat(remotepath)

    def get(self, remotepath, localpath):
        data = self.fs.get_bytes(remotepath)
        with open(localpath, "wb") as f:
            f.write(data)
        return self.fs.stat(remotepath)

    def stat(self, path):
        return self.fs.stat(path)

    def listdir_attr(self, path):
        return self.fs.listdir_attr(path)

    def mkdir(self, path):
        self.fs.mkdir(path)

    def rename(self, oldpath, newpath):
        self.fs.rename(oldpath, newpath)

    def remove(self, path):
        self.fs.remove(path)

    def open(self, path, mode="r"):
        return _FakeSFTPFile(self.fs, path, mode)

    def close(self):
        pass


class _FakeSFTPFile:
    def __init__(self, fs: FakeFS, path: str, mode: str):
        self._fs = fs
        self._path = path
        self._mode = mode
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        return len(text)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if "w" in self._mode:
            self._fs.put_bytes(self._path, self._buf.encode("utf-8"))


# ---------------------------------------------------------------------------
# FakeSSH（命令执行）
# ---------------------------------------------------------------------------

@dataclass
class CommandStub:
    """注册的假命令：按 pattern 匹配，返回 (stdout, stderr, exit_code)。"""
    pattern: str
    handler: Callable[[str], tuple[str, str, int]]


class FakeSSH(SshTransport):
    """FakeSshTransport：内存 FS + 注册命令执行器。

    测试通过 register(pattern, handler) 注册命令行为；
    未匹配的命令默认 (stdout="", stderr="", exit_code=0)。
    """

    def __init__(self, fs: Optional[FakeFS] = None, home_dir: str = "~"):
        self.fs = fs or FakeFS()
        self._home_dir = home_dir
        self.commands: list[CommandStub] = []
        self.executed: list[str] = []
        self.closed = False

    @property
    def home_dir(self) -> str:
        return self._home_dir

    def register(self, pattern: str, handler: Callable[[str], tuple[str, str, int]]):
        self.commands.append(CommandStub(pattern, handler))
        return self

    def exec_command(self, command: str, timeout=None) -> tuple[str, str, int]:
        self.executed.append(command)
        # 注册的 stub 优先（如 g16 命令内部可能含 rm -rf scratch）
        for stub in self.commands:
            if re.search(stub.pattern, command):
                return stub.handler(command)
        # 内建 fallback：独立 rm -rf 命令清理内存 FS（处理 shlex.quote 引号）
        if command.strip().startswith("rm -rf"):
            import shlex
            parts = shlex.split(command)
            if len(parts) >= 3:
                for target in parts[2:]:
                    self.fs.rmtree(target.rstrip("/"))
            return "", "", 0
        return "", "", 0

    def open_sftp(self):
        return FakeSFTP(self.fs)

    def close(self):
        self.closed = True

    # -- 测试辅助 --------------------------------------------------------

    def seed_file(self, remote_path: str, content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        self.fs.put_bytes(remote_path, data)

    def read_file(self, remote_path: str) -> bytes:
        return self.fs.get_bytes(remote_path)

    def exists(self, remote_path: str) -> bool:
        try:
            self.fs.stat(remote_path)
            return True
        except FileNotFoundError:
            return False
