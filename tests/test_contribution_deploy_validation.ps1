$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\deploy\contribution_security_helpers.ps1')

function Assert-Throws([scriptblock]$Action, [string]$Label) {
    try { & $Action } catch { return }
    throw "Expected rejection: $Label"
}

function New-SyntheticServiceXml([string]$extraInside = '', [string]$startMode = 'Manual', [string]$extraRootAttribute = '') {
    $envXml = @(
        '<env name="PYTHONPATH" value="APP_ROOT" />',
        '<env name="PYTHONDONTWRITEBYTECODE" value="1" />',
        '<env name="H2S_CONTRIB_DB" value="DATA_DB" />',
        '<env name="H2S_CONTRIB_RETENTION_DAYS" value="30" />',
        '<env name="H2S_CONTRIB_SERVICE_ACCOUNT" value="NT SERVICE\hardsub-contribution-api" />',
        '<env name="H2S_CONTRIB_PURGE_ACCOUNT" value="host\purge" />',
        '<env name="H2S_CONTRIB_LOG_DIR" value="LOG_DIR" />'
    ) -join ''
    $xmlText = '<service ' + $extraRootAttribute + '><id>hardsub-contribution-api</id>' +
        '<name>test</name><description>test</description><startmode>' + $startMode + '</startmode>' +
        '<executable>APP_ROOT\.venv\Scripts\waitress-serve.exe</executable>' +
        '<arguments>--listen=127.0.0.1:8787 contribution_api:app</arguments>' +
        '<workingdirectory>APP_ROOT</workingdirectory>' + $envXml + $extraInside +
        '<log mode="none" /></service>'
    return [xml]$xmlText
}

$root = Join-Path ([System.IO.Path]::GetTempPath()) ("h2s-contrib-validation-" + [guid]::NewGuid().ToString('N'))
$app = Join-Path $root 'app'
$venvScripts = Join-Path $app '.venv\Scripts'
$outside = Join-Path $root 'external'
$appJunction = Join-Path $root 'app-junction'
$descendantJunction = Join-Path $app 'linked-external'
$substDrive = $null
$tempPrefix = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
if (-not $root.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    [System.IO.Path]::GetFileName($root) -notmatch '^h2s-contrib-validation-[0-9a-f]{32}$') {
    throw 'Synthetic test cleanup path escaped its unique temp directory.'
}
New-Item -ItemType Directory -Force -Path $venvScripts, $outside | Out-Null
$waitress = Join-Path $venvScripts 'waitress-serve.exe'
Set-Content -LiteralPath $waitress -Value 'synthetic placeholder; never executed' -Encoding ASCII
$outsideExe = Join-Path $outside 'waitress-serve.exe'
Set-Content -LiteralPath $outsideExe -Value 'synthetic placeholder; never executed' -Encoding ASCII

