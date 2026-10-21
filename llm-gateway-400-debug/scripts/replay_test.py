#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replay_test.py —— 终极验证: 把捕获的失败请求体原样重放

    python replay_test.py <capture文件> --target http://127.0.0.1:8788 --path /chat/completions
    python replay_test.py <capture文件> --direct https://api.example.com   # 对照

判定: 同一份字节, 修复前 400 -> 修复后 200, 即闭环。
"""
import argparse
import json
import os
import ssl
import time
import urllib.error
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("capture")
ap.add_argument("--target", help="经诊断代理重放, 如 http://127.0.0.1:8788")
ap.add_argument("--direct", help="绕过代理直连上游, 作对照")
ap.add_argument("--path", default="/chat/completions")
ap.add_argument("--key", default=os.environ.get("GATEWAY_KEY", ""))
args = ap.parse_args()

if not args.key:
    mj = os.path.expanduser("~/.workbuddy/models.json")
    if os.path.exists(mj):
        args.key = json.load(open(mj, encoding="utf-8"))[0].get("apiKey", "")
if not (args.target or args.direct):
    ap.error("必须指定 --target 或 --direct 之一")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
# 强制直连, 绕过环境里的 http_proxy/https_proxy
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                     urllib.request.HTTPSHandler(context=CTX))

base = (args.target or args.direct).rstrip("/")
payload = open(args.capture, "rb").read()
print("重放: %s (%d B) -> %s%s" % (args.capture, len(payload), base, args.path))

req = urllib.request.Request(
    base + args.path, data=payload,
    headers={"Authorization": "Bearer " + args.key, "Content-Type": "application/json"})
t0 = time.time()
try:
    with OPENER.open(req, timeout=300) as r:
        st, body = r.status, r.read().decode("utf-8", "replace")
except urllib.error.HTTPError as e:
    st, body = e.code, e.read().decode("utf-8", "replace")
except Exception as e:
    st, body = "EXC", repr(e)
t = time.time() - t0

print("HTTP %s  (%.1f s)" % (st, t))
if st == 200:
    print("-> 同一请求由 400 变 200, 修复确证")
else:
    try:
        e = json.loads(body).get("error")
        print("仍失败: %s" % ((e.get("message") if isinstance(e, dict) else str(e))[:400]))
    except Exception:
        print("仍失败: %s" % body[:400])
