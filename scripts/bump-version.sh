#!/usr/bin/env bash
#
# Interactive version bump → PR into develop → auto-merge on CI green.
#
# Bumps the package version in its one source file (#767):
#   python/src/donkey_kit/__init__.py   __version__ = "…"
# (python/pyproject.toml declares the version dynamic and hatch reads it from
# there, so there is nothing to keep in lockstep; see docs/releasing.md.)
#
# The bump is tracked by an issue like any other change: the branch is named
# chore/<issue#>-bump-version-<version> per the <type>/<issue#>-<slug>
# convention, and the PR body closes the issue. Pass it with --issue N, or the
# script asks for it; Enter (or --yes) files one instead, titled
# "chore(release): bump version to <version>", labelled chore, area:release,
# priority/p2, size/xs, in the resolved milestone, and assigned to you. It is
# filed only after the final confirm, and an open issue with that exact title
# (from an earlier run that didn't finish) is reused rather than duplicated.
#
# The PR is linked to a milestone, the release the bump ships in. Pass it with
# --milestone "<title>" or --milestone <number>. Without the flag the script
# uses the tracking issue's milestone, then the open milestone whose title ends
# in "(X.Y.Z)" for the new version's base, then asks (Enter = none). The issue
# gets the milestone too when it has none; a different one already on the
# issue is left alone, with a warning.
#
# It then opens a squash PR into `develop` (branch → develop is always a squash
# merge, per [[ddk-merge-strategy]]) and enables GitHub auto-merge so it lands
# the moment the blocking CI gates pass (base-only, typecheck-and-lint incl.
# mypy --strict + lint-imports, and the test matrix). It then waits for the
# merge, switches back to `develop` (fast-forwarded), and deletes the bump
# branch locally and on origin. If the PR doesn't merge within
# MERGE_TIMEOUT_SECS (default 1800), it still returns to `develop` but keeps
# the branch.
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
#   bash scripts/bump-version.sh                # interactive (Enter at the issue prompt files one)
#   bash scripts/bump-version.sh --issue 1234   # the issue tracking this bump
#   bash scripts/bump-version.sh --milestone 21 # the milestone (title or number)
#   bash scripts/bump-version.sh --dry-run      # show every action, mutate nothing
#   bash scripts/bump-version.sh --yes          # skip the final confirm prompt (and file the issue
#                                               # without asking when --issue is absent)
#
set -uo pipefail

REPO="Donkey-Development-Kit/donkey-development-kit"
BASE_BRANCH="develop"
INIT_PY="python/src/donkey_kit/__init__.py"
MERGE_POLL_SECS="${MERGE_POLL_SECS:-15}"
MERGE_TIMEOUT_SECS="${MERGE_TIMEOUT_SECS:-1800}"

DRY_RUN=0
ASSUME_YES=0
ISSUE=""
MILESTONE_ARG=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --yes|-y)  ASSUME_YES=1 ;;
    --issue)
      [ "$#" -ge 2 ] || { printf 'error: --issue needs an issue number\n' >&2; exit 2; }
      ISSUE="$2"; shift ;;
    --issue=*) ISSUE="${1#--issue=}" ;;
    --milestone)
      [ "$#" -ge 2 ] || { printf 'error: --milestone needs a milestone title or number\n' >&2; exit 2; }
      MILESTONE_ARG="$2"; shift ;;
    --milestone=*) MILESTONE_ARG="${1#--milestone=}" ;;
    -h|--help)
      sed -n '2,/^set /p' "$0" | sed '$d' | sed -E 's/^# ?//'
      exit 0 ;;
    *) printf 'error: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
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
[ -f "$INIT_PY" ] || die "$INIT_PY not found — run this from the donkey-development-kit repo"

# --- Preconditions ------------------------------------------------------------
command -v gh >/dev/null 2>&1 || die "the GitHub CLI (gh) is required; install it and 'gh auth login'"
if [ "$DRY_RUN" -eq 0 ]; then
  # Scope to github.com: `gh auth status` (no host) checks every configured
  # host and exits non-zero if any unrelated one has a stale token.
  gh auth status --hostname github.com >/dev/null 2>&1 \
    || die "not authenticated to github.com; run 'gh auth login' (a stale token on another host does not count)"
