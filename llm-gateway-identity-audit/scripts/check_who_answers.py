#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_who_answers.py —— "你以为在用 X, 实际是不是 Y" 检测器

原理
----
USTC LLM 网关(LiteLLM 1.98.0)在响应头里暴露真实上游身份:
    x-litellm-model-name           实际提供服务的模型(权威)
    x-litellm-model-api-base       实际上游端点
    x-litellm-attempted-fallbacks  本次请求触发了几次 fallback(>0 = 已换模型)
    x-litellm-attempted-retries
而 JSON body 里的 "model" 字段只是回显请求别名, 不可用于鉴别。

判定分级
----
    OK                 别名与实际上游一致
    FALLBACK_FIRED     触发了 fallback, 已由备用模型作答          (P0)
    ALIAS_MISMATCH     跨厂商/跨系列替换, 如 claude-opus->deepseek (P0)
    VARIANT_COLLAPSE   同系列但语义变体被抹平, 如 -reasoner 与 -chat 同一部署 (P1)
    VERSION_DRIFT      同系列但版本号不同, 如 qwen3.5 -> qwen38    (P2)
    NO_HEADER          响应无 LiteLLM 头(可能经本地代理), 无法鉴别  (P2)
    ERROR              请求失败

用法
----
    python check_who_answers.py                      审计 models.json 里全部自定义模型
    python check_who_answers.py <别名> [<别名>...]    只查指定别名
    python check_who_answers.py --all                审计网关 /v1/models 全部别名

