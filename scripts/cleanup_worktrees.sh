#!/usr/bin/env bash
# Tear down the parallel-track worktrees under $PROLI_WT_ROOT once their work
# has landed. Implements the teardown line in CLAUDE.md
# ("git worktree remove <dir> && git worktree prune") as a sweep, so finishing a
# batch is one command instead of one command per track.
#
#   scripts/cleanup_worktrees.sh              # remove every merged worktree
#   scripts/cleanup_worktrees.sh --dry-run    # show what it would remove
#   scripts/cleanup_worktrees.sh pro-123      # only this one
#   scripts/cleanup_worktrees.sh --force pro-123   # even if unmerged/dirty
#
# A worktree is removed only when ALL of these hold:
#   * its PR is MERGED (or its branch is already an ancestor of origin/dev)
#   * it has no uncommitted changes
#   * it has no commits that are not on the remote
#   * it is not the directory this script is being run from
# --force skips the first three. It never skips the fourth: on Windows a
# process's cwd is locked, so a session cannot delete the folder it lives in —
# that is what leaves the empty, undeletable directories behind.

set -uo pipefail

WT_ROOT="${PROLI_WT_ROOT:-D:/Projects/proli-wt}"
DRY_RUN=0
FORCE=0
TARGETS=()

for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    --force|-f)   FORCE=1 ;;
    -h|--help)    sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)           echo "unknown flag: $arg" >&2; exit 2 ;;
    *)            TARGETS+=("$(basename "$arg")") ;;
  esac
done

REPO="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "not inside a git repository — run this from the main proli checkout" >&2
  exit 1
}
HERE="$(pwd -P)"

git -C "$REPO" fetch origin --prune --quiet 2>/dev/null

have_gh=0
command -v gh >/dev/null 2>&1 && have_gh=1

removed=0 skipped=0

# `git worktree list --porcelain` is the source of truth, not `ls $WT_ROOT`:
# a directory git no longer tracks is leftover, not a worktree.
while read -r _ dir; do
  [ -n "${dir:-}" ] || continue
  case "$dir" in "$REPO"|"$REPO"/) continue ;; esac

  name="$(basename "$dir")"
  if [ ${#TARGETS[@]} -gt 0 ]; then
    match=0
    for t in "${TARGETS[@]}"; do [ "$t" = "$name" ] && match=1; done
    [ $match -eq 1 ] || continue
  fi

  # Refuse to saw off the branch we are sitting on.
  real="$(cd "$dir" 2>/dev/null && pwd -P)" || real="$dir"
  case "$HERE" in
    "$real"|"$real"/*)
      echo "SKIP  $name — you are inside it; run this from $REPO"
      skipped=$((skipped+1)); continue ;;
  esac

  branch="$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  pr_merged=0
  state=""

  if [ $FORCE -eq 0 ]; then
    if [ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ]; then
      echo "SKIP  $name — uncommitted changes"
      skipped=$((skipped+1)); continue
    fi

    merged=0
    if [ $have_gh -eq 1 ] && [ -n "$branch" ]; then
      state="$(gh pr list --repo "$(git -C "$REPO" remote get-url origin)" \
                 --head "$branch" --state all --limit 1 \
                 --json state -q '.[0].state' 2>/dev/null)"
      if [ "$state" = "MERGED" ]; then merged=1; pr_merged=1; fi
    fi
    # No gh, or no PR found: fall back to "is this branch already in dev?".
    if [ $merged -eq 0 ] && git -C "$REPO" merge-base --is-ancestor \
         "$(git -C "$dir" rev-parse HEAD)" origin/dev 2>/dev/null; then
      merged=1
    fi
    if [ $merged -eq 0 ]; then
      echo "SKIP  $name — $branch is not merged${state:+ (PR is $state)}"
      skipped=$((skipped+1)); continue
    fi

    # Merged, but check nothing local is only here.
    if [ -n "$branch" ] && \
       [ -n "$(git -C "$dir" log --oneline "@{upstream}..HEAD" 2>/dev/null)" ]; then
      echo "SKIP  $name — has commits not pushed to its upstream"
      skipped=$((skipped+1)); continue
    fi
  fi

  if [ $DRY_RUN -eq 1 ]; then
    echo "WOULD REMOVE  $name  ($branch)"
    continue
  fi

  if git -C "$REPO" worktree remove ${FORCE:+--force} "$dir" 2>/dev/null; then
    echo "removed  $name"
    removed=$((removed+1))
  else
    # git deletes the contents and the admin entry but leaves the directory
    # itself when another process holds it open. Say so plainly rather than
    # reporting a clean removal.
    git -C "$REPO" worktree prune 2>/dev/null
    if [ -d "$dir" ] && ! rmdir "$dir" 2>/dev/null; then
      echo "PARTIAL  $name — git entry gone, folder still locked by another"
      echo "         process (a shell or editor sitting in it). Close it, then:"
      echo "         rmdir \"$dir\""
    else
      echo "removed  $name"
    fi
    removed=$((removed+1))
  fi

  # The branch is merged; the local ref is now just clutter. `-d` refuses a
  # squash merge (the commit is not an ancestor of dev, only its contents are),
  # which is exactly how this repo merges — so use `-D` when, and only when, the
  # PR itself reported MERGED. Without that proof, stay with `-d` and let git
  # refuse.
  if [ $FORCE -eq 0 ] && [ -n "$branch" ]; then
    if [ "$pr_merged" -eq 1 ]; then
      git -C "$REPO" branch -D "$branch" >/dev/null 2>&1 &&         echo "         deleted local branch $branch (squash-merged as $state)"
    else
      git -C "$REPO" branch -d "$branch" >/dev/null 2>&1 &&         echo "         deleted local branch $branch"
    fi
  fi
done < <(git -C "$REPO" worktree list --porcelain | grep '^worktree ')

git -C "$REPO" worktree prune 2>/dev/null

# Leftover directories git no longer knows about (an interrupted removal).
if [ -d "$WT_ROOT" ]; then
  for d in "$WT_ROOT"/*/; do
    [ -d "$d" ] || continue
    d="${d%/}"
    git -C "$REPO" worktree list --porcelain | grep -qF "worktree $d" && continue
    if rmdir "$d" 2>/dev/null; then
      echo "removed  $(basename "$d")  (empty leftover)"
    elif [ -z "$(ls -A "$d" 2>/dev/null)" ]; then
      # Empty but undeletable: a process still holds it as its cwd. Saying
      # "not empty; inspect it" here sent people looking for contents that were
      # never there — the fix is to close the shell or editor, not to inspect.
      echo "LOCKED   $(basename "$d") — empty, but a process still has it open."
      echo "         Close that shell or editor, then: rmdir \"$d\""
    else
      echo "LEFTOVER $(basename "$d") — not a worktree and not empty; inspect it"
    fi
  done
fi

echo
echo "$removed removed, $skipped skipped."
git -C "$REPO" worktree list