fi

if [ -n "$(git status --porcelain)" ]; then
  die "working tree is not clean — commit or stash first (this script edits tracked version files)"
fi

# --- Read the current version from its one source ----------------------------
CURRENT=$(sed -nE 's/^__version__ = "([^"]+)"/\1/p' "$INIT_PY" | head -1)
[ -n "$CURRENT" ] || die "could not read __version__ from $INIT_PY"
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

# --- The issue this bump closes (branch naming: <type>/<issue#>-<slug>) -------
ISSUE_TITLE="chore(release): bump version to ${NEW}"
NEW_ISSUE=0
if [ -z "$ISSUE" ] && [ "$ASSUME_YES" -eq 0 ]; then
  read -r -p "Issue number tracking this bump (Enter = file one): " ISSUE
  ISSUE="$(printf '%s' "$ISSUE" | tr -d '[:space:]')"
fi
if [ -z "$ISSUE" ]; then
  # NEW is validated above (digits, dots, dev/a/b/rc), so it is safe in a jq literal.
  ISSUE=$(gh issue list --repo "$REPO" --state open --search "\"${ISSUE_TITLE}\" in:title" \
    --json number,title --jq ".[] | select(.title == \"${ISSUE_TITLE}\") | .number" 2>/dev/null | head -1)
  if [ -n "$ISSUE" ]; then
    info "issue: reusing open #$ISSUE \"$ISSUE_TITLE\""
  else
    NEW_ISSUE=1
  fi
fi
# Read-only lookups still run under --dry-run (best-effort) so it can show the
# milestone it would use; a real run fails hard instead.
ISSUE_MILESTONE=""
if [ "$NEW_ISSUE" -eq 0 ]; then
  ISSUE="${ISSUE#\#}"
  [[ "$ISSUE" =~ ^[1-9][0-9]*$ ]] || die "not an issue number: '$ISSUE'"
  if issue_info=$(gh issue view "$ISSUE" --repo "$REPO" --json state,milestone \
       --jq '[.state, (.milestone.title // "")] | @tsv' 2>/dev/null); then
    IFS=$'\t' read -r issue_state ISSUE_MILESTONE <<<"$issue_info"
    if [ "$DRY_RUN" -eq 0 ] && [ "$issue_state" != "OPEN" ]; then
      die "issue #$ISSUE is $issue_state; a bump closes an open issue"
    fi
  elif [ "$DRY_RUN" -eq 0 ]; then
    die "issue #$ISSUE not found in $REPO"
  else
    info "note: could not look up issue #$ISSUE (dry run, continuing)"
  fi
fi

# --- The milestone the bump ships in -----------------------------------------
# Resolution: --milestone, then the issue's milestone, then the open milestone
# whose title ends in "(X.Y.Z)" for the new version's base, then ask.
# One "<number>\t<title>" line per open milestone.
OPEN_MILESTONES=$(gh api "repos/${REPO}/milestones?state=open&per_page=100" \
  --jq '.[] | "\(.number)\t\(.title)"' 2>/dev/null) || OPEN_MILESTONES=""

# Print the title of the open milestone with this number or exact title.
open_milestone_title() {
  local want="$1" num title
  while IFS=$'\t' read -r num title; do
    [ -n "$num" ] || continue
    if [ "$want" = "$num" ] || [ "$want" = "$title" ]; then
      printf '%s' "$title"; return 0
    fi
  done <<<"$OPEN_MILESTONES"
  return 1
}

MILESTONE=""
if [ -n "$MILESTONE_ARG" ]; then
  MILESTONE_ARG="${MILESTONE_ARG#\#}"
  MILESTONE=$(open_milestone_title "$MILESTONE_ARG") \
    || die "no open milestone '$MILESTONE_ARG' in $REPO (pass its exact title or number)"
  if [ -n "$ISSUE_MILESTONE" ] && [ "$ISSUE_MILESTONE" != "$MILESTONE" ]; then
    printf '\033[33mwarning:\033[0m issue #%s is in milestone "%s"; the PR goes in "%s" and the issue keeps its own\n' \
      "$ISSUE" "$ISSUE_MILESTONE" "$MILESTONE" >&2
  fi
