<#
Records one screencast scene for the YouTube Data API audit.

The command runs in a clean, maximized PowerShell window titled
"Ambient Bot - YouTube Data API demo", and scripts\window_recorder.py captures ONLY that
window (PrintWindow), so no other window, notification or taskbar is ever recorded.

  powershell -ExecutionPolicy Bypass -File scripts\screencast_scene.ps1 `
      -Name 01_doctor -Command "python main.py doctor" -Seconds 20
#>
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Command,
    [int]$Seconds = 30,
    [int]$StartDelay = 3,
    [switch]$KeepWindow
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$title = "Ambient Bot - YouTube Data API demo"
$out = Join-Path $root "docs\screencast\raw\$Name.mp4"
$log = Join-Path $root "docs\screencast\raw\$Name.log"
New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null

# Windows Terminal hosts the console, so match on the command line instead of the window title.
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $_.CommandLine -like "*$title*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800

$real = $Command -replace '^python', "& '$python'"
$inner = "`$host.UI.RawUI.WindowTitle = '$title'; " +
         "Set-Location '$root'; " +
         "`$env:PYTHONUNBUFFERED = '1'; " +
         "function prompt { 'ambient-bot> ' }; " +
         "Clear-Host; " +
         "Write-Host 'PS> $Command' -ForegroundColor Cyan; " +
         "Start-Sleep -Milliseconds 1500; " +
         "$real | Tee-Object -FilePath '$log'"
Start-Process powershell.exe -ArgumentList '-NoExit', '-NoProfile', '-Command', $inner -WindowStyle Maximized
Start-Sleep -Seconds $StartDelay

& $python (Join-Path $root "scripts\window_recorder.py") --title "YouTube Data API demo" --seconds $Seconds --out $out

if (-not $KeepWindow) {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like "*$title*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
