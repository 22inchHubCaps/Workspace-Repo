#!/usr/bin/env python3
"""skill-frontmatter-doctor - find and repair SKILL.md frontmatter that YAML cannot parse.

THE BUG
    An unquoted YAML scalar ends at the first ": " (colon followed by a space). So a
    perfectly reasonable-looking skill description like

        description: Summarize a repo. USE WHEN: the user pastes a GitHub link.

    is not valid YAML. The parser hits "USE WHEN: " and tries to read a mapping where a
    string should be. The whole frontmatter block fails.

WHY IT MATTERS
    A skill whose frontmatter fails to parse can still sit on disk looking healthy, but its
    description never reaches the model. The skill becomes invisible: it can only be invoked
    by typing its exact name, never selected because it fits the situation. Usage stats for
    such a skill measure the bug, not the skill.

    This failure is quiet by construction, which is why it survives code review and why the
    natural instinct - "my files are fine, it must be the harness" - is usually wrong. Run
    the checker before you believe that.

THE FIX
    Rewrite the offending value as a YAML block scalar:

        description: >-
          Summarize a repo. USE WHEN: the user pastes a GitHub link.

    Inside a block scalar, colons are ordinary characters. The folded form (>-) joins the
    wrapped lines back into a single line with single spaces, so the text you get out is the
    text you put in. Block scalars are preferred over quoting because descriptions often
    already contain quote characters, which quoting would force you to escape.

USAGE
    skill_frontmatter_doctor.py check [PATH ...]      # report breakage, exit 1 if any
    skill_frontmatter_doctor.py fix   [PATH ...]      # show the repair as a diff, write nothing
    skill_frontmatter_doctor.py fix --write [PATH]    # apply the repair in place
    skill_frontmatter_doctor.py --selftest            # run the built-in test suite

    PATH may be a SKILL.md file or a directory (searched recursively). Default: the current
    directory.

DEPENDENCIES
    None. Standard library only. If PyYAML happens to be installed it is used for a second,
    stricter verification pass; without it the built-in checker is used for that pass instead.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
import textwrap

try:  # optional, only ever used to make verification stricter
    import yaml  # type: ignore
except Exception:  # pragma: no cover - absence is a supported configuration
    yaml = None

FENCE = "---"
WRAP_WIDTH = 96
INDENT = "  "

# A top-level mapping key: no leading whitespace, then key, colon, optional inline value.
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):(?:[ \t]+(.*))?[ \t]*$")

# What actually breaks an unquoted (plain) scalar.
COLON_SPACE_RE = re.compile(r":[ \t]")
TRAILING_COLON_RE = re.compile(r":$")
INLINE_COMMENT_RE = re.compile(r"[ \t]#")


class Problem:
    def __init__(self, key: str, line_no: int, reason: str, value: str):
        self.key = key
        self.line_no = line_no
        self.reason = reason
        self.value = value

    def __repr__(self) -> str:
        return f"{self.key} (line {self.line_no}): {self.reason}"


def split_frontmatter(text: str):
    """Return (pre, frontmatter_lines, rest) or None when there is no frontmatter block.

    'pre' is whatever precedes the opening fence (normally empty). Keeping it means we can
    rebuild the file byte-for-byte apart from the lines we deliberately change.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != FENCE:
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            return lines[0], lines[1:i], lines[i:]
    return None  # unterminated frontmatter


def _plain_scalar_problem(value: str):
    """Why, if at all, this inline value is unsafe as a plain YAML scalar."""
    v = value.strip()
    if not v:
        return None  # a parent key for a nested block or list
    if v[0] in "|>":
        return None  # already a block scalar
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return None  # author quoted it deliberately
    if v[0] in "[{&*!%@`":
        return None  # flow collection / YAML directive - out of scope, do not touch
    if COLON_SPACE_RE.search(v):
        return "contains ': ' (colon-space), which ends a plain scalar and starts a mapping"
    if TRAILING_COLON_RE.search(v):
        return "ends with ':', which YAML reads as a mapping key"
    if INLINE_COMMENT_RE.search(v):
        return "contains ' #', which YAML reads as the start of a comment (text would be silently truncated)"
    return None


