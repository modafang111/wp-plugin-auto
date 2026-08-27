"""Install Task Scheduler jobs with working directory and battery allowed."""
from __future__ import annotations

import subprocess

PS = r"""
$ErrorActionPreference = 'Stop'
$root = 'D:\dev\base-wp-ja-auto'

function Install-BaseTask {
  param($Name, $Bat, $Trigger)
  $action = New-ScheduledTaskAction -Execute $Bat -WorkingDirectory $root
  $settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Settings $settings -Force | Out-Null
  Write-Output "installed $Name"
}

$registerTrigger = New-ScheduledTaskTrigger -Daily -At 07:00
Install-BaseTask 'base-wp-ja-auto-register' (Join-Path $root 'register-next.bat') $registerTrigger

$deliverTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 9999)
Install-BaseTask 'base-wp-ja-auto-deliver' (Join-Path $root 'deliver-orders.bat') $deliverTrigger

Get-ScheduledTask -TaskName 'base-wp-ja-auto-*' | ForEach-Object {
  $info = $_ | Get-ScheduledTaskInfo
  '{0} state={1} last={2} result={3} next={4}' -f $_.TaskName, $_.State, $info.LastRunTime, $info.LastTaskResult, $info.NextRunTime
}
"""

result = subprocess.run(
    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", PS],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)
print(result.stdout)
print(result.stderr)
raise SystemExit(result.returncode)
