#!/usr/bin/env bash
# Turn the KB repository (giantswarm/agent-mem-experiments-kb) into an OKF KB
# for Cartographer: run once, by a human, before the first deploy.
#
# `cartographer kb create --remote` adopts only a forge's bare initial commit
# (README/LICENSE/.gitignore/.gitattributes) and refuses the reconciler's
# scaffold (CODEOWNERS, .github/...). This script builds the KB tree in a local
# repository without a remote instead (kb create --no-remote, then map_create
# through a stdio `cartographer serve`), lays it over a clone of the remote,
# keeps every file already there, and pushes one commit authored by you.
# Re-running it on a KB only adds the maps that are missing.
#
# Needs: bash 3.2+, git (with user.name/user.email), the cartographer binary of
# the deployed release, push access to the remote.

set -euo pipefail

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

  --remote URL        KB repository (default: ${REMOTE})
  --branch BRANCH     branch the server commits to, as in cartographer.kb.branch
                      (default: ${BRANCH}; created from the default branch if missing)
  --name NAME         KB name, as in the chart's cartographer.kb.name (default: ${KB_NAME})
  --map NAME          map to create; repeat for several (default: ${MAPS[*]})
  --cartographer BIN  cartographer binary (default: cartographer on PATH)
  --yes               push without asking
  --dry-run           commit in a temporary clone, show it, do not push
  -h, --help          this help
EOF
}

REMOTE="git@github.com:giantswarm/agent-mem-experiments-kb.git"
KB_NAME="gs-memory"
# Maps mirror the source repository's areas. Teams are flat (team-<slug>):
# a map is a top-level directory of data/, and a role's map selector cannot hold "/".
MAPS=(company topics team-honeybadger team-bumblebee team-cabbage team-planeteers)
CARTOGRAPHER="cartographer"
BRANCH="kb"
ASSUME_YES=0
DRY_RUN=0

custom_maps=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote) REMOTE="$2"; shift 2 ;;
    --name) KB_NAME="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --map) custom_maps+=("$2"); shift 2 ;;
    --cartographer) CARTOGRAPHER="$2"; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
