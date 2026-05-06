$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonCmd = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }
$VenvDir = Join-Path $RootDir ".venv"
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"

function Log([string]$Message) {
    Write-Host "[bootstrap] $Message"
}

function Warn([string]$Message) {
    Write-Warning $Message
}

function Fail([string]$Message) {
    throw "[bootstrap][error] $Message"
}

function Test-Command([string]$CommandName) {
    return $null -ne (Get-Command $CommandName -ErrorAction SilentlyContinue)
}

function Copy-EnvIfMissing([string]$TargetDir) {
    $EnvPath = Join-Path $TargetDir ".env"
    $EnvExamplePath = Join-Path $TargetDir ".env.example"

    if (-not (Test-Path $EnvPath)) {
        if (Test-Path $EnvExamplePath) {
            Log "Creating .env from .env.example in $TargetDir"
            Copy-Item $EnvExamplePath $EnvPath
        }
        else {
            Warn "No .env.example found in $TargetDir"
        }
    }
    else {
        Log ".env already exists in $TargetDir"
    }
}

Log "Bootstrapping IT Asset Inventory"
Log "Root directory: $RootDir"

if (-not (Test-Command $PythonCmd)) {
    Fail "Required command not found: $PythonCmd"
}

if ((Test-Path $FrontendDir) -and (-not (Test-Command "npm"))) {
    Fail "npm is not available on PATH"
}

# ----------------------------
# Ensure .env files exist
# ----------------------------

if (Test-Path $BackendDir) {
    Copy-EnvIfMissing $BackendDir
}

if (Test-Path $FrontendDir) {
    Copy-EnvIfMissing $FrontendDir
}

# ----------------------------
# Backend setup
# ----------------------------

if (Test-Path $BackendDir) {
    if (-not (Test-Path $VenvDir)) {
        Log "Creating virtual environment..."
        & $PythonCmd -m venv $VenvDir
    }
    else {
        Log "Virtual environment already exists."
    }

    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"

    if (-not (Test-Path $VenvPython)) {
        Fail "Virtual environment Python not found at $VenvPython"
    }

    Log "Upgrading pip..."
    & $VenvPython -m pip install --upgrade pip wheel setuptools

    $BackendPyProject = Join-Path $BackendDir "pyproject.toml"
    $RootPyProject = Join-Path $RootDir "pyproject.toml"
    $BackendRequirements = Join-Path $BackendDir "requirements.txt"

    if (Test-Path $BackendPyProject) {
        Log "Installing backend package in editable mode with dev dependencies..."
        Push-Location $BackendDir
        try {
            & $VenvPython -m pip install -e ".[dev]"
        }
        finally {
            Pop-Location
        }
    }
    elseif (Test-Path $RootPyProject) {
        Log "Installing project package in editable mode with dev dependencies from repo root..."
        Push-Location $RootDir
        try {
            & $VenvPython -m pip install -e ".[dev]"
        }
        finally {
            Pop-Location
        }
    }
    elseif (Test-Path $BackendRequirements) {
        Log "Installing backend requirements..."
        & $VenvPython -m pip install -r $BackendRequirements
    }
    else {
        Warn "No backend dependency file found."
    }
}

# ----------------------------
# Frontend setup
# ----------------------------

if (Test-Path $FrontendDir) {
    $PackageLock = Join-Path $FrontendDir "package-lock.json"
    $PackageJson = Join-Path $FrontendDir "package.json"

    if (Test-Path $PackageLock) {
        Log "Installing frontend dependencies with npm ci..."
        Push-Location $FrontendDir
        try {
            & npm ci
        }
        finally {
            Pop-Location
        }
    }
    elseif (Test-Path $PackageJson) {
        Log "Installing frontend dependencies with npm install..."
        Push-Location $FrontendDir
        try {
            & npm install
        }
        finally {
            Pop-Location
        }
    }
    else {
        Warn "No frontend package.json found."
    }
}

Write-Host ""
Log "Bootstrap complete."
Log "Next steps:"
Log "  1. Start the feature locally:"
Log "     .\scripts\run-local.ps1"
Log "  2. Start the shell separately."
Write-Host ""