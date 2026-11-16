{{/*
Cartographer helpers. Called with the root context ($) unless noted.
*/}}

{{/* Image reference: registry/name:tag, with @digest when set. */}}
{{- define "agent-mem.cartographer.image" -}}
{{- $i := .Values.cartographer.image -}}
{{- $ref := printf "%s/%s:%s" $i.registry $i.name (required "cartographer.image.tag is required" $i.tag) -}}
{{- if $i.digest -}}
{{- $ref = printf "%s@%s" $ref $i.digest -}}
{{- end -}}
{{- $ref -}}
{{- end -}}

{{/* Environment variable holding a user's token: TOKEN_<ID>, "-" as "_". Called with the id. */}}
{{- define "agent-mem.cartographer.tokenEnv" -}}
{{- printf "TOKEN_%s" (. | upper | replace "-" "_") -}}
{{- end -}}

{{/* Map written by a team: team-<slug>. Called with the slug. A role's map selector cannot hold "/". */}}
{{- define "agent-mem.cartographer.teamMap" -}}
{{- printf "team-%s" . -}}
{{- end -}}

{{/* Fails the render on values the server would refuse or misread. */}}
{{- define "agent-mem.cartographer.validate" -}}
{{- $c := .Values.cartographer -}}
{{- $_ := required "cartographer.auth.existingSecret is required (Secret with one TOKEN_<ID> key per user)" $c.auth.existingSecret -}}
{{- $_ := required "cartographer.credentials.existingSecret is required (Secret with default.ssh, known_hosts, audit.key)" $c.credentials.existingSecret -}}
{{- $_ := required "cartographer.kb.name is required" $c.kb.name -}}
{{- $_ := required "cartographer.kb.remote is required" $c.kb.remote -}}
{{- if not $c.auth.users -}}
{{- fail "cartographer.auth.users must list at least one user" -}}
{{- end -}}
{{- if and $c.ui.exposed (not $c.ui.enabled) -}}
{{- fail "cartographer.ui.exposed needs cartographer.ui.enabled" -}}
{{- end -}}
{{- $ids := dict -}}
{{- range $c.auth.users -}}
{{- if not (regexMatch "^[a-z][a-z0-9-]{0,62}$" (toString .id)) -}}
{{- fail (printf "cartographer.auth.users: id %q must match ^[a-z][a-z0-9-]*$" (toString .id)) -}}
{{- end -}}
{{- if hasKey $ids .id -}}
{{- fail (printf "cartographer.auth.users: duplicate id %q" .id) -}}
{{- end -}}
{{- $_ := set $ids .id true -}}
{{- if and .admin (or .teams .writeMaps) -}}
{{- fail (printf "cartographer.auth.users: %q is admin; teams and writeMaps do not apply" .id) -}}
{{- end -}}
{{- $user := .id -}}
{{- range .teams -}}
{{- if not (has . $c.auth.teams) -}}
{{- fail (printf "cartographer.auth.users: %q names team %q, which is not in cartographer.auth.teams" $user .) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
