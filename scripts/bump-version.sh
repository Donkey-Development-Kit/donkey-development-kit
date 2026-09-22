#!/usr/bin/env bash
#
# Interactive version bump → PR into develop → auto-merge on CI green.
#
# Bumps the package version in lockstep across the two files that must always
# agree (see [[ddk-release]] "Single source of version truth"):
#   python/pyproject.toml            version = "…"
#   python/src/donkey_kit/__init__.py   __version__ = "…"
#
# then opens a squash PR into `develop` (branch → develop is always a squash
# merge, per [[ddk-merge-strategy]]) and enables GitHub auto-merge so it lands
# the moment the blocking CI gates pass (base-only, typecheck-and-lint incl.
# mypy --strict + lint-imports, and the test matrix).
#
# The version scheme is PEP 440, milestone-driven, on the pre-release ladder
#   0.1.0.dev0 < … < 0.1.0a1 < 0.1.0b1 < 0.1.0rc1 < 0.1.0
# and EVERY promotion advances the version (default: the .devN counter). This
# script only asks; it computes valid next steps and refuses anything not
# strictly greater than the current version.
#
# Merge-time approval: [[ddk-merge-strategy]] forbids autonomous merges. Running
# this script IS that explicit authorization — it interactively confirms the new
# version before it touches anything, and enables auto-merge rather than merging
# behind your back. Nothing lands until CI is green.
#
# Usage:
#   bash scripts/bump-version.sh              # interactive
#   bash scripts/bump-version.sh --dry-run    # show every action, mutate nothing
#   bash scripts/bump-version.sh --yes        # skip the final confirm prompt
#
set -uo pipefail

REPO="Donkey-Development-Kit/donkey-development-kit"
BASE_BRANCH="develop"
PYPROJECT="python/pyproject.toml"
INIT_PY="python/src/donkey_kit/__init__.py"

DRY_RUN=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --yes|-y)  ASSUME_YES=1 ;;
    -h|--help)
      grep '^#' "$0" | sed -E 's/^# ?//' | sed -n '2,40p'
      exit 0 ;;
    *) printf 'error: unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

BOLD=$'\033[1m'; RESET=$'\033[0m'
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m•\033[0m %s\n' "$*"; }
run() {
  # Execute a mutating command, or just print it under --dry-run.
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '\033[33m[dry-run]\033[0m %s\n' "$*"
  else
    printf '\033[90m$ %s\033[0m\n' "$*"
    "$@" || die "command failed: $*"
  fi
}

# --- Locate the repo root and cd there so paths resolve regardless of cwd -----
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git repository"
cd "$ROOT" || die "cannot cd to repo root: $ROOT"
[ -f "$PYPROJECT" ] || die "$PYPROJECT not found — run this from the donkey-development-kit repo"

# --- Preconditions ------------------------------------------------------------
command -v gh >/dev/null 2>&1 || die "the GitHub CLI (gh) is required; install it and 'gh auth login'"
if [ "$DRY_RUN" -eq 0 ]; then
  gh auth status >/dev/null 2>&1 || die "not authenticated with gh; run 'gh auth login'"
fi

if [ -n "$(git status --porcelain)" ]; then
  die "working tree is not clean — commit or stash first (this script edits tracked version files)"
fi

# --- Read + verify the current version (the two files MUST agree) -------------
current_pyproject=$(sed -nE 's/^version = "([^"]+)"/\1/p' "$PYPROJECT" | head -1)
current_init=$(sed -nE 's/^__version__ = "([^"]+)"/\1/p' "$INIT_PY" | head -1)
[ -n "$current_pyproject" ] || die "could not read version from $PYPROJECT"
[ -n "$current_init" ] || die "could not read __version__ from $INIT_PY"
if [ "$current_pyproject" != "$current_init" ]; then
  die "version mismatch: $PYPROJECT says '$current_pyproject' but $INIT_PY says '$current_init' — fix by hand first"
fi
CURRENT="$current_pyproject"
info "current version: ${BOLD}${CURRENT}${RESET}"

# --- Parse the PEP 440 version ------------------------------------------------
# Shape: MAJOR.MINOR.PATCH  optionally + .devN | aN | bN | rcN
if [[ "$CURRENT" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)((\.dev|a|b|rc)([0-9]+))?$ ]]; then
  major=${BASH_REMATCH[1]}; minor=${BASH_REMATCH[2]}; patch=${BASH_REMATCH[3]}
  pretype=${BASH_REMATCH[5]}   # ".dev" | "a" | "b" | "rc" | ""
  prenum=${BASH_REMATCH[6]}    # counter | ""
else
  die "current version '$CURRENT' is not a recognized PEP 440 form this script can bump; use a custom bump by editing the files, or extend this parser"
