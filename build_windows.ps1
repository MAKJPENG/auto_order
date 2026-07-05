param(
  [string]$Timestamp = "",
  [string]$Version = "",
  [switch]$SkipDeps,
  [switch]$SkipBrowserInstall
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (!(Test-Path ".\.venv\Scripts\python.exe")) {
  Write-Host "Creating Python virtual environment..."
  py -3 -m venv .venv
}

$Python = ".\.venv\Scripts\python.exe"

$ArgsList = @("tools\build_release.py", "--target", "windows")
if ($Timestamp) {
  $ArgsList += @("--timestamp", $Timestamp)
}
if ($Version) {
  $ArgsList += @("--version", $Version)
}
if ($SkipDeps) {
  $ArgsList += "--skip-deps"
}
if ($SkipBrowserInstall) {
  $ArgsList += "--skip-browser-install"
}

& $Python @ArgsList
