[![CircleCI](https://dl.circleci.com/status-badge/img/gh/giantswarm/agent-mem-experiments/tree/main.svg?style=svg)](https://dl.circleci.com/status-badge/redirect/gh/giantswarm/agent-mem-experiments/tree/main)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/giantswarm/agent-mem-experiments/badge)](https://securityscorecards.dev/viewer/?uri=github.com/giantswarm/agent-mem-experiments)

# agent-mem-experiments

A proof of concept of shared memory for coding agents (Claude Code, Claude Desktop,
Gemini) across Giant Swarm teams. It runs two candidate servers side by side on the
`operations` workload cluster of `gazelle` and fills both with the same screened sample
of the private `giantswarm/agent-memory` repository:

| Component | What it is | Endpoint |
|---|---|---|
| [agentmemory](https://github.com/rohitg00/agentmemory) 0.9.32 | Memory server: captures sessions through hooks, recalls with hybrid search (BM25 + Gemini embeddings + graph), team feed | `https://agentmemory.operations.awsprod.gigantic.io/agentmemory` |
| [Cartographer](https://github.com/BeppeTemp/cartographer) v0.28.0 | Git-backed OKF markdown wiki served over MCP, with per-map write roles | `https://cartographer.operations.awsprod.gigantic.io/mcp/routed` |

This is an experiment, not a supported app. No company content lives in this public
repository: the sample is fetched on a laptop and written straight to the servers.

```
Dockerfile                        agentmemory image: agentmemory 0.9.32, iii 0.25.0, state + http workers
helm/agent-mem-experiments/       one chart, one component per release (agentmemory | cartographer)
scripts/agentmemory-smoke-test.sh end-to-end checks against an agentmemory server
scripts/kb-bootstrap.sh           turns giantswarm/agent-mem-experiments-kb into a Cartographer KB (once)
scripts/import/                   imports the screened sample into both servers
docs/chart-conventions.md         how the chart is laid out and the platform rules it follows
```

## How it is deployed

CI publishes the chart and the image to `gsoci.azurecr.io` (the chart's `appVersion` is
the image tag). [workload-clusters-fleet](https://github.com/giantswarm/workload-clusters-fleet)
installs it twice on `gazelle/operations` with Flux, each release enabling one component:

- release `agentmemory`, `agentmemory.enabled: true`, Secret `agentmemory`
  (`AGENTMEMORY_SECRET`, `GEMINI_API_KEY`);
- release `cartographer`, `cartographer.enabled: true`, Secrets `cartographer-tokens`
  (`TOKEN_<ID>` per user) and `cartographer-credentials` (`default.ssh`, `known_hosts`,
  `audit.key`).

The Secrets are SOPS-encrypted in the fleet repository. Both servers sit behind the
`giantswarm-default` Envoy Gateway (HTTPRoute, TLS at the gateway) and accept traffic only
from it; every request needs a bearer token.

Both pods pass Pod Security Standards "restricted", run as UID 1000 with a read-only root
filesystem, one replica with `Recreate` on a 5 Gi volume.

### agentmemory

The pod runs `iii compose` in the foreground with the `state` and `http` workers baked into
the image (no worker downloads at start) and Valkey as a native sidecar on loopback.
Gemini provides the LLM (`gemini-3.7-flash`) and 768-dimension embeddings. One server-wide
team scope (`TEAM_ID=gs-ai-test`). One shared bearer secret for everyone: agentmemory has no
per-user tokens; `agentId` tells writers apart.

### Cartographer

One KB, `gs-memory`, cloned from `giantswarm/agent-mem-experiments-kb` (private) over SSH
with a deploy key. Every write is a commit on branch **`kb`**: the repository's `main` is
protected by the Giant Swarm rulesets (PR reviews), so the server never writes there.

Maps mirror the source areas; teams are flat because a map is a top-level directory:

| Map | Writers |
|---|---|
| `company` | puja, admin |
| `topics` | everyone |
| `team-honeybadger` | lukasz, admin |
| `team-bumblebee` | dominik, admin |
| `team-planeteers` | puja, admin |
| `team-cabbage` | admin |

Everyone reads the whole KB. Users and teams are values (`cartographer.auth.users`,
`cartographer.auth.teams`); a new user needs a `TOKEN_<ID>` key and a restart.
`log_append` (the session log) needs whole-KB access, so it is refused for non-admin tokens;
that is expected in this PoC.

## First-time set-up (operator)

1. **KB repository.** Add a deploy key with write access to `giantswarm/agent-mem-experiments-kb`,
   then turn it into a KB on branch `kb` (needs the `cartographer` v0.28.0 binary from the
   [release](https://github.com/BeppeTemp/cartographer/releases/tag/v0.28.0), checked against
   `sha256sums.txt`):

   ```bash
   scripts/kb-bootstrap.sh --cartographer ./cartographer --dry-run
   scripts/kb-bootstrap.sh --cartographer ./cartographer
   ```

2. **Fleet.** Merge the workload-clusters-fleet pull request with the HelmReleases and the
   SOPS Secrets. Check both pods are ready on `gazelle/operations`.

3. **Smoke test agentmemory.**

   ```bash
   AGENTMEMORY_URL=https://agentmemory.operations.awsprod.gigantic.io \
   AGENTMEMORY_SECRET=... scripts/agentmemory-smoke-test.sh
   ```

4. **Import the sample** with [`scripts/import`](scripts/import/README.md): 33 screened files
   from `company/`, `topics/` and `teams/`; nothing from `customers/` or `people/`.

## Connecting your agent (testers)

You get two secrets privately: the agentmemory secret (shared) and your own Cartographer
token. Keep both in 1Password; never commit them or paste them into a chat.

### agentmemory

**Claude Code** (0.9.32 plugin). Stop any local agentmemory daemon first, then:

```
/plugin marketplace add rohitg00/agentmemory
/plugin install agentmemory
```

When the plugin asks for its settings:

- server URL: `https://agentmemory.operations.awsprod.gigantic.io`
- server secret: the agentmemory secret (kept in the system credential store)

**Claude Desktop.** Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "agentmemory": {
      "command": "npx",
      "args": ["-y", "@agentmemory/mcp@0.9.32"],
      "env": {
        "AGENTMEMORY_URL": "https://agentmemory.operations.awsprod.gigantic.io",
        "AGENTMEMORY_SECRET": "<agentmemory secret>",
        "AGENTMEMORY_FORCE_PROXY": "1"
      }
    }
  }
}
```

### Cartographer

**Claude Code**, tools only:

```bash
claude mcp add --transport http --scope user gs-memory \
  https://cartographer.operations.awsprod.gigantic.io/mcp/routed \
  --header "Authorization: Bearer <your Cartographer token>"
```

**Claude Code** with Cartographer's skills and hooks (needs the `cartographer` binary; writes
skills to `~/.claude/skills/`, a block in `~/.claude/CLAUDE.md` and a 0600 token file):

```bash
export CARTOGRAPHER_TOKEN=<your Cartographer token>
cartographer connect claude --server-url https://cartographer.operations.awsprod.gigantic.io/mcp \
  --auth --token-env CARTOGRAPHER_TOKEN --no-input
```

**Claude Desktop.** Custom connectors take OAuth only, and Cartographer has bearer tokens,
so use a local stdio bridge (not yet tested):

```json
{
  "mcpServers": {
    "gs-memory": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://cartographer.operations.awsprod.gigantic.io/mcp/routed",
               "--header", "Authorization:${AUTH_HEADER}"],
      "env": { "AUTH_HEADER": "Bearer <your Cartographer token>" }
    }
  }
}
```

### What to try

- Ask your agent about something in the sample (Kyverno PSS and debug pods, Flux multi-tenancy,
  muster workflows) and check which server answers better.
- Save a fact from your session: in agentmemory through the hooks or `memory_save`; in
  Cartographer into your team's map or `topics`. Try writing to another team's map: it must be
  refused.
- Note what was useful, what was noise and what was missing.

## Development

See [docs/chart-conventions.md](docs/chart-conventions.md). After changing `values.yaml`,
regenerate `values.schema.json` and the chart README with pre-commit
(`pre-commit run -a`). `ci/ci-values.yaml` renders both components for the CI checks.