fi
base="${major}.${minor}.${patch}"

# --- Build the menu of valid next versions (all strictly greater) -------------
labels=(); targets=()
add() { labels+=("$1"); targets+=("$2"); }

if [ -n "$pretype" ]; then
  # On the pre-release ladder. Same-rung bump is the common case (default).
  prettytype=${pretype#.}   # "dev" | "a" | "b" | "rc"
  add "next ${prettytype} pre-release (bump the counter)" "${base}${pretype}$((prenum + 1))"
  case "$pretype" in
    .dev) rank=0 ;;
    a)    rank=1 ;;
    b)    rank=2 ;;
    rc)   rank=3 ;;
  esac
  [ "$rank" -lt 1 ] && add "promote to alpha"             "${base}a1"
  [ "$rank" -lt 2 ] && add "promote to beta"              "${base}b1"
  [ "$rank" -lt 3 ] && add "promote to release candidate" "${base}rc1"
  add "cut the final release ${base}" "${base}"
else
  # Currently a final X.Y.Z release.
  add "patch / hotfix"                       "${major}.${minor}.$((patch + 1))"
  add "next minor, start dev ladder"         "${major}.$((minor + 1)).0.dev0"
  add "next minor, final"                    "${major}.$((minor + 1)).0"
fi
add "custom (type it in)" "__CUSTOM__"

# --- Present the menu ---------------------------------------------------------
printf '\nWhat kind of bump? (default: 1)\n\n'
for i in "${!labels[@]}"; do
  n=$((i + 1))
  t="${targets[$i]}"
  if [ "$t" = "__CUSTOM__" ]; then
    printf '  %d) %s\n' "$n" "${labels[$i]}"
  else
    printf '  %d) %-42s → \033[1m%s\033[0m\n' "$n" "${labels[$i]}" "$t"
  fi
done
printf '\n'

choice=""
read -r -p "Choice [1]: " choice
choice="${choice:-1}"
[[ "$choice" =~ ^[0-9]+$ ]] || die "not a number: $choice"
idx=$((choice - 1))
if [ "$idx" -lt 0 ] || [ "$idx" -ge "${#targets[@]}" ]; then
  die "choice out of range: $choice"
fi

NEW="${targets[$idx]}"
if [ "$NEW" = "__CUSTOM__" ]; then
  read -r -p "Enter the new PEP 440 version: " NEW
  NEW="$(printf '%s' "$NEW" | tr -d '[:space:]')"
  [ -n "$NEW" ] || die "no version entered"
  # Sanity-check the shape (same grammar as above).
  [[ "$NEW" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)((\.dev|a|b|rc)([0-9]+))?$ ]] \
    || die "'$NEW' is not a PEP 440 form this script recognizes (X.Y.Z[.devN|aN|bN|rcN])"
fi

# --- Enforce: strictly greater than current (guards tag collisions) -----------
# Best-effort via packaging.version; skipped gracefully if unavailable.
if command -v python3 >/dev/null 2>&1; then
  cmp=$(python3 - "$CURRENT" "$NEW" <<'PY' 2>/dev/null
import sys
try:
    from packaging.version import Version
    cur, new = Version(sys.argv[1]), Version(sys.argv[2])
    print("gt" if new > cur else ("eq" if new == cur else "lt"))
except Exception:
    print("skip")
PY
)
  case "$cmp" in
    lt) die "new version $NEW is LOWER than current $CURRENT — every bump must move forward" ;;
    eq) die "new version $NEW equals current $CURRENT — a bump must advance the version (a reused version collides the release tag)" ;;
    gt) : ;;
    *)  info "note: could not verify ordering (packaging not importable); trusting your input" ;;
  esac
fi

# --- Confirm ------------------------------------------------------------------
BRANCH="release/bump-${NEW}"
printf '\n'
printf '  version:  \033[1m%s\033[0m  →  \033[1m%s\033[0m\n' "$CURRENT" "$NEW"
printf '  branch:   %s  (off %s)\n' "$BRANCH" "$BASE_BRANCH"
printf '  files:    %s, %s\n' "$PYPROJECT" "$INIT_PY"
printf '  merge:    squash into %s, auto-merge when CI is green\n' "$BASE_BRANCH"
[ "$DRY_RUN" -eq 1 ] && printf '  \033[33mmode:     DRY RUN — nothing will be changed\033[0m\n'
printf '\n'

if [ "$ASSUME_YES" -eq 0 ]; then
  reply=""
  read -r -p "Proceed? [y/N]: " reply
  case "$reply" in
    y|Y|yes|YES) : ;;
    *) info "aborted; nothing changed"; exit 0 ;;
  esac