if [[ ${#custom_maps[@]} -gt 0 ]]; then
  MAPS=("${custom_maps[@]}")
fi

die() { echo "error: $*" >&2; exit 1; }
log() { echo "==> $*" >&2; }

for m in "${MAPS[@]}"; do
  [[ "$m" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || die "map name '$m' is not kebab-case"
  [[ "$m" != services ]] || die "'services' is reserved and cannot be a map"
done
command -v git >/dev/null || die "git not found"
command -v "$CARTOGRAPHER" >/dev/null || die "cartographer not found (--cartographer)"
git config user.email >/dev/null || die "set git user.name and user.email: the commit is authored by you"

# Keep the run off the user's client and server configuration and quiet.
export CARTOGRAPHER_NO_UPDATE_CHECK=1
log "cartographer $("$CARTOGRAPHER" version)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
clone="$work/clone"
build="$work/data/$KB_NAME"

log "cloning $REMOTE"
git clone --quiet "$REMOTE" "$clone"
if git -C "$clone" rev-parse --verify --quiet HEAD >/dev/null; then
  branch="$(git -C "$clone" symbolic-ref --short HEAD)"
else
  branch="main"
  git -C "$clone" symbolic-ref HEAD "refs/heads/$branch"
fi
# The default branch is protected (PR reviews), so the server writes to its own branch.
if [[ -n "$BRANCH" && "$BRANCH" != "$branch" ]]; then
  if git -C "$clone" rev-parse --verify --quiet "refs/remotes/origin/$BRANCH" >/dev/null; then
    git -C "$clone" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
  else
    log "branch $BRANCH does not exist on $REMOTE: creating it from $branch"
    git -C "$clone" checkout --quiet -b "$BRANCH"
  fi
  branch="$BRANCH"
fi

# The KB is built in a repository without a remote, so the server never syncs.
if [[ -f "$clone/data/index.md" ]]; then
  log "$REMOTE ($branch) is already an OKF KB: adding missing maps only"
  is_kb=1
  mkdir -p "$work/data"
  git clone --quiet --no-local "$clone" "$build"
  git -C "$build" remote remove origin
else
  is_kb=0
  log "scaffolding KB $KB_NAME"
  "$CARTOGRAPHER" kb create "$KB_NAME" --no-remote --local --data "$work/data" >&2
fi

missing=()
for m in "${MAPS[@]}"; do
  [[ -f "$build/data/$m/_map.md" ]] || missing+=("$m")
done

if [[ ${#missing[@]} -gt 0 ]]; then
  log "creating maps: ${missing[*]}"
  title() { echo "$1" | awk -F- '{ for (i = 1; i <= NF; i++) $i = toupper(substr($i, 1, 1)) substr($i, 2) } 1'; }
  {
    echo '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"kb-bootstrap","version":"1"}}}'
    echo '{"jsonrpc":"2.0","method":"notifications/initialized"}'
    id=1
    for m in "${missing[@]}"; do
      printf '{"jsonrpc":"2.0","id":%d,"method":"tools/call","params":{"name":"map_create","arguments":{"name":"%s","title":"%s"}}}\n' \
        "$id" "$m" "$(title "$m")"
      id=$((id + 1))
    done
  } >"$work/requests.jsonl"
  "$CARTOGRAPHER" serve --kb "$build" <"$work/requests.jsonl" >"$work/responses.jsonl" 2>"$work/serve.log" ||
    { cat "$work/serve.log" >&2; die "cartographer serve failed"; }
  if grep -q '"isError":true\|"error":{' "$work/responses.jsonl"; then
    cat "$work/responses.jsonl" >&2
    die "map_create failed"
  fi
  for m in "${missing[@]}"; do
    [[ -f "$build/data/$m/_map.md" ]] || { cat "$work/responses.jsonl" >&2; die "map $m was not created"; }
  done
fi

# Lay the built tree over the clone. A file the remote already has is kept,
# except .gitattributes, which gets the KB's lines appended.
if [[ $is_kb -eq 0 ]]; then
  while IFS= read -r path; do
    [[ -e "$clone/$path" ]] || continue
    if [[ "$path" == .gitattributes ]]; then
      continue
    fi
    cmp -s "$build/$path" "$clone/$path" || die "$path exists in $REMOTE with other content; resolve it by hand"
  done < <(git -C "$build" ls-tree -r --name-only HEAD)
  if [[ -f "$clone/.gitattributes" ]]; then
    cp "$clone/.gitattributes" "$work/gitattributes.remote"
  fi
fi
git -C "$build" archive HEAD | tar -x -C "$clone"
if [[ -f "$work/gitattributes.remote" ]]; then
  kb_attrs="$(cat "$clone/.gitattributes")"
  cp "$work/gitattributes.remote" "$clone/.gitattributes"
  while IFS= read -r line; do
    grep -qxF -- "$line" "$clone/.gitattributes" || printf '%s\n' "$line" >>"$clone/.gitattributes"
  done <<<"$kb_attrs"
fi

git -C "$clone" add -A
if git -C "$clone" diff --cached --quiet; then
  log "nothing to do: $REMOTE already holds the KB and its maps"
  exit 0
fi
if [[ $is_kb -eq 0 ]]; then
  subject="feat: bootstrap OKF KB $KB_NAME"
else
  subject="feat: add KB maps ${missing[*]}"
fi
git -C "$clone" commit --quiet -m "$subject" -m "Maps: ${MAPS[*]}. Generated by scripts/kb-bootstrap.sh with cartographer $("$CARTOGRAPHER" version)."
git -C "$clone" --no-pager show --stat --format='%h %an <%ae>%n%s%n' HEAD >&2

if [[ $DRY_RUN -eq 1 ]]; then
  log "dry run: not pushed"
  exit 0
fi
if [[ $ASSUME_YES -eq 0 ]]; then
  read -r -p "Push to $REMOTE ($branch)? [y/N] " answer
  [[ "$answer" == [yY] ]] || die "not pushed"
fi
git -C "$clone" push --quiet origin "HEAD:refs/heads/$branch"
log "pushed $(git -C "$clone" rev-parse --short HEAD) to $REMOTE ($branch)"
