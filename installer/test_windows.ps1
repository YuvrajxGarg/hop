# Installs dist\<App>-Setup.exe silently, checks the install, runs a command
# against the installed app, then uninstalls and checks it's all gone. CI only.
#   pwsh installer\test_windows.ps1 -App Tag [-Smoke "python tests/smoke_app.py"]
param(
  [Parameter(Mandatory = $true)][string]$App,
  [string]$Smoke = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dir = "$env:LOCALAPPDATA\Programs\$App"
$exe = "$dir\$App.exe"
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PXLVZN.${App}_is1"

Write-Host "== install"
$p = Start-Process "$root\dist\$App-Setup.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$env:TEMP\$App-setup.log" -Wait -PassThru
if ($p.ExitCode -ne 0) { Get-Content "$env:TEMP\$App-setup.log" | Out-Host; throw "setup exited with $($p.ExitCode)" }
if (-not (Test-Path $exe)) { throw "$exe missing after install" }
if (-not (Test-Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\$App.lnk")) { throw "Start menu shortcut missing" }
$reg = Get-ItemProperty $key
Write-Host "registered: $($reg.DisplayName) $($reg.DisplayVersion) at $($reg.InstallLocation)"
if (-not $reg.UninstallString) { throw "uninstaller not registered" }
$uninst = $reg.UninstallString.Trim('"')
if ($uninst.StartsWith($dir)) { throw "the uninstaller is inside the app folder, an update would delete it" }

if ($Smoke) {
  Write-Host "== smoke test the installed app"
  Invoke-Expression "$Smoke `"$exe`""
  if ($LASTEXITCODE -ne 0) { throw "smoke test failed ($LASTEXITCODE)" }
}

Write-Host "== simulate an update (the updater swaps the whole folder)"
Move-Item $dir "$dir.old"
Copy-Item -Recurse "$dir.old" $dir
Remove-Item -Recurse -Force "$dir.old"

Write-Host "== uninstall"
$p = Start-Process $uninst -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "uninstall exited with $($p.ExitCode)" }
for ($i = 0; $i -lt 30 -and ((Test-Path $dir) -or (Test-Path $key)); $i++) { Start-Sleep 1 }  # the uninstaller finishes in a child process
if (Test-Path $dir) { throw "$dir still there after uninstall" }
if (Test-Path $key) { throw "uninstall entry still registered" }
if (Test-Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\$App.lnk") { throw "Start menu shortcut left behind" }
Write-Host "installer OK"
