# Chart conventions (agent-mem-experiments)

One chart, `helm/agent-mem-experiments`, generated CI publishes it (and the root
`Dockerfile` image) to gsoci. It is installed once per component, with only that
component enabled:

- release `agentmemory`  -> `agentmemory.enabled: true`
- release `cartographer` -> `cartographer.enabled: true`

## Layout

- `templates/<component>/*.yaml`, every file wrapped in `{{- if .Values.<component>.enabled }}`.
- Values under the component key in `values.yaml`; shared values at the top level (`gateway`).
- Names: `{{ include "agent-mem.fullname" (dict "root" $ "component" "<component>") }}`.
- Labels: `agent-mem.labels`, selectors: `agent-mem.selectorLabels` (same dict). They add
  `application.giantswarm.io/team` from Chart.yaml and `app.kubernetes.io/component`.
- `values.schema.json` is generated from `values.yaml` by helm-values-schema-json
  (`.schema.yaml`, `noAdditionalProperties: true`): annotate values with `# @schema`
  comments where the type is not inferable (e.g. `[]` lists, `{}` maps, nullable).

## Platform requirements (gazelle / operations)

- Pod Security Standards restricted (Kyverno): pod `runAsNonRoot`, non-zero `runAsUser`,
  `seccompProfile: RuntimeDefault`; every container (init too) `allowPrivilegeEscalation: false`,
  `capabilities.drop: [ALL]`, `readOnlyRootFilesystem: true`, `runAsNonRoot: true`.
- Every `emptyDir` has a `sizeLimit` (`require-emptydir-requests-and-limits`).
- No `:latest` image tags (`disallow-latest-tag`); pin tags (ideally digests).
- Volumes only: configMap, secret, emptyDir, persistentVolumeClaim, projected, downwardAPI, csi, ephemeral.
- Storage class: cluster default (`gp3`); single replica + `Recreate` for single-writer stores.
- Exposure: Gateway API `HTTPRoute` to `giantswarm-default` in `envoy-gateway-system`,
  `sectionName: https`, hostname `<component>.operations.awsprod.gigantic.io`.
  Optional `BackendTrafficPolicy` (gateway.envoyproxy.io/v1alpha1) for timeouts.
- NetworkPolicy: ingress only from `envoy-gateway-system` on the app port; egress DNS + 443.
- Secrets are never rendered from values in production; charts reference `existingSecret`
  (created from SOPS files in workload-clusters-fleet). A `secret.create` path is for CI/tests only.
- `automountServiceAccountToken: false`.
