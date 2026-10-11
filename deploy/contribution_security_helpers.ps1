function Assert-ContributionWaitressExecutable([string]$Executable, [string]$AppRoot) {
    if (-not [System.IO.Path]::IsPathRooted($Executable)) {
        throw 'Waitress executable mutlak yol olmalı.'
    }
    $root = [System.IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
    $full = [System.IO.Path]::GetFullPath($Executable)
    if (-not $full.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Waitress executable, korunan AppRoot içinde olmalı.'
    }
    if ([System.IO.Path]::GetFileName($full) -ine 'waitress-serve.exe' -or
        -not (Test-Path -LiteralPath $full -PathType Leaf)) {
        throw 'AppRoot içinde waitress-serve.exe gerekli.'
    }
    $cursor = $full
    while ($cursor -and $cursor.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Waitress executable yolu symlink/junction/reparse point içeremez.'
            }
        }
        $parent = Split-Path -Path $cursor -Parent
        if (-not $parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $full
}

function Assert-ContributionServiceArguments([string]$Arguments) {
    $approved = '--listen=127.0.0.1:8787 contribution_api:app'
    if (-not [String]::Equals($Arguments, $approved, [StringComparison]::Ordinal)) {
        throw 'Waitress argümanları yalnızca onaylı loopback komutuyla birebir eşleşmeli.'
    }
}

function Assert-ContributionPathHasNoReparsePoints([string]$Path) {
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        throw 'Korunan AppRoot mutlak yol olmalı.'
    }
    $full = [System.IO.Path]::GetFullPath($Path)
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Korunan AppRoot yolu symlink/junction/reparse point içeremez.'
            }
        }
        $parent = Split-Path -Path $cursor -Parent
        if (-not $parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $full
}

function Assert-ContributionTreeHasNoReparsePoints([string]$Root) {
    $rootPath = Assert-ContributionPathHasNoReparsePoints $Root
    if (-not (Test-Path -LiteralPath $rootPath -PathType Container)) {
        throw 'Recursive ACL root must be an existing directory.'
    }
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($rootPath)
    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Recursive ACL tree contains a symlink/junction/reparse point.'
            }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
}

function Assert-ContributionDirectoryTarget([string]$Path, [string]$Label) {
    if (-not [System.IO.Path]::IsPathRooted($Path)) { throw "$Label must be an absolute directory path." }
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $existing = Get-Item -LiteralPath $full -Force -ErrorAction SilentlyContinue
    if ($existing -and -not $existing.PSIsContainer) {
        throw "$Label exists as a file; an existing directory is required or the path must be unused."
    }
    $cursor = Split-Path -Path $full -Parent
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $parentItem = Get-Item -LiteralPath $cursor -Force
            if (-not $parentItem.PSIsContainer) { throw "$Label has an existing file in its parent path." }
            break
        }
        $parent = Split-Path -Path $cursor -Parent
        if (-not $parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $full
}

