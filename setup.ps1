# One-command setup for Windows PowerShell:  .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found. Install Python 3.9+ first: https://python.org/downloads" -ForegroundColor Red
    exit 1
}

Write-Host "Installing gcode..." -ForegroundColor Cyan
python -m pip install -e . --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed" -ForegroundColor Red; exit 1 }

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env" -ForegroundColor Green
}

$hasKey = (Get-Content .env -Raw) -notmatch "REPLACE_ME"
Write-Host ""
Write-Host "Installed. gcode is on your PATH." -ForegroundColor Green
if (-not $hasKey) {
    Write-Host "Next: put your OpenAI key in $PSScriptRoot\.env" -ForegroundColor Yellow
    Write-Host "      OPENAI_API_KEY=sk-proj-..." -ForegroundColor Yellow
    Write-Host "      GCODE_USER=your-name" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Then, from any project folder:  gcode" -ForegroundColor Cyan
