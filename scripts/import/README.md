# agent-memory sample import

Imports a screened sample of the private `giantswarm/agent-memory` repo (OKF v0.1
markdown) into the two PoC memory systems:

- an **agentmemory 0.9.32** server, over its REST API;
- the **Cartographer** knowledge base `gs-memory`, a git repo of OKF markdown at
  `git@github.com:giantswarm/agent-mem-experiments-kb.git`.

This directory holds code and the file list only. **No company content may ever be
committed here.** `fetch` and the agentmemory state file refuse to write inside this
repo; keep the working directory somewhere else (see below) and delete it afterwards.

| File | What it is |
|---|---|
| `sample-files.txt` | The 33 approved source paths (screened for customer names). |
| `import.py` | `fetch`, `agentmemory` and `cartographer` subcommands. Every command prints a summary. |

## Prerequisites

- Python 3.10+ (standard library only, no `pip install` needed).
- `gh` logged in with an account that can read `giantswarm/agent-memory`
  (`gh api repos/giantswarm/agent-memory -q .full_name` must work).
- For agentmemory: the server URL and its bearer secret in `AGENTMEMORY_SECRET`. The server
  must run with `TEAM_ID` and `USER_ID` set, otherwise team sharing is disabled and every
  share fails with "Team memory not enabled".
- For Cartographer:
  - `cartographer` v0.28.0 on `PATH` (or `--bin` / `CARTOGRAPHER_BIN`), from the GitHub release
    asset `cartographer-linux-amd64` / `-darwin-arm64`, checked against `sha256sums.txt`;
  - SSH push access to the KB repo;
  - the KB already initialized on branch `kb` (`scripts/kb-bootstrap.sh`);
  - the KB repo **private**: `--push` checks its visibility with `gh` and refuses a public repo.

## Run it

```bash
cd agent-mem-experiments            # this repo
WORK=$(mktemp -d "$HOME/agent-memory-import.XXXXXX")   # outside the repo

# 1. Download the approved files (refuses customers/ and people/ even if listed)
python3 scripts/import/import.py fetch --out "$WORK/src"

# 2. agentmemory: plan, then import (safe to re-run)
export AGENTMEMORY_URL=https://agentmemory.example.internal   # the PoC server
export AGENTMEMORY_SECRET=...                                 # from the server's secret
python3 scripts/import/import.py agentmemory --src "$WORK/src" --url "$AGENTMEMORY_URL" --dry-run
python3 scripts/import/import.py agentmemory --src "$WORK/src" --url "$AGENTMEMORY_URL"

# 3. Cartographer: clone the KB, plan, import + commit + push (safe to re-run)
git clone -b kb git@github.com:giantswarm/agent-mem-experiments-kb.git "$WORK/kb"   # the branch Cartographer writes (cartographer.kb.branch)
python3 scripts/import/import.py cartographer --src "$WORK/src" --kb-clone "$WORK/kb" --dry-run
python3 scripts/import/import.py cartographer --src "$WORK/src" --kb-clone "$WORK/kb" --push

# 4. Remove the downloaded content
rm -rf "$WORK"
```

The agentmemory state file lives in `~/.local/state/agent-memory-import/` (or
`$XDG_STATE_HOME`), one per server URL; keep it between runs. Losing it is harmless (see
below). Exit status is 0 when everything succeeded and 1 when a file was refused, missing,
invalid or failed to import or share.

## What the import does

### agentmemory

One memory per file, posted to `POST /agentmemory/remember`:

| Field | Value |
|---|---|
| `content` | title, description, body without the leading H1, then `Resource: <url>` when the file has `resource` |
| `type` | `reference` → `fact`, `topic` → `pattern`, `project` → `architecture` (anything else → `fact`) |
| `concepts` | the file's `tags` plus its area: `company`, `topics` or `team:<slug>` |
| `files` | `["giantswarm/agent-memory:<path>"]`, which is also how the importer finds its memories again |
| `project` | `giantswarm/agent-memory` |
| `agentId` | `agent-memory-import` |

Each memory is then shared with `POST /agentmemory/team/share` (`itemType: memory`). In
the content, links to other sampled files keep their repo-root path (`/topics/x.md`);
links to files outside the sample become their label text.

agentmemory 0.9.32 has no memory update endpoint. When you `remember` content that is more
than 70% similar (word-set Jaccard) to a latest memory with the same `agentId` and
`project`, the server supersedes that memory: it creates version n+1 and marks the old one
`isLatest: false`. Reposting the same file would therefore create a new version each time,
not a duplicate, so the importer must skip unchanged files itself:

