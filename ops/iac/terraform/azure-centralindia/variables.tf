variable "prefix" {
  description = "Short name prefix for every resource (e.g. fsp)."
  type        = string
  default     = "fsp"
}

variable "location" {
  description = "Azure region. Data residency: keep client data in India (Central India / South India)."
  type        = string
  default     = "centralindia"
  validation {
    condition     = contains(["centralindia", "southindia", "westindia"], var.location)
    error_message = "FS-Platform data must stay in an India region."
  }
}

variable "admin_ssh_public_key" {
  description = "SSH public key of the platform administrators (break-glass access is separate)."
  type        = string
}

variable "admin_cidrs" {
  description = "Office / VPN networks allowed to reach SSH and RDP (no public management ports)."
  type        = list(string)
}

variable "app_vm_size" {
  description = "Linux application node size (04 §8: 8 vCPU / 16 GB minimum)."
  type        = string
  default     = "Standard_D8s_v5"
}

variable "data_vm_size" {
  description = "Linux data node size (PostgreSQL + MinIO in the single-node-per-role layout)."
  type        = string
  default     = "Standard_E8s_v5"
}

variable "render_vm_size" {
  description = "Windows render worker size (Excel; one Excel per job)."
  type        = string
  default     = "Standard_D4s_v5"
}

variable "windows_admin_password" {
  description = "Initial local administrator password for the render VM (rotate after domain join)."
  type        = string
  sensitive   = true
}

variable "backup_immutability_days" {
  description = "Object-lock (immutability) period of the off-site backup container (07 §3: 30 days)."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  description = "Security / access log retention in India (CERT-In: at least 180 days)."
  type        = number
  default     = 180
  validation {
    condition     = var.log_retention_days >= 180
    error_message = "CERT-In requires at least 180 days of log retention."
  }
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default = {
    system      = "fs-platform"
    environment = "prod"
    data_class  = "client-confidential"
  }
}
