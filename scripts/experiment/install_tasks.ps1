# Install / re-point PoolNode experiment scheduled tasks (PN-RECOVERY-01).
# ASCII only on purpose: Windows PowerShell 5.1 misreads non-BOM UTF-8.
param(
    [string]$RepoRoot = "",
    [string]$Python = "",
    [switch]$WhatIfOnly,
    [switch]$EnableCmin
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
if (-not (Test-Path (Join-Path $RepoRoot "scripts\experiment\runner.py"))) {
    throw "runner.py not found under $RepoRoot"
}
if (-not $Python) {
    $Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (-not $Python) { throw "python.exe not found; pass -Python" }
}
if (-not (Test-Path $Python)) { throw "python not found: $Python" }

$runner = Join-Path $RepoRoot "scripts\experiment\runner.py"
$health = Join-Path $RepoRoot "scripts\experiment\health.py"
Write-Output "repo   = $RepoRoot"
Write-Output "python = $Python"
Write-Output "runner = $runner"

$map = @(
    @{ Name = "PoolCminExp";   Task = "cmin" },
    @{ Name = "PoolRCv05";     Task = "rcv05" },
    @{ Name = "PoolRCm";       Task = "rcmatrix" },
    @{ Name = "PoolProbeV05";  Task = "probe" },
    @{ Name = "PoolProbeSync"; Task = "sync" }
)

foreach ($item in $map) {
    $arg = '-X utf8 "' + $runner + '" --task ' + $item.Task
    $action = New-ScheduledTaskAction -Execute $Python -Argument $arg -WorkingDirectory $RepoRoot
    if ($WhatIfOnly) {
        Write-Output ("[whatif] " + $item.Name + " -> " + $arg)
        continue
    }
    $existing = Get-ScheduledTask -TaskName $item.Name -ErrorAction SilentlyContinue
    if (-not $existing) {
        throw ("task not found: " + $item.Name + " (run this only after the original tasks exist)")
    }
    Set-ScheduledTask -TaskName $item.Name -Action $action | Out-Null
    $check = (Get-ScheduledTask -TaskName $item.Name).Actions[0]
    Write-Output ("[ok] " + $item.Name + " -> " + $check.Execute + " " + $check.Arguments)
}

# Manual smoke-test task (no trigger): always same entry point as the scheduled ones
$sName = "PoolSmokeTest"
$sArg = '-X utf8 "' + $runner + '" --smoke-all'
$sAction = New-ScheduledTaskAction -Execute $Python -Argument $sArg -WorkingDirectory $RepoRoot
if ($WhatIfOnly) {
    Write-Output ("[whatif] " + $sName + " -> " + $sArg)
} else {
    if (Get-ScheduledTask -TaskName $sName -ErrorAction SilentlyContinue) {
        Set-ScheduledTask -TaskName $sName -Action $sAction | Out-Null
    } else {
        Register-ScheduledTask -TaskName $sName -Action $sAction | Out-Null
    }
    Write-Output ("[ok] " + $sName + " -> " + $sArg)
}

# Hourly health check task (:50), same user, interactive token
$hName = "PoolHealthCheck"
$hArg = '-X utf8 "' + $health + '"'
$hAction = New-ScheduledTaskAction -Execute $Python -Argument $hArg -WorkingDirectory $RepoRoot
$hTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date "00:50") -RepetitionInterval (New-TimeSpan -Hours 1)
$hSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
if ($WhatIfOnly) {
    Write-Output ("[whatif] " + $hName + " -> " + $hArg)
} else {
    if (Get-ScheduledTask -TaskName $hName -ErrorAction SilentlyContinue) {
        Set-ScheduledTask -TaskName $hName -Action $hAction -Trigger $hTrigger -Settings $hSettings | Out-Null
    } else {
        Register-ScheduledTask -TaskName $hName -Action $hAction -Trigger $hTrigger -Settings $hSettings | Out-Null
    }
    Write-Output ("[ok] " + $hName + " -> " + $hArg)
}

if (-not $WhatIfOnly) {
    # C_MIN formal runs need an explicit window authorization (PN-EXPERIMENT-01 4.3).
    # Without -EnableCmin the task stays disabled; runner.py also refuses a
    # suspended task, so a manual "Enable-ScheduledTask" cannot start traffic.
    if ($EnableCmin) {
        Enable-ScheduledTask -TaskName "PoolCminExp" | Out-Null
        Write-Output "[ok] PoolCminExp enabled (explicit authorization)"
    } else {
        Disable-ScheduledTask -TaskName "PoolCminExp" | Out-Null
        Write-Output "[ok] PoolCminExp left disabled (no authorization yet)"
    }
}

if (-not $WhatIfOnly) {
    Write-Output ""
    Write-Output "=== current actions ==="
    foreach ($n in @("PoolCminExp","PoolRCv05","PoolRCm","PoolProbeV05","PoolProbeSync","PoolHealthCheck","PoolSmokeTest")) {
        $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
        if ($t) {
            $a = $t.Actions[0]
            $i = $t | Get-ScheduledTaskInfo
            Write-Output ("{0} | {1} | last={2} result={3}" -f $n, $a.Arguments, $i.LastRunTime, $i.LastTaskResult)
        }
    }
}
