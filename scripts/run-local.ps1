$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$BackendProcess = $null
$BootstrapProcess = $null
$FrontendProcess = $null

$BackendPort = if ($env:BACKEND_PORT) { $env:BACKEND_PORT } else { "8100" }
$BootstrapPort = if ($env:BOOTSTRAP_PORT) { $env:BOOTSTRAP_PORT } else { "3050" }
$FrontendPort = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { "3200" }
$RegistryUrl = if ($env:REGISTRY_URL) { $env:REGISTRY_URL } else { "http://localhost:8010" }
$PublishLocalEnabled = if ($env:PUBLISH_LOCAL_ENABLED) { $env:PUBLISH_LOCAL_ENABLED } else { "true" }

$ApiBasePath = "/api/inventory/it"
$BackendHealthPath = if ($env:BACKEND_HEALTH_PATH) { $env:BACKEND_HEALTH_PATH } else { "$ApiBasePath/health" }
$BootstrapHealthPath = if ($env:BOOTSTRAP_HEALTH_PATH) { $env:BOOTSTRAP_HEALTH_PATH } else { "/bootstrap" }

$env:FEATURE_ENVIRONMENT = if ($env:FEATURE_ENVIRONMENT) { $env:FEATURE_ENVIRONMENT } else { "local" }
$env:FEATURE_FRONTEND_ENTRY_URL = if ($env:FEATURE_FRONTEND_ENTRY_URL) { $env:FEATURE_FRONTEND_ENTRY_URL } else { "http://localhost:$FrontendPort/src/bootstrap-entry.tsx" }
$env:FEATURE_BACKEND_BASE_URL = if ($env:FEATURE_BACKEND_BASE_URL) { $env:FEATURE_BACKEND_BASE_URL } else { "http://localhost:$BackendPort$ApiBasePath" }
$env:REGISTRY_URL = $RegistryUrl

$PythonExe = Join-Path $RootDir ".venv\Scripts\python.exe"
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"
$BootstrapScript = Join-Path $RootDir "scripts\serve-bootstrap-mock.py"
$PublishScript = Join-Path $RootDir "scripts\publish-local.ps1"

function Log([string]$Message) {
    Write-Host "[run-local] $Message"
}

function Warn([string]$Message) {
    Write-Warning $Message
}

function Fail([string]$Message) {
    throw "[run-local][error] $Message"
}

function Test-Command([string]$CommandName) {
    return $null -ne (Get-Command $CommandName -ErrorAction SilentlyContinue)
}

function Wait-Url([string]$Url, [string]$Name, [int]$Retries = 30, [int]$DelaySeconds = 1) {
    for ($i = 0; $i -lt $Retries; $i++) {
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing | Out-Null
            Log "$Name is ready at $Url"
            return $true
        }
        catch {
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    Warn "Timed out waiting for $Name at $Url"
    return $false
}

function Stop-DevProcess($Process) {
    if ($null -ne $Process) {
        try {
            if (-not $Process.HasExited) {
                Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
            }
        }
        catch {
        }
    }
}

function Publish-LocalManifest {
    if ($PublishLocalEnabled -ne "true") {
        Log "Local registry publish disabled. Set PUBLISH_LOCAL_ENABLED=true to enable."
        return
    }

    if (-not (Test-Path $PublishScript)) {
        Warn "Publish script not found at $PublishScript"
        return
    }

    Log "Publishing local feature manifest to registry..."
    Log "Registry: $RegistryUrl"
    Log "Feature frontend entry: $env:FEATURE_FRONTEND_ENTRY_URL"
    Log "Feature backend base URL: $env:FEATURE_BACKEND_BASE_URL"

    try {
        & $PublishScript
        Log "Local feature manifest published."
    }
    catch {
        Warn "Local registry publish failed. Feature services are still running."
        Warn "Check that registry is running at $RegistryUrl."
    }
}

try {
    Log "Starting local development for IT Asset Inventory"
    Log "Root directory: $RootDir"

    if ((Test-Path $BackendDir) -or (Test-Path $BootstrapScript)) {
        if (-not (Test-Path $PythonExe)) {
            Fail "Python virtual environment not found: $PythonExe"
        }
    }

    if (Test-Path $FrontendDir) {
        if (-not (Test-Command "npm")) {
            Fail "npm is not available on PATH"
        }

        $NodeModulesDir = Join-Path $FrontendDir "node_modules"
        if (-not (Test-Path $NodeModulesDir)) {
            Warn "frontend\node_modules not found. Run 'npm install' in $FrontendDir"
        }
    }

    if (Test-Path $BackendDir) {
        Log "Starting backend on port $BackendPort ..."
        $env:PYTHONPATH = $BackendDir

        $backendArgs = @(
            "-m", "uvicorn",
            "app.main:app",
            "--app-dir", $BackendDir,
            "--host", "0.0.0.0",
            "--port", $BackendPort
        )

        $BackendProcess = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList $backendArgs `
            -WorkingDirectory $BackendDir `
            -PassThru

        Log "Backend PID: $($BackendProcess.Id)"
    }
    else {
        Warn "No backend directory found at $BackendDir"
    }

    if (Test-Path $BootstrapScript) {
        Log "Starting bootstrap mock on port $BootstrapPort ..."

        $BootstrapProcess = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList "`"$BootstrapScript`"" `
            -WorkingDirectory $RootDir `
            -PassThru

        Log "Bootstrap PID: $($BootstrapProcess.Id)"
    }
    else {
        Warn "No bootstrap mock found at $BootstrapScript"
    }

    if (Test-Path $FrontendDir) {
        Log "Starting frontend dev server ..."

        $frontendArgs = @(
            "run", "dev", "--",
            "--host", "0.0.0.0",
            "--port", $FrontendPort
        )

        $FrontendProcess = Start-Process `
            -FilePath "npm" `
            -ArgumentList $frontendArgs `
            -WorkingDirectory $FrontendDir `
            -PassThru

        Log "Frontend PID: $($FrontendProcess.Id)"
    }
    else {
        Warn "No frontend directory found at $FrontendDir"
    }

    if ($null -ne $BackendProcess) {
        Wait-Url "http://localhost:$BackendPort$BackendHealthPath" "Backend" 30 1 | Out-Null
    }

    if ($null -ne $BootstrapProcess) {
        Wait-Url "http://localhost:$BootstrapPort$BootstrapHealthPath" "Bootstrap" 30 1 | Out-Null
    }

    if ($null -ne $FrontendProcess) {
        Wait-Url "http://localhost:$FrontendPort" "Frontend" 30 1 | Out-Null
    }

    Publish-LocalManifest

    Write-Host ""
    Log "Local services started."
    if ($null -ne $BackendProcess)   { Log "Backend:   http://localhost:$BackendPort" }
    if ($null -ne $BootstrapProcess) { Log "Bootstrap: http://localhost:$BootstrapPort/bootstrap" }
    if ($null -ne $FrontendProcess)  { Log "Frontend:  http://localhost:$FrontendPort" }
    Log "Registry:  $RegistryUrl"
    Log "Publish:   PUBLISH_LOCAL_ENABLED=$PublishLocalEnabled"
    Log "Shell runs separately."
    Log "Press Ctrl+C to stop."
    Write-Host ""

    while ($true) {
        Start-Sleep -Seconds 5
    }
}
finally {
    Log "Stopping local services..."
    Stop-DevProcess $FrontendProcess
    Stop-DevProcess $BootstrapProcess
    Stop-DevProcess $BackendProcess
}