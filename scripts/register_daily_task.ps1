# 註冊/更新每日自動更新的 Windows 工作排程器任務。
# 用法：以一般權限執行 `powershell -File register_daily_task.ps1`
# 移除：Unregister-ScheduledTask -TaskName "EbcMoneyShowDailyUpdate" -Confirm:$false

$TaskName = "EbcMoneyShowDailyUpdate"
$Python = (Get-Command python).Source
$Script = Join-Path $PSScriptRoot "daily_update.py"

$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`"" -WorkingDirectory $PSScriptRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At 4:00AM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Description "理財達人秀 dashboard 每日自動更新(抓集數/轉錄/分析/建站/push)" `
    -Force

Write-Output "已註冊排程任務 '$TaskName'，每天 04:00 執行 $Script"
Write-Output "手動測試：schtasks /run /tn `"$TaskName`""
Write-Output "移除：Unregister-ScheduledTask -TaskName `"$TaskName`" -Confirm:`$false"
