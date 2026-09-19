# CadBridge P0 inventory: enumerate installed AutoCAD hosts (read-only).
$results = @()
$roots = @('D:\Program Files\Autodesk', 'C:\Program Files\Autodesk', 'D:\Program Files (x86)\Autodesk', 'C:\Program Files (x86)\Autodesk')
foreach ($root in $roots) {
  if (-not (Test-Path $root)) { continue }
  foreach ($d in (Get-ChildItem $root -Directory -ErrorAction SilentlyContinue)) {
    $exe = Join-Path $d.FullName 'acad.exe'
    if (-not (Test-Path $exe)) {
      # maybe nested one level
      $nested = Get-ChildItem $d.FullName -Directory -ErrorAction SilentlyContinue
      foreach ($n in $nested) {
        $exe2 = Join-Path $n.FullName 'acad.exe'
        if (Test-Path $exe2) { $exe = $exe2; break }
      }
    }
    if (-not (Test-Path $exe)) { continue }
    $base = Split-Path $exe -Parent
    $vi = (Get-Item $exe).VersionInfo
    $results += [PSCustomObject]@{
      Dir        = $base
      Product    = $vi.ProductVersion
      FileVer    = $vi.FileVersion
      Adapter    = (Test-Path (Join-Path $base 'AutoLispDebugAdapter.exe'))
      acmgd      = (Test-Path (Join-Path $base 'acmgd.dll'))
      acdbmgd    = (Test-Path (Join-Path $base 'acdbmgd.dll'))
      accoremgd  = (Test-Path (Join-Path $base 'accoremgd.dll'))
      AdWindows   = (Test-Path (Join-Path $base 'AdWindows.dll'))
      CoreConsole = (Test-Path (Join-Path $base 'accoreconsole.exe'))
    }
  }
}
$results | Sort-Object Dir | Format-Table -AutoSize | Out-String -Width 400
"COUNT=$($results.Count)"
