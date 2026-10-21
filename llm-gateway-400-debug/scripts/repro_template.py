#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三组对照实验模板 —— 确证 400 根因的标准姿势

判定标准: R1(坏形态) 400 且错误原文与线上一致; R2/R3 各去掉一个嫌疑 -> 200。
复制本文件, 改 R1/R2/R3 的 messages 即可。

用法: python repro_template.py --upstream https://api.example.com --model 你的模型名
"""
import argparse
import json
import os
import ssl
import time
import urllib.error
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--upstream", default="https://api.llm.ustc.edu.cn")
ap.add_argument("--model", required=True)
ap.add_argument("--path", default="/v1/chat/completions")
ap.add_argument("--key", default=os.environ.get("GATEWAY_KEY", ""))
args = ap.parse_args()

if not args.key:
    mj = os.path.expanduser("~/.workbuddy/models.json")
    if os.path.exists(mj):
        args.key = json.load(open(mj, encoding="utf-8"))[0].get("apiKey", "")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
# 关键: 强制直连, 绕过环境里的 http_proxy/https_proxy
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                     urllib.request.HTTPSHandler(context=CTX))

BASE = {"model": args.model, "stream": False, "max_tokens": 48}


def call(messages, tag):
    body = dict(BASE)
    body["messages"] = messages
    req = urllib.request.Request(
        args.upstream.rstrip("/") + args.path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + args.key,
                 "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with OPENER.open(req, timeout=200) as r:
            st, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        st, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        st, raw = "EXC", repr(e)
    t = time.time() - t0
    if st == 200:
        try:
            info = repr(json.loads(raw)["choices"][0]["message"]["content"])[:40]
        except Exception:
            info = raw[:40]
    else:
        try:
            e = json.loads(raw).get("error")
            info = (e.get("message") if isinstance(e, dict) else str(e))[:220]
        except Exception:
            info = raw[:220]
    print("%-38s HTTP %-4s (%.1fs)\n    %s\n" % (tag, st, t, info))
    time.sleep(3.5)          # 尊重常见 20 rpm/key 限速
    return st


U1 = {"role": "user", "content": "1+1=?"}
U2 = {"role": "user", "content": "那 3+3 呢?"}

# ---- 在此填入三组对照(示例: 别名键同现) ----
# R1 坏形态: 两个嫌疑键同现        -> 期望 400 且错误与线上一致
# R2 对照: 只保留嫌疑 A           -> 期望 200
# R3 对照: 只保留嫌疑 B           -> 期望 200
call([U1, {"role": "assistant", "content": "2",
           "reasoning": "想", "reasoning_content": "想"}, U2], "R1 两键同现(坏形态)")
call([U1, {"role": "assistant", "content": "2", "reasoning": "想"}, U2], "R2 只带 reasoning")
call([U1, {"role": "assistant", "content": "2", "reasoning_content": "想"}, U2], "R3 只带 reasoning_content")
