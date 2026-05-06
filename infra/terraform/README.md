# Infrastructure

## Ownership Model

This infrastructure follows a **feature-local ownership model**, meaning all infrastructure defined here is owned and managed by this feature only.

By default, Terraform **creates a dedicated Azure resource group** for this feature (`create_resource_group = true`). Set `create_resource_group = false` in your tfvars only when you intentionally deploy into an existing resource group.

## Deployment Model

This feature deploys workloads separately:

- API backend → Azure Container App
- Scheduled jobs → Container App Jobs
- Event-driven workers → Container App Jobs
- Event listeners → Container Apps (internal)

## Cache configuration

This feature supports cache configuration through Terraform variables.

- `cache_backend`

Use `memory` for simple local or single-instance usage.
