# CadBridge: capture AutoCAD user-profile security/behaviour baseline (read-only).
# Used as the A01 "before/after" comparison source. Never writes to AutoCAD settings.
[CmdletBinding()]
param([string]$OutFile = "")

$ErrorActionPreference = 'Continue'

$valuesOfInterest = @('SECURELOAD','TRUSTEDPATHS','LISPSYS','LOADCTRLS','ACADLSPASDOC','STARTUP','DEMANDLOAD','TRUSTEDDOMAINS','AUTOLOAD')

$rows = @()
$root = 'HKCU:\Software\Autodesk\AutoCAD'
if (Test-Path $root) {
  $vers = Get-ChildItem $root -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^R\d' }
  foreach ($v in $vers) {
    $prods = Get-ChildItem $v.PSPath -ErrorAction SilentlyContinue
    foreach ($p in $prods) {
      $profRootKey = Join-Path $p.PSPath 'Profiles'
      if (-not (Test-Path $profRootKey)) { continue }
      foreach ($prof in (Get-ChildItem $profRootKey -ErrorAction SilentlyContinue)) {
        $varKey = Join-Path $prof.PSPath 'Variables'
        if (-not (Test-Path $varKey)) { continue }
        $props = Get-ItemProperty $varKey -ErrorAction SilentlyContinue
        $vals = @{}
        foreach ($n in $valuesOfInterest) {
          $pv = $props.PSObject.Properties[$n]
          if ($pv) { $vals[$n] = $pv.Value }
        }
        $rows += [PSCustomObject]@{
          release_key   = $v.PSChildName
          product_key   = $p.PSChildName
          profile       = $prof.PSChildName
          variables_key = $varKey
          values        = $vals
        }
      }
    }
  }
}

# Profile list only (cheap, avoids full Variables dump) for all releases.
$profileList = @()
if (Test-Path $root) {
  foreach ($v in (Get-ChildItem $root -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^R\d' })) {
    foreach ($p in (Get-ChildItem $v.PSPath -ErrorAction SilentlyContinue)) {
      $profRootKey = Join-Path $p.PSPath 'Profiles'
      if (-not (Test-Path $profRootKey)) { continue }
      foreach ($prof in (Get-ChildItem $profRootKey -ErrorAction SilentlyContinue)) {
        $profileList += "{0}\{1}\{2}" -f $v.PSChildName, $p.PSChildName, $prof.PSChildName
      }
    }
  }
}

# Machine/enterprise policy that can block NETLOAD (observation only; NOT modified).
$secureLoadPolicy = @()
foreach ($k in @('HKLM:\SOFTWARE\Autodesk\AutoCAD','HKLM:\SOFTWARE\Policies\Autodesk','HKCU:\SOFTWARE\Policies\Autodesk')) {
  if (Test-Path $k) {
    $secureLoadPolicy += ($k + ' exists')
  }
}

$result = [PSCustomObject]@{
  captured_at_utc      = (Get-Date).ToUniversalTime().ToString('o')
  purpose              = 'A01/A02 pre-change baseline. Read-only snapshot of AutoCAD user profile variables and profile inventory.'
  note                 = 'CadBridge did not modify any of these values. Profiles named Codex* were created by earlier agent sessions and are listed, not evaluated.'
  variables_observed   = $valuesOfInterest
  profile_count        = $profileList.Count
  profiles             = $profileList
  profiles_with_values = $rows
}

$json = $result | ConvertTo-Json -Depth 8
if ($OutFile) {
  [System.IO.File]::WriteAllText($OutFile, $json, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "WROTE $OutFile"
} else { $json }
