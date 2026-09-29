---
name: git-commit-skill
description: 'Automatically write and execute high-quality git commit messages. Use whenever the user asks to commit changes, create a git commit, mentions "/commit", asks for help writing or improving commit messages, or complains about a messy commit history — even if they do not name a skill. Analyzes the diff to determine type and scope, generates Conventional Commits messages that follow the seven rules of a great commit message, stages files logically, and executes the commit safely. Supports type/scope/description overrides.'
license: MIT
allowed-tools: Bash
---

# Git Commit

## Overview

Write standardized, semantic git commit messages automatically — and execute them. Read the actual diff, pick the right type, scope, and wording, stage files logically, then commit. Two foundations:

- **Conventional Commits** — the machine-friendly `<type>[scope]: <description>` format
- **The Seven Rules** — the human-friendly rules that keep a log readable

A diff shows *what* changed; only the commit message can explain *why*. A well-kept log is what makes `git blame`, `revert`, `rebase`, and `git log` useful months later — give the message the same care as the code.

## The Seven Rules

Apply all seven to every message:

1. Separate the subject from the body with a blank line
2. Limit the subject line to 50 characters (72 is the hard limit)
3. Capitalize the subject's description (the type prefix stays lowercase)
4. Do not end the subject line with a period
5. Use the imperative mood in the subject line
6. Wrap the body at 72 characters
7. Use the body to explain *what* and *why*, not *how*

The rule 5 self-check: the subject should complete the sentence "If applied, this commit will ___":

- ✓ "add JWT authentication" → `feat: Add JWT authentication`
- ✗ "added JWT authentication" (past tense), "more fixes for broken stuff" (contents, not intent)

Rule 3 resolves the clash between the two foundations: the type/scope prefix is lowercase because the spec requires it (`feat:`, `fix(auth):`); the description after the colon is capitalized and imperative.

See `references/seven-rules.md` for the rationale behind each rule and worked examples if needed.

## Conventional Commit Format

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

| Type       | Purpose                        |
| ---------- | ------------------------------ |
| `feat`     | New feature                    |
| `fix`      | Bug fix                        |
| `docs`     | Documentation only             |
| `style`    | Formatting/style (no logic)    |
| `refactor` | Code refactor (no feature/fix) |
| `perf`     | Performance improvement        |
| `test`     | Add/update tests               |
| `build`    | Build system/dependencies      |
| `ci`       | CI/config changes              |
| `chore`    | Maintenance/misc               |
| `revert`   | Revert a commit                |

`docs`, `style`, and `chore` signal "no logic changed"; picking the right type lets reviewers filter what deserves attention.

## Breaking Changes

```
# Exclamation mark after type/scope
feat!: Remove deprecated endpoint
```
```
# BREAKING CHANGE footer
feat: Allow config to extend other configs

BREAKING CHANGE: `extends` key behavior changed
```

## Workflow

### 1. Analyze the Diff

```bash
git diff --staged      # if files are staged
git diff               # if nothing is staged, use the working tree
git status --porcelain
git log --oneline -20  # check the repo's existing message style
```

If the repo has one dominant message style (lowercase descriptions, ticket-ID footers, emoji prefixes), match it — consistency inside a repo beats the rules. If the history is mixed, apply this skill's rules.

### 2. Stage Files (if needed)

Stage only when nothing is staged or the user wants different grouping. Prefer logical grouping so each commit is one change:

```bash
git add path/to/file1 path/to/file2
git add *.test.*
git add -p   # interactive, when splitting hunks helps
```

Never stage or commit secrets (.env, credentials.json, private keys). Halt and tell the user if any change looks like a secret or an accidental artifact.

### 3. Generate the Message

Determine:

- **Type**: what kind of change this is (table above)
- **Scope**: the area/module affected, only when it adds information
- **Description**: imperative, present-tense summary. The whole subject line — `<type>[scope]: <description>` — should stay ≤50 characters (72 hard limit)
- **Body**: only when the change needs context — the problem before, why this approach, side effects or non-obvious consequences. Never restate the diff. Wrap at 72 characters
- **Footer**: issue references — `Closes #123`, `Refs #456`

A one-line message is fine for trivial changes (a typo fix): the diff explains the rest. Add a body when a future reader would otherwise have to reverse-engineer the intent.

### 4. Execute the Commit

```bash
# Single line
git commit -m "<type>[scope]: <description>"

# With body/footer
git commit -m "$(cat <<'EOF'
<type>[scope]: <description>

<optional body, wrapped at 72 chars>

<optional footer>
EOF
)"
```

## Edge Cases

- **Merge commits**: keep git's default message ("Merge branch ...") — do not rewrite it
- **Reverts**: follow git's own format — subject `Revert "original subject"` and the standard body (`This reverts commit <sha>`)
- **Nothing to commit**: report that the tree is clean; never fabricate a commit
- **User override**: if the user gives an explicit type/scope/description, use their words — the rules guide defaults, not explicit instructions

## Git Safety Protocol

- Never update git config
- Never run destructive commands (--force, hard reset) without an explicit request
- Never skip hooks (--no-verify) unless the user asks
- Never force push to main/master
- If a commit fails due to hooks, fix the issue and create a NEW commit (do not amend)
