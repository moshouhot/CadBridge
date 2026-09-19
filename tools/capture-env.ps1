# CadBridge P0: capture environment + document baseline (read-only).

[CmdletBinding()]
param([string]$OutDir = "docs/evidence/P0/current/raw")

$ErrorActionPreference = 'Continue'
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

function Write-Utf8NoBom([string]$Path, [string]$Text) {
  [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

# --- document hashes ---
$docRows = foreach ($f in @('PRD.md','DESIGN.md','IMPLEMENTATION_PLAN.md','ACCEPTANCE.md','技术架构生成提示词.md')) {
  if (Test-Path -LiteralPath $f) {
    $i = Get-Item -LiteralPath $f
    [PSCustomObject]@{
      file = $f
      bytes = $i.Length
      sha256 = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash
      modified_utc = $i.LastWriteTimeUtc.ToString('o')
    }
  }
}
Write-Utf8NoBom (Join-Path $OutDir 'documents.json') ($docRows | ConvertTo-Json -Depth 4)

# --- installed dotnet SDKs / runtimes ---
$dotnet = & dotnet --info 2>&1 | Out-String
Write-Utf8NoBom (Join-Path $OutDir 'dotnet-info.txt') $dotnet

# --- .NET Framework ---
$ndp = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -ErrorAction SilentlyContinue
Write-Utf8NoBom (Join-Path $OutDir 'dotnet-framework.json') (@{
  release = $ndp.Release; version = $ndp.Version; sp = $ndp.SP
} | ConvertTo-Json)

# --- OS ---
Write-Utf8NoBom (Join-Path $OutDir 'os.json') (@{
  os_version   = [System.Environment]::OSVersion.VersionString
  is_64bit     = [System.Environment]::Is64BitOperatingSystem
  ps_version   = $PSVersionTable.PSVersion.ToString()
  hostname     = $env:COMPUTERNAME
  current_build = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').CurrentBuild
  ubr           = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').UBR
} | ConvertTo-Json)

# --- running CAD processes (observation only; never touched) ---
$procs = Get-Process -Name acad,accoreconsole -ErrorAction SilentlyContinue | ForEach-Object {
  [PSCustomObject]@{
    name = $_.ProcessName
    pid = $_.Id
    path = $_.Path
    start_time = if ($_.StartTime) { $_.StartTime.ToUniversalTime().ToString('o') } else { $null }
    main_window_handle = $_.MainWindowHandle
    main_window_title = $_.MainWindowTitle
  }
}
Write-Utf8NoBom (Join-Path $OutDir 'running-cad-processes.json') ($procs | ConvertTo-Json -Depth 4)

# --- ObjectARX local SDK roots ---
$sdk = if (Test-Path 'D:\CAD APPLOAD\ObjectARX') {
  Get-ChildItem 'D:\CAD APPLOAD\ObjectARX' -Directory | Select-Object -ExpandProperty Name
} else { @() }
Write-Utf8NoBom (Join-Path $OutDir 'objectarx-sdk-roots.json') (@{ root = 'D:\CAD APPLOAD\ObjectARX'; versions = @($sdk) } | ConvertTo-Json)

# --- ACL observation on the AutoCAD_2024.1.5 tree (does the host-loading account have write access?) ---
$aclTarget = 'D:\Program Files\Autodesk\AutoCAD_2024.1.5\AutoCAD 2024'
$aclOut = [PSCustomObject]@{
  path = $aclTarget
  exists = (Test-Path -LiteralPath $aclTarget)
  can_write_test_file = $null
  error = $null
}
if ($aclOut.exists) {
  try {
    # Probe writability WITHOUT creating anything: use GetAccessControl + an explicit access check.
    $acl = Get-Acl -LiteralPath $aclTarget
    $aclOut | Add-Member -NotePropertyName owner -NotePropertyValue $acl.Owner
    $aclOut | Add-Member -NotePropertyName access_rules -NotePropertyValue (
      $acl.Access | ForEach-Object { "{0}:{1}:{2}" -f $_.IdentityReference, $_.AccessControlType, $_.FileSystemRights })
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object System.Security.Principal.WindowsPrincipal($id)
    $aclOut | Add-Member -NotePropertyName current_user -NotePropertyValue $id.Name
    $aclOut | Add-Member -NotePropertyName is_admin -NotePropertyValue $pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
  } catch {
    $aclOut.error = $_.Exception.Message
  }
}
Write-Utf8NoBom (Join-Path $OutDir 'acl-2024-host.json') ($aclOut | ConvertTo-Json -Depth 5)

Write-Host "P0 environment capture complete -> $OutDir"
Get-ChildItem $OutDir | Select-Object Name, Length | Format-Table -AutoSize