def scan_frontmatter(fm_lines):
    """Find top-level keys whose inline value is an unsafe plain scalar.

    Returns (problems, entries) where entries maps key -> (start_idx, end_idx, joined_value).
    A value may span several lines: YAML lets a plain scalar continue on more-indented
    following lines, and long descriptions in the wild often do.
    """
    problems = []
    entries = {}
    i = 0
    while i < len(fm_lines):
        m = KEY_RE.match(fm_lines[i].rstrip("\n"))
        if not m:
            i += 1
            continue
        key, inline = m.group(1), (m.group(2) or "")
        start = i
        parts = [inline.strip()] if inline.strip() else []
        j = i + 1
        # Gather continuation lines: indented, and not themselves a top-level key.
        while j < len(fm_lines):
            nxt = fm_lines[j].rstrip("\n")
            if not nxt.strip():
                break
            if KEY_RE.match(nxt):
                break
            if not nxt[:1].isspace():
                break
            if inline.strip() == "":
                break  # this is a nested mapping, not a wrapped scalar - leave it alone
            parts.append(nxt.strip())
            j += 1
        value = " ".join(p for p in parts if p)
        reason = _plain_scalar_problem(inline) if inline.strip() else None
        if reason:
            problems.append(Problem(key, start + 2, reason, value))  # +2: 1-indexed, past fence
            entries[key] = (start, j, value)
        i = j if j > i else i + 1
    return problems, entries


def repair_frontmatter(fm_lines):
    """Return (new_lines, problems). Only unsafe plain scalars are rewritten."""
    problems, entries = scan_frontmatter(fm_lines)
    if not problems:
        return list(fm_lines), problems
    bad_keys = {p.key for p in problems}
    out = []
    i = 0
    while i < len(fm_lines):
        handled = False
        for key, (start, end, value) in entries.items():
            if key in bad_keys and i == start:
                out.append(f"{key}: >-\n")
                for wrapped in textwrap.wrap(value, width=WRAP_WIDTH) or [""]:
                    out.append(f"{INDENT}{wrapped}\n")
                i = end
                handled = True
                break
        if not handled:
            out.append(fm_lines[i])
            i += 1
    return out, problems


def _normalize(s: str) -> str:
    return " ".join(s.split())


def verify_repair(new_fm_lines, expected: dict):
    """Confirm the repaired frontmatter parses AND still says the same thing.

    Preserving meaning is the property that matters. A "fix" that parses but quietly drops
    or mangles half a description is worse than the bug, because it looks like success.
    """
    residual, _ = scan_frontmatter(new_fm_lines)
    if residual:
        return False, f"still unparseable after repair: {residual[0]}"
    if yaml is not None:
        try:
            data = yaml.safe_load("".join(new_fm_lines))
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"PyYAML still rejects it: {str(exc).splitlines()[0]}"
        if not isinstance(data, dict):
            return False, "frontmatter did not parse to a mapping"
        for key, original in expected.items():
            got = data.get(key)
            if got is None:
                return False, f"key {key!r} vanished during repair"
            if _normalize(str(got)) != _normalize(original):
                return False, f"text of {key!r} changed during repair"
    return True, "ok"


def iter_skill_files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "__pycache__"}]
                for f in sorted(files):
                    if f == "SKILL.md":
                        yield os.path.join(root, f)


