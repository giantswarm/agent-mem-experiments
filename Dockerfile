# agentmemory server image for the agentmemory component of
# helm/agent-mem-experiments.
#
# CI builds it for linux/amd64 and linux/arm64 and pushes it to
# gsoci.azurecr.io/giantswarm/agent-mem-experiments, tagged with the release
# version; the chart defaults agentmemory.image.tag to that appVersion.
#
# Contents: agentmemory from npm, the iii engine, and the iii `state` and `http`
# workers, all pinned and SHA-256 verified per TARGETARCH. Baking the workers
# in means the pod never downloads them from the iii worker registry.
#
# Differences from upstream deploy/Dockerfile:
#   - no root entrypoint, gosu or chown: the image runs as UID 1000 and the
#     chart starts `iii compose` itself (start.sh in the runtime ConfigMap),
#     so it passes Pod Security Standards "restricted" with a read-only root
#     filesystem;
#   - installs the published npm package instead of building from source.
#
# Local build:
#   docker buildx build --platform linux/amd64,linux/arm64 .
#
# Bump all pins together, from the agentmemory release:
#   AGENTMEMORY_VERSION  package.json version
#   III_VERSION          package.json "iii-sdk", hashes in src/cli/engine-install.ts
#   STATE/HTTP hashes    COMPOSE_WORKER_VERSIONS in src/cli/compose-project.ts,
#                        hashes in the iii-hq/workers release notes (WORKERS_BUILD)

ARG AGENTMEMORY_VERSION=0.9.32
ARG III_VERSION=0.25.0
ARG WORKERS_BUILD=76a005d77a9c9f12067104ea5eb9f860e8590b5d

# Downloads and checks only, so it runs on the build platform without emulation.
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS fetch
ARG TARGETARCH
ARG III_VERSION
ARG WORKERS_BUILD
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /fetch
RUN set -eu; \
  case "${TARGETARCH:-amd64}" in \
    amd64) T=x86_64-unknown-linux-gnu; \
      III_SHA=4c4d14063eb55d79663db553e960497731420df6d78eca3aafe670e2fbaba8e2; \
      STATE_SHA=8dabdbf08abb356c64df3a48df1d04a1a6f6bcbc5d88b63562485068df0091d7; \
      HTTP_SHA=9af3dc67977ad69eb7a77a6471772ac0fae7e7f293c08a7be5d6464afe7dd5a2 ;; \
    arm64) T=aarch64-unknown-linux-gnu; \
      III_SHA=5e52d4738a0beb609f4d32970d37be7c00d4848ee9d06300248498a00ea4bf0e; \
      STATE_SHA=6dcfe91ffc7ec7892f11b6a0fbdb29ae4d939935c0ac1c955159f06acad4f502; \
      HTTP_SHA=1703aa7ea408422dda2c91a8b037fc0cc0f7b8a1b95416bb5882b2f919b3e26f ;; \
    *) echo "unsupported TARGETARCH ${TARGETARCH}" >&2; exit 1 ;; \
  esac; \
  W="https://github.com/iii-hq/workers/releases/download/build-${WORKERS_BUILD}"; \
  curl -fsSL -o iii.tgz "https://github.com/iii-hq/iii/releases/download/iii/v${III_VERSION}/iii-${T}.tar.gz"; \
  curl -fsSL -o state.tgz "${W}/state-${T}.tar.gz"; \
  curl -fsSL -o http.tgz "${W}/http-${T}.tar.gz"; \
  printf '%s  iii.tgz\n%s  state.tgz\n%s  http.tgz\n' "$III_SHA" "$STATE_SHA" "$HTTP_SHA" | sha256sum -c -; \
  mkdir -p bin workers; \
  tar xzf iii.tgz -C bin; tar xzf state.tgz -C workers; tar xzf http.tgz -C workers; \
  chmod 0755 bin/iii workers/state workers/http

FROM node:22-bookworm-slim
ARG AGENTMEMORY_VERSION
ARG III_VERSION

COPY --from=fetch /fetch/bin/iii /usr/local/bin/iii
COPY --from=fetch /fetch/workers/ /opt/iii-workers/
RUN iii --version 2>&1 | grep -qF "${III_VERSION}" \
 || { echo "iii binary is not ${III_VERSION}" >&2; exit 1; }

WORKDIR /opt/agentmemory
RUN printf '{"name":"agentmemory-image","version":"1.0.0","private":true}\n' > package.json \
 && npm install "@agentmemory/agentmemory@${AGENTMEMORY_VERSION}" --omit=optional --no-fund --no-audit \
 && npm cache clean --force \
 && test "$(node -p "require('./node_modules/iii-sdk/package.json').version")" = "${III_VERSION}"

ENV AGENTMEMORY_DATA_DIR=/data \
    HOME=/home/node \
    III_TELEMETRY_ENABLED=false \
    MALLOC_ARENA_MAX=2

USER 1000:1000
EXPOSE 3111
# The chart overrides the command with its start.sh; this is for ad-hoc runs.
ENTRYPOINT ["/opt/agentmemory/node_modules/.bin/agentmemory"]
