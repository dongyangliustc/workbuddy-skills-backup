"""test_cli.py — CLI 契约：参数校验、JSON 输出、--dry-run 不产生 I/O。

通过 monkeypatch 将 ParamikoSsh 替换为 FakeSSH，验证 submit 全流程
（上传 → 执行 → 回传 → 清理）与状态输出。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gtransfer  # noqa: E402
import gtransfer_lib.connection as connection_mod  # noqa: E402
from fakes import FakeSSH  # noqa: E402


@pytest.fixture()
def conf_file(tmp_path: Path) -> Path:
    p = tmp_path / "transfer.conf"
    # 真实存在的密钥文件（Windows 路径需用正斜杠写配置）
    key = tmp_path / "id_rsa"
    key.write_text("fake key", encoding="utf-8")
    p.write_text(
        f"""[server:default]
host = mock-host
user = dy
key_file = {key.as_posix()}
execution_mode = direct
remote_work_root = ~/gt

[transfer:default]
download_glob = *.log
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def fake_ssh(monkeypatch):
    ssh = FakeSSH()
    # gtransfer 通过 from-import 绑定，须 patch gtransfer 命名空间
    monkeypatch.setattr(gtransfer, "ParamikoSsh", lambda profile, **kw: ssh)
    return ssh


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------

