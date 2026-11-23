[CmdletBinding()]
param(
    [string]$PrivateRoot = (Join-Path $env:USERPROFILE '.oci/research-workspace')
)

$ErrorActionPreference = 'Stop'
$taskName = 'Research Workspace - Oracle free capacity'
$privateDirectory = (Resolve-Path -LiteralPath $PrivateRoot).Path
$python = Join-Path $privateDirectory 'tools-env/Scripts/pythonw.exe'
$config = Join-Path $privateDirectory 'config'
foreach ($required in @($python, $config)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing local prerequisite: $required"
    }
}

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    throw 'The capacity checker already exists. Inspect or remove that task before starting a new three-day window.'
}

$stateDirectory = Join-Path $privateDirectory 'capacity-monitor'
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$checker = Join-Path $stateDirectory 'oracle_capacity.py'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'oracle_capacity.py') -Destination $checker
$start = Get-Date
$firstScheduled = $start.AddHours(3)
$expires = $start.AddDays(3)
$deadline = $expires.ToUniversalTime().ToString('o')
$arguments = '"{0}" --config "{1}" --state-dir "{2}" --expires-at "{3}"' -f $checker, $config, $stateDirectory, $deadline
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $stateDirectory
$trigger = New-ScheduledTaskTrigger -Once -At $firstScheduled -RepetitionInterval (New-TimeSpan -Hours 3) -RepetitionDuration ($expires - $firstScheduled)
$trigger.EndBoundary = $deadline
$trigger.Repetition.StopAtDurationEnd = $true
$expiryTrigger = New-ScheduledTaskTrigger -Once -At $expires.AddMinutes(1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$principal = New-ScheduledTaskPrincipal -UserId $userSid -LogonType Interactive -RunLevel Limited
$description = 'Read-only Oracle A1 capacity reports every three hours for three days. No provisioning or paid fallback. Runs only while the owner is signed in.'
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($trigger, $expiryTrigger) -Settings $settings -Principal $principal -Description $description | Out-Null

$schedule = [ordered]@{
    task_name = $taskName
    starts_at = $start.ToUniversalTime().ToString('o')
    first_scheduled_at = $firstScheduled.ToUniversalTime().ToString('o')
    expires_at = $deadline
    interval_hours = 3
    duration_days = 3
    requires_signed_in_user = $true
    provisions_resources = $false
}
$schedule | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stateDirectory 'schedule.json') -Encoding UTF8
Start-ScheduledTask -TaskName $taskName
$schedule | ConvertTo-Json
