{{/* Common names, labels and images for the fs-platform chart. */}}
{{- define "fs-platform.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "fs-platform.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "fs-platform.labels" -}}
app.kubernetes.io/name: {{ include "fs-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}

{{- define "fs-platform.selector" -}}
app.kubernetes.io/name: {{ include "fs-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "fs-platform.image" -}}
{{- $root := index . 0 -}}
{{- $img := index . 1 -}}
{{- if $img.digest -}}
{{- printf "%s/%s@%s" $root.Values.image.registry $img.repository $img.digest -}}
{{- else -}}
{{- printf "%s/%s:%s" $root.Values.image.registry $img.repository $img.tag -}}
{{- end -}}
{{- end -}}

{{- define "fs-platform.envFrom" -}}
- configMapRef:
    name: {{ include "fs-platform.fullname" . }}-config
{{- end -}}

{{/* Secret-backed variables every application container needs (never the AI key). */}}
{{- define "fs-platform.secretKeys" -}}
FSWEB_SECRET_KEY FSWEB_KMS_MASTER_KEY FSWEB_DATABASE_URL FSWEB_APP_DATABASE_URL FSWEB_S3_ACCESS_KEY FSWEB_S3_SECRET_KEY FSWEB_WORKER_TOKEN FSWEB_PKG_SIGNING_KEY FSWEB_OIDC_CLIENT_SECRET FSWEB_REDIS_URL
{{- end -}}

{{/* Render env entries for (list $root $keys). */}}
{{- define "fs-platform.secretEnvFor" -}}
{{- $root := index . 0 -}}
{{- range $key := index . 1 }}
- name: {{ $key }}
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.existingSecret }}
      key: {{ $key }}
      optional: true
{{- end }}
{{- end -}}

{{- define "fs-platform.secretEnv" -}}
{{- include "fs-platform.secretEnvFor" (list . (splitList " " (include "fs-platform.secretKeys" .))) -}}
{{- end -}}
