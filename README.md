# skill-frontmatter-doctor

Finds and repairs `SKILL.md` frontmatter that YAML cannot parse — the quiet failure that makes an
agent skill invisible while it still looks perfectly healthy on disk.

No dependencies. Python 3.8+. Standard library only.

## The bug

An unquoted YAML scalar ends at the first `": "` — a colon followed by a space. So this
entirely reasonable-looking skill description is not valid YAML:

```yaml
---
name: repo-summarizer
description: Summarize a repository. USE WHEN: someone pastes a GitHub link.
---
```

The parser reaches `USE WHEN: ` and tries to read a mapping where a string should be. The whole
frontmatter block fails.

## Why it's worth a tool

The failure is silent. The file still exists, still has a description, still opens fine in an
editor. But the harness can't parse its frontmatter, so **the description never reaches the model** —
and a skill with no description can only be invoked by typing its exact name. It can never be
selected because it fits the situation.

That produces a nasty second-order effect: usage statistics for such a skill measure the bug, not
the skill. If you're deciding which skills to prune based on how often they fire, broken frontmatter
looks identical to "nobody wants this."

The natural instinct when descriptions go missing — *my files are fine, it must be the harness* — is
worth testing before you believe it. In one real 54-skill library, **18 files (33%) were affected**,
and nearly all of them shared a single house style: a `WORD:` marker in the middle of the
description. The convention written to make those skills easier to trigger was the thing stopping
them from being seen.

## Install

None. Copy the single file, or:

```bash
curl -O https://raw.githubusercontent.com/22inchHubCaps/Workspace-Repo/main/skill_frontmatter_doctor.py
```

## Use

```bash
# report what's broken; exits 1 if anything is
python3 skill_frontmatter_doctor.py check ~/.claude/skills

# preview the repair as a unified diff — writes nothing
python3 skill_frontmatter_doctor.py fix ~/.claude/skills

# apply it
python3 skill_frontmatter_doctor.py fix --write ~/.claude/skills

# run the built-in test suite
python3 skill_frontmatter_doctor.py --selftest
```

`check` is the default command, and the current directory is the default path. Directories are
searched recursively for files named `SKILL.md`.

## What it detects

| Pattern | Why YAML rejects it |
|---|---|
| `description: Do X. WHEN: a thing happens` | `": "` ends the plain scalar and starts a mapping |
| `description: The rule is simple:` | a trailing `:` reads as a mapping key |
| `description: Route #frontend issues` | `" #"` starts a comment — the rest is silently dropped |

## What it deliberately leaves alone

- values the author already quoted (`description: "colons: fine"`)
- values already written as block scalars (`>-`, `|`)
- nested mappings and lists under a key
- flow collections (`[...]`, `{...}`) and YAML directives — out of scope, too easy to corrupt
- everything after the closing `---`; the markdown body is never touched

## The repair

Offending values are rewritten as folded block scalars:

```yaml
description: >-
  Summarize a repository. USE WHEN: someone pastes a GitHub link.
```

Inside a block scalar, colons are ordinary characters. The folded form (`>-`) rejoins wrapped lines
with single spaces, so the text you get out is the text you put in.

Block scalars are used rather than quoting because descriptions frequently already contain quote
characters, and quoting would force escaping them.

## Safety

A repair is verified **before** the file is written, and the file is left untouched if verification
fails. Verification checks two things:

1. the repaired frontmatter parses, and
2. **every repaired value still says exactly what it said before** (whitespace-normalized).

The second check is the one that matters. A "fix" that parses but quietly truncates half a
description is worse than the original bug, because it looks like success.

If PyYAML happens to be installed it's used for a stricter second pass. It is not required.

## Testing

`--selftest` builds a temporary fixture set covering each broken pattern plus the cases that must be
left alone, then checks detection, repair, meaning preservation, idempotence, and safety.

The last step is a **mutation check**: it disables the colon-space detector on purpose and confirms
the suite then reads a known-broken file as clean. That's there because a test suite that passes
while testing nothing is the failure mode this whole tool exists to catch — a green run should mean
detection actually fired, not that nothing was examined.

## License

MIT.
