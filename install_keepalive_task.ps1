$ErrorActionPreference = "Stop"
$root = "D:\dev\base-wp-ja-auto"
$action = New-ScheduledTaskAction -Execute (Join-Path $root "base-keepalive.bat") -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At ([datetime]::Today) -RepetitionInterval (New-TimeSpan -Hours 3) -RepetitionDuration (New-TimeSpan -Days 9999)
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -DontStopOnIdleEnd `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
  -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "base-wp-ja-auto-keepalive" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Get-ScheduledTask -TaskName "base-wp-ja-auto-keepalive" | Get-ScheduledTaskInfo | Format-List LastRunTime, NextRunTime, LastTaskResult
Get-ScheduledTask -TaskName "base-wp-ja-auto-keepalive" | Select-Object TaskName, State | Format-List