- **Unchanged file** (same sha256 of the full request payload as in the state file, and the
  memory is still the latest on the server): skipped, nothing is posted.
- **No state entry, but the server has an identical latest memory for that path**
  (lost state file, another laptop): adopted into the state, not reposted.
- **Changed file**: posted again. If the server superseded the old memory, the old one stays
  as a non-latest version. If not (more than 30% changed), the importer deletes the old one
  with `POST /agentmemory/forget`, so each file always has exactly one latest memory. The
  new memory is shared. The old team-feed entry stays, because there is no unshare API.
- **Team share**: skipped when the state file or `GET /agentmemory/team/feed` already lists
  the current memory id.
- Before writing, the importer computes the same Jaccard score between the sampled files and
  stops if any two are similar enough to supersede each other.

### Cartographer

The files are staged in a temporary directory with the KB layout, then written with
`cartographer import --map <m>=<m> … --message …`. Maps are top-level only, with no
nesting (data-plane.md, D77), so areas map as follows:

| Source | KB map (`data/<map>/`) |
|---|---|
| `company/` | `company` |
| `topics/` | `topics` |
| `teams/<slug>/` | `team-<slug>` |

Before it calls `cartographer import`, the importer:

- maps `type` to the KB's PascalCase types: `Reference`, `Topic`, `Project`;
- quotes the frontmatter so Cartographer's parser reads it back;
- writes `tags` and `related` as one-line lists;
- adds `provenance: [https://github.com/giantswarm/agent-memory/blob/main/<path>]`;
- replaces a leading H1 with `# <title>`, so the reworded H1s in the source do not raise
  `title_h1_mismatch`;
- rewrites root-absolute links (`/topics/x.md`), in the body and in `related`, to paths
  relative to the file (`../team-bumblebee/x.md`);
- turns links to files outside the sample (including any under `customers/` or `people/`)
  into their label text.

Because the corpus is staged with its final map names, the link-rewrite pass in
`cartographer import` changes nothing. The summary reports any file where it did ("link
drift", expected 0). `cartographer import` adds `status: imported`, scaffolds each new
map (`_map.md`, `index.md`, `log.md`), and makes one commit with only the paths it wrote.

Re-runs are idempotent: concepts that already exist are skipped, and no commit is made.
To push changed source files, re-run with `--overwrite`. That replaces the imported pages
and **discards any curation done in the KB since**. Unchanged files produce no diff.

## Verify

### agentmemory

```bash
H="Authorization: Bearer $AGENTMEMORY_SECRET"
# 33 latest memories from the importer (total also counts superseded versions)
curl -s -H "$H" "$AGENTMEMORY_URL/agentmemory/memories?agentId=agent-memory-import&project=giantswarm/agent-memory&count=true"
# team feed holds one entry per shared memory (plus stale entries after updates)
curl -s -H "$H" "$AGENTMEMORY_URL/agentmemory/team/feed?limit=1000" | jq .total
# retrieval smoke test: the top hit should be topics/kyverno-pss-debug-pods.md
curl -s -H "$H" -H 'Content-Type: application/json' -X POST "$AGENTMEMORY_URL/agentmemory/smart-search" \
  -d '{"query":"kyverno debug pods"}' | jq '.results[:3] | map(.obsId)'
# a second import must report created 0, updated 0, shared 0
python3 scripts/import/import.py agentmemory --src "$WORK/src" --url "$AGENTMEMORY_URL"
```

### Cartographer

```bash
git -C "$WORK/kb" log --oneline -3          # one "import: giantswarm/agent-memory sample (33 files)" commit
ls "$WORK/kb/data"                          # company team-bumblebee team-cabbage team-honeybadger team-planeteers topics
for d in "$WORK"/kb/data/*/; do echo "$d $(ls "$d" | grep -cv '^_map.md$\|^index.md$\|^log.md$')"; done
# once the server has pulled the commit (rebuild search if needed: cartographer reindex)
cartographer kb repair gs-memory            # plan only: every mechanical repair at 0
```

On the test KB, `lint` reported only `imported_draft` (33, one per page, until it is
curated), `index_incomplete` (33, because the scaffolded map indexes do not list the pages
yet) and `orphan` (26, pages with no inbound links). There were no `broken_link`,
frontmatter or title findings.

## Removing an import

- agentmemory: `POST /agentmemory/forget {"memoryId": "<id>"}` for each id in the state
  file, then delete the state file.
- Cartographer: `git revert` the import commit in the KB repo and push.
