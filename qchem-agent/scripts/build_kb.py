#!/usr/bin/env python3
"""build_kb.py — 从 Q-Chem 官方手册 PDF + 官方样例输入构建「版本精确」知识库。

产物（写入 references/<version>/）：
  keywords.json    $rem 关键字注册表：类型 / 默认值 / 允许取值 / 手册页码
  sections.json    合法输入段（$molecule 等），来自官方样例 + 手册
  chunks.jsonl     手册分页文本块（供 lexical 检索，附章节面包屑）
  samples.json     官方样例索引：每个样例用了哪些段与关键字
  meta.json        构建元信息（手册版本、页数、条数、来源）

设计原则（防幻觉）
------------------
1. **版本精确**：以「本机安装版自带的 $QC/doc/manual.pdf」为唯一定义来源，
   而不是任意版本的线上手册 —— 关键字/取值必须以"能在本机跑"为准。
2. **双源交叉**：白名单主源为手册定义块；官方样例给出「实际被使用过」的
   经验确认与可运行范例。两源都不认的关键字视为可疑。
3. **可复现**：本脚本可对任意版本手册重跑，产出同结构 KB。

手册定义块格式（实测 Q-Chem 6.1）：
    NO_REORIENT
    Disable rotation into the standard nuclear orientation.
    TYPE:
    LOGICAL
    DEFAULT:
    FALSE
    OPTIONS:
    FALSE
    Use the standard nuclear orientation.
    TRUE
    Use the coordinate system defined by the $molecule section.
    RECOMMENDATION:
    ...
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import pymupdf
except ImportError:  # 兼容旧名
    import fitz as pymupdf

#: 关键字 token：全大写 + 下划线/数字。**长度 >= 2** ——
#: 实测 Q-Chem 有 `DH`/`O2`/`S2` 等两字母关键字（DH 定义在 6.1 手册 p.236），
#: 早期用 {2,}（长度>=3）会漏掉它们。误收噪声由"其后须出现 TYPE:"这一条件兜住。
KEYWORD_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,}$")
#: 输入段名（$molecule / $rem / $pcm ...）
SECTION_RE = re.compile(r"^\$([a-z][a-z0-9_]*)\s*$", re.IGNORECASE)
#: 章标题（用于分块面包屑）
CHAPTER_RE = re.compile(r"^(Chapter\s+\d+[:.].*|Appendix\s+[A-Z][:.].*)$")
#: 选项行：`VALUE` 或 `VALUE (or N)` 开头，其后可跟说明
OPTION_LINE_RE = re.compile(r"^([A-Za-z0-9_.+\-]+(?:\s*\(or\s*[^)]*\))?)\s*(.*)$")


#: LaTeX PDF 文本层里的连字（ligature）。若不归一，"User-deﬁned" 会被
#: 正则截成 "User-de"，且检索时含连字的词永远匹配不到。
_LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
              "\ufb03": "ffi", "\ufb04": "ffl", "\u0132": "IJ", "\u0133": "ij"}


def normalize(text: str) -> str:
    for k, v in _LIGATURES.items():
        text = text.replace(k, v)
    return text


def iter_pages(doc) -> list[str]:
    return [normalize(doc[i].get_text()) for i in range(doc.page_count)]


def parse_keywords(pages: list[str]) -> dict[str, dict]:
    """解析手册中的关键字定义块。"""
    kw: dict[str, dict] = {}
    for pno, text in enumerate(pages):
        lines = [l.rstrip() for l in text.splitlines()]
        for i, line in enumerate(lines):
            s = line.strip()
            if not KEYWORD_RE.match(s):
                continue
            # 定义块特征：其后 16 行内出现 TYPE:（放宽窗口以容纳较长描述）
            window = [l.strip() for l in lines[i + 1:i + 18]]
            if "TYPE:" not in window:
                continue
            # 描述行 = 紧随其后的第一个非空、非 TYPE: 行
            desc = ""
            for cand in window:
                if not cand or cand == "TYPE:":
                    if cand == "TYPE:":
                        break
                    continue
                if KEYWORD_RE.match(cand):
                    break
                desc = cand
                break
            ti = window.index("TYPE:")
            ktype = window[ti + 1] if ti + 1 < len(window) else ""
            default = ""
            options: list[str] = []
            if "DEFAULT:" in window:
                di = window.index("DEFAULT:")
                default = window[di + 1] if di + 1 < len(window) else ""
            if "OPTIONS:" in window:
                oi = window.index("OPTIONS:")
                for cand in window[oi + 1:]:
                    if cand in ("RECOMMENDATION:", "TYPE:", "DEFAULT:", "OPTIONS:"):
                        break
                    m = OPTION_LINE_RE.match(cand)
                    if not m:
                        continue
                    val, rest = m.group(1).strip(), m.group(2).strip()
                    if len(val) > 24:
                        continue
                    # 去掉 "(or 0)" 这类同义写法后再判形态
                    core = re.sub(r"\s*\(or[^)]*\)", "", val).strip()
                    looks_value = bool(re.match(r"^[A-Z0-9_*+-]+$", core))
                    # 单独成行、无空格的短 token 也接受（如 "User-defined"）；
                    # 说明句因带后继文字（rest 非空）而被排除
                    standalone = (rest == "" and " " not in val and len(val) <= 20)
                    if not (looks_value or standalone):
                        continue   # 形如 "Do not apply ..." / "Apply DH-DFT scheme" 的说明句
                    if val not in options:
                        options.append(val)
            entry = kw.setdefault(s, {
                "type": ktype, "default": default, "options": [],
                "description": desc, "pages": [], "source": ["manual"],
            })
            if pno + 1 not in entry["pages"]:
                entry["pages"].append(pno + 1)      # 1-based 手册页
            if len(desc) > len(entry.get("description", "")):
                entry["description"] = desc
            if ktype and not entry.get("type"):
                entry["type"] = ktype
            if default and not entry.get("default"):
                entry["default"] = default
            for o in options:
                if o not in entry["options"]:
                    entry["options"].append(o)
    return kw


def merge_sample_keywords(kw: dict[str, dict], samples: dict) -> dict[str, dict]:
    """把「官方样例实际使用过」的关键字并入注册表，并标注来源。

    为什么必须并入：本版手册并未为**所有**可用关键字提供定义块
    （实测 CC_FRZN_CORE / EOM_DAVIDSON_CONV / EMPIRICAL_DISPERSION 等
    在 6.1 手册中无独立定义，却在官方样例里被真实使用）。
    "随本版本一起发布的官方样例用过"本身就是该关键字在本版本可用的经验证据，
    故作为与手册并列的第二来源，并以 source 字段区分可信度：
      manual        —— 手册有定义（含 TYPE/DEFAULT/OPTIONS）
      samples       —— 手册无定义，但官方样例在用（无文档元数据）
    """
    for name, info in samples.items():
        for k in info["keywords"]:
            e = kw.setdefault(k, {
                "type": "", "default": "", "options": [],
                "description": "", "pages": [], "source": [],
            })
            if "samples" not in e["source"]:
                e["source"].append("samples")
            e.setdefault("samples_using", [])
            if len(e["samples_using"]) < 8 and name not in e["samples_using"]:
                e["samples_using"].append(name)
    return kw


def observed_values(samples: dict) -> dict[str, dict]:
    """统计官方样例中每个关键字实际出现的**取值**（模块选择所需的地面真值）。

    例：METHOD 的观测取值会包含 HF/B3LYP/CCSD(T)/... —— 全部来自随本版本
    发布的官方样例，因此是"本版本确实可跑"的取值，而非模型记忆。
    """
    obs: dict[str, dict] = {}
    for name, info in samples.items():
        for k, v in info.get("values", {}).items():
            d = obs.setdefault(k, {})
            for val in v:
                # 大小写归一：样例里 HF/hf、B3LYP/b3lyp 混用，统一用大写作键，
                # 并保留原始拼写作为 spellings，避免"同值多键"造成计数分裂。
                key = val.upper()
                rec = d.setdefault(key, {"count": 0, "examples": [], "spellings": []})
                rec["count"] += 1
                if val not in rec["spellings"]:
                    rec["spellings"].append(val)
                if len(rec["examples"]) < 3 and name not in rec["examples"]:
                    rec["examples"].append(name)
    # 只保留取值有意义的（排除纯开关类噪声过多的情形由调用方判断）
    return {k: dict(sorted(v.items(), key=lambda x: -x[1]["count"]))
            for k, v in sorted(obs.items())}


def parse_sections(pages: list[str]) -> set[str]:
    out: set[str] = set()
    for text in pages:
        for line in text.splitlines():
            m = SECTION_RE.match(line.strip())
            if m:
                out.add("$" + m.group(1).lower())
    return out


def build_chunks(pages: list[str], max_chars: int = 2600) -> list[dict]:
    """按页分块（过长的页按段再切），附章节面包屑。"""
    chunks: list[dict] = []
    chapter = ""
    for pno, text in enumerate(pages):
        first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
        if CHAPTER_RE.match(first_line):
            chapter = first_line
        body = text.strip()
        if not body:
            continue
        if len(body) <= max_chars:
            chunks.append({"page": pno + 1, "chapter": chapter, "text": body})
        else:
            paras = [p for p in body.split("\n\n") if p.strip()]
            buf = ""
            part = 0
            for para in paras:
                if len(buf) + len(para) > max_chars and buf:
                    part += 1
                    chunks.append({"page": pno + 1, "chapter": chapter,
                                   "part": part, "text": buf.strip()})
                    buf = ""
                buf += para + "\n\n"
            if buf.strip():
                part += 1
                chunks.append({"page": pno + 1, "chapter": chapter,
                               "part": part, "text": buf.strip()})
    return chunks


def index_samples(samples_dir: Path) -> dict:
    idx: dict[str, dict] = {}
    for f in sorted(samples_dir.rglob("*.in")):
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        secs, kws = [], []
        values: dict[str, list[str]] = {}
        in_rem = False
        for raw in txt.splitlines():
            line = raw.strip()
            if not line or line.startswith("!") or line.startswith("#"):
                continue
            m = SECTION_RE.match(line)
            if m:
                sec = "$" + m.group(1).lower()
                secs.append(sec)
                in_rem = sec == "$rem"
                continue
            if line.lower() == "$end":
                in_rem = False
                continue
            if in_rem:
                m2 = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*=?\s*(.*)$", line)
                if m2:
                    key = m2.group(1).upper()
                    kws.append(key)
                    rest = m2.group(2).strip()
                    # 去掉行尾注释后取首个取值 token
                    rest = re.split(r"\s+!", rest)[0].strip()
                    if rest:
                        val = rest.split()[0].strip().rstrip(",")
                        if val:
                            values.setdefault(key, [])
                            if val not in values[key]:
                                values[key].append(val)
        idx[str(f.relative_to(samples_dir))] = {
            "sections": sorted(set(secs)), "keywords": sorted(set(kws)),
            "values": values,
        }
    return idx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", required=True, help="版本精确的手册 PDF")
    ap.add_argument("--samples", required=True, help="官方样例输入目录")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--label", default="", help="版本标签，如 6.1.1")
    args = ap.parse_args()

    manual = Path(args.manual)
    doc = pymupdf.open(manual)
    pages = iter_pages(doc)
    title = doc.metadata.get("title", "")

    print(f"手册: {manual.name}  页数={doc.page_count}  title={title!r}")

    kw = parse_keywords(pages)
    secs_manual = parse_sections(pages)
    chunks = build_chunks(pages)
    samples = index_samples(Path(args.samples))

    manual_only = set(kw)
    kw = merge_sample_keywords(kw, samples)
    obs_vals = observed_values(samples)

    # 样例经验：实际被使用过的关键字与段
    used_kw: dict[str, int] = {}
    used_sec: dict[str, int] = {}
    for info in samples.values():
        for k in info["keywords"]:
            used_kw[k] = used_kw.get(k, 0) + 1
        for s in info["sections"]:
            used_sec[s] = used_sec.get(s, 0) + 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    both = [k for k, v in kw.items() if len(v["source"]) == 2]
    samples_only = sorted(set(used_kw) - manual_only)
    neither = sorted(set(used_kw) - set(kw))

    for name, obj in [
        ("keywords.json", kw),
        ("values_observed.json", obs_vals),
        ("sections.json", {
            "from_manual": sorted(secs_manual),
            "from_samples": sorted(used_sec),
            "union": sorted(set(secs_manual) | set(used_sec)),
        }),
        ("samples.json", samples),
        ("meta.json", {
            "label": args.label,
            "manual_title": title,
            "manual_pages": doc.page_count,
            "manual_file": manual.name,
            "keywords_manual": len(manual_only),
            "keywords_samples_used": len(used_kw),
            "keywords_merged_total": len(kw),
            "keywords_both_sources": len(both),
            "keywords_samples_only": len(samples_only),
            "keywords_samples_only_list": samples_only,
            "keywords_unrecognized": neither,
            "samples": len(samples),
            "chunks": len(chunks),
            "keyword_usage_top": sorted(used_kw.items(),
                                        key=lambda x: -x[1])[:40],
        }),
    ]:
        (out / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print(f"  写出 {name}  ({len(json.dumps(obj))//1024} KB)")

    with (out / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"  写出 chunks.jsonl ({len(chunks)} 块)")

    print(f"\n关键字: 手册定义 {len(manual_only)} 个；样例使用 {len(used_kw)} 个；"
          f"合并后 {len(kw)} 个（双源 {len(both)} / 仅样例 {len(samples_only)}）")
    if neither:
        print("  ⚠ 样例用过但仍未入册:", ", ".join(neither[:20]))
    print("样例实测取值索引:", len(obs_vals), "个关键字有观测取值")
    for k in ("METHOD", "BASIS", "EXCHANGE", "JOBTYPE", "PURECART"):
        if k in obs_vals:
            top = list(obs_vals[k].items())[:8]
            print(f"  {k}: " + ", ".join(f"{v}({c['count']})" for v, c in top))
    print("输出目录:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
