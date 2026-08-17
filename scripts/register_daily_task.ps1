# 註冊/更新每日自動更新的 Windows 工作排程器任務。
# 用法：以一般權限執行 `powershell -File register_daily_task.ps1`
# 移除：Unregister-ScheduledTask -TaskName "EbcMoneyShowDailyUpdate" -Confirm:$false

$TaskName = "EbcMoneyShowDailyUpdate"
$Python = (Get-Command python).Source
$Script = Join-Path $PSScriptRoot "daily_update.py"

$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`"" -WorkingDirectory $PSScriptRoot
# 凌晨4點的每日觸發，加上開機/登入10分鐘後的備援觸發——
# 電腦若在4點是「完全關機」(而非睡眠)，每日觸發不會被記錄成missed run、
# StartWhenAvailable 也救不回來；備援觸發確保當天一開機還是會補跑一次。
$TriggerDaily = New-ScheduledTaskTrigger -Daily -At 4:00AM
$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn
$TriggerLogon.Delay = "PT10M"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $TriggerDaily, $TriggerLogon `
    -Settings $Settings -Description "理財達人秀 dashboard 每日自動更新(抓集數/轉錄/分析/建站/push)" `
    -Force

Write-Output "已註冊排程任務 '$TaskName'，每天 04:00 執行 $Script"
Write-Output "手動測試：schtasks /run /tn `"$TaskName`""
Write-Output "移除：Unregister-ScheduledTask -TaskName `"$TaskName`" -Confirm:`$false"