try {
    $resolved = Assert-ContributionWaitressExecutable $waitress $app
    if ($resolved -ne [System.IO.Path]::GetFullPath($waitress)) { throw 'Valid AppRoot executable path changed.' }
    $checkedRoot = Assert-ContributionPathHasNoReparsePoints $app
    if ($checkedRoot -ne [System.IO.Path]::GetFullPath($app)) { throw 'Valid AppRoot path changed.' }
    $physicalApp = Get-ContributionCanonicalDirectoryPath $app
    Assert-Throws { Get-ContributionCanonicalDirectoryPath (Join-Path $root 'TEMP~1\alias') } '8.3 short-name path'
    $usedLetters = @(Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Name.ToUpperInvariant() })
    $freeLetter = @('Z','Y','X','W','V','U','T','S') | Where-Object { $_ -notin $usedLetters } | Select-Object -First 1
    if ($freeLetter) {
        $substExe = Join-Path $env:SystemRoot 'System32\subst.exe'
        & $substExe ($freeLetter + ':') $root
        if ($LASTEXITCODE -eq 0) {
            $substDrive = $freeLetter + ':'
            $substApp = Get-ContributionCanonicalDirectoryPath ($substDrive + '\app')
            if (-not [string]::Equals($physicalApp, $substApp, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'SUBST alias did not resolve to the same physical AppRoot.'
            }
            if (-not (Test-ContributionPathOverlap $physicalApp $substApp)) {
                throw 'Physical path overlap failed to detect a SUBST alias.'
            }
        } else {
            Write-Output 'SUBST mapping unavailable; physical alias case skipped.'
        }
    } else {
        Write-Output 'No free drive letter; SUBST physical alias case skipped.'
    }
    $junctionCreated = $false
    try {
        New-Item -ItemType Junction -Path $appJunction -Target $app -ErrorAction Stop | Out-Null
        $junctionCreated = $true
    } catch {
        Write-Output 'Junction creation unavailable; AppRoot reparse test skipped.'
    }
    if ($junctionCreated) {
        Assert-Throws { Assert-ContributionPathHasNoReparsePoints $appJunction } 'AppRoot reparse point'
        & $env:ComSpec /d /c rmdir $appJunction
        if ($LASTEXITCODE -ne 0) { throw 'Synthetic AppRoot junction could not be removed.' }
    }
    $descendantCreated = $false
    try {
        New-Item -ItemType Junction -Path $descendantJunction -Target $outside -ErrorAction Stop | Out-Null
        $descendantCreated = $true
    } catch {
        Write-Output 'Descendant junction creation unavailable; descendant reparse test skipped.'
    }
    if ($descendantCreated) {
        Assert-Throws { Assert-ContributionTreeHasNoReparsePoints $app } 'AppRoot descendant reparse point'
        & $env:ComSpec /d /c rmdir $descendantJunction
        if ($LASTEXITCODE -ne 0) { throw 'Synthetic descendant junction could not be removed.' }
    }
    Assert-Throws { Assert-ContributionWaitressExecutable $outsideExe $app } 'executable outside AppRoot'
    Assert-Throws { Assert-ContributionServiceArguments '--listen=0.0.0.0:8787 contribution_api:app' } 'public listener'
    Assert-Throws { Assert-ContributionServiceArguments '--listen=127.0.0.1:8787 contribution_api:app --host=0.0.0.0' } 'extra listener option'
    Assert-ContributionServiceArguments '--listen=127.0.0.1:8787 contribution_api:app'
    Assert-ContributionServicePathName ('"' + $waitress + '"') $waitress
    Assert-Throws { Assert-ContributionServicePathName ('"' + $outsideExe + '"') $waitress } 'different existing service executable'
    Assert-Throws { Assert-ContributionServicePathName ('"' + $waitress + '" --service') $waitress } 'extra existing service args'
    $validXml = Assert-ContributionServiceXml (New-SyntheticServiceXml)
    if ($validXml.startmode -cne 'Manual' -or $validXml.env.Count -ne 7) { throw 'Valid allowlisted WinSW XML was not preserved.' }
    Assert-Throws { Read-ContributionServiceXml '<!DOCTYPE service [<!ENTITY x SYSTEM "file:///never-read">]><service />' } 'DTD/entity XML'
    Assert-Throws { Assert-ContributionServiceXml (New-SyntheticServiceXml '<onfailure action="restart" />') } 'WinSW lifecycle hook'
    Assert-Throws { Assert-ContributionServiceXml (New-SyntheticServiceXml '<startarguments>--service</startarguments>') } 'extra start arguments'
    Assert-Throws { Assert-ContributionServiceXml (New-SyntheticServiceXml '<env name="EXTRA" value="x" />') } 'unknown env name'
    Assert-Throws { Assert-ContributionServiceXml (New-SyntheticServiceXml '<env name="PYTHONPATH" value="other" />') } 'duplicate env name'
    Assert-Throws { Assert-ContributionServiceXml (New-SyntheticServiceXml '' 'Automatic') } 'automatic service startup'
    Assert-Throws { Assert-ContributionServiceXml (New-SyntheticServiceXml '' 'Manual' 'unexpected="value"') } 'extra root attribute'
    Assert-Throws { Assert-ContributionServiceXml (New-SyntheticServiceXml '<stoparguments>stop</stoparguments>') } 'stop arguments'
    $badExecutableAttribute = New-SyntheticServiceXml
    $badExecutableAttribute.SelectSingleNode('/service/executable').SetAttribute('extra', 'value')
    Assert-Throws { Assert-ContributionServiceXml $badExecutableAttribute } 'extra executable attribute'
    $badEnvironmentAttribute = New-SyntheticServiceXml
    $badEnvironmentAttribute.SelectSingleNode('/service/env').SetAttribute('extra', 'value')
    Assert-Throws { Assert-ContributionServiceXml $badEnvironmentAttribute } 'extra environment attribute'
    $badEnvironmentText = New-SyntheticServiceXml
    $badEnvironmentText.SelectSingleNode('/service/env').AppendChild($badEnvironmentText.CreateTextNode('ignored-value')) | Out-Null
    Assert-Throws { Assert-ContributionServiceXml $badEnvironmentText } 'environment text content'
    $largeXml = Join-Path $root 'oversized-service.xml'
    $largeStream = [System.IO.File]::Open($largeXml, [System.IO.FileMode]::CreateNew)
    try { $largeStream.SetLength(1048577) } finally { $largeStream.Dispose() }
    try {
        $null = Read-ContributionServiceXmlFile $largeXml
        throw 'Oversized XML should be rejected before reading/parsing.'
    } catch {
        if ($_.Exception.Message -notmatch '1 MiB') { throw }
    }

    $null = Assert-ContributionDirectoryTarget $app 'existing AppRoot'
    $missingDirectory = Join-Path $root 'data-new'
    if ((Assert-ContributionDirectoryTarget $missingDirectory 'new data directory') -ne [System.IO.Path]::GetFullPath($missingDirectory)) {
        throw 'Valid new data directory path changed.'
    }
    $existingFile = Join-Path $root 'existing-file'
    Set-Content -LiteralPath $existingFile -Value 'synthetic' -Encoding ASCII
    Assert-Throws { Assert-ContributionDirectoryTarget $existingFile 'DataDirectory' } 'data path already a file'
    Assert-Throws { Assert-ContributionDirectoryTarget (Join-Path $existingFile 'child') 'LogDirectory' } 'file in directory path'
    $deploySource = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\deploy\configure_contribution_windows.ps1') -Raw
    foreach ($requiredCall in @('contribution_security_helpers.ps1', 'Assert-ContributionServiceArguments',
                               'Assert-ContributionWaitressExecutable', 'Assert-ContributionPathHasNoReparsePoints',
                               'Assert-ContributionServiceXml', 'Read-ContributionServiceXmlFile',
                               'Assert-ContributionDirectoryTarget',
                               'Assert-ContributionPathAcl -Path $databaseFile')) {
        if (-not $deploySource.Contains($requiredCall)) { throw "Deployment guard is not wired: $requiredCall" }
    }
    $servicePathCheck = $deploySource.IndexOf('Assert-ContributionServicePathName ([string]$service.PathName) $winswPath')
    $disableStart = $deploySource.IndexOf('& sc.exe config $serviceName start= disabled')
    $startModeVerify = $deploySource.IndexOf("if (`$service.StartMode -ne 'Disabled')")
    $stopCall = $deploySource.IndexOf('Stop-Service -Name $serviceName')
    if ($servicePathCheck -lt 0 -or $disableStart -lt 0 -or $startModeVerify -lt 0 -or $stopCall -lt 0 -or
        $servicePathCheck -gt $disableStart -or $disableStart -gt $startModeVerify -or $startModeVerify -gt $stopCall) {
        throw 'Existing service PathName and Disabled startup mode must be verified before stopping it.'
    }
    $directoryCheck = $deploySource.IndexOf('Assert-ContributionDirectoryTarget $dataPath')
    $serviceQuery = $deploySource.IndexOf('Get-CimInstance Win32_Service')
    if ($directoryCheck -lt 0 -or $serviceQuery -lt 0 -or $directoryCheck -gt $serviceQuery) {
        throw 'Existing directory target types must be checked before service install/query.'
    }
    $readerHelpers = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\deploy\contribution_security_helpers.ps1') -Raw
    $sizeCheck = $readerHelpers.IndexOf('$file.Length -gt 1048576')
    $xmlRead = $readerHelpers.IndexOf('Get-Content -LiteralPath $Path -Raw -Encoding UTF8')
    if ($sizeCheck -lt 0 -or $xmlRead -lt 0 -or $sizeCheck -gt $xmlRead) {
        throw 'WinSW XML size must be checked before reading the file contents.'
    }

    $admin = 'S-1-5-32-544'; $system = 'S-1-5-18'; $service = 'S-1-5-80-111'; $purge = 'S-1-5-80-222'
    $inherit = [int]([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
                     [System.Security.AccessControl.InheritanceFlags]::ObjectInherit)
    $snapshot = @{
        owner = $admin; protected = $true; isDirectory = $true
        rules = @(
            @{ sid=$service; type='Allow'; rights=0x301BF; inherited=$false; inheritanceFlags=$inherit; propagationFlags=0 },
            @{ sid=$system; type='Allow'; rights=0x1F01FF; inherited=$false; inheritanceFlags=$inherit; propagationFlags=0 },
            @{ sid=$admin; type='Allow'; rights=0x1F01FF; inherited=$false; inheritanceFlags=$inherit; propagationFlags=0 }
        )
    }
    $expected = @{$service='Modify';$system='FullControl';$admin='FullControl'}
    Assert-ContributionAclSnapshot -Snapshot $snapshot -ExpectedOwner $admin -ExpectedRules $expected -IsDirectory $true

    $badOwner = $snapshot.Clone(); $badOwner.owner = $service
    Assert-Throws { Assert-ContributionAclSnapshot -Snapshot $badOwner -ExpectedOwner $admin -ExpectedRules $expected -IsDirectory $true } 'unexpected ACL owner'
    $badPrincipal = $snapshot.Clone(); $badPrincipal.rules += @{sid='S-1-1-0';type='Allow';rights=0x1F01FF;inherited=$false;inheritanceFlags=$inherit;propagationFlags=0}
    Assert-Throws { Assert-ContributionAclSnapshot -Snapshot $badPrincipal -ExpectedOwner $admin -ExpectedRules $expected -IsDirectory $true } 'broad ACL principal'
    $inherited = $snapshot.Clone(); $inherited.rules[0].inherited = $true
    Assert-Throws { Assert-ContributionAclSnapshot -Snapshot $inherited -ExpectedOwner $admin -ExpectedRules $expected -IsDirectory $true } 'inherited ACL rule'
    Write-Output 'Synthetic contribution deploy validation passed.'
} finally {
    if ($substDrive) {
        $substExe = Join-Path $env:SystemRoot 'System32\subst.exe'
        & $substExe $substDrive /D
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Synthetic SUBST drive cleanup failed; remove it manually.' }
    }
    Remove-Item -LiteralPath $root -Recurse -Force
}
