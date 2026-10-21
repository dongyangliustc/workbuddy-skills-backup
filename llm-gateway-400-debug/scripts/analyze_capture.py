#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_capture.py —— 结构化分析捕获的失败请求体（不打印任何会话内容）

    python analyze_capture.py <capture文件> [可疑字段名]

输出:
  - 文件大小 / 顶层键 / 消息条数与每条的 role + 键名列表
  - 重复键检测(object_pairs_hook, 含重复字段名)
  - 可疑字段的出现偏移、所在消息下标、值类型
"""
import json
import re
import sys


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_capture.py <capture文件> [可疑字段名]")
        return 1
    path = sys.argv[1]
    suspicious = sys.argv[2] if len(sys.argv) > 2 else "reasoning_content"

    raw = open(path, "rb").read()
    text = raw.decode("utf-8", "replace")
    print("文件: %s  (%d B)" % (path, len(raw)))

    dups = []

    def hook(pairs):
        seen, d = set(), []
        for k, _ in pairs:
            if k in seen and k not in d:
                d.append(k)
            seen.add(k)
        if d:
            dups.append(d)
        return dict(pairs)

    obj = json.loads(text, object_pairs_hook=hook)
    print("\n[顶层键] %s" % sorted(obj.keys()) if isinstance(obj, dict) else type(obj))
    print("[重复键对象] %d 个 %s" % (len(dups), dups[:5] if dups else ""))

    msgs = obj.get("messages", []) if isinstance(obj, dict) else []
    print("\n[消息] 共 %d 条" % len(msgs))
    for i, m in enumerate(msgs):
        if isinstance(m, dict):
            extra = ""
            if suspicious in m:
                v = m[suspicious]
                extra = "  %s: 类型=%s" % (suspicious, type(v).__name__)
                if isinstance(v, str):
                    extra += "(len=%d)" % len(v)
            print("  [%2d] role=%-10s 键=%s%s" % (i, m.get("role"), sorted(m.keys()), extra))

    print("\n[%s 出现位置] 共 %d 次" % (suspicious, text.count('"%s"' % suspicious)))
    for m in re.finditer(r'"%s"' % re.escape(suspicious), text):
        idx = _msg_index_at(obj, text, m.start()) if msgs else "?"
        print("  offset %7d  (约在第 %s 条消息附近)" % (m.start(), idx))

    other = _sibling_keys(obj, suspicious)
    if other:
        print("\n[与 %s 同现的其它键] %s" % (suspicious, sorted(other)))
        print("  -> 若上游把其中某个声明为 %s 的 alias, 两键同现即 duplicate field" % suspicious)
    else:
        print("\n[与 %s 同现的其它键] 无(该字段独占其所在对象)" % suspicious)
    return 0


def _msg_index_at(obj, text, offset):
    """粗略定位: 统计 offset 之前出现的 '"role"' 次数"""
    return text[:offset].count('"role"')


def _sibling_keys(obj, field):
    """递归遍历解析后的结构, 收集与可疑字段同一对象内的其它键名"""
    keys = set()

    def walk(node):
        if isinstance(node, dict):
            if field in node:
                keys.update(k for k in node.keys() if k != field)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(obj)
    return keys


if __name__ == "__main__":
    sys.exit(main())
