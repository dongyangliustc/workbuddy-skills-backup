"""test_config.py — 配置加载：模板+用户覆盖、env 展开、非法值。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gtransfer_lib.config import ConfigError, load_config  # noqa: E402

# 测试用最小配置模板
TEMPLATE = """\
[server:default]
host = template-host
user = template-user
key_file = ${GT_KEY}
execution_mode = direct
remote_work_root = ~/gtransfer

[transfer:default]
upload_glob = *.gjf
download_glob = *.log
"""

USER_CFG = """\
[server:default]
host = real-host
execution_mode = slurm
"""


@pytest.fixture()
def template_file(tmp_path: Path) -> Path:
    """把模板写到 temp 目录的 references/transfer.conf.example 位置。"""
    ref_dir = tmp_path / "references"
    ref_dir.mkdir()
    tpl = ref_dir / "transfer.conf.example"
    tpl.write_text(TEMPLATE, encoding="utf-8")
    return tpl


@pytest.fixture()
def user_cfg_file(tmp_path: Path) -> Path:
    p = tmp_path / "user.conf"
    p.write_text(USER_CFG, encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _no_template_by_default(monkeypatch, tmp_path):
    """默认屏蔽真实模板，防止影响缺字段/非法值测试。

    需要模板的测试在函数体内显式 monkeypatch.setattr(_template_path, ...)。
    """
    import gtransfer_lib.config as config_mod
    monkeypatch.setattr(config_mod, "_template_path",
                        lambda: tmp_path / "nonexistent" / "transfer.conf.example")


@pytest.fixture(autouse=True)
def _cleanup_env(monkeypatch):
    monkeypatch.delenv("GTRANSFER_CONFIG", raising=False)


def test_load_with_only_template(template_file, monkeypatch, tmp_path):
    """仅模板（无用户配置）：host 来自模板，key_file 从 env 展开。"""
    key = tmp_path / "id_rsa"
    key.write_text("fake key", encoding="utf-8")
    monkeypatch.setenv("GT_KEY", str(key))

    import gtransfer_lib.config as config_mod
    monkeypatch.setattr(config_mod, "_template_path", lambda: template_file)

    cfg = load_config("/nonexistent/user.conf")
    assert cfg.server.host == "template-host"
    assert cfg.server.user == "template-user"
    assert cfg.server.execution_mode == "direct"
    assert cfg.server.key_file == str(key)
    assert cfg.upload_glob == "*.gjf"
    assert cfg.download_glob == "*.log"


def test_user_config_overrides_template(template_file, user_cfg_file, monkeypatch, tmp_path):
    """用户配置的 host / execution_mode 覆盖模板。"""
    key = tmp_path / "id_rsa"
    key.write_text("fake key", encoding="utf-8")
    monkeypatch.setenv("GT_KEY", str(key))
    monkeypatch.setenv("GTRANSFER_CONFIG", str(user_cfg_file))

    import gtransfer_lib.config as config_mod
    monkeypatch.setattr(config_mod, "_template_path", lambda: template_file)

    cfg = load_config(str(user_cfg_file))
    assert cfg.server.host == "real-host"
    assert cfg.server.execution_mode == "slurm"
    assert cfg.server.user == "template-user"  # 模板值保留
    assert cfg.download_glob == "*.log"
    assert cfg.server.key_file == str(key)


def test_missing_required_field(tmp_path):
    """缺少 host 时报 ConfigError。"""
    bad = tmp_path / "bad.conf"
    bad.write_text("[server:default]\nuser = dy\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(bad))


def test_invalid_execution_mode(tmp_path):
    """execution_mode 非法时报错。"""
    bad = tmp_path / "bad_mode.conf"
    bad.write_text(
        "[server:default]\nhost = h\nuser = u\nexecution_mode = lsf\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="execution_mode"):
        load_config(str(bad))


def test_unresolved_env_in_host(tmp_path):
    """host 引用未定义环境变量（展开为空）→ 必填校验报错。"""
    bad = tmp_path / "bad_env.conf"
    bad.write_text(
        "[server:default]\nhost = ${UNDEFINED_VAR}\nuser = u\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="必填"):
        load_config(str(bad))


def test_missing_key_file(tmp_path, monkeypatch):
    """key_file 指向不存在的文件报错。"""
    monkeypatch.delenv("GT_KEY", raising=False)
    bad = tmp_path / "bad_key.conf"
    bad.write_text(
        "[server:default]\nhost = h\nuser = u\nkey_file = /nonexistent/key\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="密钥文件不存在"):
        load_config(str(bad))


def test_unknown_server_section(tmp_path):
    """请求不存在的 server profile 报错。"""
    c = tmp_path / "c.conf"
    c.write_text("[server:default]\nhost = h\nuser = u\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="缺少"):
        load_config(str(c), server_name="other")


def test_password_auth_mode(tmp_path, monkeypatch):
    """[回归] 口令认证：password 从环境变量注入，auth_mode 判定正确。"""
    monkeypatch.setenv("GTRANSFER_PASSWORD", "secret123")
    c = tmp_path / "pw.conf"
    c.write_text(
        "[server:default]\nhost = h\nuser = u\n"
        "password = ${GTRANSFER_PASSWORD}\n",
        encoding="utf-8",
    )
    cfg = load_config(str(c))
    assert cfg.server.auth_mode == "password"
    assert cfg.server.password == "secret123"
    assert cfg.server.key_file is None


def test_key_auth_mode(tmp_path):
    """[回归] 密钥认证：key_file 配置时 auth_mode 为 key。"""
    key = tmp_path / "id_rsa"
    key.write_text("k", encoding="utf-8")
    c = tmp_path / "key.conf"
    c.write_text(
        f"[server:default]\nhost = h\nuser = u\nkey_file = {key.as_posix()}\n",
        encoding="utf-8",
    )
    cfg = load_config(str(c))
    assert cfg.server.auth_mode == "key"
    assert cfg.server.password is None


def test_no_auth_mode(tmp_path):
    """[回归] 无任何认证方式 → auth_mode none（连接层报错）。"""
    c = tmp_path / "noauth.conf"
    c.write_text("[server:default]\nhost = h\nuser = u\n", encoding="utf-8")
    cfg = load_config(str(c))
    assert cfg.server.auth_mode == "none"


def test_gaussian_env_from_config(tmp_path):
    """[回归] gaussian_exec / env_setup 从配置读取（固化服务器环境）。"""
    c = tmp_path / "env.conf"
    c.write_text(
        "[server:default]\nhost = h\nuser = u\n"
        "gaussian_exec = /opt/g16/g16\n"
        "env_setup = export GAUSS_ROOT=/opt/g16; export GAUSS_EXEDIR=/opt/g16:/opt/g16/bsd\n",
        encoding="utf-8",
    )
    cfg = load_config(str(c))
    assert cfg.server.gaussian_exec == "/opt/g16/g16"
    assert "GAUSS_EXEDIR" in cfg.server.env_setup


def test_gaussian_env_defaults(tmp_path):
    """[回归] 未配置时 gaussian_exec 默认 g16，env_setup 为空。"""
    c = tmp_path / "min.conf"
    c.write_text("[server:default]\nhost = h\nuser = u\n", encoding="utf-8")
    cfg = load_config(str(c))
    assert cfg.server.gaussian_exec == "g16"
    assert cfg.server.env_setup == ""


def test_env_setup_keeps_bare_shell_vars(tmp_path, monkeypatch):
    """[回归] env_setup 中的裸 $VAR（$PATH）必须保留字面，不被本机环境误展开。"""
    monkeypatch.setenv("PATH", "C:/windows-local-path")
    c = tmp_path / "envshell.conf"
    c.write_text(
        "[server:default]\nhost = h\nuser = u\n"
        "env_setup = export PATH=$PATH:/opt/g16; export GAUSS_EXEDIR=/opt/g16\n",
        encoding="utf-8",
    )
    cfg = load_config(str(c))
    # $PATH 保留字面（供远端展开），不被展开成 C:/windows-local-path
    assert "$PATH:/opt/g16" in cfg.server.env_setup
    assert "C:/windows-local-path" not in cfg.server.env_setup
    # ${VAR} 显式形式仍被展开
    monkeypatch.setenv("GTRANSFER_PASSWORD", "pw123")
    c2 = tmp_path / "envbrace.conf"
    c2.write_text(
        "[server:default]\nhost = h\nuser = u\npassword = ${GTRANSFER_PASSWORD}\n",
        encoding="utf-8",
    )
    cfg2 = load_config(str(c2))
    assert cfg2.server.password == "pw123"
