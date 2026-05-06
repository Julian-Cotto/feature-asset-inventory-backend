variable "name_prefix" {
  type        = string
  description = "Prefix used for generated resource names."
}

variable "environment" {
  type        = string
  description = "Environment name such as dev, test, or prod."
}

variable "location" {
  type        = string
  description = "Azure region."
}

variable "resource_group_name" {
  type        = string
  description = "Name for this feature's resource group. Created by Terraform when create_resource_group is true (default); must already exist when create_resource_group is false."
}

variable "create_resource_group" {
  type        = bool
  description = "When true (default), create azurerm_resource_group named resource_group_name in var.location. Set false to use an existing resource group."
  default     = true
}

variable "container_environment_id" {
  type        = string
  description = "Azure Container Apps environment resource ID."
  default     = ""
}

variable "log_analytics_workspace_id" {
  type        = string
  description = "Optional Log Analytics Workspace ID associated with the shared Container Apps environment."
  default     = ""
}

variable "tags" {
  type        = map(string)
  description = "Common tags applied to resources."
  default     = {}
}

variable "backend_enabled" {
  type        = bool
  description = "Whether backend API deployment is enabled."
  default     = true
}

variable "backend_image" {
  type        = string
  description = "Container image for the backend API."
  default     = ""
}

variable "api_container_app_name" {
  type        = string
  description = "Optional explicit API container app name."
  default     = ""
}

variable "api_port" {
  type        = number
  description = "Port exposed by the API container app."
  default     = 8000
}

variable "scheduled_jobs" {
  type        = list(any)
  description = "Declared scheduled jobs for this feature."
  default     = []
}

variable "scheduled_job_images" {
  type        = map(string)
  description = "Map of scheduled job name => container image."
  default     = {}
}

variable "scheduled_job_container_app_names" {
  type        = map(string)
  description = "Map of scheduled job name => container app job name."
  default     = {}
}

variable "event_driven_jobs" {
  type        = list(any)
  description = "Declared event-driven worker jobs for this feature."
  default     = []
}

variable "event_worker_images" {
  type        = map(string)
  description = "Map of event-driven worker name => container image."
  default     = {}
}

variable "event_worker_container_app_names" {
  type        = map(string)
  description = "Map of event-driven worker name => container app job name."
  default     = {}
}

variable "event_listeners" {
  type        = list(any)
  description = "Declared event listeners for this feature."
  default     = []
}

variable "listener_images" {
  type        = map(string)
  description = "Map of event listener name => container image."
  default     = {}
}

variable "listener_container_app_names" {
  type        = map(string)
  description = "Map of event listener name => container app name."
  default     = {}
}

variable "auth_mode" {
  description = "Authentication mode for the backend API and related runtime components."
  type        = string
  default     = "mock"
}

variable "auth_debug_headers_enabled" {
  description = "Allow debug auth headers in mock mode."
  type        = bool
  default     = true
}

variable "auth_required_permissions_raw" {
  description = "Comma-separated permissions required by this feature backend."
  type        = string
  default     = "asset-inventory.view"
}

variable "entra_tenant_id" {
  description = "Microsoft Entra tenant ID."
  type        = string
  default     = ""
}

variable "entra_client_id" {
  description = "Microsoft Entra application/client ID used by the backend API."
  type        = string
  default     = ""
}

variable "entra_audience" {
  description = "Expected Entra audience for backend token validation."
  type        = string
  default     = ""
}

variable "entra_issuer" {
  description = "Expected Entra issuer override for backend token validation."
  type        = string
  default     = ""
}

variable "entra_jwks_url" {
  description = "JWKS endpoint override for backend token validation."
  type        = string
  default     = ""
}

variable "entra_clock_skew_seconds" {
  description = "Clock skew tolerance for token validation."
  type        = number
  default     = 60
}

variable "frontend_auth_mode" {
  description = "Authentication mode passed to the frontend build/runtime."
  type        = string
  default     = "mock"
}

variable "frontend_api_base_url" {
  description = "API base URL exposed to the frontend."
  type        = string
  default     = ""
}

variable "cache_backend" {
  description = "Cache backend for the feature service."
  type        = string
  default     = "memory"
}
