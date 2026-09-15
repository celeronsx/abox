variable "cluster_name" {
  description = "Cluster Name"
  type        = string
  default     = "abox"
}

variable "oci_registry" {
  description = "OCI registry base URL"
  type        = string
  default     = "oci://ghcr.io/celeronsx/abox"
}

variable "releases_artifact" {
  description = "OCI repository holding the releases artifact, under var.oci_registry"
  type        = string
  # main publishes to "releases". A v* tag cut from a feature branch would land
  # in that same stream -- the RSIP filter has limit 1, so the newest tag from
  # any branch would win and a cluster bootstrapped from main would get that
  # branch's bundle. .github/workflows/flux-push.yaml derives the name per
  # branch; a feature branch sets this to match.
  default = "releases"
}

variable "releases_version" {
  description = "Default tag for releases OCI artifact bootstrap"
  type        = string
  default     = "0.1.0"
}

variable "flux_operator_version" {
  description = "flux-operator Helm chart version. Unset in the module defaults, which floats to latest."
  type        = string
  default     = "0.59.0"
}

variable "bootstrap_revision" {
  description = "Bump to force the flux-operator bootstrap Job to re-run without an input change"
  type        = number
  default     = 1
}
