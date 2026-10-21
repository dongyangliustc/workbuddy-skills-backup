#!/usr/bin/env python3
"""qc_kb.py — Q-Chem 知识库查询与输入校验（防幻觉闸门）。

三个能力
--------
1. `search`   在**本机版本的官方手册**里检索（不是模型记忆），返回页码与原文片段
2. `keyword`  查关键字定义（类型/默认值/允许取值/手册页码/来源）
3. `validate` 校验一个输入文件：段名、$rem 关键字、取值是否都在知识库中；
   未知项一律**报错并给出最接近的候选**，而不是静默放过

设计原则
--------
- **版本精确**：一切判定基于随本机 Q-Chem 一起安装的手册与官方样例，
  默认不采信模型记忆，也不默认采信别的版本（如线上 7.0）手册。
- **双源白名单**：manual（有文档定义）/ samples（官方样例在用）。
- **零依赖**：纯标准库，直接可跑。

用法示例
--------
  python qc_kb.py search "EOM-CCSD excitation energy" --top 5
  python qc_kb.py keyword MEM_TOTAL
  python qc_kb.py values METHOD
  python qc_kb.py validate job.in            # 退出码 0=通过 / 2=有未知项
  python qc_kb.py stats
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

DEFAULT_KB = Path(__file__).resolve().parent.parent / "references" / "qchem-6.1.1" / "kb"

SECTION_LINE_RE = re.compile(r"^\$([a-z][a-z0-9_]*)\s*$", re.IGNORECASE)
REM_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*=?\s*(.*)$")


class KB:
    def __init__(self, root: Path):
        self.root = root
        if not (root / "keywords.json").exists():
            raise SystemExit(f"知识库不存在或未构建: {root}\n"
                             f"请先运行 scripts/build_kb.py（见 SKILL.md）")
        self.keywords = json.loads((root / "keywords.json").read_text(encoding="utf-8"))
        self.values = json.loads((root / "values_observed.json").read_text(encoding="utf-8"))
        self.sections = json.loads((root / "sections.json").read_text(encoding="utf-8"))
        self.meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        self._chunks = None

    # -- 惰性加载分块（检索用）-----------------------------------------
    @property
    def chunks(self) -> list[dict]:
        if self._chunks is None:
            p = self.root / "chunks.jsonl"
            self._chunks = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        return self._chunks

    def lookup(self, name: str):
        return self.keywords.get(name.upper())

    def suggest(self, name: str, n: int = 5) -> list[str]:
        return difflib.get_close_matches(name.upper(), list(self.keywords), n=n, cutoff=0.6)

    def observed(self, name: str) -> dict:
        return self.values.get(name.upper(), {})

    def search(self, query: str, top: int = 5) -> list[dict]:
        """轻量词法检索：命中词数 + 词频/长度归一 + 关键字名加权。"""
        toks = [t for t in re.split(r"[^A-Za-z0-9_.()+-]+", query.lower()) if len(t) > 1]
        if not toks:
            return []
        scored = []
        for c in self.chunks:
            text = c["text"]
            low = text.lower()
            score = 0.0
            hits = 0
            for t in toks:
                n = low.count(t)
                if n:
                    hits += 1
                    score += 1.0 + (n - 1) * 0.15
            if not hits:
                continue
            score *= hits / len(toks)                 # 覆盖率
            score /= (1.0 + len(text) / 4000.0)       # 长度惩罚
            # 关键字名整词出现加权（手册定义块往往因此排前）
            if any(re.search(rf"\b{re.escape(t.upper())}\b", text) for t in toks):
                score *= 1.3
            scored.append((score, c))
        scored.sort(key=lambda x: -x[0])
        out = []
        for s, c in scored[:top]:
            snippet = c["text"]
            for t in toks:
                m = re.search(re.escape(t), snippet, re.IGNORECASE)
                if m:
                    a = max(0, m.start() - 160)
                    snippet = snippet[a:a + 700]
                    break
            out.append({"score": round(s, 3), "manual_page": c["page"],
                        "chapter": c.get("chapter", ""), "snippet": snippet.strip()})
        return out


COMMENT_SECTIONS = ("$comment", "$comments", "$comment0", "$commment")


def parse_input(path: Path):
    """返回 (段名列表, [(关键字, 取值, 行号)], 无法解析的行, 注释陷阱)。

    注释陷阱：**`$comment` 块内以 `$` 开头的行会被 Q-Chem 解析器当成真正的段头**。
    实测踩过：在注释里写 `$occupied 依据：...` 导致解析器把后续中文注释当
    `$occupied` 的内容去解析整数，报 `non-integral value in $occupied block`（极难定位）。
    因此这里把它作为错误单独报出。
    """
    secs: list[str] = []
    rems: list[tuple[str, str, int]] = []
    odd: list[str] = []
    traps: list[str] = []
    in_rem = False
    in_comment = False
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if line.lower() == "$end":
            in_rem = False
            in_comment = False
            continue
        if in_comment:
            if line.startswith("$"):
                traps.append(f"L{lineno}: 注释块内出现以 $ 开头的行 {line[:40]!r}"
                             f"（会被当作段头解析）")
            continue
        if not line or line.startswith("!"):
            continue
        m = SECTION_LINE_RE.match(line)
        if m:
            sec = "$" + m.group(1).lower()
            secs.append(sec)
            in_rem = sec == "$rem"
            in_comment = sec in COMMENT_SECTIONS
            continue
        if in_rem:
            m2 = REM_LINE_RE.match(line)
            if m2:
                rest = re.split(r"\s+!", m2.group(2))[0].strip()
                rems.append((m2.group(1).upper(), rest, lineno))
            else:
                odd.append(f"{lineno}: {line}")
    return secs, rems, odd, traps


def cmd_search(kb: KB, args) -> int:
    for r in kb.search(" ".join(args.query), args.top):
        print(f"--- 手册 p.{r['manual_page']}  {r['chapter']}  (score {r['score']}) ---")
        print(r["snippet"])
        print()
    return 0


def cmd_keyword(kb: KB, args) -> int:
    name = args.name.upper()
    e = kb.lookup(name)
    if not e:
        print(f"✗ 知识库中没有关键字 {name}")
        cand = kb.suggest(name)
        if cand:
            print("  最接近的候选:", ", ".join(cand))
        print("  ⇒ 不要凭记忆使用该关键字；请在手册中检索确认，"
              "或用 `qc_kb.py search <关键词>` 找到正式名称。")
        return 2
    print(f"## {name}")
    print("描述      :", e.get("description") or "(手册未提供)")
    print("类型      :", e.get("type") or "(未记录)")
    print("默认值    :", e.get("default") or "(未记录)")
    print("文档取值  :", ", ".join(e.get("options") or []) or "(未记录)")
    o = kb.observed(name)
    if o:
        top = list(o.items())[:12]
        print("样例实测值:", ", ".join(f"{k}×{v['count']}" for k, v in top))
        ex = [x for v in o.values() for x in v["examples"]][:3]
        print("示例文件  :", ", ".join(ex))
    print("来源      :", ", ".join(e.get("source") or []))
    print("手册页码  :", ", ".join(str(p) for p in (e.get("pages") or [])) or "(无定义块)")
    return 0


def cmd_values(kb: KB, args) -> int:
    o = kb.observed(args.name)
    if not o:
        print(f"（官方样例中未出现该关键字的取值观测）")
        return 0
    print(f"## {args.name.upper()} 的官方样例实测取值（本版本确实可跑）")
    for v, info in o.items():
        print(f"  {v:<28} 次数={info['count']:<4} 例: {', '.join(info['examples'][:2])}")
    print("\n注意：这是**下界**而非全集 —— 样例没用过不等于不支持；"
          "但**样例用过必然支持**，可作首选。")
    return 0


def cmd_list(kb: KB, args) -> int:
    pat = re.compile(args.match.upper()) if args.match else None
    rows = [(k, v) for k, v in kb.keywords.items()
            if (not pat or pat.search(k))
            and (not args.source or args.source in (v.get("source") or []))]
    for k, v in sorted(rows):
        flag = "M" if "manual" in (v.get("source") or []) else " "
        flag += "S" if "samples" in (v.get("source") or []) else " "
        print(f"  {k:<34} [{flag}] {v.get('type','')}")
    print(f"\n共 {len(rows)} 个（M=手册定义, S=官方样例在用）")
    return 0


def cmd_stats(kb: KB, args) -> int:
    print(json.dumps(kb.meta, ensure_ascii=False, indent=1)[:4000])
    return 0


def cmd_validate(kb: KB, args) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"文件不存在: {path}")
        return 2
    secs, rems, odd, traps = parse_input(path)
    known_secs = set(kb.sections["union"])
    errors: list[str] = []
    warns: list[str] = []
    print(f"=== 校验 {path.name}（基准：Q-Chem {kb.meta['label']} 手册 + 官方样例）===")
    print(f"段: {', '.join(secs) or '(无)'}")
    for t in traps:
        errors.append(t)
    for s in secs:
        if s not in known_secs:
            errors.append(f"未知输入段 {s}（不在手册与官方样例的段集合中）")
    print(f"\n$rem 关键字 {len(rems)} 个：")
    for name, val, lineno in rems:
        e = kb.lookup(name)
        if not e:
            cand = kb.suggest(name)
            errors.append(f"L{lineno}: 未知关键字 {name}"
                          + (f"（是否想写 {'/'.join(cand)}？）" if cand else ""))
            print(f"  ✗ L{lineno} {name}")
            continue
        src = "+".join(e.get("source") or [])
        pages = ",".join(str(p) for p in (e.get("pages") or [])[:3]) or "-"
        tag = f"[{src} p{pages}]"
        # 取值核对：文档 OPTIONS 或 官方样例实测值
        note = ""
        if val:
            v0 = val.split()[0].rstrip(",").upper()
            ktype = (e.get("type") or "").upper()
            # **按类型判定**：只有真正可枚举的类型才做硬性取值检查，
            # 否则会产生假警报（实测 MEM_TOTAL=28000 / CC_CONVERGENCE=6
            # 被误报，而它们分别是任意整数与收敛限，本就合法）。
            if "LOGICAL" in ktype:
                if v0 not in ("TRUE", "FALSE", "1", "0", "T", "F"):
                    note = f"  ⚠ LOGICAL 型取值应为 TRUE/FALSE，收到 {v0}"
                    warns.append(f"L{lineno}: {name}={v0} 非逻辑值")
            elif "INTEGER" in ktype:
                if not re.match(r"^[+-]?\d+(\.\d+)?$", v0):
                    note = f"  ⚠ INTEGER 型取值应为整数，收到 {v0}"
                    warns.append(f"L{lineno}: {name}={v0} 非整数")
            else:
                # 字符串/多选型：文档枚举通常不完备，仅作提示、不计入警告
                opts = {o.upper() for o in (e.get("options") or [])}
                obs = set(kb.observed(name))
                if (opts or obs) and v0 not in opts and v0 not in obs:
                    note = (f"  ℹ 取值 {v0} 未见于文档枚举/官方样例"
                            f"（不代表不支持，仅提示）")
        print(f"  ✓ L{lineno} {name}{'=' + val if val else ''} {tag}{note}")
    for line in odd:
        warns.append(f"无法解析的行 {line}")
    print()
    if warns:
        print("警告:")
        for w in warns:
            print("  ⚠", w)
    if errors:
        print("错误:")
        for e in errors:
            print("  ✗", e)
        print(f"\n结论：不通过（{len(errors)} 项未知）。"
              f"请用 `qc_kb.py search` / `keyword` 核实后再提交，"
              f"不要凭模型记忆拼关键字。")
        return 2
    print("结论：通过 —— 所有段名与 $rem 关键字均可在本版本知识库中溯源。")
    if warns:
        print("（存在警告项，建议人工确认取值）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Q-Chem 知识库查询与输入校验")
    ap.add_argument("--kb", default=str(DEFAULT_KB), help="知识库目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="在官方手册中检索")
    p.add_argument("query", nargs="+")
    p.add_argument("--top", type=int, default=5)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("keyword", help="查关键字定义")
    p.add_argument("name")
    p.set_defaults(func=cmd_keyword)

    p = sub.add_parser("values", help="查官方样例实测取值")
    p.add_argument("name")
    p.set_defaults(func=cmd_values)

    p = sub.add_parser("list", help="列出关键字")
    p.add_argument("--match", default="")
    p.add_argument("--source", default="", choices=["", "manual", "samples"])
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("validate", help="校验输入文件（未知项即报错）")
    p.add_argument("file")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("stats", help="知识库元信息")
    p.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    kb = KB(Path(args.kb))
    return args.func(kb, args)


if __name__ == "__main__":
    sys.exit(main())
