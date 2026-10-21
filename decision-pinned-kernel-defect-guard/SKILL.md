---
name: decision-pinned-kernel-defect-guard
description: >
  Install a safe, non-invasive regression guard for a KNOWN correctness defect in a
  shared numerical kernel when you must NOT change production numerics yet (re-baseline
  is gated on a human decision). Covers: (1) an INDEPENDENT adversarial reference that
  structurally cannot inherit the same bug; (2) an xfail(strict) decision pin that
  flips to a test FAILURE the moment the fix lands; (3) an AST blast-radius quantification
  before any flip. Use when you find a kernel computes wrong values for part of its domain
  (e.g. a silently-returned 0, a bad summation bound, a wrong default) but the fix would
  change published/production results. Keywords: silent zero, off-by-bound, summation
  range, regression lock, xfail strict, decision pin, blast radius, do-not-flip-yet.
agent_created: true
---

# Decision-pinned guard for a known kernel defect (pre-fix, zero-numeric-change)

## When to use

You have *confirmed* a correctness bug in a shared numerical function, but you may **not**
change it yet because the fix would alter production/baseline results whose re-baseline
is a human decision (or would interact with another unresolved fix). You still want to
make progress: lock the defect, prove the fix is cheap/safe, and make the fix impossible
to forget. This is the "守卫优先于翻转" (guard before flip) discipline.

Do NOT use this to declare the bug fixed. Do NOT use it to silently change behaviour.

## Three parts (all read-only wrt production numerics)

### 1. Independent adversarial reference
Write a reference implementation that is **structurally forbidden from reproducing the
bug**. Examples:
- Instead of trusting a closed-form index bound (`k_min`/`k_max`), scan a generous range
  and keep a term only if every factorial/exponent argument is legal.
- Instead of reusing the library's helper, enumerate the small domain and compute from
  the definition.
The test asserts `library == reference`. Independence is the whole point — a reference
that shares the buggy formula proves nothing.

### 2. xfail(strict) decision pin
Split the domain by the *activation condition* of the bug (e.g. `beta != 0`):
- **Immune sub-domain** → a normal test that MUST PASS today and forever.
- **Affected sub-domain** (+ a named minimal counterexample) → `@pytest.mark.xfail(strict=True, reason="H-B.. live: <one-line cause>")`.
  It fails today (xfail = expected); when the fix lands it **XPASSes**, and `strict=True`
  turns that into a hard test FAILURE → the fixer cannot miss the guard, and must remove
  the marker deliberately.
Record the measured mismatch count in the assertion message so drift is visible.

### 3. AST blast radius (before anyone flips a default)
Parse the live tree (skip `backup/`, `_remote_bundle/`, scratch `_run*`), find every call
site of the defective function, and statically classify each by whether the bug's
activation argument can be non-default (literal 0 => immune; literal nonzero => exposed;
else reachable). Report: total sites, exposed count, and the *only* live production
consumer(s). Conclusion should be "single-expression change / N-module spread".

## Steps

1. Read the defective function; pin the exact wrong expression and the correct form.
2. Write the AST blast-radius script → JSON evidence (`.json`).
3. Write the guard test file; run it: expect `1 passed, K xfailed`.
4. Run the *neighbouring* regression files to prove zero disturbance.
5. If the repo has a manifest/enumeration gate (counts of test files/items), run it too —
   a new test file must not break it.
6. Annotate the hazard ledger **in place** (no row reorder/delete; keep numbering
   continuous): idempotent anchored replacements, count-checked, EOL preserved, line
   count unchanged. Back up first.
7. ZERO library / config / production change → **no re-baseline**. State it explicitly.

## Pitfalls

- CJK strings in Python must not contain ASCII `"` (0x22) — it terminates the literal.
  Use 「」/“”. Always `ast.parse(src)` a generated `.py` before running.
- Do not mark the xfail as non-strict — that silently drops the alert when fixed.
- Do not put a real behavioural change behind a new on/off flag here; that recreates the
  silent-fallback hazard you are trying to avoid. This guard adds tests only.
- Ledger/hazard files: never `rm`/rewrite whole-file; anchor-replace and verify counts.
- Keep the guard in the normal test collection so a CI/gate run exercises it.
