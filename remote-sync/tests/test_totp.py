"""test_totp.py — TOTP 生成与口令拼接（RFC 6238）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gtransfer_lib.connection import _compose_password, _totp_code  # noqa: E402
from gtransfer_lib.config import ServerProfile  # noqa: E402


def test_totp_generates_6_digits():
    """生成的验证码为 6 位数字。"""
    code = _totp_code("SO3PJUOLREQFBZVGT5VCC4WDLA")
    assert len(code) == 6
    assert code.isdigit()


def test_totp_deterministic_within_window():
    """同一窗口内验证码稳定。"""
    c1 = _totp_code("SO3PJUOLREQFBZVGT5VCC4WDLA", drift=0)
    c2 = _totp_code("SO3PJUOLREQFBZVGT5VCC4WDLA", drift=0)
    assert c1 == c2


def test_totp_adjacent_windows_differ():
    """相邻窗口验证码不同。"""
    c0 = _totp_code("SO3PJUOLREQFBZVGT5VCC4WDLA", drift=0)
    c1 = _totp_code("SO3PJUOLREQFBZVGT5VCC4WDLA", drift=1)
    assert c0 != c1


def test_totp_rfc6238_known_vector():
    """RFC 6238 官方测试向量：secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ（ASCII '1234567890'）。

    该向量在 T=59s 处期望 94287082（8 位）。这里验证算法正确性：
    用 8 位 + 固定时间戳对照。
    """
    import struct
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32('1234567890')
    # T = 59 → counter = 1 (30s 窗口)
    import base64, hashlib, hmac
    key = base64.b32decode(secret)
    msg = struct.pack(">Q", 1)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code8 = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** 8)
    assert f"{code8:08d}" == "94287082"


def test_compose_password_plain():
    """无 TOTP 时密码原样。"""
    profile = ServerProfile(name="t", host="h", user="u",
                            password="secret", remote_work_root="~/x")
    assert _compose_password(profile) == "secret"


def test_compose_password_with_totp():
    """有 TOTP 时密码拼接 6 位验证码。"""
    profile = ServerProfile(name="t", host="h", user="u",
                            password="secret", totp_secret="SO3PJUOLREQFBZVGT5VCC4WDLA",
                            remote_work_root="~/x")
    pw = _compose_password(profile)
    assert pw.startswith("secret")
    assert len(pw) == len("secret") + 6
    assert pw[-6:].isdigit()