elif [ -n "$ISSUE_MILESTONE" ]; then
  if MILESTONE=$(open_milestone_title "$ISSUE_MILESTONE"); then
    info "milestone: using issue #$ISSUE's milestone"
  else
    info "note: issue #$ISSUE's milestone \"$ISSUE_MILESTONE\" is not open; not linking it to the PR"
  fi
fi

if [ -z "$MILESTONE" ] && [ -z "$ISSUE_MILESTONE" ]; then
  new_base=""
  [[ "$NEW" =~ ^([0-9]+\.[0-9]+\.[0-9]+) ]] && new_base=${BASH_REMATCH[1]}
  matches=$(printf '%s\n' "$OPEN_MILESTONES" | awk -F'\t' -v s="(${new_base})" \
    'length($2) >= length(s) && substr($2, length($2) - length(s) + 1) == s { print $2 }')
  if [ -n "$matches" ] && [ "$(printf '%s\n' "$matches" | wc -l)" -eq 1 ]; then
    MILESTONE="$matches"
    info "milestone: matched version ${new_base}"
  elif [ -n "$OPEN_MILESTONES" ] && [ "$ASSUME_YES" -eq 0 ]; then
    printf '\nWhich milestone does this bump ship in? (Enter = none)\n\n'
    ms_titles=()
    while IFS=$'\t' read -r _ title; do
      [ -n "$title" ] || continue
      ms_titles+=("$title")
      printf '  %d) %s\n' "${#ms_titles[@]}" "$title"
    done <<<"$OPEN_MILESTONES"
    printf '\n'
    ms_choice=""
    read -r -p "Milestone: " ms_choice
    if [ -n "$ms_choice" ]; then
      [[ "$ms_choice" =~ ^[0-9]+$ ]] && [ "$ms_choice" -ge 1 ] && [ "$ms_choice" -le "${#ms_titles[@]}" ] \
        || die "milestone choice out of range: $ms_choice"
      MILESTONE="${ms_titles[$((ms_choice - 1))]}"
    fi
  fi
fi

# --- Confirm ------------------------------------------------------------------
printf '\n'
printf '  version:  \033[1m%s\033[0m  →  \033[1m%s\033[0m\n' "$CURRENT" "$NEW"
if [ "$NEW_ISSUE" -eq 1 ]; then
  printf '  issue:    new: "%s" (filed on proceed)\n' "$ISSUE_TITLE"
else
  printf '  issue:    #%s\n' "$ISSUE"
fi
if [ -n "$MILESTONE" ]; then
  printf '  milestone: %s\n' "$MILESTONE"
  [ "$NEW_ISSUE" -eq 0 ] && [ -z "$ISSUE_MILESTONE" ] \
    && printf '            (also set on issue #%s, which has none)\n' "$ISSUE"
else
  printf '  milestone: none\n'
fi
# The slug is kebab-case with no punctuation: 0.1.3.dev0 -> 0-1-3-dev0.
printf '  branch:   chore/%s-bump-version-%s  (off %s)\n' "${ISSUE:-<new>}" "${NEW//./-}" "$BASE_BRANCH"
printf '  file:     %s\n' "$INIT_PY"
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

# --- File the tracking issue when none was given -----------------------------
if [ "$NEW_ISSUE" -eq 1 ]; then
  ISSUE_BODY="## Summary
