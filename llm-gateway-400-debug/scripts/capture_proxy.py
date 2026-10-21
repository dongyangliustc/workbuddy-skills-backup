#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capture_proxy.py —— 网关 400 诊断用本地捕获代理（零依赖，仅标准库）

    客户端 -> 127.0.0.1:<port> -> 上游网关

功能
----
1. 逐请求元数据日志(方法/路径/顶层字段名/变更/上游状态/耗时)，不打正文
2. **仅当响应非 200 时**把该次请求体落盘到 capture/（成功请求永不落盘）
3. 可选字段剥离: --strip f1,f2
4. 消息级规范化钩子: sanitize_messages() —— 针对具体网关的修复写在这里，
   必须附实测依据注释
5. 调试端点: GET /_health ; POST /_echo(返回将要转发的请求体, 零模型调用)

用法
----
    python capture_proxy.py --port 8788 --upstream https://api.example.com
    python capture_proxy.py --port 8788 --upstream ... --strip truncation,safety_identifier

然后把客户端配置的 baseUrl 改为 http://127.0.0.1:<port>/
"""
import argparse
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "strip_proxy.log")
LOG_MAX = 256 * 1024
CAPTURE_DIR = os.path.join(BASE_DIR, "capture")

# ---- 按需修改: 上游明确拒绝的参数(Responses API 专属字段等) ----
STRIP_FIELDS = ()
STRIP_EXTRA = ()
HOP_BY_HOP = ("connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade",
              "proxy-connection", "content-length")

# 强制直连: 绕过环境里的 http_proxy/https_proxy, 否则本地端口会被劫持
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_log_lock = threading.Lock()


def log_line(msg):
    line = "[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with _log_lock:
        try:
            if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX:
                bak = LOG_PATH + ".1"
                if os.path.exists(bak):
                    os.remove(bak)
                os.rename(LOG_PATH, bak)
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:
            pass


def sanitize_messages(obj):
    """消息级规范化钩子。针对具体网关的修复写在这里, 必须附实测依据。
    返回变更说明列表。默认无操作。

    示例(实测确证: 上游把 reasoning 声明为 reasoning_content 的 serde alias,
    两键同现即 400 duplicate field):
        for i, m in enumerate(obj.get("messages", [])):
            if isinstance(m, dict) and "reasoning" in m and "reasoning_content" in m:
                del m["reasoning"]
                changed.append("msg%d:drop_reasoning_alias" % i)
    """
    return []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GatewayCaptureProxy/1.0"
    strip = False
    upstream = "https://api.llm.ustc.edu.cn"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except Exception:
            pass

    def do_GET(self):
        if self.path.rstrip("/") in ("/_health", "/_health/"):
            self._send(200, json.dumps({"ok": True, "strip": self.strip,
                                        "upstream": self.upstream}))
            return
        self._proxy(b"")

    def do_HEAD(self):
        self._proxy(b"", keep_body=False)

    def do_POST(self):
        raw = self._read_body()
        if self.path.rstrip("/") in ("/_echo", "/_echo/"):
            body, removed = self._transform(raw)
            self._send(200, json.dumps({"strip": self.strip, "removed": removed,
                                        "forwarded_body": body.decode("utf-8", "replace")},
                                       ensure_ascii=False))
            return
        self._proxy(raw)

    def do_PUT(self):
        self.do_POST()

    def do_DELETE(self):
        self.do_POST()

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _transform(self, raw):
        removed = []
        body = raw
        if raw:
            try:
                obj = json.loads(raw.decode("utf-8"))
                if isinstance(obj, dict):
                    if self.strip:
                        for f in STRIP_FIELDS + STRIP_EXTRA:
                            if f in obj:
                                obj.pop(f, None)
                                removed.append(f)
                    removed.extend(sanitize_messages(obj))
                    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                log_line("WARN body-not-json path=%s err=%s" % (self.path, e))
        return body, removed

    def _proxy(self, raw, keep_body=True):
        t0 = time.time()
        url = self.upstream.rstrip("/") + self.path
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in HOP_BY_HOP and k.lower() != "host"}
        headers["Host"] = urllib.parse.urlparse(self.upstream).netloc
        body, removed = self._transform(raw)
        seen = []
        if raw:
            try:
                o = json.loads(raw.decode("utf-8"))
                if isinstance(o, dict):
                    seen = sorted(o.keys())
            except Exception:
                pass

        req = urllib.request.Request(url, data=body if body else None,
                                     headers=headers, method=self.command)
        status, resp_headers, resp = 502, {}, b'{"error":"upstream unreachable"}'
        try:
            with OPENER.open(req, timeout=300) as r:
                status, resp_headers = r.status, {k.lower(): v for k, v in r.headers.items()}
                resp = r.read() if keep_body else b""
        except urllib.error.HTTPError as e:
            status = e.code
            resp_headers = {k.lower(): v for k, v in e.headers.items()}
            try:
                resp = e.read()
            except Exception:
                resp = b""
        except Exception as e:
            resp = json.dumps({"error": "upstream error", "detail": repr(e)}).encode()
            log_line("ERR upstream path=%s err=%s" % (self.path, repr(e)))

        ms = int((time.time() - t0) * 1000)
        log_line("%s %s fields=%s removed=%s -> %s (%d ms, %d B)"
                 % (self.command, self.path, ",".join(seen) or "-",
                    ",".join(removed) or "-", status, ms, len(resp)))
        if status != 200:
            try:
                log_line("    ERROR-BODY: %s" % resp.decode("utf-8", "replace")[:900])
            except Exception:
                pass
            if raw:
                try:
                    os.makedirs(CAPTURE_DIR, exist_ok=True)
                    fn = os.path.join(CAPTURE_DIR, "%s_%s_%d_req.json"
                                      % (time.strftime("%H%M%S"), status, int(t0) % 100000))
                    with open(fn, "wb") as fh:
                        fh.write(raw)
                    log_line("    CAPTURED-REQ: %s (%d B)" % (fn, len(raw)))
                except Exception as e:
                    log_line("    CAPTURE-FAIL: %s" % e)

        self.send_response(status)
        for k, v in resp_headers.items():
            if k in HOP_BY_HOP or k == "content-length":
                continue
            try:
                self.send_header(k, v)
            except Exception:
                pass
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        if keep_body and resp:
            try:
                self.wfile.write(resp)
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--strip", default="", help="逗号分隔的待删除顶层字段")
    args = ap.parse_args()

    if args.strip:
        Handler.strip = True
        globals()["STRIP_FIELDS"] = tuple(s.strip() for s in args.strip.split(",") if s.strip())

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        log_line("EXIT port %d busy (%s)" % (args.port, e))
        print("port %d already in use; exit." % args.port)
        return
    srv.daemon_threads = True
    Handler.upstream = args.upstream
    log_line("START port=%d upstream=%s strip=%s fields=%s pid=%d"
             % (args.port, args.upstream, Handler.strip, globals().get("STRIP_FIELDS"),
                os.getpid()))
    print("capture proxy on 127.0.0.1:%d -> %s (strip=%s)"
          % (args.port, args.upstream, Handler.strip), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
