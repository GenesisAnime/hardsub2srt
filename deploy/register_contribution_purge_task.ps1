param(
    [Parameter(Mandatory=$true)][string]$TaskName,
    [Parameter(Mandatory=$true)][string]$PurgeAccount,
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$AppRoot,
    [Parameter(Mandatory=$true)][string]$WinSWXml,
    [Parameter(Mandatory=$true)][string]$DatabasePath,
    [Parameter(Mandatory=$true)][ValidateRange(1,3650)][int]$RetentionDays,
    [Parameter(Mandatory=$true)][string]$ServiceAccount
)

$ErrorActionPreference = 'Stop'
if ($TaskName -ne 'hardsub2srt-retention-purge') { throw 'Yalnız hardsub2srt-retention-purge task adı kullanılabilir.' }
$identity = New-Object System.Security.Principal.NTAccount -ArgumentList $PurgeAccount
$null = $identity.Translate([System.Security.Principal.SecurityIdentifier])
$config = [xml](Get-Content -LiteralPath $WinSWXml -Raw -Encoding UTF8)
$envMap = @{}
foreach ($item in $config.service.env) { $envMap[$item.name] = $item.value }
$expectedDb = [System.IO.Path]::GetFullPath($DatabasePath)
if ($envMap['H2S_CONTRIB_DB'] -ne $expectedDb -or
    $envMap['H2S_CONTRIB_RETENTION_DAYS'] -ne [string]$RetentionDays -or
    $envMap['H2S_CONTRIB_SERVICE_ACCOUNT'] -ne $ServiceAccount -or
    $envMap['H2S_CONTRIB_PURGE_ACCOUNT'] -ne $PurgeAccount) {
    throw 'Scheduled purge DB, retention veya hesap ayarları WinSW API yapılandırmasıyla aynı olmalı.'
}
$exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$script = Join-Path $AppRoot 'deploy\purge_contributions.ps1'
$taskArguments = '-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "{0}" -PythonExe "{1}" -AppRoot "{2}" -DatabasePath "{3}" -RetentionDays {4} -ServiceAccount "{5}" -PurgeAccount "{6}"' -f `
    $script, $PythonExe, $AppRoot, $expectedDb, $RetentionDays, $ServiceAccount, $PurgeAccount
$action = New-ScheduledTaskAction -Execute $exe -Argument $taskArguments
$trigger = New-ScheduledTaskTrigger -Daily -At '3:15AM'
$principal = New-ScheduledTaskPrincipal -UserId $PurgeAccount -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Write-Output "Scheduled daily retention purge at 03:15 under $PurgeAccount. Verify the task's Log on as a batch job right and run a manual maintenance window before launch."
