# CadBridge P0 read-only inventory of AutoCAD hosts.
# Emits machine-readable JSON. Never starts, attaches to, or closes any CAD process.
[CmdletBinding()]
param(
  [string]$JsonOut = "",
  [int]$MaxDepth = 3
)

$ErrorActionPreference = 'Stop'
$roots = @(
  'C:\Program Files\Autodesk',
  'C:\Program Files (x86)\Autodesk',
  'D:\Program Files\Autodesk',
  'D:\Program Files (x86)\Autodesk'
)

function Get-RelDepth([string]$base, [string]$child) {
  $b = $base.TrimEnd('\').Split('\').Count
  $c = $child.TrimEnd('\').Split('\').Count
  return ($c - $b)
}

$found = @{}
foreach ($root in $roots) {
  if (-not (Test-Path -LiteralPath $root)) { continue }
  # recursive search so nested layouts (e.g. AutoCAD_2022.1.5\AutoCAD 2022) are not missed
  $hits = Get-ChildItem -LiteralPath $root -Filter 'acad.exe' -File -Recurse -Depth $MaxDepth -ErrorAction SilentlyContinue
  foreach ($h in $hits) {
    $base = $h.Directory.FullName
    if ($found.ContainsKey($base)) { continue }
    $found[$base] = $h
  }
}

$rows = foreach ($base in ($found.Keys | Sort-Object)) {
  $exe = $found[$base]
  $vi  = $exe.VersionInfo
  $p = { param($n) Test-Path -LiteralPath (Join-Path $base $n) }

  # A directory is a nested copy (not a standalone installation) when another discovered
  # acad.exe directory is an ancestor of it. Nested copies are recorded but never treated
  # as separate test targets.
  $ancestor = $null
  foreach ($other in $found.Keys) {
    if ($other -eq $base) { continue }
    if ($base.StartsWith($other.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
      if ((-not $ancestor) -or ($other.Length -gt $ancestor.Length)) { $ancestor = $other }
    }
  }

  # Provenance is recorded as an observation, not as a conclusion.
  $prov = 'unverified'
  $provenanceArtifacts = @()
  $lower = $base.ToLowerInvariant()
  foreach ($marker in @('crack', 'cracked', 'keygen', 'patch', 'loader')) {
    if ($lower -match [regex]::Escape($marker)) { $provenanceArtifacts += "self:$marker" }
  }
  # Also flag hosts whose install tree contains third-party patch material in subfolders.
  foreach ($child in (Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue)) {
    $cl = $child.Name.ToLowerInvariant()
    foreach ($marker in @('crack', 'cracked', 'keygen')) {
      if ($cl -match [regex]::Escape($marker)) { $provenanceArtifacts += "child:$($child.Name)" }
    }
  }
  if ($provenanceArtifacts.Count -gt 0) { $prov = 'observed-third-party-patch-material' }

  # Detect package-manager / "lite" / "green" distribution rather than a vendor installer.
  # Marker files observed next to this kind of install: AutoCAD-PackageManager.ps1,
  # TUP_portable.reg, manage.py, 安装_*.cmd, 1.bat, a '使用说明' folder, and .rar/.7z archives.
  $pkgMarkers = @()
  $parents = @($base, (Split-Path $base -Parent)) | Select-Object -Unique
  foreach ($dir in $parents) {
    if (-not (Test-Path -LiteralPath $dir)) { continue }
    foreach ($pat in @('AutoCAD-PackageManager*.ps1','TUP_portable.reg','manage.py','*.ps1.bak-*','安装_*.cmd','1.bat','2.txt')) {
      foreach ($m in (Get-ChildItem -LiteralPath $dir -Filter $pat -ErrorAction SilentlyContinue)) {
        $pkgMarkers += $m.Name
      }
    }
    foreach ($sub in @('使用说明','autocad-package-manager-py','incident-evidence-*')) {
      if (Test-Path -LiteralPath (Join-Path $dir $sub)) { $pkgMarkers += $sub }
    }
  }
  $pkgMarkers = @($pkgMarkers | Select-Object -Unique)
  $installKind = if ($pkgMarkers.Count -gt 0) { 'package-managed-third-party' } else { 'unknown-vendor-or-manual' }

  [PSCustomObject]@{
    install_dir            = $base
    install_root           = if ($ancestor) { $ancestor } else { $base }
    is_nested_copy         = [bool]$ancestor
    nested_under           = $ancestor
    install_kind           = $installKind
    install_kind_markers   = $pkgMarkers
    license_provenance     = $prov
    license_artifacts      = $provenanceArtifacts
    license_notes          = 'Installation provenance and licensing NOT verified by CadBridge. ' +
                             'Third-party patch or package-managed distribution means this host must not be used as a release-evidence target until licensing is confirmed by the project owner.'
    acad_exe               = $exe.FullName
    acad_exe_sha256        = (Get-FileHash -LiteralPath $exe.FullName -Algorithm SHA256).Hash
    product_version        = $vi.ProductVersion
    file_version           = $vi.FileVersion
    product_name           = $vi.ProductName
    file_description       = $vi.FileDescription
    # Modern runtime detection: presence of acdbmgd.runtimeconfig.json => .NET (Core) host
    is_dotnet_core_host    = (& $p 'acdbmgd.runtimeconfig.json')
    runtimeconfig          = if (& $p 'acdbmgd.runtimeconfig.json') {
                               (Get-Content -LiteralPath (Join-Path $base 'acdbmgd.runtimeconfig.json') -Raw | ConvertFrom-Json)
                             } else { $null }
    has_autolisp_adapter   = (& $p 'AutoLispDebugAdapter.exe')
    autolisp_adapter       = if (& $p 'AutoLispDebugAdapter.exe') {
                               $a = Get-Item -LiteralPath (Join-Path $base 'AutoLispDebugAdapter.exe')
                               [PSCustomObject]@{
                                 path = $a.FullName
                                 size = $a.Length
                                 file_version = $a.VersionInfo.FileVersion
                                 sha256 = (Get-FileHash -LiteralPath $a.FullName -Algorithm SHA256).Hash
                               }
                             } else { $null }
    assemblies = @{}
    core_console           = (& $p 'accoreconsole.exe')
  }
}

foreach ($r in $rows) {
  $asm = @{}
  foreach ($n in @('acmgd.dll','acdbmgd.dll','accoremgd.dll','AdWindows.dll','AcCui.dll','acdbmgdbrep.dll')) {
    $fp = Join-Path $r.install_dir $n
    if (Test-Path -LiteralPath $fp) {
      $item = Get-Item -LiteralPath $fp
      $ver = 'UNREADABLE'
      try { $ver = ([System.Reflection.AssemblyName]::GetAssemblyName($fp)).Version.ToString() } catch { }
      $asm[$n] = [PSCustomObject]@{
        path = $fp
        size = $item.Length
        asm_version = $ver
        file_version = $item.VersionInfo.FileVersion
        sha256 = (Get-FileHash -LiteralPath $fp -Algorithm SHA256).Hash
      }
    }
  }
  $r.assemblies = $asm
}

$result = [PSCustomObject]@{
  scan_utc        = (Get-Date).ToUniversalTime().ToString('o')
  host_name       = $env:COMPUTERNAME
  os_version      = [System.Environment]::OSVersion.VersionString
  os_build        = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').CurrentBuild
  is_64bit_os     = [System.Environment]::Is64BitOperatingSystem
  dotnet_framework_release = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -ErrorAction SilentlyContinue).Release
  dotnet_framework_version = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -ErrorAction SilentlyContinue).Version
  search_roots    = $roots
  max_depth       = $MaxDepth
  install_count   = @($rows).Count
  install_count_standalone = @($rows | Where-Object { -not $_.is_nested_copy }).Count
  install_count_package_managed = @($rows | Where-Object { $_.install_kind -eq 'package-managed-third-party' }).Count
  installs        = @($rows | Sort-Object product_version)
}

if ($JsonOut) {
  # Write UTF-8 without BOM so downstream JSON parsers accept it directly.
  $json = $result | ConvertTo-Json -Depth 8
  [System.IO.File]::WriteAllText($JsonOut, $json, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "WROTE $JsonOut"
} else {
  $result | ConvertTo-Json -Depth 8
}
