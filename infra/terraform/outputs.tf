output "resource_group_id" {
  description = "Azure resource group resource ID when this stack created the RG (default); null if create_resource_group is false."
  value       = length(azurerm_resource_group.feature) > 0 ? azurerm_resource_group.feature[0].id : null
}

output "resource_group_name" {
  description = "Resource group name used for this feature (created or existing)."
  value       = local.effective_resource_group_name
}

output "feature_identity_id" {
  description = "User-assigned identity resource ID for the feature."
  value       = azurerm_user_assigned_identity.feature.id
}

output "feature_identity_principal_id" {
  description = "Principal ID of the feature user-assigned identity."
  value       = azurerm_user_assigned_identity.feature.principal_id
}

output "api_container_app_id" {
  description = "Resource ID of the API container app."
  value       = length(azurerm_container_app.api) > 0 ? azurerm_container_app.api[0].id : null
}

output "api_container_app_name" {
  description = "Name of the API container app."
  value       = length(azurerm_container_app.api) > 0 ? azurerm_container_app.api[0].name : null
}

output "scheduled_job_ids" {
  description = "Resource IDs of scheduled container app jobs."
  value       = { for k, v in azurerm_container_app_job.scheduled : k => v.id }
}

output "scheduled_job_names" {
  description = "Names of scheduled container app jobs."
  value       = { for k, v in azurerm_container_app_job.scheduled : k => v.name }
}

output "event_worker_job_ids" {
  description = "Resource IDs of event-driven worker container app jobs."
  value       = { for k, v in azurerm_container_app_job.event_worker : k => v.id }
}

output "event_worker_job_names" {
  description = "Names of event-driven worker container app jobs."
  value       = { for k, v in azurerm_container_app_job.event_worker : k => v.name }
}

output "listener_container_app_ids" {
  description = "Resource IDs of listener container apps."
  value       = { for k, v in azurerm_container_app.listener : k => v.id }
}

output "listener_container_app_names" {
  description = "Names of listener container apps."
  value       = { for k, v in azurerm_container_app.listener : k => v.name }
}

output "observability_enabled" {
  description = "Whether centralized logging metadata was provided to this feature deployment."
  value       = local.observability_enabled
}

output "log_analytics_workspace_id" {
  description = "Log Analytics Workspace ID associated with the shared Container Apps environment, when provided."
  value       = var.log_analytics_workspace_id
}