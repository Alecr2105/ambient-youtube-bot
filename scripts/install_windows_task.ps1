# Registers the Ambient Bot worker in Windows Task Scheduler.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_windows_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_windows_task.ps1 -Uninstall
#
# The task starts `main.py run-scheduler` when you log on and every 15 minutes if it is not
# running, may wake the computer and never stops because of battery. Keep the laptop plugged in and set Windows to not
# sleep while plugged in, or at least allow wake timers (Power Options > Sleep > Allow wake timers).

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$TaskName = "AmbientBot Worker"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed task '$TaskName'."
    exit 0
}

if (-not (Test-Path $Python)) { throw "Python venv not found at $Python" }

$action = New-ScheduledTaskAction -Execute $Python -Argument "main.py run-scheduler" -WorkingDirectory $Root
# "Restart on failure" only covers a task that fails to start, not a worker that dies later
# (e.g. killed when the laptop wakes from sleep). The repeating trigger is a watchdog: every
# 15 minutes it starts the worker again, and IgnoreNew makes it a no-op while one is running.
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15)
$trigger = @((New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME), $watchdog)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -WakeToRun -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Registered '$TaskName'. Start it now with: Start-ScheduledTask -TaskName '$TaskName'"
