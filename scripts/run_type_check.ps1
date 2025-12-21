#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Runs comprehensive type checking across all PlexiChat repositories

.DESCRIPTION
    This script runs pyright type checking on plexichat, common-utils, and encryption,
    then generates detailed reports organized by category with prioritized recommendations.

.PARAMETER SkipSubmoduleUpdate
    Skip updating git submodules (useful if submodules are already initialized)

.EXAMPLE
    .\scripts\run_type_check.ps1
    Runs type checking with submodule update

.EXAMPLE
    .\scripts\run_type_check.ps1 -SkipSubmoduleUpdate
    Runs type checking without updating submodules
#>

param(
    [switch]$SkipSubmoduleUpdate
)

$ErrorActionPreference = "Stop"

# Get script directory and repository root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "PlexiChat Type Check - All Repositories" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""

# Check prerequisites
Write-Host "Checking prerequisites..." -ForegroundColor Yellow

# Check for pyright
try {
    $pyrightVersion = pyright --version 2>&1
    Write-Host "✓ pyright is installed: $pyrightVersion" -ForegroundColor Green
} catch {
    Write-Host "✗ pyright not found" -ForegroundColor Red
    Write-Host "  Install with: npm install -g pyright" -ForegroundColor Red
    exit 1
}

# Check for Python
try {
    $pythonVersion = python --version 2>&1
    Write-Host "✓ Python is installed: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "✗ Python not found" -ForegroundColor Red
    exit 1
}

# Check for virtual environment activation
if (-not $env:VIRTUAL_ENV) {
    Write-Host "⚠ Virtual environment is not activated" -ForegroundColor Yellow
    Write-Host "  Consider activating: .venv\Scripts\activate" -ForegroundColor Yellow
}

# Update submodules if not skipped
if (-not $SkipSubmoduleUpdate) {
    Write-Host ""
    Write-Host "Updating git submodules..." -ForegroundColor Yellow
    Push-Location $RepoRoot
    try {
        git submodule update --init --recursive
        Write-Host "✓ Submodules updated" -ForegroundColor Green
    } catch {
        Write-Host "✗ Failed to update submodules: $_" -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Pop-Location
}

# Run type checking
Write-Host ""
Write-Host "Running type checker..." -ForegroundColor Yellow
Write-Host ""

Push-Location $RepoRoot
try {
    python scripts/type_check_all.py
    $exitCode = $LASTEXITCODE
} catch {
    Write-Host ""
    Write-Host "✗ Type checking failed: $_" -ForegroundColor Red
    Pop-Location
    exit 1
}
Pop-Location

# Summary
Write-Host ""
Write-Host "=" * 80 -ForegroundColor Cyan
if ($exitCode -eq 0) {
    Write-Host "✓ Type checking completed successfully!" -ForegroundColor Green
} else {
    Write-Host "⚠ Type checking completed with issues" -ForegroundColor Yellow
}
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""
Write-Host "Reports available in: type_check_reports/" -ForegroundColor Cyan
Write-Host "- type_check_summary_consolidated.md (start here)" -ForegroundColor White
Write-Host "- type_check_report_plexichat.md" -ForegroundColor White
Write-Host "- type_check_report_common-utils.md" -ForegroundColor White
Write-Host "- type_check_report_encryption.md" -ForegroundColor White
Write-Host ""

exit $exitCode