退出码  0=无发现   1=有发现   2=执行出错
"""
import json, os, re, ssl, sys, time, urllib.request, urllib.error

BASE_DEFAULT = "https://api.llm.ustc.edu.cn"
MODELS_JSON = os.path.expanduser("~/.workbuddy/models.json")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

TIER = {"FALLBACK_FIRED": "P0", "ALIAS_MISMATCH": "P0",
        "VARIANT_COLLAPSE": "P1", "VERSION_DRIFT": "P2", "NO_HEADER": "P2"}
# 有语义含义的模型变体后缀; 若别名有而上游没有 -> 变体被抹平
SEMANTIC = ("reasoner", "thinking", "non-thinking", "nonthinking",
            "vision", "coder", "embedding", "reranker", "ocr")

if sys.stdout.isatty():
    C_OK, C_W, C_B, C_OFF = "\033[32m", "\033[33m", "\033[31m", "\033[0m"
else:
    C_OK = C_W = C_B = C_OFF = ""


def split_name(s):
    """-> (字母系列, 版本数字串, 后缀集合)"""
    s = (s or "").split("/")[-1].strip().lower().replace(".", "")
    m = re.match(r"^([a-z]+)", s)
    fam = m.group(1) if m else ""
    rest = s[len(fam):]
    ver = "".join(re.findall(r"\d+", rest.split("-")[0]))     # 首个 - 之前的数字 = 版本号
    if not ver:
        nums = re.findall(r"\d+", rest)
        ver = nums[0] if nums else ""
    toks = set(re.split(r"[-_ ]+", rest)) - {""}
    return fam, ver, toks


def classify(alias, upstream, fb):
    try:
        if int(fb) > 0:
            return "FALLBACK_FIRED"
    except Exception:
        pass
    if not upstream:
        return "NO_HEADER"
    fa, va, ta = split_name(alias)
    fu, vu, tu = split_name(upstream)
    if fa != fu:
        return "ALIAS_MISMATCH"
    if ta & set(SEMANTIC) and not (ta & set(SEMANTIC) & tu):
        return "VARIANT_COLLAPSE"
    if va and vu and va != vu:
        return "VERSION_DRIFT"
    return "OK"


def probe(base, key, alias, timeout=120):
    body = {"model": alias,
            "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
            "max_tokens": 16, "stream": False}
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status, r.headers, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")
    except Exception as e:
        return "EXC", {}, repr(e)


def audit_one(base, key, alias):
    st, h, raw = probe(base, key, alias)
    rec = {"alias": alias, "status": st, "upstream": h.get("x-litellm-model-name"),
           "api_base": h.get("x-litellm-model-api-base"),
           "fallbacks": h.get("x-litellm-attempted-fallbacks"),
           "base": base}
    try:
        rec["json_model"] = json.loads(raw).get("model")
    except Exception:
        rec["json_model"] = None
    if st != 200:
        rec["verdict"] = "ERROR"
        try:
            e = json.loads(raw).get("error")
            rec["error"] = (e.get("message") if isinstance(e, dict) else str(e))[:200]
        except Exception:
            rec["error"] = raw[:200]
        return rec
    rec["verdict"] = classify(alias, rec["upstream"], rec["fallbacks"])
    return rec


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--all" in sys.argv
    cfg = json.load(open(MODELS_JSON, encoding="utf-8")) if os.path.exists(MODELS_JSON) else []
    default_key = cfg[0].get("apiKey", "") if cfg else ""

    if args:
        targets = [(a, BASE_DEFAULT, default_key) for a in args]
    else:
        targets = [(m["id"], m.get("url") or BASE_DEFAULT, m.get("apiKey", "")) for m in cfg]
    if full:
        req = urllib.request.Request(BASE_DEFAULT + "/v1/models",
                                     headers={"Authorization": "Bearer " + default_key})
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            ids = [m["id"] for m in json.loads(r.read().decode())["data"]]
        targets = [(i, BASE_DEFAULT, default_key) for i in ids]

    if not targets:
        print("没有可审计的目标(models.json 不存在且未指定别名)")
        return 2

    print("=" * 100)
    print("%-28s %-5s %-17s %-4s %-30s %s" % ("别名(你请求的)", "HTTP", "判定", "级别", "实际作答模型", "fb"))
    print("=" * 100)
    findings = {"P0": 0, "P1": 0, "P2": 0}
    for i, (alias, base, key) in enumerate(targets):
        r = audit_one(base, key, alias)
        v = r["verdict"]
        t = TIER.get(v, "-")
        if t in findings:
            findings[t] += 1
        col = C_OK if v == "OK" else (C_B if t == "P0" else C_W)
        print("%s%-28s %-5s %-17s %-4s %-30s %s%s"
              % (col, alias, r["status"], v, t, r["upstream"] or "-", r["fallbacks"], C_OFF))
        if v == "ALIAS_MISMATCH":
            print("    ! 请求 %r, 实际由 %s 作答" % (alias, r["upstream"]))
        elif v == "FALLBACK_FIRED":
            print("    ! 触发 fallback %s 次, 由 %s 作答" % (r["fallbacks"], r["upstream"]))
        elif v == "VARIANT_COLLAPSE":
            print("    ! 变体后缀未体现在上游: 请求 %s -> %s (与同系列非推理版本同一部署)" % (alias, r["upstream"]))
        elif v == "VERSION_DRIFT":
            print("    ! 版本漂移: 请求 %s -> %s" % (alias, r["upstream"]))
        elif v == "NO_HEADER":
            print("    ! 无 LiteLLM 响应头(base=%s), 无法鉴别真实模型" % r["base"])
        elif v == "ERROR":
            print("    ! %s" % (r.get("error") or "")[:160])
        sys.stdout.flush()
        if i < len(targets) - 1:
            time.sleep(3.2)                     # 尊重 20 rpm 限速

    print("=" * 100)
    print("汇总: P0=%d  P1=%d  P2=%d" % (findings["P0"], findings["P1"], findings["P2"]))
    if findings["P0"]:
        print("%s存在跨模型替换。JSON 的 model 字段不可信, 请以 x-litellm-model-name 为准。%s" % (C_B, C_OFF))
    elif findings["P1"] or findings["P2"]:
        print("%s无跨厂商替换, 但存在变体/版本层面的命名与实现不一致。%s" % (C_W, C_OFF))
    else:
        print("%s全部一致: 请求别名 == 实际作答模型, 未触发 fallback。%s" % (C_OK, C_OFF))
    return 1 if (findings["P0"] or findings["P1"] or findings["P2"]) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("执行出错:", repr(e))
        sys.exit(2)
