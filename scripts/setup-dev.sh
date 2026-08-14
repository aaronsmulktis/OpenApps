#!/usr/bin/env bash
#
# setup-dev.sh — wire up the local git config this repo expects.
#
#   ./scripts/setup-dev.sh           install
#   ./scripts/setup-dev.sh --check   report status, exit 1 if a guard is missing
#
# Safe to re-run. Touches only this clone's git config; never the global config
# and never a remote.
#
# If you have no `internal` remote this script is close to a no-op — the push
# guard is inert in a public-only clone, because such a clone cannot contain
# internal commits in the first place.

set -u

cd "$(git rev-parse --show-toplevel)" || exit 1

MODE=install
case "${1:-}" in
    --check) MODE=check ;;
    "")      ;;
    *)       echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

has_internal=0
git remote get-url internal >/dev/null 2>&1 && has_internal=1

status=0

# ---------------------------------------------------------------------------
# 1. Push guard
#
# core.hooksPath is per-clone and cannot be set by cloning — Git refuses to let
# a repo install its own hooks, since that would make `git clone` arbitrary code
# execution. This line is the opt-in.
# ---------------------------------------------------------------------------
echo
echo "push guard"
if [ "$MODE" = install ]; then
    git config core.hooksPath .githooks
    # core.hooksPath activates every hook in the directory, so commit-msg
    # (the int/* trailer stamp) comes along with pre-push.
    chmod +x .githooks/pre-push .githooks/commit-msg 2>/dev/null
fi

current_hooks_path=$(git config core.hooksPath || true)
if [ "$current_hooks_path" = ".githooks" ]; then
    ok "core.hooksPath = .githooks"
elif [ -n "$current_hooks_path" ]; then
    bad "core.hooksPath = $current_hooks_path (expected .githooks)"; status=1
elif [ -x .git/hooks/pre-push ]; then
    ok "core.hooksPath unset, but .git/hooks/pre-push exists (template shim)"
else
    bad "no push guard active — run $0"; status=1
fi

if [ -x .githooks/pre-push ]; then
    ok ".githooks/pre-push is executable"
else
    bad ".githooks/pre-push missing or not executable"; status=1
fi

# ---------------------------------------------------------------------------
# 2. Merge driver for permanently-divergent files
#
# Files that exist at the same path on both public and int/* branches with
# deliberately different content (cluster configs and the like) are marked
# `merge=keep-internal` in .gitattributes on int/* . The driver below resolves
# those in favour of the internal side so that merging public -> internal stops
# conflicting on every single sync.
#
# A merge driver named in .gitattributes does NOTHING without this local config
# — Git silently falls back to a normal merge. That failure is quiet, which is
# why it belongs in a setup script rather than in a README.
#
# THE TRADE: upstream edits to those paths are dropped on the internal side
# without a conflict marker. That is intended, but silent drops are how config
# rots. .githooks/keep-internal resolves the same way while printing a drift
# notice on the merge that caused it.
# ---------------------------------------------------------------------------
DRIVER='.githooks/keep-internal %O %A %B %P'

echo
echo "merge driver (permanently-divergent paths)"
if [ "$MODE" = install ]; then
    git config merge.keep-internal.name "keep the internal version of a divergent file"
    git config merge.keep-internal.driver "$DRIVER"
    chmod +x .githooks/keep-internal 2>/dev/null
fi

configured=$(git config merge.keep-internal.driver || true)
if [ "$configured" = "$DRIVER" ]; then
    ok "merge.keep-internal configured (with drift notice)"
elif [ "$configured" = "true" ]; then
    warn "merge.keep-internal is the old silent driver — re-run $0 to upgrade"
elif [ "$has_internal" = 1 ]; then
    bad "merge.keep-internal missing — public->internal merges will conflict"; status=1
else
    warn "merge.keep-internal not configured (not needed without an internal remote)"
fi

if [ -x .githooks/keep-internal ]; then
    ok ".githooks/keep-internal is executable"
elif [ "$has_internal" = 1 ]; then
    bad ".githooks/keep-internal missing or not executable"; status=1
fi

# ---------------------------------------------------------------------------
# 3. Clone shape
# ---------------------------------------------------------------------------
echo
echo "clone shape"
if [ "$has_internal" = 1 ]; then
    warn "hybrid clone: public and internal remotes both configured"
    echo "      the push guard is the only thing standing between an int/*"
    echo "      commit and a public remote. Keep internal work on int/* ."
    if git rev-parse --verify --quiet refs/remotes/internal/int/main >/dev/null; then
        ok "int/main topology present"
    else
        warn "no int/* branches yet — lineage and content checks are inactive"
    fi
else
    ok "public-only clone — nothing internal can leak from here"
fi

# ---------------------------------------------------------------------------
# 4. Fresh-clone coverage (informational; global config, not touched here)
# ---------------------------------------------------------------------------
echo
echo "fresh-clone coverage"
if [ -n "$(git config --global init.templateDir || true)" ]; then
    ok "init.templateDir = $(git config --global init.templateDir)"
else
    warn "init.templateDir unset — future clones need $0 run by hand"
    echo "      to cover them automatically (one time, per machine):"
    echo "        git config --global init.templateDir ~/.git-templates"
fi

echo
if [ "$MODE" = check ] && [ "$status" -ne 0 ]; then
    printf '\033[31mchecks failed\033[0m\n\n'
    exit 1
fi
[ "$MODE" = install ] && printf 'done — verify any time with: %s --check\n\n' "$0"
exit 0