function Get-ContributionCanonicalDirectoryPath([string]$Path) {
    if (-not [System.IO.Path]::IsPathRooted($Path)) { throw 'Physical path comparison requires absolute paths.' }
    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full.StartsWith('\\')) { throw 'UNC paths are not accepted for contribution data/AppRoot separation.' }
    foreach ($component in ($full -split '[\\/]')) {
        if ($component -match '~[0-9]+(?:\.|$)') {
            throw 'Short-name (8.3) path components are not accepted.'
        }
    }
    Assert-ContributionPathHasNoReparsePoints $full | Out-Null
    $missing = New-Object 'System.Collections.Generic.Stack[string]'
    $existing = $full
    while (-not (Test-Path -LiteralPath $existing -PathType Container)) {
        if (Test-Path -LiteralPath $existing) { throw 'Canonical directory path has a file component.' }
        $leaf = Split-Path -Path $existing -Leaf
        if (-not $leaf) { throw 'Canonical directory path has no existing directory prefix.' }
        $missing.Push($leaf)
        $parent = Split-Path -Path $existing -Parent
        if (-not $parent -or $parent -eq $existing) { throw 'No existing directory prefix for canonical path.' }
        $existing = $parent
    }
    $physical = [HardsubContributionFinalPath]::ResolveDirectory($existing)
    while ($missing.Count -gt 0) {
        $physical = $physical.TrimEnd('\') + '\' + $missing.Pop()
    }
    return $physical.TrimEnd('\')
}

function Test-ContributionPathOverlap([string]$Left, [string]$Right) {
    $a = $Left.TrimEnd('\')
    $b = $Right.TrimEnd('\')
    return ($a.Equals($b, [StringComparison]::OrdinalIgnoreCase) -or
        $a.StartsWith($b + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $b.StartsWith($a + '\', [StringComparison]::OrdinalIgnoreCase))
}

if (-not ('HardsubContributionFinalPath' -as [type])) {
    $nativePathSource = @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class HardsubContributionFinalPath {
    private const uint FILE_SHARE_READ = 0x1;
    private const uint FILE_SHARE_WRITE = 0x2;
    private const uint FILE_SHARE_DELETE = 0x4;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
    private const uint VOLUME_NAME_GUID = 0x1;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "CreateFileW")]
    private static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr security,
        uint creation, uint flags, IntPtr template);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "GetFinalPathNameByHandleW")]
    private static extern uint GetFinalPathNameByHandle(SafeFileHandle handle, StringBuilder path,
        uint length, uint flags);

    public static string ResolveDirectory(string path) {
        using (SafeFileHandle handle = CreateFile(path, 0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, IntPtr.Zero,
            OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, IntPtr.Zero)) {
            if (handle == null || handle.IsInvalid)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot open existing directory for canonicalization.");
            uint capacity = 512;
            while (true) {
                StringBuilder result = new StringBuilder((int)capacity);
                uint length = GetFinalPathNameByHandle(handle, result, capacity, VOLUME_NAME_GUID);
                if (length == 0)
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "GetFinalPathNameByHandle failed.");
                if (length < capacity)
                    return result.ToString();
                capacity = length + 1;
            }
        }
    }
}
'@
    Add-Type -TypeDefinition $nativePathSource -Language CSharp
}