def test_submit_dry_run_operations(conf_file, tmp_path, capsys):
    gjf = tmp_path / "water.gjf"
    gjf.write_text("gjf", encoding="utf-8")

    code = gtransfer.main([
        "submit", "--config", str(conf_file), "--local", str(gjf),
        "--remote", "~/gt/t1", "--dry-run",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert payload["remote_work"] == "~/gt/t1"
    ops = [op["op"] for op in payload["operations"]]
    assert ops == ["upload", "exec", "download", "cleanup_remote"]


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------

def test_ping_success(conf_file, fake_ssh, capsys):
    fake_ssh.register(r"echo ok", lambda cmd: ("ok\nmocknode\n123456\n", "", 0))
    code = gtransfer.main(["ping", "--config", str(conf_file)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert "mocknode" in payload["remote"]


def test_ping_dry_run(conf_file, capsys):
    code = gtransfer.main(["ping", "--config", str(conf_file), "--dry-run"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["dry_run"] is True
    assert payload["would_connect"]["host"] == "mock-host"


# ---------------------------------------------------------------------------
# submit 全流程（direct）
# ---------------------------------------------------------------------------

def test_submit_full_direct_success(conf_file, fake_ssh, tmp_path, capsys):
    """上传→执行(成功)→回传 log→清理 全链路。"""
    gjf = tmp_path / "water.gjf"
    gjf.write_text("%nprocshared=8\n\n#P opt\n\nwater\n\n0 1\nO 0 0 0\n",
                   encoding="utf-8")
    out_dir = tmp_path / "results"
    out_dir.mkdir()

    # g16 成功：写入远端 log 并回显退出码
    def g16_handler(cmd):
        fake_ssh.seed_file("~/gt/t1/water.log", "Normal termination of Gaussian 16\n")
        return ("__GT_EXIT=0", "", 0)

    fake_ssh.register(r"g16", g16_handler)

    code = gtransfer.main([
        "submit", "--config", str(conf_file), "--local", str(gjf),
        "--remote", "~/gt/t1", "--out-dir", str(out_dir),
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["state"]["status"] == "SUCCESS"
    # 上传记录含 .gjf（远端已按 keep_remote=false 清理，以记录为准）
    assert any(f.endswith("water.gjf") for f in payload["files"]["uploaded"])
    # 回传过 .log 到本地
    assert (out_dir / "water.log").exists()
    # 远端已清理：rm 命令使用展开后的绝对路径（~ 不被单引号锁死）
    rm_cmds = [c for c in fake_ssh.executed if c.startswith("rm -rf")]
    assert rm_cmds, "应执行 rm -rf 清理"
    assert "'/home/user/gt/t1'" in rm_cmds[0] or "gt/t1" in rm_cmds[0]
    assert not fake_ssh.exists("~/gt/t1/water.gjf")


def test_submit_full_direct_failed(conf_file, fake_ssh, tmp_path, capsys):
    """g16 失败：即使失败也回传日志，状态 FAILED，退出码 1。"""
    gjf = tmp_path / "fail.gjf"
    gjf.write_text("bad input", encoding="utf-8")
    out_dir = tmp_path / "results"
    out_dir.mkdir()

    def g16_handler(cmd):
        fake_ssh.seed_file("~/gt/t1/fail.log", "Error termination\n")
        return ("Error termination\n__GT_EXIT=1", "stderr", 1)

    fake_ssh.register(r"g16", g16_handler)

    code = gtransfer.main([
        "submit", "--config", str(conf_file), "--local", str(gjf),
        "--remote", "~/gt/t1", "--out-dir", str(out_dir),
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["state"]["status"] == "FAILED"
    # 失败日志仍被回传用于排查
    assert (out_dir / "fail.log").exists()


def test_submit_remote_escape_rejected(conf_file, tmp_path, capsys):
    """--remote 越界 remote_work_root 被合规策略拦截（exit 4）。"""
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    code = gtransfer.main([
        "submit", "--config", str(conf_file), "--local", str(gjf),
        "--remote", "/etc/evil",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert payload["ok"] is False
    assert payload["policy"] is True
    assert payload["rule_id"] == "POLICY-PATH-ESCAPE"
    assert payload["remedy"]


def test_submit_remote_prefix_bypass_rejected(conf_file, tmp_path, capsys):
    """[回归] --remote 用同前缀目录（如 ~/gt2）绕过校验必须被拒。"""
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    # remote_work_root = ~/gt，~/gt2 不应通过 startswith("~/gt") 校验
    code = gtransfer.main([
        "submit", "--config", str(conf_file), "--local", str(gjf),
        "--remote", "~/gt2/evil",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert payload["ok"] is False
    assert payload["rule_id"] == "POLICY-PATH-ESCAPE"


def test_submit_remote_relative_bypass_rejected(conf_file, tmp_path, capsys):
    """[回归] --remote 用 ../ 相对路径绕过校验必须被拒。"""
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    code = gtransfer.main([
        "submit", "--config", str(conf_file), "--local", str(gjf),
        "--remote", "~/gt/../etc",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 4
    assert payload["ok"] is False
    assert payload["rule_id"] == "POLICY-PATH-ESCAPE"


def test_status_invalid_job_name(conf_file, capsys):
    """[回归] status 非法 job 名被拒（防注入）。"""
    code = gtransfer.main([
        "status", "--config", str(conf_file), "--job", "1; rm -rf /",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "非法 job 名" in payload["error"]


def test_fetch_invalid_job_name(conf_file, capsys):
    """[回归] fetch 非法 job 名被拒（防越界/注入）。"""
    code = gtransfer.main([
        "fetch", "--config", str(conf_file), "--job", "../etc/passwd",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "非法 job 名" in payload["error"]


def test_submit_passes_editor_command(conf_file, fake_ssh, tmp_path, capsys):
    """[通用命令模式] --command 必须传递给执行器（取代 g16 流程）。"""
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "results"
    out_dir.mkdir()

    seen_cmd = {}

    def py_handler(cmd):
        seen_cmd["cmd"] = cmd
        return ("__GT_EXIT=0", "", 0)

    fake_ssh.register(r"python main.py", py_handler)

    code = gtransfer.main([
        "submit", "--config", str(conf_file), "--local", str(gjf),
        "--remote", "~/gt/t1", "--command", "python main.py",
        "--out-dir", str(out_dir),
    ])
    assert code == 0
    assert "python main.py > gtransfer.log" in seen_cmd["cmd"]
    assert "g16 <" not in seen_cmd["cmd"]


def test_submit_command_falls_back_to_config(conf_file, fake_ssh, tmp_path, capsys):
    """[通用命令模式] 未传 --command 时回退到配置的 command 字段。"""
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "results"
    out_dir.mkdir()

    conf = conf_file.read_text(encoding="utf-8")
    conf_file.write_text(
        conf.replace("execution_mode = direct",
                     "execution_mode = direct\ncommand = python run.py"),
        encoding="utf-8",
    )

    seen_cmd = {}

    def py_handler(cmd):
        seen_cmd["cmd"] = cmd
        return ("__GT_EXIT=0", "", 0)

    fake_ssh.register(r"python run.py", py_handler)

    code = gtransfer.main([
        "submit", "--config", str(conf_file), "--local", str(gjf),
        "--remote", "~/gt/t1", "--out-dir", str(out_dir),
    ])
    assert code == 0
    assert "python run.py > gtransfer.log" in seen_cmd["cmd"]


def test_submit_passes_executor_args(conf_file, fake_ssh, tmp_path, capsys):
    """[回归] --gaussian-exec 等参数必须传递给执行器（此前死参数）。"""
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "results"
    out_dir.mkdir()

    seen_cmd = {}

    def g16_handler(cmd):
        seen_cmd["cmd"] = cmd
        return ("__GT_EXIT=0", "", 0)

    fake_ssh.register(r"g09", g16_handler)

    code = gtransfer.main([
        "submit", "--config", str(conf_file), "--local", str(gjf),
        "--remote", "~/gt/t1", "--gaussian-exec", "g09",
        "--out-dir", str(out_dir),
    ])
    assert code == 0
    assert "g09" in seen_cmd["cmd"]


def test_submit_falls_back_to_config_gaussian_exec(conf_file, fake_ssh, tmp_path, capsys):
    """[回归] 未传 --gaussian-exec 时回退到配置值（固化服务器环境）。"""
    gjf = tmp_path / "x.gjf"
    gjf.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "results"
    out_dir.mkdir()

    # 配置中固化 gaussian_exec = /opt/g16/g16
    conf = conf_file.read_text(encoding="utf-8")
    conf_file.write_text(
        conf.replace("execution_mode = direct",
                     "execution_mode = direct\ngaussian_exec = /opt/g16/g16"),
        encoding="utf-8",
    )

    seen_cmd = {}

    def g16_handler(cmd):
        seen_cmd["cmd"] = cmd
        return ("__GT_EXIT=0", "", 0)

    fake_ssh.register(r"/opt/g16/g16", g16_handler)

    code = gtransfer.main([
        "submit", "--config", str(conf_file), "--local", str(gjf),
        "--remote", "~/gt/t1", "--out-dir", str(out_dir),
    ])
    assert code == 0
    assert "/opt/g16/g16" in seen_cmd["cmd"]


def test_submit_missing_local(conf_file, tmp_path, capsys):
    code = gtransfer.main([
        "submit", "--config", str(conf_file),
        "--local", str(tmp_path / "nope.gjf"),
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "不存在" in payload["error"]


# ---------------------------------------------------------------------------
# status / fetch
# ---------------------------------------------------------------------------

def test_status_slurm_query(conf_file, fake_ssh, capsys):
    """status 子命令：slurm 模式查询。"""
    # 构造 slurm 模式的独立配置（status 使用 execution_mode 决定查询器）
    import tempfile
    from pathlib import Path as P

    slurm_conf = P(tempfile.mkdtemp()) / "slurm.conf"
    slurm_conf.write_text(
        f"""[server:default]
host = mock-host
user = dy
key_file = {conf_file.parent / 'id_rsa'}
execution_mode = slurm
remote_work_root = ~/gt
""",
        encoding="utf-8",
    )
    fake_ssh.register(r"squeue", lambda cmd: ("RUNNING", "", 0))
    code = gtransfer.main(["status", "--config", str(slurm_conf), "--job", "12345"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "RUNNING"


def test_fetch_download(conf_file, fake_ssh, tmp_path, capsys):
    """fetch：按 glob 回传远端结果。"""
    fake_ssh.seed_file("~/gt/t1/a.log", "log-a")
    fake_ssh.seed_file("~/gt/t1/b.chk", "chk-b")
    out = tmp_path / "fetched"
    code = gtransfer.main([
        "fetch", "--config", str(conf_file), "--job", "t1",
        "--glob", "*.log *.chk", "--out", str(out),
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert len(payload["files"]) == 2
    assert (out / "a.log").exists()
    assert (out / "b.chk").exists()


def test_unknown_command(conf_file, capsys):
    with pytest.raises(SystemExit):
        gtransfer.main(["nonsense", "--config", str(conf_file)])
