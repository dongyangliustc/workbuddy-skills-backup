"""test_transport.py — SftpTransfer 上传/下载/校验/路径安全。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeSSH  # noqa: E402
from gtransfer_lib.config import ServerProfile, TransferConfig  # noqa: E402
from gtransfer_lib.transport import SftpTransfer, TransportError  # noqa: E402


def make_cfg(**over) -> TransferConfig:
    profile = ServerProfile(
        name="test",
        host="127.0.0.1",
        user="dy",
        key_file="/fake/key",
        execution_mode="direct",
        remote_work_root="~/gt",
    )
    return TransferConfig(server=profile, **over)


def test_upload_single_file(tmp_path):
    """上传单个文件：目标为目录路径时追加文件名。"""
    ssh = FakeSSH()
    transfer = SftpTransfer(ssh, make_cfg())
    src = tmp_path / "water.gjf"
    src.write_text("%mem=8GB\n\n#P opt\n\nwater\n\n0 1\nO 0 0 0\n", encoding="utf-8")

    uploaded = transfer.upload(str(src), "~/gt/task1")
    assert uploaded == ["~/gt/task1/water.gjf"]
    # .gjf 上传时自动归一化换行（CRLF→LF 且补末尾空行）
    got = ssh.read_file("~/gt/task1/water.gjf")
    assert got == b"%mem=8GB\n\n#P opt\n\nwater\n\n0 1\nO 0 0 0\n\n"
    # 原子写入的 tmp 已 rename，不残留
    assert not ssh.exists("~/gt/task1/water.gjf.tmp-water.gjf")


def test_upload_dir_recursive(tmp_path):
    """上传目录：递归保留相对结构。"""
    ssh = FakeSSH()
    transfer = SftpTransfer(ssh, make_cfg())
    src = tmp_path / "jobdir"
    src.mkdir()
    (src / "in.gjf").write_text("gjf", encoding="utf-8")
    (src / "sub").mkdir()
    (src / "sub" / "aux.txt").write_text("aux", encoding="utf-8")

    uploaded = transfer.upload(str(src), "~/gt/jobdir")
    assert set(uploaded) == {"~/gt/jobdir/in.gjf", "~/gt/jobdir/sub/aux.txt"}
    assert ssh.read_file("~/gt/jobdir/sub/aux.txt") == b"aux"


def test_upload_missing_local(tmp_path):
    """本地路径不存在时报 TransportError。"""
    transfer = SftpTransfer(FakeSSH(), make_cfg())
    with pytest.raises(TransportError, match="不存在"):
        transfer.upload(str(tmp_path / "nope.gjf"), "~/gt/t1")


def test_upload_size_mismatch_detected(tmp_path, monkeypatch):
    """上传后大小校验失败时抛错（模拟远端丢字节）。"""
    ssh = FakeSSH()
    transfer = SftpTransfer(ssh, make_cfg())
    src = tmp_path / "a.gjf"
    src.write_text("hello", encoding="utf-8")

    # 篡改 FakeFS 的 put：写入后截断内容
    orig_put = ssh.fs.put_bytes

    def evil_put(path, data):
        orig_put(path, data[:2])  # 只写入前 2 字节

    monkeypatch.setattr(ssh.fs, "put_bytes", evil_put)
    with pytest.raises(TransportError, match="校验失败"):
        transfer.upload(str(src), "~/gt/t1")


def test_upload_overwrite_existing_target(tmp_path):
    """[回归] 目标已存在且服务器拒绝覆盖 rename 时自动回退 remove+rename。"""
    ssh = FakeSSH()
    # 预先放置目标文件，并标记该文件不允许覆盖 rename
    ssh.seed_file("~/gt/t1/a.gjf", "old")
    ssh.fs._files["~/gt/t1/a.gjf"].rename_overwrite_ok = False

    transfer = SftpTransfer(ssh, make_cfg())
    src = tmp_path / "a.gjf"
    src.write_text("new content", encoding="utf-8")

    uploaded = transfer.upload(str(src), "~/gt/t1")
    assert uploaded == ["~/gt/t1/a.gjf"]
    # .gjf 换行归一化后补末尾空行
    assert ssh.read_file("~/gt/t1/a.gjf") == b"new content\n\n"
    # tmp 不残留
    assert not any("tmp" in f for f in ssh.fs._files)


def test_upload_converts_crlf_for_gjf(tmp_path):
    """[回归] .gjf 上传自动 CRLF→LF 并补末尾空行（防 Gaussian ZSymb 解析失败）。"""
    ssh = FakeSSH()
    transfer = SftpTransfer(ssh, make_cfg())
    src = tmp_path / "h2o.gjf"
    src.write_bytes(b"#P HF/STO-3G\r\n\r\nh2o\r\n\r\n0 1\r\nO 0.0 0.0 0.0\r\nH 0.0 0.5 0.0\r\n")

    transfer.upload(str(src), "~/gt/t1")
    data = ssh.read_file("~/gt/t1/h2o.gjf")
    assert b"\r\n" not in data          # CRLF 已转 LF
    assert data.endswith(b"\n\n")       # 末尾空行
    assert data.startswith(b"#P HF/STO-3G\n")


def test_upload_keeps_binary_for_chk(tmp_path):
    """[回归] 非文本扩展名（.chk）原样上传不做换行转换。"""
    ssh = FakeSSH()
    transfer = SftpTransfer(ssh, make_cfg())
    src = tmp_path / "a.chk"
    src.write_bytes(b"\x00\x01\r\n\x02\x00\r\nBINARY")

    transfer.upload(str(src), "~/gt/t1")
    assert ssh.read_file("~/gt/t1/a.chk") == b"\x00\x01\r\n\x02\x00\r\nBINARY"


def test_download_glob_filter(tmp_path):
    """下载按 glob 过滤：只回传 *.log。"""
    ssh = FakeSSH()
    for name in ("out.log", "job.chk", "job.gjf"):
        ssh.seed_file(f"~/gt/t1/{name}", f"content-{name}")

    transfer = SftpTransfer(ssh, make_cfg(download_glob="*.log"))
    files = transfer.download("~/gt/t1", str(tmp_path / "res"))
    assert [Path(f).name for f in files] == ["out.log"]
    assert (tmp_path / "res" / "out.log").exists()


def test_download_multi_glob(tmp_path):
    """下载支持多模式 glob。"""
    ssh = FakeSSH()
    ssh.seed_file("~/gt/t1/a.log", "a")
    ssh.seed_file("~/gt/t1/b.chk", "b")
    ssh.seed_file("~/gt/t1/c.gjf", "c")

    transfer = SftpTransfer(ssh, make_cfg(download_glob="*.log *.chk"))
    files = transfer.download("~/gt/t1", str(tmp_path / "res"))
    names = sorted(Path(f).name for f in files)
    assert names == ["a.log", "b.chk"]


def test_download_file_single(tmp_path):
    """下载单个文件到指定路径。"""
    ssh = FakeSSH()
    ssh.seed_file("~/gt/t1/out.log", "gaussian output")
    transfer = SftpTransfer(ssh, make_cfg())
    dest = tmp_path / "my" / "out.log"
    got = transfer.download_file("~/gt/t1/out.log", str(dest))
    assert got == str(dest)
    assert dest.read_text(encoding="utf-8") == "gaussian output"


def test_download_verify_failure(tmp_path, monkeypatch):
    """下载大小校验失败报错。"""
    ssh = FakeSSH()
    ssh.seed_file("~/gt/t1/out.log", "full content here")
    transfer = SftpTransfer(ssh, make_cfg())

    orig_get = ssh.fs.get_bytes

    def evil_get(path):
        return orig_get(path)[:4]

    monkeypatch.setattr(ssh.fs, "get_bytes", evil_get)
    with pytest.raises(TransportError, match="下载校验失败"):
        transfer.download_file("~/gt/t1/out.log", str(tmp_path / "out.log"))
    assert not (tmp_path / "out.log").exists()  # 失败不留半文件


def test_download_rejects_malicious_names(tmp_path):
    """[回归] 恶意远端文件名（.. / 含分隔符）不下载到本地。"""
    ssh = FakeSSH()
    # 直接注入恶意条目到内存 FS
    ssh.fs.put_bytes("~/gt/t1/..", b"evil")           # 名为 .. 的文件
    ssh.fs.put_bytes("~/gt/t1/a/b.log", b"nested")     # 含斜杠条目
    ssh.seed_file("~/gt/t1/ok.log", "fine")

    transfer = SftpTransfer(ssh, make_cfg(download_glob="*.log"))
    files = transfer.download("~/gt/t1", str(tmp_path / "res"))
    names = [Path(f).name for f in files]
    assert names == ["ok.log"]
    # res 目录下只有 ok.log，无 .. 或 a 条目
    assert sorted(p.name for p in (tmp_path / "res").iterdir()) == ["ok.log"]


def test_remote_root_escape_rejected(tmp_path):
    """远端目标目录穿越被拒（upload 前 CLI 校验，此处验证工具层 join）。"""
    ssh = FakeSSH()
    transfer = SftpTransfer(ssh, make_cfg())
    src = tmp_path / "x.gjf"
    src.write_text("x", encoding="utf-8")
    # _safe_join_remote 独立测试
    from gtransfer_lib.transport import _safe_join_remote
    with pytest.raises(TransportError, match="越界"):
        _safe_join_remote("~/gt", "../etc/passwd")
    # 正常路径
    assert _safe_join_remote("~/gt", "a/b.gjf") == "~/gt/a/b.gjf"
