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

$appPath = (Resolve-Path -LiteralPath $AppRoot).Path.TrimEnd('\')
$winswPath = (Resolve-Path -LiteralPath $WinSWExe).Path
if (-not $winswPath.StartsWith($appPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'WinSW executable/XML, AppRoot içinde olmalı ve aynı korumalı ACL kapsamını kullanmalı.'
}
$xmlPath = $winswPath + '.xml'
if (-not (Test-Path -LiteralPath $xmlPath -PathType Leaf)) { throw "WinSW XML bulunamadı: $xmlPath" }
$dataPath = Resolve-AbsoluteOutsideApp $DataDirectory 'DataDirectory' $appPath
$logPath = Resolve-AbsoluteOutsideApp $LogDirectory 'LogDirectory' $appPath
$certPath = Resolve-AbsoluteOutsideApp $CertificateDirectory 'CertificateDirectory' $appPath
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

$xml = [xml](Get-Content -LiteralPath $xmlPath -Raw -Encoding UTF8)
$envMap = @{}
foreach ($item in $xml.service.env) { $envMap[$item.name] = $item.value }
if (-not [System.IO.Path]::IsPathRooted($xml.service.executable) -or
    -not (Test-Path -LiteralPath $xml.service.executable -PathType Leaf) -or
    $xml.service.id -ne $serviceName -or
    $xml.service.arguments -notmatch '--listen=127\.0\.0\.1:8787\s+contribution_api:app' -or
    $xml.service.workingdirectory -ne $appPath -or
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
foreach ($account in @($serviceAccount, $PurgeAccount, $CaddyAccount)) {
    if (-not $account -or $account.Trim().ToLowerInvariant() -in $denied) { throw 'Geniş veya sistem hesabı reddedildi.' }
    [void](Resolve-Sid $account)
}
$adminSid = 'S-1-5-32-544'
$adminMembers = Get-LocalGroupMember -SID $adminSid -ErrorAction Stop
$adminMemberSids = @($adminMembers | ForEach-Object { Resolve-Sid $_.Name })
if ((Resolve-Sid $serviceAccount) -in $adminMemberSids -or
    (Resolve-Sid $PurgeAccount) -in $adminMemberSids -or
    (Resolve-Sid $CaddyAccount) -in $adminMemberSids) {
    throw 'API, purge ve Caddy hesapları Administrators üyesi olamaz.'
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
if (-not $service) {
    & $winswPath install | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'WinSW servisi kurulamadı.' }
    Start-Sleep -Seconds 1
    $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
}
if (-not $service) { throw 'WinSW servisi bulunamadı; yapılandırma durduruldu.' }
if ($service.State -ne 'Stopped') { Stop-Service -Name $serviceName -Force }
& sc.exe config $serviceName obj= $serviceAccount password= '' | Out-Host
if ($LASTEXITCODE -ne 0) {
    & $winswPath uninstall | Out-Host
    throw 'Dedicated service account atanamadı; servis kaldırma denendi ve başlatma yapılmadı.'
}
& sc.exe sidtype $serviceName unrestricted | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'Dedicated service SID etkinleştirilemedi.' }
$service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
if ($service.StartName -ine $serviceAccount) { throw 'Servis hesabı doğrulanamadı; LocalSystem ile çalıştırılmayacak.' }

$system = '*S-1-5-18'
$admins = '*S-1-5-32-544'
Set-RestrictedAcl $appPath $admins @(
    "${serviceAccount}:(OI)(CI)RX", "${system}:(OI)(CI)F", "${admins}:(OI)(CI)F")
Set-RestrictedAcl $dataPath $serviceAccount @(
    "${serviceAccount}:(OI)(CI)M", "${PurgeAccount}:(OI)(CI)M",
    "${system}:(OI)(CI)F", "${admins}:(OI)(CI)F")
Set-RestrictedAcl $logPath $serviceAccount @(
    "${serviceAccount}:(OI)(CI)M", "${system}:(OI)(CI)F", "${admins}:(OI)(CI)F")
Set-RestrictedAcl $certPath $admins @(
    "${CaddyAccount}:(OI)(CI)RX", "${system}:(OI)(CI)F", "${admins}:(OI)(CI)F")

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

Write-Output "Configured service identity: $serviceAccount"
Write-Output "Restricted data/log/certificate ACLs. DB path: $expectedDb"
Write-Output 'Servisi başlatmadan önce deploy/purge_contributions.ps1 ve Scheduled Task adımlarını tamamlayın.'
