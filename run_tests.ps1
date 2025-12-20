#!/usr/bin/env pwsh
# Comprehensive test runner for Windows PowerShell
# Usage: .\run_tests.ps1 [-Fast] [-Security] [-Coverage] [-Parallel]

param(
    [switch]$Fast,
    [switch]$Slow,
    [switch]$Security,
    [string]$Module,
    [switch]$Parallel,
    [switch]$Coverage,
    [switch]$Verbose,
    [switch]$Quiet,
    [int]$Workers = 0,
    [switch]$FailFast,
    [switch]$JUnit,
    [int]$Durations = 0
)

# Build pytest arguments
$pytestArgs = @()

# Test path
$pytestArgs += "src/tests/"

# Test selection
if ($Fast) {
    $pytestArgs += "-m", "unit"
}
elseif ($Security) {
    $pytestArgs += "-m", "security"
}
elseif ($Module) {
    $pytestArgs += "-m", $Module
}
else {
    # Default: exclude slow tests
    if (-not $Slow) {
        $pytestArgs += "-m", "not slow"
    }
}

# Parallel execution
if ($Parallel) {
    if ($Workers -gt 0) {
        $pytestArgs += "-n", $Workers.ToString()
    }
    else {
        $pytestArgs += "-n", "auto"
    }
}

# Verbosity
if ($Verbose) {
    $pytestArgs += "-v"
}
elseif ($Quiet) {
    $pytestArgs += "-q"
}

# Fail fast
if ($FailFast) {
    $pytestArgs += "-x"
}

# Coverage
if ($Coverage) {
    $pytestArgs += "--cov=src", "--cov-report=term-missing", "--cov-report=html", "--cov-report=xml"
}

# Reporting
if ($JUnit) {
    $pytestArgs += "--junitxml=test-results.xml"
}

if ($Durations -gt 0) {
    $pytestArgs += "--durations", $Durations.ToString()
}

# Always show short traceback
$pytestArgs += "--tb=short"

# Run tests
Write-Host "Running: pytest $($pytestArgs -join ' ')" -ForegroundColor Cyan
Write-Host ("-" * 80)

pytest @pytestArgs
$exitCode = $LASTEXITCODE

# Print summary
Write-Host ""
Write-Host ("=" * 80)
if ($exitCode -eq 0) {
    Write-Host "✓ All tests passed!" -ForegroundColor Green
}
else {
    Write-Host "✗ Some tests failed" -ForegroundColor Red
}
Write-Host ("=" * 80)

exit $exitCode
