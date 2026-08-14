# Git hooks

## Enabling

Hooks are not versioned by Git — `.git/hooks/` is per-clone and never cloned.
This directory is tracked instead, and each clone opts in with one line:

```sh
git config core.hooksPath .githooks
```

Nothing runs until you do that. Verify with `git config core.hooksPath`.

Note that `core.hooksPath` and the `pre-commit` framework are mutually
exclusive — `pre-commit install` refuses to run when `core.hooksPath` is set.
If this repo later adopts `pre-commit` for linting and formatting, that
decision needs to be made deliberately.

## `pre-push`

Blocks internal-only commits from reaching a public remote. Three checks:

| # | Check | Catches | Active |
|---|-------|---------|--------|
| 1 | Ref name | Pushing an `int/*` branch to a public remote | Immediately |
| 2 | Lineage | Internal commits, including edits to *shared* files | After the `int/*` rename |
| 3 | Content | Internal content hand-copied onto a public branch | Once `patterns.txt` exists |

Check 2 is the important one — it's the only check that sees a modification to
a file that also exists publicly, where neither the path nor the surrounding
content looks unusual.

The hook exits silently and immediately in a clone with no `internal` remote,
so public contributors are unaffected.

### Pattern list

Check 3 reads two sources, neither of which can reach a public branch:

1. `$GIT_DIR/patterns.local` — untracked by construction, per-clone, machine-local.
2. `.githooks/patterns.txt` **on `int/main` only** — read via
   `git show refs/remotes/internal/int/main:.githooks/patterns.txt`, so it
   resolves regardless of which branch is checked out while existing in no
   commit that goes public.

One line per POSIX ERE pattern; `#` comments and blank lines ignored. Refresh
with `git fetch internal`.

Failures report commit SHAs and a pattern *index* — never the matched text or
the pattern itself, since that output ends up in scrollback and pasted logs.

### Limits

- `git push --no-verify` bypasses everything.
- A fresh clone is unguarded until someone runs the config line.
- Check 2 only sees commits on `int/*`. Internal work on any other branch is
  invisible to it.
- `git log -G` skips merge diffs, so content arriving via a merge from `int/*`
  is invisible to check 3 — check 2 catches it on lineage instead.

This is fast local feedback, not an enforcement boundary. The boundaries are
separate clones (public remotes and `internal` never configured together) and
server-side rules on the public repo.
