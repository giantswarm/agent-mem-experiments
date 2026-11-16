# agent-mem-experiments

PoC of shared agent memory servers (agentmemory and Cartographer) for Claude Code, Claude Desktop and Gemini. One component per release.

**Homepage:** <https://github.com/giantswarm/agent-mem-experiments>

## Source Code

* <https://github.com/giantswarm/agent-mem-experiments>
* <https://github.com/rohitg00/agentmemory>
* <https://github.com/BeppeTemp/cartographer>

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| gateway.parentRefs | list | `[{"name":"giantswarm-default","namespace":"envoy-gateway-system","sectionName":"https"}]` | Gateway API parent references for the components' HTTPRoutes. |
| agentmemory.enabled | bool | `false` | Render the agentmemory component. |
| agentmemory.image | object | `{"name":"giantswarm/agent-mem-experiments","pullPolicy":"IfNotPresent","registry":"gsoci.azurecr.io","tag":""}` | agentmemory image, built from the root Dockerfile. |
| agentmemory.image.tag | string | `""` | Defaults to the chart's appVersion. |
| agentmemory.imagePullSecrets | list | `[]` |  |
| agentmemory.replicaCount | int | `1` | Single writer on a ReadWriteOnce volume: 0 or 1. |
| agentmemory.teamId | string | `"gs-ai-test"` | Team scope (server-wide; every share is recorded as userId). "" disables team mode. |
| agentmemory.userId | string | `"shared"` |  |
| agentmemory.llmProvider | string | `"gemini"` | LLM provider: gemini, or "" for none (no summaries, compression, consolidation). A key alone does not enable a provider since 0.9.32. |
| agentmemory.embeddingProvider | string | `"gemini"` | Embedding provider: gemini (768 dims), or "" for BM25 + graph only. |
| agentmemory.gemini.model | string | `"gemini-3.7-flash"` |  |
| agentmemory.autoCompress | bool | `true` |  |
| agentmemory.injectContext | bool | `true` |  |
| agentmemory.consolidation | bool | `false` |  |
| agentmemory.graphExtraction | bool | `false` |  |
| agentmemory.tools | string | `"all"` | MCP tool surface for every client: all or core (8 tools). Decided on the server. |
| agentmemory.extraEnv | list | `[]` | Extra plain env for the agentmemory worker, written into the compose manifest. Secrets do not belong here. |
| agentmemory.secret.existingSecret | string | `""` | Name of an existing Secret (production; created from SOPS in workload-clusters-fleet). |
| agentmemory.secret.create | bool | `false` | Render the Secret from the values below. CI and tests only. |
| agentmemory.secret.agentmemorySecret | string | `""` |  |
| agentmemory.secret.geminiApiKey | string | `""` |  |
| agentmemory.runtime | object | `{"nodeBin":"/usr/local/bin/node","packageDir":"/opt/agentmemory/node_modules/@agentmemory/agentmemory","workerBinDir":"/opt/iii-workers"}` | Paths inside the image (root Dockerfile). |
| agentmemory.valkey.enabled | bool | `true` |  |
| agentmemory.valkey.image | object | `{"name":"valkey/valkey","pullPolicy":"IfNotPresent","registry":"docker.io","tag":"8.1.11-alpine"}` | Third-party image (Docker Hub), not built or mirrored by Giant Swarm. Pin the full patch version; 8.1-alpine is a moving tag. |
| agentmemory.valkey.resources.requests.cpu | string | `"50m"` |  |
| agentmemory.valkey.resources.requests.memory | string | `"64Mi"` |  |
| agentmemory.valkey.resources.limits.memory | string | `"512Mi"` |  |
| agentmemory.valkey.securityContext | object | `{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"privileged":false,"readOnlyRootFilesystem":true,"runAsGroup":1000,"runAsNonRoot":true,"runAsUser":999,"seccompProfile":{"type":"RuntimeDefault"}}` | Runs as the image's valkey user (UID 999). |
| agentmemory.persistence.enabled | bool | `true` |  |
| agentmemory.persistence.storageClassName | string | `""` | "" uses the cluster default (gp3). |
| agentmemory.persistence.accessMode | string | `"ReadWriteOnce"` |  |
| agentmemory.persistence.size | string | `"5Gi"` |  |
| agentmemory.persistence.existingClaim | string | `""` | Use an existing PVC instead of creating one. |
| agentmemory.service.port | int | `3111` |  |
| agentmemory.httpRoute | object | `{"backendTrafficPolicy":{"enabled":true,"timeout":"0s"},"enabled":true,"hostnames":["agentmemory.operations.awsprod.gigantic.io"]}` | Gateway API HTTPRoute for /agentmemory on gateway.parentRefs. |
| agentmemory.httpRoute.backendTrafficPolicy | object | `{"enabled":true,"timeout":"0s"}` | Envoy Gateway BackendTrafficPolicy on the HTTPRoute. |
| agentmemory.httpRoute.backendTrafficPolicy.timeout | string | `"0s"` | Request timeout; 0s disables it (long-lived MCP streams). |
| agentmemory.networkPolicy.enabled | bool | `true` |  |
| agentmemory.networkPolicy.ingressNamespaces | list | `["envoy-gateway-system"]` | Namespaces allowed to reach port 3111. |
| agentmemory.networkPolicy.egressHttpsCidrs | list | `["0.0.0.0/0"]` | Egress HTTPS (443) destinations, for the Gemini API. DNS is always allowed. |
| agentmemory.resources.requests.cpu | string | `"250m"` |  |
| agentmemory.resources.requests.memory | string | `"512Mi"` |  |
| agentmemory.resources.limits.memory | string | `"1536Mi"` |  |
| agentmemory.podSecurityContext.runAsNonRoot | bool | `true` |  |
| agentmemory.podSecurityContext.runAsUser | int | `1000` |  |
| agentmemory.podSecurityContext.runAsGroup | int | `1000` |  |
| agentmemory.podSecurityContext.fsGroup | int | `1000` |  |
| agentmemory.podSecurityContext.fsGroupChangePolicy | string | `"OnRootMismatch"` |  |
| agentmemory.podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| agentmemory.securityContext.allowPrivilegeEscalation | bool | `false` |  |
| agentmemory.securityContext.privileged | bool | `false` |  |
| agentmemory.securityContext.runAsNonRoot | bool | `true` |  |
| agentmemory.securityContext.runAsUser | int | `1000` |  |
| agentmemory.securityContext.runAsGroup | int | `1000` |  |
| agentmemory.securityContext.readOnlyRootFilesystem | bool | `true` |  |
| agentmemory.securityContext.capabilities.drop[0] | string | `"ALL"` |  |
| agentmemory.securityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| agentmemory.podAnnotations | object | `{}` |  |
| agentmemory.podLabels | object | `{}` |  |
| agentmemory.nodeSelector | object | `{}` |  |
| agentmemory.tolerations | list | `[]` |  |
| agentmemory.affinity | object | `{}` |  |
| cartographer.enabled | bool | `false` | Render the cartographer component. The release `cartographer` sets it. |
| cartographer.image.registry | string | `"ghcr.io"` |  |
| cartographer.image.name | string | `"beppetemp/cartographer"` | Upstream image (third-party, not mirrored by Giant Swarm). |
| cartographer.image.tag | string | `"v0.28.0"` | Release tag; never `latest`. |
| cartographer.image.digest | string | `""` | Optional digest (`sha256:...`), appended as `<tag>@<digest>`. |
| cartographer.image.pullPolicy | string | `"IfNotPresent"` |  |
| cartographer.kb.name | string | `"gs-memory"` | KB name: the role rules, the clone directory and the credential file prefix (`<name>.ssh`). |
| cartographer.kb.remote | string | `"git@github.com:giantswarm/agent-mem-experiments-kb.git"` | Git remote cloned at start; every write is pushed to it. Must already hold an OKF KB (scripts/kb-bootstrap.sh). |
| cartographer.kb.branch | string | `"kb"` | Branch this server commits to (`kbs[].branch`); created on first push if missing. Giant Swarm repos protect the default branch (PR reviews), so writes go to their own branch. Empty follows the remote's default branch. |
| cartographer.gitIdentity | string | `"agent-mem-experiments PoC <noreply@giantswarm.io>"` | Author and committer of writes that carry no `Cartographer-Author` header (`git.identities.default`). The token id is recorded in the `Cartographer-Token` trailer of every write. |
| cartographer.auth.existingSecret | string | `""` | Existing Secret with one key per user, `TOKEN_<ID>` (id upper-cased, `-` as `_`). |
| cartographer.auth.teams | list | `["honeybadger","bumblebee","cabbage","planeteers"]` | Team slugs. Each team gets the map `team-<slug>` and a role `team-<slug>` with rw on it. |
| cartographer.auth.sharedWriteMaps | list | `["topics"]` | Maps every non-admin user writes, besides their team maps. |
| cartographer.auth.users | list | `[{"admin":true,"id":"admin"},{"id":"lukasz","teams":["honeybadger"]},{"id":"dominik","teams":["bumblebee"]},{"id":"puja","teams":["planeteers"],"writeMaps":["company"]}]` | Token holders. `admin: true` grants everything. Everyone else reads the whole KB and writes `sharedWriteMaps`, the maps of `teams`, and `writeMaps`. |
| cartographer.credentials.existingSecret | string | `""` | Existing Secret mounted as the credentials directory: `default.ssh` (or `<kb>.ssh`), `known_hosts` with github.com's host keys, and `audit.key` (`openssl rand -hex 32`). |
| cartographer.audit.mode | string | `"best_effort"` | `best_effort` logs a failed append and carries on; `required` refuses calls without the log. |
| cartographer.updateCheck | bool | `false` | Daily GitHub lookup of the latest release, reported in /health. Renovate tracks the tag instead. |
| cartographer.ui.enabled | bool | `true` | Serve the read-only Atlas UI (`/ui/`) and its API (`/api/ui/v1`, token required). |
| cartographer.ui.exposed | bool | `false` | Route `/ui` and `/api/ui` through the HTTPRoute. Needs `enabled`. |
| cartographer.persistence.size | string | `"5Gi"` | Size of the data volume (KB clone, index, audit log). |
| cartographer.persistence.storageClassName | string | `""` | Storage class; empty uses the cluster default. |
| cartographer.persistence.existingClaim | string | `""` | Use this claim instead of creating one. |
| cartographer.httpRoute | object | `{"annotations":{},"backendTrafficPolicy":{"enabled":true,"timeout":"0s"},"enabled":true,"hostnames":["cartographer.operations.awsprod.gigantic.io"]}` | Gateway API HTTPRoute for /mcp, the probes and (optionally) the UI, on gateway.parentRefs. |
| cartographer.httpRoute.annotations | object | `{}` | Annotations of the HTTPRoute. |
| cartographer.httpRoute.backendTrafficPolicy | object | `{"enabled":true,"timeout":"0s"}` | Envoy Gateway BackendTrafficPolicy on the HTTPRoute. |
| cartographer.httpRoute.backendTrafficPolicy.timeout | string | `"0s"` | Request timeout; 0s disables it (reindex and git sync have no fixed bound). |
| cartographer.networkPolicy.enabled | bool | `true` |  |
| cartographer.networkPolicy.ingressNamespaces | list | `["envoy-gateway-system"]` | Namespaces allowed to reach the app port (the Gateway's Envoy pods). |
| cartographer.networkPolicy.egressCIDRs | list | `["0.0.0.0/0"]` | Destinations of the git remote. Narrow to GitHub's `git` ranges (https://api.github.com/meta) once verified from the cluster. |
| cartographer.networkPolicy.egressPorts | list | `[22,443]` | Ports allowed to `egressCIDRs`: git over SSH and HTTPS. |
| cartographer.resources | object | `{"limits":{"memory":"1Gi"},"requests":{"cpu":"100m","memory":"256Mi"}}` | Container resources. |
| cartographer.tmpSizeLimit | string | `"256Mi"` | Size limit of the `/tmp` emptyDir (the root filesystem is read-only). |
| cartographer.terminationGracePeriodSeconds | int | `60` | HTTP drain (10s) plus the KB's pending push (10s), with margin. |
| cartographer.podAnnotations | object | `{}` |  |
| cartographer.nodeSelector | object | `{}` |  |
| cartographer.tolerations | list | `[]` |  |
| cartographer.affinity | object | `{}` |  |

## CI values

`ci/ci-values.yaml` holds the values the CI render (`helm template` in app-build-suite) needs beyond the defaults, such as a `required` value without a default.