fi

# --- Cut the branch off the latest develop ------------------------------------
info "fetching latest $BASE_BRANCH"
run git fetch origin "$BASE_BRANCH"

# Refuse to clobber an existing branch of the same name.
if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  die "local branch $BRANCH already exists — delete it or pick a different version"
fi
run git switch -c "$BRANCH" "origin/${BASE_BRANCH}"

# --- Edit the two files in lockstep (anchored, exact-match on current) --------
if [ "$DRY_RUN" -eq 1 ]; then
  printf '\033[33m[dry-run]\033[0m would set version %s → %s in %s and %s\n' \
    "$CURRENT" "$NEW" "$PYPROJECT" "$INIT_PY"
else
  perl -i -pe "s/^version = \"\Q$CURRENT\E\"/version = \"$NEW\"/"        "$PYPROJECT" || die "failed to edit $PYPROJECT"
  perl -i -pe "s/^__version__ = \"\Q$CURRENT\E\"/__version__ = \"$NEW\"/" "$INIT_PY"   || die "failed to edit $INIT_PY"
  # Verify both actually changed and now agree.
  got_p=$(sed -nE 's/^version = "([^"]+)"/\1/p' "$PYPROJECT" | head -1)
  got_i=$(sed -nE 's/^__version__ = "([^"]+)"/\1/p' "$INIT_PY" | head -1)
  [ "$got_p" = "$NEW" ] || die "post-edit check failed: $PYPROJECT is '$got_p', expected '$NEW'"
  [ "$got_i" = "$NEW" ] || die "post-edit check failed: $INIT_PY is '$got_i', expected '$NEW'"
  info "bumped both files to $NEW"
fi

# --- Commit + push ------------------------------------------------------------
COMMIT_MSG="chore: bump version to ${NEW}"
run git add "$PYPROJECT" "$INIT_PY"
run git commit -m "$COMMIT_MSG"
run git push -u origin "$BRANCH"

# --- Open the PR into develop -------------------------------------------------
PR_TITLE="chore: bump version to ${NEW}"
PR_BODY="Bumps the package version ${CURRENT} → ${NEW} in lockstep across \`${PYPROJECT}\` and \`${INIT_PY}\`.

Every promotion to \`main\` advances the version; this lands the bump on \`${BASE_BRANCH}\` ahead of the promotion PR (see the release conventions).

Auto-merge (squash) is enabled — this will merge once the blocking CI gates pass (base-only, typecheck-and-lint incl. mypy --strict + lint-imports, and the test matrix)."

if [ "$DRY_RUN" -eq 1 ]; then
  printf '\033[33m[dry-run]\033[0m would run: gh pr create --repo %s --base %s --head %s --title %q --body <…>\n' \
    "$REPO" "$BASE_BRANCH" "$BRANCH" "$PR_TITLE"
  printf '\033[33m[dry-run]\033[0m would run: gh pr merge --repo %s --squash --auto --delete-branch --subject %q --head %s\n' \
    "$REPO" "chore: bump version to ${NEW}" "$BRANCH"
  info "dry run complete — no branch, commit, or PR was created"
  exit 0
fi

info "opening PR into $BASE_BRANCH"
gh pr create --repo "$REPO" \
  --base "$BASE_BRANCH" --head "$BRANCH" \
  --title "$PR_TITLE" --body "$PR_BODY" \
  || die "gh pr create failed"

PR_NUM=$(gh pr view "$BRANCH" --repo "$REPO" --json number --jq .number) \
  || die "could not resolve the PR number just created"
info "opened PR #$PR_NUM"

# --- Enable auto-merge (squash) — merges the moment CI goes green -------------
info "enabling auto-merge (squash) on PR #$PR_NUM"
if gh pr merge "$PR_NUM" --repo "$REPO" \
     --squash --auto --delete-branch \
     --subject "chore: bump version to ${NEW} (#${PR_NUM})"; then
  printf '\n\033[32m✓\033[0m PR #%s opened with auto-merge enabled.\n' "$PR_NUM"
  printf '  It will squash-merge into %s automatically once CI is green.\n' "$BASE_BRANCH"
  printf '  Watch it:  gh pr checks %s --repo %s --watch\n' "$PR_NUM" "$REPO"
else
  printf '\n\033[33m!\033[0m PR #%s is open, but enabling auto-merge failed.\n' "$PR_NUM"
  printf '  This usually means repository auto-merge is disabled. Once CI is green, merge with:\n'
  printf '    gh pr merge %s --repo %s --squash --delete-branch --subject "chore: bump version to %s (#%s)"\n' \
    "$PR_NUM" "$REPO" "$NEW" "$PR_NUM"
  exit 1
fi