function Assert-ContributionServicePathName([string]$PathName, [string]$ExpectedExecutable) {
    if ([string]::IsNullOrWhiteSpace($PathName) -or [string]::IsNullOrWhiteSpace($ExpectedExecutable)) {
        throw 'Existing service and expected WinSW executable paths are required.'
    }
    $raw = $PathName.Trim()
    if ($raw.StartsWith('"')) {
        $closingQuote = $raw.IndexOf('"', 1)
        if ($closingQuote -le 1 -or -not [string]::IsNullOrWhiteSpace($raw.Substring($closingQuote + 1))) {
            throw 'Existing service PathName is ambiguous or has extra service arguments.'
        }
        $actualExecutable = $raw.Substring(1, $closingQuote - 1)
    } else {
        if ($raw.Contains(' ')) { throw 'Unquoted existing service PathName with spaces is ambiguous.' }
        $actualExecutable = $raw
    }
    if (-not [System.IO.Path]::IsPathRooted($actualExecutable)) {
        throw 'Existing service PathName must be absolute.'
    }
    $actualFull = [System.IO.Path]::GetFullPath($actualExecutable)
    $expectedFull = [System.IO.Path]::GetFullPath($ExpectedExecutable)
    if (-not [string]::Equals($actualFull, $expectedFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Existing Windows service belongs to a different executable; no changes applied.'
    }
}

function Assert-ContributionServiceXml([xml]$Document) {
    if ($null -eq $Document -or $null -eq $Document.DocumentElement) {
        throw 'WinSW XML document/root is missing.'
    }
    if ($Document.DocumentType -or $Document.DocumentElement.LocalName -cne 'service' -or
        $Document.DocumentElement.NamespaceURI -or $Document.DocumentElement.Attributes.Count -ne 0) {
        throw 'WinSW XML must have a plain service root without attributes or DTD.'
    }
    $scalarNames = @('id','name','description','startmode','executable','arguments','workingdirectory')
    $expectedEnvNames = @('PYTHONPATH','PYTHONDONTWRITEBYTECODE','H2S_CONTRIB_DB',
        'H2S_CONTRIB_RETENTION_DAYS','H2S_CONTRIB_SERVICE_ACCOUNT',
        'H2S_CONTRIB_PURGE_ACCOUNT','H2S_CONTRIB_LOG_DIR')
    $seen = @{}
    $envMap = @{}
    $logSeen = $false
    foreach ($node in $Document.DocumentElement.ChildNodes) {
        if ($node.NodeType -eq [System.Xml.XmlNodeType]::Comment -or
            $node.NodeType -eq [System.Xml.XmlNodeType]::Whitespace -or
            $node.NodeType -eq [System.Xml.XmlNodeType]::SignificantWhitespace) { continue }
        if ($node.NodeType -ne [System.Xml.XmlNodeType]::Element -or $node.NamespaceURI) {
            if (($node.NodeType -eq [System.Xml.XmlNodeType]::Text) -and [string]::IsNullOrWhiteSpace($node.Value)) { continue }
            throw 'WinSW XML contains an unsupported node.'
        }
        $name = $node.LocalName
        if ($name -in $scalarNames) {
            if ($seen.ContainsKey($name) -or $node.Attributes.Count -ne 0 -or
                @($node.ChildNodes | Where-Object { $_.NodeType -eq [System.Xml.XmlNodeType]::Element }).Count -ne 0) {
                throw "WinSW XML scalar '$name' is duplicated or has nested attributes/elements."
            }
            $seen[$name] = $node.InnerText
            continue
        }
        if ($name -ceq 'env') {
            if ($node.Attributes.Count -ne 2 -or @($node.Attributes | Where-Object { $_.Name -notin @('name','value') }).Count -ne 0 -or
                @($node.ChildNodes | Where-Object { $_.NodeType -eq [System.Xml.XmlNodeType]::Element }).Count -ne 0 -or
                -not [string]::IsNullOrWhiteSpace($node.InnerText)) {
                throw 'WinSW env entries must contain only name/value attributes and no child elements.'
            }
            $envName = $node.GetAttribute('name')
            if ($envName -notin $expectedEnvNames -or $envMap.ContainsKey($envName)) {
                throw 'WinSW XML contains an unknown or duplicate environment name.'
            }
            $envMap[$envName] = $node.GetAttribute('value')
            continue
        }
        if ($name -ceq 'log') {
            if ($logSeen -or $node.Attributes.Count -ne 1 -or
                $node.Attributes[0].Name -cne 'mode' -or $node.GetAttribute('mode') -cne 'none' -or
                @($node.ChildNodes | Where-Object { $_.NodeType -eq [System.Xml.XmlNodeType]::Element }).Count -ne 0 -or
                -not [string]::IsNullOrWhiteSpace($node.InnerText)) {
                throw 'WinSW log element must occur once with only mode=none and no children.'
            }
            $logSeen = $true
            continue
        }
        throw "Unsupported WinSW XML element: $name"
    }
    foreach ($name in $scalarNames) {
        if (-not $seen.ContainsKey($name) -or [string]::IsNullOrWhiteSpace([string]$seen[$name])) {
            throw "Required WinSW XML element missing: $name"
        }
    }
    if ($seen['startmode'] -cne 'Manual' -or -not $logSeen -or $envMap.Count -ne $expectedEnvNames.Count) {
        throw 'WinSW startmode must be Manual, log must be disabled, and required env entries must be unique.'
    }
    foreach ($name in $expectedEnvNames) {
        if (-not $envMap.ContainsKey($name) -or [string]::IsNullOrWhiteSpace([string]$envMap[$name])) {
            throw "Required WinSW environment value missing: $name"
        }
    }
    return @{ id=$seen['id']; name=$seen['name']; description=$seen['description'];
              startmode=$seen['startmode']; executable=$seen['executable'];
              arguments=$seen['arguments']; workingdirectory=$seen['workingdirectory'];
              env=$envMap }
}

function Read-ContributionServiceXml([string]$XmlText) {
    $settings = New-Object System.Xml.XmlReaderSettings
    $settings.DtdProcessing = [System.Xml.DtdProcessing]::Prohibit
    $settings.XmlResolver = $null
    $settings.MaxCharactersInDocument = 1048576
    $settings.MaxCharactersFromEntities = 0
    $inputText = [System.IO.StringReader]::new($XmlText)
    $reader = [System.Xml.XmlReader]::Create($inputText, $settings)
    try {
        $document = New-Object System.Xml.XmlDocument
        $document.XmlResolver = $null
        $document.Load($reader)
        return $document
    } finally {
        $reader.Dispose()
        $inputText.Dispose()
    }
}

function Read-ContributionServiceXmlFile([string]$Path) {
    $file = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($file.PSIsContainer -or $file.Length -gt 1048576) {
        throw 'WinSW XML must be a regular file no larger than 1 MiB.'
    }
    $xmlText = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($xmlText -match '<!\s*(DOCTYPE|ENTITY)') {
        throw 'WinSW DTD/entity declarations are not permitted.'
    }
    return Read-ContributionServiceXml $xmlText
}

function Assert-ContributionAclSnapshot {
    param(
        [Parameter(Mandatory=$true)][hashtable]$Snapshot,
        [Parameter(Mandatory=$true)][string]$ExpectedOwner,
        [Parameter(Mandatory=$true)][hashtable]$ExpectedRules,
        [Parameter(Mandatory=$true)][bool]$IsDirectory
    )
    if ($Snapshot.owner -ne $ExpectedOwner -or $Snapshot.isDirectory -ne $IsDirectory -or
        $Snapshot.protected -ne $true -or -not ($Snapshot.rules -is [System.Collections.IEnumerable])) {
        throw 'Yol sahibi, tür veya ACL inheritance koruması beklenen değer değil.'
    }
    $access = @{}
    $expectedInherit = if ($IsDirectory) {
        [int]([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
             [System.Security.AccessControl.InheritanceFlags]::ObjectInherit)
    } else { [int][System.Security.AccessControl.InheritanceFlags]::None }
    foreach ($rule in $Snapshot.rules) {
        $sid = $rule.sid
        if (-not $ExpectedRules.ContainsKey($sid) -or $rule.type -ne 'Allow' -or
            $rule.inherited -ne $false -or [int]$rule.inheritanceFlags -ne $expectedInherit -or
            [int]$rule.propagationFlags -ne [int][System.Security.AccessControl.PropagationFlags]::None) {
            throw 'ACL içinde beklenmeyen principal, deny ACE veya inheritance kuralı var.'
        }
        $access[$sid] = [int]($access[$sid] -bor [int]$rule.rights)
    }
    if ($access.Count -ne $ExpectedRules.Count) { throw 'ACL principal kümesi eksik veya fazla.' }
    $sync = 0x100000
    $rightsMasks = @{
        Modify = @(0x301BF, (0x301BF -bor $sync))
        FullControl = @(0x1F01FF, (0x1F01FF -bor $sync))
        ReadAndExecute = @([int][System.Security.AccessControl.FileSystemRights]::ReadAndExecute,
            ([int][System.Security.AccessControl.FileSystemRights]::ReadAndExecute -bor $sync))
    }
    foreach ($sid in $ExpectedRules.Keys) {
        $role = $ExpectedRules[$sid]
        if (-not $rightsMasks.ContainsKey($role) -or $access[$sid] -notin $rightsMasks[$role]) {
            throw 'ACL principalinin yetkisi beklenen en düşük yetkiyle eşleşmiyor.'
        }
    }
}

function Assert-ContributionPathAcl {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$ExpectedOwner,
        [Parameter(Mandatory=$true)][hashtable]$ExpectedRules,
        [Parameter(Mandatory=$true)][bool]$IsDirectory
    )
    $item = Get-Item -LiteralPath $Path -Force
    if ([bool]$item.PSIsContainer -ne $IsDirectory) { throw 'ACL yolu türü beklenenden farklı.' }
    $acl = Get-Acl -LiteralPath $Path
    $sidType = [System.Security.Principal.SecurityIdentifier]
    $owner = ([System.Security.Principal.NTAccount]$acl.Owner).Translate($sidType).Value
    $rules = @()
    foreach ($rule in $acl.GetAccessRules($true, $false, $sidType)) {
        $rules += @{
            sid = $rule.IdentityReference.Value
            type = $rule.AccessControlType.ToString()
            rights = [int]$rule.FileSystemRights
            inherited = [bool]$rule.IsInherited
            inheritanceFlags = [int]$rule.InheritanceFlags
            propagationFlags = [int]$rule.PropagationFlags
        }
    }
    Assert-ContributionAclSnapshot -Snapshot @{
        owner = $owner; protected = [bool]$acl.AreAccessRulesProtected
        isDirectory = [bool]$item.PSIsContainer; rules = $rules
    } -ExpectedOwner $ExpectedOwner -ExpectedRules $ExpectedRules -IsDirectory $IsDirectory
}
