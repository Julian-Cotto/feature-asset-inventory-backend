locals {
  feature_name        = "asset-inventory"
  common_tags         = merge({ feature = local.feature_name, environment = var.environment, managed_by = "terraform" }, var.tags)

  scheduled_jobs_map  = { for item in var.scheduled_jobs : item.name => item }
  event_workers_map   = { for item in var.event_driven_jobs : item.name => item }
  event_listeners_map = { for item in var.event_listeners : item.name => item }

  effective_resource_group_name = var.create_resource_group ? azurerm_resource_group.feature[0].name : var.resource_group_name

  observability_enabled = var.log_analytics_workspace_id != ""
}

resource "azurerm_resource_group" "feature" {
  count    = var.create_resource_group ? 1 : 0
  name     = var.resource_group_name
  location = var.location
  tags     = local.common_tags
}

resource "null_resource" "observability_notice" {
  count = local.observability_enabled ? 1 : 0

  triggers = {
    log_analytics_workspace_id = var.log_analytics_workspace_id
    container_environment_id   = var.container_environment_id
  }

  provisioner "local-exec" {
    command = "echo 'Observability enabled: Container App logs are expected to flow through the shared Container Apps environment to Log Analytics.'"
  }
}

resource "azurerm_user_assigned_identity" "feature" {
  name                = "${var.name_prefix}-${var.environment}-id"
  location            = var.location
  resource_group_name = local.effective_resource_group_name
  tags                = local.common_tags
}

#
# API CONTAINER APP
#
resource "azurerm_container_app" "api" {
  count = var.backend_enabled && var.container_environment_id != "" && var.backend_image != "" ? 1 : 0

  name                         = var.api_container_app_name != "" ? var.api_container_app_name : "${var.name_prefix}-${var.environment}-api"
  container_app_environment_id = var.container_environment_id
  resource_group_name          = local.effective_resource_group_name
  revision_mode                = "Single"
  tags                         = local.common_tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.feature.id]
  }

  template {
    container {
      name   = "api"
      image  = var.backend_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "FEATURE_KEY"
        value = "asset-inventory"
      }

      env {
        name  = "FEATURE_API_BASE_PATH"
        value = "/api/inventory/it"
      }

      env {
        name  = "AUTH_MODE"
        value = var.auth_mode
      }

      env {
        name  = "REGISTRY_MODE"
        value = "rest"
      }

      env {
        name  = "WORKLOAD_KIND"
        value = "api"
      }

      env {
        name  = "AUTH_DEBUG_HEADERS_ENABLED"
        value = tostring(var.auth_debug_headers_enabled)
      }

      env {
        name  = "ENTRA_TENANT_ID"
        value = var.entra_tenant_id
      }

      env {
        name  = "ENTRA_CLIENT_ID"
        value = var.entra_client_id
      }

      env {
        name  = "ENTRA_AUDIENCE"
        value = var.entra_audience
      }

      env {
        name  = "ENTRA_ISSUER"
        value = var.entra_issuer
      }

      env {
        name  = "ENTRA_JWKS_URL"
        value = var.entra_jwks_url
      }

      env {
        name  = "ENTRA_CLOCK_SKEW_SECONDS"
        value = tostring(var.entra_clock_skew_seconds)
      }

      env {
        name  = "AUTH_REQUIRED_PERMISSIONS_RAW"
        value = var.auth_required_permissions_raw
      }

      env {
        name  = "APP_ENVIRONMENT"
        value = var.environment
      }

      env {
        name  = "LOG_ANALYTICS_WORKSPACE_ID"
        value = var.log_analytics_workspace_id
      }

      env {
        name  = "CACHE_BACKEND"
        value = var.cache_backend
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = var.api_port
    transport        = "auto"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}

#
# SCHEDULED JOBS -> CONTAINER APP JOBS
#
resource "azurerm_container_app_job" "scheduled" {
  for_each = {
    for k, v in local.scheduled_jobs_map : k => v
    if lookup(var.scheduled_job_images, k, "") != ""
  }

  name                         = lookup(var.scheduled_job_container_app_names, each.key, "") != "" ? var.scheduled_job_container_app_names[each.key] : "${var.name_prefix}-${var.environment}-${each.key}"
  location                     = var.location
  resource_group_name          = local.effective_resource_group_name
  container_app_environment_id = var.container_environment_id
  tags                         = local.common_tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.feature.id]
  }

  replica_timeout_in_seconds = 1800
  replica_retry_limit        = 3

  schedule_trigger_config {
    cron_expression          = each.value.schedule
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name   = replace(each.key, "_", "-")
      image  = var.scheduled_job_images[each.key]
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "FEATURE_KEY"
        value = "asset-inventory"
      }

      env {
        name  = "JOB_NAME"
        value = each.key
      }

      env {
        name  = "JOB_ENTRYPOINT"
        value = each.value.entrypoint
      }

      env {
        name  = "WORKLOAD_KIND"
        value = "scheduled-job"
      }

      env {
        name  = "LOG_ANALYTICS_WORKSPACE_ID"
        value = var.log_analytics_workspace_id
      }

    }
  }
}

