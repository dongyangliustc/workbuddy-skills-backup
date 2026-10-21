"""skill 生态归并 — 最终全量校验。

用法: python final_verify.py
"""
import os
import re

S = r"C:/Users/Administrator.DESKTOP-7RU274I/.workbuddy/skills"

name_re = re.compile(r"^name:\s*[\"']?([^\"'\s]+)", re.M)
fm_re = re.compile(r"^---\s*$(.*?)^---\s*$", re.M | re.S)

# 1. 收集注册名
registered = {}
for entry in sorted(os.listdir(S)):
    skill_md = os.path.join(S, entry, "SKILL.md")
    if not os.path.isfile(skill_md):
        continue
    with open(skill_md, encoding="utf-8-sig") as fh:
        text = fh.read()
    match = name_re.search(text)
    if match:
        registered[match.group(1)] = entry

print("=" * 62)
print("1. 技能注册名与目录一致性")
print("=" * 62)
print("注册技能数: %d" % len(registered))
mismatch = [(d, n) for n, d in registered.items() if n != d]
for d, n in mismatch:
    print("  目录 != 注册名: %s -> %s" % (d, n))
if not mismatch:
    print("  全部一致")

# 2. router 目标名校验
print()
print("=" * 62)
print("2. skill-router-* 路由目标可达性")
print("=" * 62)
routers = [d for d in sorted(os.listdir(S)) if d.startswith("skill-router-")]
ok = 0
bad = []
for router in routers:
    with open(os.path.join(S, router, "SKILL.md"), encoding="utf-8-sig") as fh:
        text = fh.read()
    for target in sorted(set(re.findall(r"`([A-Za-z0-9_-]+)`", text))):
        if target in registered:
            ok += 1
        else:
            bad.append((router, target))
print("路由表数量: %d" % len(routers))
print("有效目标: %d" % ok)
for router, target in bad:
    print("  非注册名: %s -> %s" % (router, target))

# 3. frontmatter 合规
print()
print("=" * 62)
print("3. frontmatter 合规性")
print("=" * 62)
problems = []
for entry in sorted(os.listdir(S)):
    skill_md = os.path.join(S, entry, "SKILL.md")
    if not os.path.isfile(skill_md):
        continue
    with open(skill_md, encoding="utf-8-sig") as fh:
        text = fh.read()
    if text.startswith("\ufeff"):
        problems.append((entry, "BOM 残留"))
    block = fm_re.search(text)
    if not block:
        problems.append((entry, "无 frontmatter"))
    elif not name_re.search(block.group(1)):
        problems.append((entry, "无 name 字段"))
for entry, issue in problems:
    print("  %s: %s" % (entry, issue))
if not problems:
    print("  全部合规")

# 4. manifest 覆盖率
print()
print("=" * 62)
print("4. nature-* manifest 覆盖率")
print("=" * 62)
nature_dirs = [d for d in sorted(os.listdir(S)) if d.startswith("nature-")]
missing = [d for d in nature_dirs if not os.path.isfile(os.path.join(S, d, "manifest.yaml"))]
print("nature-* 技能: %d, 缺 manifest: %d" % (len(nature_dirs), len(missing)))
for d in missing:
    print("  %s" % d)
if not missing:
    print("  全部具备")

# 5. 体积
print()
print("=" * 62)
print("5. 体积分布")
print("=" * 62)
sizes = []
for entry in sorted(os.listdir(S)):
    path = os.path.join(S, entry)
    if not os.path.isdir(path):
        continue
    total = sum(
        os.path.getsize(os.path.join(root, f))
        for root, _, files in os.walk(path)
        for f in files
        if os.path.exists(os.path.join(root, f))
    )
    sizes.append((total, entry))
total_mb = sum(s for s, _ in sizes) / 1048576
print("技能目录数: %d, 总体积: %.1f MB" % (len(sizes), total_mb))
for size, entry in sorted(sizes, reverse=True)[:5]:
    print("  %8.1f MB  %s" % (size / 1048576, entry))
