$ErrorActionPreference = "Stop"

$FeatureKey = "asset-inventory"

if ($FeatureKey -eq "catalog") {
    $FrontendPort = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { "3300" }
    $BackendPort  = if ($env:BACKEND_PORT)  { $env:BACKEND_PORT }  else { "8200" }
}
else {
    $FrontendPort = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { "3200" }
    $BackendPort  = if ($env:BACKEND_PORT)  { $env:BACKEND_PORT }  else { "8100" }
}

$ApiBasePath = "/api/inventory/it"

$RegistryUrl = if ($env:REGISTRY_URL) { $env:REGISTRY_URL } else { "http://localhost:8010" }
$FeatureEnvironment = if ($env:FEATURE_ENVIRONMENT) { $env:FEATURE_ENVIRONMENT } else { "local" }

$env:FEATURE_FRONTEND_ENTRY_URL = if ($env:FEATURE_FRONTEND_ENTRY_URL) {
    $env:FEATURE_FRONTEND_ENTRY_URL
} else {
    "http://localhost:$FrontendPort/src/bootstrap-entry.tsx"
}

$env:FEATURE_BACKEND_BASE_URL = if ($env:FEATURE_BACKEND_BASE_URL) {
    $env:FEATURE_BACKEND_BASE_URL
} else {
    "http://localhost:$BackendPort$ApiBasePath"
}

$env:FEATURE_ENVIRONMENT = $FeatureEnvironment

Write-Host "Publishing feature: $FeatureKey"
Write-Host "Frontend: $env:FEATURE_FRONTEND_ENTRY_URL"
Write-Host "Backend:  $env:FEATURE_BACKEND_BASE_URL"

python scripts/render-manifest.py
python scripts/validate-manifest.py

Invoke-RestMethod `
  -Method Post `
  -Uri "$RegistryUrl/api/releases" `
  -Headers @{ "Content-Type" = "application/json" } `
  -InFile "build/feature-manifest.resolved.json"

Invoke-RestMethod `
  -Method Post `
  -Uri "$RegistryUrl/api/admin/features/$FeatureKey/versions/0.1.0/activate?environment=$FeatureEnvironment"

Write-Host ""
Write-Host "Publish complete"
Write-Host ""
Write-Host "Verify:"
Write-Host "  curl -s `"$RegistryUrl/api/runtime/features?environment=$FeatureEnvironment`" | jq"