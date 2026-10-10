param(
    [Parameter(Mandatory=$true)][string]$WinSWExe,
    [Parameter(Mandatory=$true)][string]$AppRoot,
    [Parameter(Mandatory=$true)][string]$DataDirectory,
    [Parameter(Mandatory=$true)][string]$LogDirectory,
    [Parameter(Mandatory=$true)][string]$CertificateDirectory,
    [Parameter(Mandatory=$true)][string]$PurgeAccount,
    [Parameter(Mandatory=$true)][string]$CaddyAccount,
    [Parameter(Mandatory=$true)][ValidateRange(1,3650)][int]$RetentionDays
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'contribution_security_helpers.ps1')
$serviceName = 'hardsub-contribution-api'
$serviceAccount = "NT SERVICE\$serviceName"

function Resolve-Sid([string]$Name) {
    return ([System.Security.Principal.NTAccount]$Name).Translate(
        [System.Security.Principal.SecurityIdentifier]).Value
}

function Resolve-AbsoluteOutsideApp([string]$Name, [string]$Label, [string]$AppPath) {
    if (-not [System.IO.Path]::IsPathRooted($Name)) { throw "$Label mutlak yol olmalı" }
    $full = [System.IO.Path]::GetFullPath($Name).TrimEnd('\')
    $app = $AppPath.TrimEnd('\')
    if ($full.Equals($app, [StringComparison]::OrdinalIgnoreCase) -or
        $full.StartsWith($app + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $app.StartsWith($full + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label uygulama/kaynak klasörünün içinde veya üst dizini olamaz"
    }
    if ($full -match '^[A-Z]:$' -or $full -match '^\\\\[^\\]+\\[^\\]+$') { throw "$Label kök/share dizini olamaz" }
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label yolu symlink/junction/reparse point içeremez"
            }
        }
        $parent = Split-Path -Path $cursor -Parent
        if (-not $parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $full
}

function Invoke-Icacls([string[]]$Arguments) {
    & icacls.exe @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "icacls başarısız oldu (exit $LASTEXITCODE)" }
}

function Set-RestrictedAcl([string]$Path, [string]$Owner, [string[]]$Grants) {
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    Invoke-Icacls @($Path, '/reset', '/T', '/C')
    Invoke-Icacls @($Path, '/inheritance:r', '/T', '/C')
    Invoke-Icacls @($Path, '/setowner', $Owner, '/T', '/C')
    $grantArgs = @($Path, '/grant:r') + $Grants + @('/T', '/C')
    Invoke-Icacls $grantArgs
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Yönetici yetkisi gerekli.' }

$appInputPath = Assert-ContributionPathHasNoReparsePoints ([System.IO.Path]::GetFullPath($AppRoot))
$appPath = (Resolve-Path -LiteralPath $appInputPath).Path.TrimEnd('\')
$winswPath = (Resolve-Path -LiteralPath $WinSWExe).Path
if (-not $winswPath.StartsWith($appPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'WinSW executable/XML, AppRoot içinde olmalı ve aynı korumalı ACL kapsamını kullanmalı.'
}
$xmlPath = $winswPath + '.xml'
if (-not (Test-Path -LiteralPath $xmlPath -PathType Leaf)) { throw "WinSW XML bulunamadı: $xmlPath" }
$dataPath = Resolve-AbsoluteOutsideApp $DataDirectory 'DataDirectory' $appPath
$logPath = Resolve-AbsoluteOutsideApp $LogDirectory 'LogDirectory' $appPath
$certPath = Resolve-AbsoluteOutsideApp $CertificateDirectory 'CertificateDirectory' $appPath
$dataPath = Assert-ContributionDirectoryTarget $dataPath 'DataDirectory'
$logPath = Assert-ContributionDirectoryTarget $logPath 'LogDirectory'
$certPath = Assert-ContributionDirectoryTarget $certPath 'CertificateDirectory'
$dirs = @($dataPath, $logPath, $certPath)
for ($i=0; $i -lt $dirs.Count; $i++) {
    for ($j=$i+1; $j -lt $dirs.Count; $j++) {
        if ($dirs[$i].Equals($dirs[$j], [StringComparison]::OrdinalIgnoreCase) -or
            $dirs[$i].StartsWith($dirs[$j] + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $dirs[$j].StartsWith($dirs[$i] + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Data, log ve sertifika klasörleri ayrı ve iç içe olmayan dizinler olmalı.'
        }
    }
}
$canonicalPaths = @([pscustomobject]@{ Label='AppRoot'; Path=(Get-ContributionCanonicalDirectoryPath $appPath) })
foreach ($path in $dirs) {
    $label = if ($path -eq $dataPath) { 'DataDirectory' } elseif ($path -eq $logPath) { 'LogDirectory' } else { 'CertificateDirectory' }
    $canonicalPaths += [pscustomobject]@{ Label=$label; Path=(Get-ContributionCanonicalDirectoryPath $path) }
}
for ($i=0; $i -lt $canonicalPaths.Count; $i++) {
    for ($j=$i+1; $j -lt $canonicalPaths.Count; $j++) {
        if (Test-ContributionPathOverlap $canonicalPaths[$i].Path $canonicalPaths[$j].Path) {
            throw "$($canonicalPaths[$i].Label) and $($canonicalPaths[$j].Label) resolve to overlapping physical directories."
        }
    }
}
Assert-ContributionTreeHasNoReparsePoints $appPath
foreach ($path in $dirs) {
    if (Test-Path -LiteralPath $path -PathType Container) {
        Assert-ContributionTreeHasNoReparsePoints $path
    }
}

$xml = Read-ContributionServiceXmlFile $xmlPath
$serviceConfig = Assert-ContributionServiceXml $xml
$envMap = $serviceConfig.env
Assert-ContributionServiceArguments ([string]$serviceConfig.arguments)
$waitressPath = Assert-ContributionWaitressExecutable ([string]$serviceConfig.executable) $appPath
if ($serviceConfig.id -ne $serviceName -or
    $serviceConfig.workingdirectory -ne $appPath -or
    $envMap['PYTHONPATH'] -ne $appPath -or
    $envMap['PYTHONDONTWRITEBYTECODE'] -ne '1') {
    throw 'WinSW service id veya loopback Waitress uygulama komutu beklenen değer değil.'
}
$expectedDb = Join-Path $dataPath 'contributions.sqlite3'
if ($envMap['H2S_CONTRIB_DB'] -ne $expectedDb -or
    $envMap['H2S_CONTRIB_SERVICE_ACCOUNT'] -ne $serviceAccount -or
    $envMap['H2S_CONTRIB_PURGE_ACCOUNT'] -ne $PurgeAccount -or
    $envMap['H2S_CONTRIB_RETENTION_DAYS'] -ne [string]$RetentionDays -or
    $envMap['H2S_CONTRIB_LOG_DIR'] -ne $logPath) {
    throw 'WinSW XML ayarları verilen DB, hesap, log klasörü ve retention değerleriyle eşleşmiyor.'
}

$denied = @('localsystem','local system','nt authority\system','system',
    'nt authority\localservice','nt authority\networkservice',
    'builtin\users','everyone','authenticated users')
foreach ($account in @($PurgeAccount, $CaddyAccount)) {
    if (-not $account -or $account.Trim().ToLowerInvariant() -in $denied) { throw 'Geniş veya sistem hesabı reddedildi.' }
    [void](Resolve-Sid $account)
}
$purgeSid = Resolve-Sid $PurgeAccount
$purgeUser = Get-LocalUser | Where-Object { $_.SID.Value -eq $purgeSid }
if (-not $purgeUser) { throw 'PurgeAccount yerel, dedicated bir Windows user hesabı olmalı.' }
$membershipEdges = @()
foreach ($group in Get-LocalGroup) {
    foreach ($member in Get-LocalGroupMember -Group $group.Name -ErrorAction Stop) {
        $membershipEdges += [pscustomobject]@{
            MemberSid = Resolve-Sid $member.Name
            GroupSid = $group.SID.Value
        }
    }
}
$reachableGroups = New-Object 'System.Collections.Generic.HashSet[string]'
$frontier = @($purgeSid)
while ($frontier.Count -gt 0) {
    $next = @()
    foreach ($edge in $membershipEdges) {
        if ($edge.MemberSid -in $frontier -and $reachableGroups.Add($edge.GroupSid)) {
            $next += $edge.GroupSid
        }
    }
    $frontier = $next
}
if ($reachableGroups.Count -ne 1 -or -not $reachableGroups.Contains('S-1-5-32-545')) {
    throw 'PurgeAccount yalnız BUILTIN\Users üyesi olabilir; başka yerel grup üyeliği reddedildi.'
}
if ($PurgeAccount -ieq $serviceAccount -or $CaddyAccount -ieq $serviceAccount -or $CaddyAccount -ieq $PurgeAccount) {
    throw 'API, purge ve Caddy için ayrı hesaplar kullanın.'
}

$service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
$installedHere = $false
if ($service) {
    # Do not stop or reconfigure an unrelated service that happens to share the
    # requested name. This preflight is read-only and precedes all service edits.
    Assert-ContributionServicePathName ([string]$service.PathName) $winswPath
}
if (-not $service) {
    & $winswPath install | Out-Host
    if ($LASTEXITCODE -ne 0) {
        & $winswPath uninstall | Out-Host
        throw 'WinSW servisi kurulamadı; kısmi kayıt kaldırma denendi.'
    }
    $installedHere = $true
    $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
}
if (-not $service) {
    & $winswPath uninstall | Out-Host
    throw 'WinSW servisi bulunamadı; kaldırma denendi ve yapılandırma durduruldu.'
}
try {
    Assert-ContributionServicePathName ([string]$service.PathName) $winswPath
} catch {
    if ($installedHere -and $service.State -eq 'Stopped') { & $winswPath uninstall | Out-Host }
    throw
}
# Disable automatic start before stopping an existing Automatic service. This
# avoids a reboot/crash window where the old identity could start again.
& sc.exe config $serviceName start= disabled | Out-Host
if ($LASTEXITCODE -ne 0) {
    if ($installedHere -and $service.State -eq 'Stopped') { & $winswPath uninstall | Out-Host }
    throw 'Servis disabled durumuna alınamadı; stop/identity/ACL değişikliği yapılmadı.'
}
$service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
Assert-ContributionServicePathName ([string]$service.PathName) $winswPath
if ($service.StartMode -ne 'Disabled') {
    if ($installedHere -and $service.State -eq 'Stopped') { & $winswPath uninstall | Out-Host }
    throw 'Servis başlangıç türü Disabled olarak doğrulanamadı; service identity/ACL değiştirilmedi.'
}
if ($service.State -ne 'Stopped') {
    Stop-Service -Name $serviceName -Force
    Start-Sleep -Seconds 1
    $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
    if ($service.State -ne 'Stopped') { throw 'Servis durdurulamadı; identity/ACL değiştirilmedi.' }
}
& sc.exe config $serviceName obj= $serviceAccount password= '' | Out-Host
if ($LASTEXITCODE -ne 0) {
    & $winswPath uninstall | Out-Host
    throw 'Dedicated service account atanamadı; servis kaldırma denendi ve başlatma yapılmadı.'
}
& sc.exe sidtype $serviceName unrestricted | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'Dedicated service SID etkinleştirilemedi.' }
$service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
if ($service.StartName -ine $serviceAccount) { throw 'Servis hesabı doğrulanamadı; LocalSystem ile çalıştırılmayacak.' }
if ($service.State -ne 'Stopped' -or $service.StartMode -ne 'Disabled') {
    throw 'Servis dedicated identity ile stopped/disabled durumda değil; fail-closed.'
}
$serviceSid = Resolve-Sid $serviceAccount
$reservedSids = @('S-1-5-18','S-1-5-19','S-1-5-20','S-1-5-32-544','S-1-5-32-545','S-1-5-11','S-1-1-0')
if ($serviceSid -in $reservedSids) { throw 'Sanal hizmet hesabı dedicated/low-privilege SID değil.' }
$adminSid = 'S-1-5-32-544'
$adminMembers = Get-LocalGroupMember -SID $adminSid -ErrorAction Stop
$adminMemberSids = @($adminMembers | ForEach-Object { Resolve-Sid $_.Name })
if ($serviceSid -in $adminMemberSids -or $purgeSid -in $adminMemberSids -or
    (Resolve-Sid $CaddyAccount) -in $adminMemberSids) {
    throw 'API, purge ve Caddy hesapları Administrators üyesi olamaz.'
}

$system = '*S-1-5-18'
$admins = '*S-1-5-32-544'
# Re-scan immediately before recursive ACL changes to catch reparse entries
# created after initial preflight. Create protected roots explicitly first.
Assert-ContributionTreeHasNoReparsePoints $appPath
foreach ($path in $dirs) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    Assert-ContributionTreeHasNoReparsePoints $path
}
Set-RestrictedAcl $appPath $admins @(
    "${serviceAccount}:(OI)(CI)RX", "${system}:(OI)(CI)F", "${admins}:(OI)(CI)F")
Set-RestrictedAcl $dataPath $serviceAccount @(
    "${serviceAccount}:(OI)(CI)M", "${PurgeAccount}:(OI)(CI)M",
    "${system}:(OI)(CI)F", "${admins}:(OI)(CI)F")
Set-RestrictedAcl $logPath $serviceAccount @(
    "${serviceAccount}:(OI)(CI)M", "${system}:(OI)(CI)F", "${admins}:(OI)(CI)F")
Set-RestrictedAcl $certPath $admins @(
    "${CaddyAccount}:(OI)(CI)RX", "${system}:(OI)(CI)F", "${admins}:(OI)(CI)F")

# Verify the restricted trees after writing ACLs; do not rely on icacls exit
# codes alone before leaving the service disabled for operator review.
$adminSid = 'S-1-5-32-544'
$systemSid = 'S-1-5-18'
$caddySid = Resolve-Sid $CaddyAccount
Assert-ContributionPathAcl -Path $appPath -ExpectedOwner $adminSid -IsDirectory $true -ExpectedRules @{
    $serviceSid = 'ReadAndExecute'; $systemSid = 'FullControl'; $adminSid = 'FullControl'
}
Assert-ContributionPathAcl -Path $waitressPath -ExpectedOwner $adminSid -IsDirectory $false -ExpectedRules @{
    $serviceSid = 'ReadAndExecute'; $systemSid = 'FullControl'; $adminSid = 'FullControl'
}
Assert-ContributionPathAcl -Path $dataPath -ExpectedOwner $serviceSid -IsDirectory $true -ExpectedRules @{
    $serviceSid = 'Modify'; $purgeSid = 'Modify'; $systemSid = 'FullControl'; $adminSid = 'FullControl'
}
Assert-ContributionPathAcl -Path $logPath -ExpectedOwner $serviceSid -IsDirectory $true -ExpectedRules @{
    $serviceSid = 'Modify'; $systemSid = 'FullControl'; $adminSid = 'FullControl'
}
Assert-ContributionPathAcl -Path $certPath -ExpectedOwner $adminSid -IsDirectory $true -ExpectedRules @{
    $caddySid = 'ReadAndExecute'; $systemSid = 'FullControl'; $adminSid = 'FullControl'
}

# Pre-create and own the SQLite main file before any process opens it. This
# prevents an administrator/Task Scheduler identity from becoming its owner.
$databaseFile = Join-Path $dataPath 'contributions.sqlite3'
if (-not (Test-Path -LiteralPath $databaseFile -PathType Leaf)) {
    New-Item -ItemType File -Path $databaseFile | Out-Null
}
Invoke-Icacls @($databaseFile, '/inheritance:r')
Invoke-Icacls @($databaseFile, '/setowner', $serviceAccount)
Invoke-Icacls @($databaseFile, '/grant:r',
    "${serviceAccount}:M", "${PurgeAccount}:M", "${system}:F", "${admins}:F")
Assert-ContributionPathAcl -Path $databaseFile -ExpectedOwner $serviceSid -IsDirectory $false -ExpectedRules @{
    $serviceSid = 'Modify'; $purgeSid = 'Modify'; $systemSid = 'FullControl'; $adminSid = 'FullControl'
}

Write-Output "Configured service identity: $serviceAccount"
Write-Output "Restricted data/log/certificate ACLs. DB path: $expectedDb"
Write-Output 'Servisi başlatmadan önce deploy/purge_contributions.ps1 ve Scheduled Task adımlarını tamamlayın.'
