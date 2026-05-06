# Mirrors scripts/validate.sh: backend/tests plus each jobs/workers/listeners package dir that contains test_*.py.
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$hasPythonTests =
    (Test-Path (Join-Path $RootDir "backend")) -or
    (Test-Path (Join-Path $RootDir "jobs")) -or
    (Test-Path (Join-Path $RootDir "workers")) -or
    (Test-Path (Join-Path $RootDir "listeners"))

if ($hasPythonTests) {
    Write-Host "Running backend and Python component tests..."

    $venvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "Missing $venvPython — run bootstrap or create the virtual environment first."
    }

    $env:PYTHONPATH = "$(Join-Path $RootDir 'backend');$RootDir"

    $testPaths = @()

    $backendTests = Join-Path $RootDir "backend\tests"
    if (Test-Path $backendTests) {
        $testPaths += $backendTests
    }

    # Jobs / workers / listeners: one pytest target per package dir so relative imports (from .config) work.
    foreach ($kind in @("jobs", "workers", "listeners")) {
        $base = Join-Path $RootDir $kind
        if (-not (Test-Path $base)) { continue }
        Get-ChildItem -Path $base -Directory | ForEach-Object {
            $pkg = $_.FullName
            $hasTests = @(Get-ChildItem -Path $pkg -Filter "test_*.py" -File -ErrorAction SilentlyContinue)
            if ($hasTests.Count -gt 0) {
                $testPaths += $pkg
            }
        }
    }

    if ($testPaths.Count -gt 0) {
        & $venvPython -m pytest @testPaths
    }
    else {
        Write-Host "No Python test directories found (backend/tests or job/worker/listener packages with test_*.py)."
    }
}

if (Test-Path (Join-Path $RootDir "frontend")) {
    Write-Host "Running frontend tests..."
    Push-Location (Join-Path $RootDir "frontend")
    try {
        npm test
    }
    finally {
        Pop-Location
    }
}

Write-Host "Validation complete."