#
# EVENT-DRIVEN WORKERS -> CONTAINER APP JOBS
#
resource "azurerm_container_app_job" "event_worker" {
  for_each = {
    for k, v in local.event_workers_map : k => v
    if lookup(var.event_worker_images, k, "") != ""
  }

  name                         = lookup(var.event_worker_container_app_names, each.key, "") != "" ? var.event_worker_container_app_names[each.key] : "${var.name_prefix}-${var.environment}-${each.key}"
  location                     = var.location
  resource_group_name          = local.effective_resource_group_name
  container_app_environment_id = var.container_environment_id
  tags                         = local.common_tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.feature.id]
  }

  replica_timeout_in_seconds = 1800
  replica_retry_limit        = 3

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name   = replace(each.key, "_", "-")
      image  = var.event_worker_images[each.key]
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "FEATURE_KEY"
        value = "asset-inventory"
      }

      env {
        name  = "WORKER_NAME"
        value = each.key
      }

      env {
        name  = "TRIGGER_EVENT_NAME"
        value = try(each.value.trigger.event_name, "")
      }

      env {
        name  = "WORKLOAD_KIND"
        value = "event-worker"
      }

      env {
        name  = "LOG_ANALYTICS_WORKSPACE_ID"
        value = var.log_analytics_workspace_id
      }

    }
  }
}

#
# EVENT LISTENERS -> SEPARATE CONTAINER APPS
#
resource "azurerm_container_app" "listener" {
  for_each = {
    for k, v in local.event_listeners_map : k => v
    if lookup(var.listener_images, k, "") != ""
  }

  name                         = lookup(var.listener_container_app_names, each.key, "") != "" ? var.listener_container_app_names[each.key] : "${var.name_prefix}-${var.environment}-${each.key}"
  container_app_environment_id = var.container_environment_id
  resource_group_name          = local.effective_resource_group_name
  revision_mode                = "Single"
  tags                         = local.common_tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.feature.id]
  }

  template {
    min_replicas = 1
    max_replicas = 2

    container {
      name   = replace(each.key, "_", "-")
      image  = var.listener_images[each.key]
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "FEATURE_KEY"
        value = "asset-inventory"
      }

      env {
        name  = "LISTENER_NAME"
        value = each.key
      }

      env {
        name  = "LISTENER_EVENT_NAME"
        value = each.value.event_name
      }

      env {
        name  = "WORKLOAD_KIND"
        value = "event-listener"
      }

      env {
        name  = "LOG_ANALYTICS_WORKSPACE_ID"
        value = var.log_analytics_workspace_id
      }

    }
  }

  ingress {
    external_enabled = false
    target_port      = 8080
    transport        = "auto"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}