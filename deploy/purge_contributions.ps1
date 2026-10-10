param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$AppRoot,
    [Parameter(Mandatory=$true)][string]$DatabasePath,
    [Parameter(Mandatory=$true)][ValidateRange(1,3650)][int]$RetentionDays,
    [Parameter(Mandatory=$true)][string]$ServiceAccount,
    [Parameter(Mandatory=$true)][string]$PurgeAccount
)

$ErrorActionPreference = 'Stop'
$expected = (New-Object System.Security.Principal.NTAccount -ArgumentList $PurgeAccount).Translate(
    [System.Security.Principal.SecurityIdentifier]).Value
$actual = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($actual -ne $expected) { throw 'Purge scheduled task yanlış Windows hesabı altında çalışıyor.' }
$env:H2S_CONTRIB_DB = [System.IO.Path]::GetFullPath($DatabasePath)
$env:H2S_CONTRIB_RETENTION_DAYS = [string]$RetentionDays
$env:H2S_CONTRIB_SERVICE_ACCOUNT = $ServiceAccount
$env:H2S_CONTRIB_PURGE_ACCOUNT = $PurgeAccount
Push-Location -LiteralPath $AppRoot
try {
    & $PythonExe (Join-Path $AppRoot 'contribution_api.py') purge
    if ($LASTEXITCODE -ne 0) { throw "Retention purge başarısız oldu (exit $LASTEXITCODE)." }
} finally {
    Pop-Location
}
