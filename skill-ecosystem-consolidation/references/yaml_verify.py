"""严格校验：用 YAML 解析器验证所有 SKILL.md frontmatter 与 manifest.yaml。

这也是对 final_verify.py 的独立补充——后者用正则，本脚本用真正的 YAML 解析器。
"""
import os
import yaml

S = r"C:/Users/Administrator.DESKTOP-7RU274I/.workbuddy/skills"

fm_ok, fm_bad = [], []
mf_ok, mf_bad, mf_missing = [], [], []

for entry in sorted(os.listdir(S)):
    path = os.path.join(S, entry)
    if not os.path.isdir(path):
        continue
    skill_md = os.path.join(path, "SKILL.md")
    if os.path.isfile(skill_md):
        with open(skill_md, encoding="utf-8-sig") as fh:
            text = fh.read()
        parts = text.split("---")
        if len(parts) >= 3:
            try:
                data = yaml.safe_load(parts[1])
                name = data.get("name") if isinstance(data, dict) else None
                if name:
                    fm_ok.append((entry, name))
                else:
                    fm_bad.append((entry, "frontmatter 无 name"))
            except Exception as exc:
                fm_bad.append((entry, "YAML 解析失败: %s" % exc))
        else:
            fm_bad.append((entry, "无 --- 分隔的 frontmatter"))

    mf = os.path.join(path, "manifest.yaml")
    if os.path.isfile(mf):
        try:
            with open(mf, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if isinstance(data, dict):
                mf_ok.append((entry, data.get("name")))
            else:
                mf_bad.append((entry, "根节点不是映射"))
        except Exception as exc:
            mf_bad.append((entry, "YAML 解析失败: %s" % exc))

print("=" * 60)
print("A. frontmatter YAML 解析")
print("=" * 60)
print("成功: %d" % len(fm_ok))
for entry, issue in fm_bad:
    print("  失败 %s: %s" % (entry, issue))
if not fm_bad:
    print("  全部通过")

print()
print("=" * 60)
print("B. manifest.yaml YAML 解析")
print("=" * 60)
print("成功: %d" % len(mf_ok))
for entry, issue in mf_bad:
    print("  失败 %s: %s" % (entry, issue))
if not mf_bad:
    print("  全部通过")

# manifest 的 name 是否与目录/frontmatter 一致
print()
print("=" * 60)
print("C. manifest.name 与目录名一致性")
print("=" * 60)
mm = [(e, n) for e, n in mf_ok if n and n != e]
for e, n in mm:
    print("  不一致: %s -> %s" % (e, n))
if not mm:
    print("  全部一致")