Bump \`__version__\` \`${CURRENT} → ${NEW}\` in \`${INIT_PY}\`, its one source (\`pyproject.toml\` reads it from there), ahead of the next \`${BASE_BRANCH} → main\` promotion.

## Proposal
- \`scripts/bump-version.sh\` edits \`${INIT_PY}\` only and opens the squash PR into \`${BASE_BRANCH}\` with auto-merge.

No source behavior changes.

## Done when
- [ ] \`${BASE_BRANCH}\` reads \`__version__ = \"${NEW}\"\`."
  ISSUE_ARGS=(--repo "$REPO" --title "$ISSUE_TITLE" --body "$ISSUE_BODY" --assignee "@me"
    --label chore --label area:release --label priority/p2 --label size/xs)
  [ -n "$MILESTONE" ] && ISSUE_ARGS+=(--milestone "$MILESTONE")
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '\033[33m[dry-run]\033[0m would run: gh issue create --repo %s --title %q --assignee @me --label chore --label area:release --label priority/p2 --label size/xs%s --body <…>\n' \
      "$REPO" "$ISSUE_TITLE" "${MILESTONE:+ --milestone \"$MILESTONE\"}"
    ISSUE="<new>"
  else
    info "filing the tracking issue"
    issue_url=$(gh issue create "${ISSUE_ARGS[@]}") || die "gh issue create failed"
    ISSUE="${issue_url##*/}"
    [[ "$ISSUE" =~ ^[1-9][0-9]*$ ]] || die "could not read the issue number from: $issue_url"
    info "filed issue #$ISSUE: $issue_url"
  fi
fi
BRANCH="chore/${ISSUE}-bump-version-${NEW//./-}"

# --- Cut the branch off the latest develop ------------------------------------
info "fetching latest $BASE_BRANCH"
run git fetch origin "$BASE_BRANCH"

# Refuse to clobber an existing branch of the same name.
if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  die "local branch $BRANCH already exists — delete it or pick a different version"
fi
run git switch -c "$BRANCH" "origin/${BASE_BRANCH}"

# --- Edit the one version source (anchored, exact-match on current) ----------
if [ "$DRY_RUN" -eq 1 ]; then
  printf '\033[33m[dry-run]\033[0m would set __version__ %s → %s in %s\n' \
    "$CURRENT" "$NEW" "$INIT_PY"
else
  perl -i -pe "s/^__version__ = \"\Q$CURRENT\E\"/__version__ = \"$NEW\"/" "$INIT_PY" || die "failed to edit $INIT_PY"
  got_i=$(sed -nE 's/^__version__ = "([^"]+)"/\1/p' "$INIT_PY" | head -1)
  [ "$got_i" = "$NEW" ] || die "post-edit check failed: $INIT_PY is '$got_i', expected '$NEW'"
  info "bumped $INIT_PY to $NEW"
fi

# --- Commit + push ------------------------------------------------------------
COMMIT_MSG="chore: bump version to ${NEW}"
run git add "$INIT_PY"
run git commit -m "$COMMIT_MSG"
run git push -u origin "$BRANCH"

# --- Open the PR into develop -------------------------------------------------
PR_TITLE="chore: bump version to ${NEW}"
PR_BODY="Closes #${ISSUE}

Bumps the package version ${CURRENT} → ${NEW} in \`${INIT_PY}\`, its one source (\`pyproject.toml\` reads it from there).

Every promotion to \`main\` advances the version; this lands the bump on \`${BASE_BRANCH}\` ahead of the promotion PR (see the release conventions).

Auto-merge (squash) is enabled — this will merge once the blocking CI gates pass (base-only, typecheck-and-lint incl. mypy --strict + lint-imports, and the test matrix)."

if [ "$DRY_RUN" -eq 1 ]; then
  printf '\033[33m[dry-run]\033[0m would run: gh pr create --repo %s --base %s --head %s --title %q --body <…>\n' \
    "$REPO" "$BASE_BRANCH" "$BRANCH" "$PR_TITLE"
  if [ -n "$MILESTONE" ]; then
    printf '\033[33m[dry-run]\033[0m   … with --milestone "%s"\n' "$MILESTONE"
    [ "$NEW_ISSUE" -eq 0 ] && [ -z "$ISSUE_MILESTONE" ] \
      && printf '\033[33m[dry-run]\033[0m would run: gh issue edit %s --repo %s --milestone "%s"\n' \
        "$ISSUE" "$REPO" "$MILESTONE"
  fi
  printf '\033[33m[dry-run]\033[0m would run: gh pr merge --repo %s --squash --auto --delete-branch --subject %q --head %s\n' \
    "$REPO" "chore: bump version to ${NEW}" "$BRANCH"
  printf '\033[33m[dry-run]\033[0m would wait for the merge, then: git switch %s && git pull --ff-only origin %s\n' \
    "$BASE_BRANCH" "$BASE_BRANCH"
  printf '\033[33m[dry-run]\033[0m would run: git branch -D %s && git push origin --delete %s\n' "$BRANCH" "$BRANCH"
  info "dry run complete — no branch, commit, or PR was created"
  exit 0
fi

info "opening PR into $BASE_BRANCH"
MILESTONE_ARGS=()
[ -n "$MILESTONE" ] && MILESTONE_ARGS=(--milestone "$MILESTONE")
# ${a[@]+…} because bash 3.2 (macOS) treats an empty array as unset under -u.
gh pr create --repo "$REPO" \
  --base "$BASE_BRANCH" --head "$BRANCH" \
  --title "$PR_TITLE" --body "$PR_BODY" \
  ${MILESTONE_ARGS[@]+"${MILESTONE_ARGS[@]}"} \
  || die "gh pr create failed"

PR_NUM=$(gh pr view "$BRANCH" --repo "$REPO" --json number --jq .number) \
  || die "could not resolve the PR number just created"
info "opened PR #$PR_NUM"

if [ -n "$MILESTONE" ] && [ "$NEW_ISSUE" -eq 0 ] && [ -z "$ISSUE_MILESTONE" ]; then
  info "setting milestone \"$MILESTONE\" on issue #$ISSUE"
  gh issue edit "$ISSUE" --repo "$REPO" --milestone "$MILESTONE" >/dev/null \
    || printf '\033[33mwarning:\033[0m could not set the milestone on issue #%s; set it by hand\n' "$ISSUE" >&2
fi

# --- Enable auto-merge (squash) — merges the moment CI goes green -------------
info "enabling auto-merge (squash) on PR #$PR_NUM"
if gh pr merge "$PR_NUM" --repo "$REPO" \
     --squash --auto --delete-branch \
     --subject "chore: bump version to ${NEW} (#${PR_NUM})"; then
  printf '\n\033[32m✓\033[0m PR #%s opened with auto-merge enabled.\n' "$PR_NUM"
  printf '  It will squash-merge into %s automatically once CI is green.\n' "$BASE_BRANCH"
else
  printf '\n\033[33m!\033[0m PR #%s is open, but enabling auto-merge failed.\n' "$PR_NUM"
  printf '  This usually means repository auto-merge is disabled. Once CI is green, merge with:\n'
  printf '    gh pr merge %s --repo %s --squash --delete-branch --subject "chore: bump version to %s (#%s)"\n' \
    "$PR_NUM" "$REPO" "$NEW" "$PR_NUM"
  git switch "$BASE_BRANCH" >/dev/null 2>&1 || true
  exit 1
fi

# --- Wait for the merge, then return to develop and delete the bump branch ----
# The branch can only be deleted AFTER the merge: deleting the head branch of an
# open PR closes it unmerged.
info "waiting for PR #$PR_NUM to merge (polling every ${MERGE_POLL_SECS}s, timeout ${MERGE_TIMEOUT_SECS}s; Ctrl-C to stop waiting)"
state="OPEN"
waited=0
while [ "$waited" -lt "$MERGE_TIMEOUT_SECS" ]; do
  state=$(gh pr view "$PR_NUM" --repo "$REPO" --json state --jq .state 2>/dev/null || echo "UNKNOWN")
  case "$state" in MERGED|CLOSED) break ;; esac
  sleep "$MERGE_POLL_SECS"
  waited=$((waited + MERGE_POLL_SECS))
done

run git switch "$BASE_BRANCH"
run git pull --ff-only origin "$BASE_BRANCH"

if [ "$state" != "MERGED" ]; then
  printf '\n\033[33m!\033[0m PR #%s is %s — kept branch %s (local and remote).\n' "$PR_NUM" "$state" "$BRANCH"
  printf '  Watch it:  gh pr checks %s --repo %s --watch\n' "$PR_NUM" "$REPO"
  printf '  After it merges:  git branch -D %s && git push origin --delete %s\n' "$BRANCH" "$BRANCH"
  exit 1
fi

# Squash merges leave the branch tip unreachable from develop, so -d would refuse.
run git branch -D "$BRANCH"
if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  run git push origin --delete "$BRANCH"
fi
run git fetch --prune origin

printf '\n\033[32m✓\033[0m PR #%s merged; on %s at %s, branch %s deleted locally and remotely.\n' \
  "$PR_NUM" "$BASE_BRANCH" "$NEW" "$BRANCH"