def process(path, write=False, show_diff=False):
    """Returns (state, detail). state in {ok, broken, fixed, wouldfix, nofrontmatter, error}."""
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        return "error", str(exc)
    split = split_frontmatter(text)
    if split is None:
        return "nofrontmatter", "no --- frontmatter block (or it is unterminated)"
    head, fm_lines, rest = split
    problems, entries = scan_frontmatter(fm_lines)
    if not problems:
        return "ok", ""
    if not (write or show_diff):
        return "broken", "; ".join(str(p) for p in problems)

    new_fm, _ = repair_frontmatter(fm_lines)
    expected = {p.key: entries[p.key][2] for p in problems}
    good, why = verify_repair(new_fm, expected)
    if not good:
        return "error", f"repair rejected by verification, file untouched: {why}"

    new_text = head + "".join(new_fm) + "".join(rest)
    if show_diff and not write:
        diff = difflib.unified_diff(
            text.splitlines(keepends=True), new_text.splitlines(keepends=True),
            fromfile=path, tofile=path + " (repaired)",
        )
        return "wouldfix", "".join(diff)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    return "fixed", "; ".join(str(p) for p in problems)


def run(paths, write=False, show_diff=False):
    files = list(iter_skill_files(paths or ["."]))
    counts = {}
    broken_files = []
    for path in files:
        state, detail = process(path, write=write, show_diff=show_diff)
        counts[state] = counts.get(state, 0) + 1
        if state in {"broken", "fixed", "wouldfix", "error"}:
            broken_files.append(path)
            label = {"broken": "BROKEN", "fixed": "FIXED ", "wouldfix": "REPAIR", "error": "ERROR "}[state]
            if state == "wouldfix":
                print(f"{label} {path}\n{detail}")
            else:
                print(f"{label} {path}\n       {detail}")
    total = len(files)
    print(f"\nscanned {total} SKILL.md file(s): "
          f"{counts.get('ok', 0)} ok, "
          f"{counts.get('broken', 0) + counts.get('wouldfix', 0)} broken, "
          f"{counts.get('fixed', 0)} fixed, "
          f"{counts.get('error', 0)} error, "
          f"{counts.get('nofrontmatter', 0)} without frontmatter")
    if yaml is None:
        print("note: PyYAML not installed - used the built-in checker for verification "
              "(install PyYAML for a stricter second pass)")
    return 1 if (counts.get("broken") or counts.get("wouldfix") or counts.get("error")) else 0


# --------------------------------------------------------------------------- selftest

BROKEN_COLON = """---
name: repo-summarizer
description: Summarize a repository for a newcomer. USE WHEN: someone pastes a GitHub link, or asks what a project does.
---

# body preserved
"""

BROKEN_NESTED = """---
name: changelog-writer
description: Draft a changelog from commits. Note: it never invents entries.
metadata:
  type: workflow
  author: example
---

# body
"""

BROKEN_TRAILING = """---
name: trailing
description: The rule is simple:
---
"""

BROKEN_COMMENT = """---
name: commenty
description: Tag issues by area #frontend and route them onward.
---
"""

OK_QUOTED = """---
name: already-quoted
description: "Handles colons safely: because the author quoted it."
---
"""

OK_BLOCK = """---
name: already-block
description: >-
  Handles colons safely: because this is a block scalar.
---
"""

OK_PLAIN = """---
name: plain
description: Nothing unusual here at all.
---
"""

NO_FM = """# just a heading, no frontmatter
"""

CASES = [
    ("broken_colon.md", BROKEN_COLON, "broken"),
    ("broken_nested.md", BROKEN_NESTED, "broken"),
    ("broken_trailing.md", BROKEN_TRAILING, "broken"),
    ("broken_comment.md", BROKEN_COMMENT, "broken"),
    ("ok_quoted.md", OK_QUOTED, "ok"),
    ("ok_block.md", OK_BLOCK, "ok"),
    ("ok_plain.md", OK_PLAIN, "ok"),
    ("no_frontmatter.md", NO_FM, "nofrontmatter"),
]


