{{/*
Name helpers (the conventional chart boilerplate).
*/}}
{{- define "presence.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "presence.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "presence.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "presence.labels" -}}
helm.sh/chart: {{ include "presence.chart" . }}
app.kubernetes.io/name: {{ include "presence.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Per-component selector labels. Call as
`include "presence.selectorLabels" (dict "ctx" . "component" "web")`.
The component label is what keeps the web Service from also selecting the
runner pods — the runner serves no HTTP and must never receive traffic.
*/}}
{{- define "presence.selectorLabels" -}}
app.kubernetes.io/name: {{ include "presence.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
The application image, shared by the web role, the runner role and the
runner's init container so a release can never run mixed versions.
*/}}
{{- define "presence.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}

{{/*
Secret holding every sensitive value. Either the one this chart renders or
the operator's own (django.existingSecret).
*/}}
{{- define "presence.secretName" -}}
{{- if .Values.django.existingSecret }}
{{- .Values.django.existingSecret }}
{{- else }}
{{- printf "%s-secret" (include "presence.fullname" .) }}
{{- end }}
{{- end }}

{{- define "presence.compressionName" -}}
{{- printf "%s-compress" (include "presence.fullname" .) }}
{{- end }}

{{/*
Annotations for the Ingress: the operator's own, plus the cert-manager
issuer reference and the compress middleware reference when those apply.
Anything the operator set explicitly wins in both cases.

The middleware is named `<namespace>-<name>@kubernetescrd`, the flat
provider-qualified form this annotation takes. The `<namespace>/<name>` form
belongs to an IngressRoute's structured `middlewares` list and resolves to
nothing here — whereupon Traefik drops the router from every entrypoint and
the Ingress serves nothing at all, rather than merely serving uncompressed.

The annotation's value is a comma-separated list, so an operator-supplied
reference (an auth middleware, say) is appended to rather than overwritten.
*/}}
{{- define "presence.ingressAnnotations" -}}
{{- $annotations := deepCopy (default dict .Values.ingress.annotations) -}}
{{- $issuerKey := "cert-manager.io/cluster-issuer" -}}
{{- if and .Values.ingress.tls.enabled .Values.ingress.tls.clusterIssuer -}}
{{- if not (hasKey $annotations $issuerKey) -}}
{{- $_ := set $annotations $issuerKey .Values.ingress.tls.clusterIssuer -}}
{{- end -}}
{{- end -}}
{{- if .Values.compression.enabled -}}
{{- $key := "traefik.ingress.kubernetes.io/router.middlewares" -}}
{{- $ref := printf "%s-%s@kubernetescrd" .Release.Namespace (include "presence.compressionName" .) -}}
{{- $existing := default "" (get $annotations $key) -}}
{{- if $existing -}}
{{- $ref = printf "%s,%s" $existing $ref -}}
{{- end -}}
{{- $_ := set $annotations $key $ref -}}
{{- end -}}
{{- toYaml $annotations -}}
{{- end }}

{{- define "presence.postgresql.fullname" -}}
{{- printf "%s-postgresql" (include "presence.fullname" .) }}
{{- end }}

{{/*
Database connection values, from the bundled StatefulSet or the external
server. DJANGO_DB_HOST being non-empty is what selects PostgreSQL over
SQLite in settings.resolve_databases, so it must always be set here.
*/}}
{{- define "presence.db.host" -}}
{{- if .Values.postgresql.enabled }}
{{- include "presence.postgresql.fullname" . }}
{{- else }}
{{- required "externalDatabase.host is required when postgresql.enabled is false" .Values.externalDatabase.host }}
{{- end }}
{{- end }}

{{- define "presence.db.port" -}}
{{- if .Values.postgresql.enabled }}5432{{ else }}{{ .Values.externalDatabase.port }}{{ end }}
{{- end }}

{{- define "presence.db.name" -}}
{{- if .Values.postgresql.enabled }}{{ .Values.postgresql.database }}{{ else }}{{ .Values.externalDatabase.database }}{{ end }}
{{- end }}

{{- define "presence.db.user" -}}
{{- if .Values.postgresql.enabled }}{{ .Values.postgresql.username }}{{ else }}{{ .Values.externalDatabase.username }}{{ end }}
{{- end }}

{{- define "presence.db.password" -}}
{{- if .Values.postgresql.enabled }}{{ .Values.postgresql.password }}{{ else }}{{ .Values.externalDatabase.password }}{{ end }}
{{- end }}

{{/*
The host name kubelet's HTTP probes send. Probes address the pod by IP,
which ALLOWED_HOSTS would reject with a 400 (a failed probe), so they send
this instead — and it is always in the allowed list below.
*/}}
{{- define "presence.probeHost" -}}
localhost
{{- end }}

{{/*
ALLOWED_HOSTS: the probe hosts, the ingress host when there is one, and
anything the operator adds.
*/}}
{{- define "presence.allowedHosts" -}}
{{- $hosts := list (include "presence.probeHost" .) "127.0.0.1" "[::1]" -}}
{{- if .Values.ingress.enabled -}}
{{- $hosts = append $hosts .Values.ingress.host -}}
{{- end -}}
{{- $hosts = concat $hosts .Values.django.extraAllowedHosts -}}
{{- join "," (uniq $hosts) -}}
{{- end }}

{{/*
The `:port` suffix a CSRF trusted origin needs, empty when the site is
published on the default port for the scheme it serves (443 with TLS, 80
without).

Django compares its trusted origins against the browser's Origin header
verbatim, and a browser writes the port there only when it is not that
default — so `:8443` has to appear and `:443` must not.
*/}}
{{- define "presence.publicPortSuffix" -}}
{{- $port := int .Values.ingress.publicPort -}}
{{- $default := ternary 443 80 .Values.ingress.tls.enabled -}}
{{- if and $port (ne $port $default) -}}
{{- printf ":%d" $port -}}
{{- end -}}
{{- end }}

{{/*
CSRF_TRUSTED_ORIGINS. Django needs the scheme, and an HTTPS origin must be
listed explicitly even though its host is already in ALLOWED_HOSTS —
otherwise the login POST fails the origin check. ALLOWED_HOSTS deliberately
carries no port: Django strips it before matching there.
*/}}
{{- define "presence.csrfTrustedOrigins" -}}
{{- $origins := list -}}
{{- if .Values.ingress.enabled -}}
{{- $host := printf "%s%s" .Values.ingress.host (include "presence.publicPortSuffix" .) -}}
{{- $origins = append $origins (printf "https://%s" $host) -}}
{{- if not .Values.ingress.tls.enabled -}}
{{- $origins = append $origins (printf "http://%s" $host) -}}
{{- end -}}
{{- end -}}
{{- $origins = concat $origins .Values.django.extraCsrfTrustedOrigins -}}
{{- join "," (uniq $origins) -}}
{{- end }}

{{/*
Environment shared by every role (web, runner, and the runner's init
container). Sensitive values are always read from the Secret, never
rendered into the pod spec.
*/}}
{{- define "presence.commonEnv" -}}
- name: DJANGO_DEBUG
  value: {{ ternary "True" "False" .Values.django.debug | quote }}
- name: DJANGO_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "presence.secretName" . }}
      key: django-secret-key
- name: DJANGO_DB_HOST
  value: {{ include "presence.db.host" . | quote }}
- name: DJANGO_DB_PORT
  value: {{ include "presence.db.port" . | quote }}
- name: DJANGO_DB_NAME
  value: {{ include "presence.db.name" . | quote }}
- name: DJANGO_DB_USER
  value: {{ include "presence.db.user" . | quote }}
- name: DJANGO_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "presence.secretName" . }}
      key: db-password
{{- end }}

{{/*
Scheduling constraints, applied identically to every pod the chart creates.
Call sites wrap this in `with` so an unconstrained release renders no stray
blank line where the block would have been.
*/}}
{{- define "presence.scheduling" -}}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}