def selftest():
    import shutil
    import tempfile

    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
        print(f"  {'PASS' if cond else 'FAIL'}  {msg}")

    tmp = tempfile.mkdtemp(prefix="sfd-selftest-")
    try:
        print("1. detection - the checker must FAIL on broken input before it is trusted")
        expect_broken = 0
        for fname, content, expected in CASES:
            d = os.path.join(tmp, fname[:-3])
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "SKILL.md")
            open(path, "w", encoding="utf-8").write(content)
            state, _ = process(path)
            check(state == expected, f"{fname}: expected {expected}, got {state}")
            if expected == "broken":
                expect_broken += 1
        check(expect_broken == 4, f"fixture set contains {expect_broken} broken files (expected 4)")

        print("2. repair - every broken file becomes parseable")
        for fname, content, expected in CASES:
            if expected != "broken":
                continue
            path = os.path.join(tmp, fname[:-3], "SKILL.md")
            state, detail = process(path, write=True)
            check(state == "fixed", f"{fname}: repair reported {state} ({detail})")
            state2, _ = process(path)
            check(state2 == "ok", f"{fname}: still not ok after repair ({state2})")

        print("3. meaning preserved - the text must survive the repair unchanged")
        path = os.path.join(tmp, "broken_colon", "SKILL.md")
        fixed = open(path, encoding="utf-8").read()
        original_desc = ("Summarize a repository for a newcomer. USE WHEN: someone pastes a "
                         "GitHub link, or asks what a project does.")
        if yaml is not None:
            data = yaml.safe_load(fixed.split(FENCE)[1])
            check(_normalize(data["description"]) == _normalize(original_desc),
                  "description round-trips through the repair byte-for-byte (modulo wrapping)")
            check(data["name"] == "repo-summarizer", "sibling key 'name' untouched")
        else:
            check(_normalize(original_desc) in _normalize(fixed), "description text still present")
        check("# body preserved" in fixed, "markdown body after the frontmatter is untouched")

        nested = open(os.path.join(tmp, "broken_nested", "SKILL.md"), encoding="utf-8").read()
        check("type: workflow" in nested and "author: example" in nested,
              "nested mapping under 'metadata' left alone")

        print("4. idempotence - repairing an already-repaired file changes nothing")
        before = open(path, encoding="utf-8").read()
        process(path, write=True)
        check(open(path, encoding="utf-8").read() == before, "second run is a no-op")

        print("5. safety - a file the checker calls ok is never rewritten")
        okpath = os.path.join(tmp, "ok_quoted", "SKILL.md")
        before_ok = open(okpath, encoding="utf-8").read()
        process(okpath, write=True)
        check(open(okpath, encoding="utf-8").read() == before_ok, "clean file left byte-identical")

        print("6. mutation - break the detector on purpose and confirm the suite notices")
        global COLON_SPACE_RE
        saved = COLON_SPACE_RE
        COLON_SPACE_RE = re.compile(r"(?!x)x")  # matches nothing
        mut = os.path.join(tmp, "mutant")
        os.makedirs(mut, exist_ok=True)
        mpath = os.path.join(mut, "SKILL.md")
        open(mpath, "w", encoding="utf-8").write(BROKEN_COLON)
        mutant_state, _ = process(mpath)
        COLON_SPACE_RE = saved
        check(mutant_state == "ok",
              "with detection disabled the broken file reads as ok - so a green run means "
              "detection is actually firing, not that nothing was tested")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"SELFTEST FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELFTEST PASSED")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="skill_frontmatter_doctor.py",
        description="Find and repair SKILL.md frontmatter that YAML cannot parse.")
    ap.add_argument("command", nargs="?", default="check", choices=["check", "fix"])
    ap.add_argument("paths", nargs="*", help="SKILL.md files or directories (default: .)")
    ap.add_argument("--write", action="store_true",
                    help="with 'fix', apply repairs in place (default is a diff, writing nothing)")
    ap.add_argument("--selftest", action="store_true", help="run the built-in test suite and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.command == "check":
        return run(args.paths, write=False, show_diff=False)
    return run(args.paths, write=args.write, show_diff=not args.write)


if __name__ == "__main__":
    sys.exit(main())
