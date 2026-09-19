# CadBridge P0: collect managed reference assembly metadata + hashes (read-only).
param(
  [string]$OutFile = ""
)
$sdkRoot = 'D:\CAD APPLOAD\ObjectARX'
$rows = @()
foreach ($v in (Get-ChildItem $sdkRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name)) {
  $incRoots = @((Join-Path $v.FullName 'inc'), (Join-Path $v.FullName 'inc-x64'))
  foreach ($inc in $incRoots) {
    if (-not (Test-Path $inc)) { continue }
    foreach ($dll in (Get-ChildItem $inc -Filter '*.dll' -ErrorAction SilentlyContinue)) {
      try { $an = [System.Reflection.AssemblyName]::GetAssemblyName($dll.FullName) } catch { $an = $null }
      $vi = $dll.VersionInfo
      $rows += [PSCustomObject]@{
        Sdk        = $v.Name
        File       = $dll.Name
        AsmVersion = if ($an) { $an.Version.ToString() } else { 'NOT_MANAGED' }
        FileVersion= $vi.FileVersion
        Product    = $vi.ProductVersion
        Size       = $dll.Length
        Sha256     = (Get-FileHash $dll.FullName -Algorithm SHA256).Hash
        Path       = $dll.FullName
      }
    }
  }
}
if ($OutFile) {
  $json = $rows | ConvertTo-Json -Depth 4
  [System.IO.File]::WriteAllText($OutFile, $json, (New-Object System.Text.UTF8Encoding($false)))
}
$rows | Where-Object { $_.File -in @('AcCoreMgd.dll','AcDbMgd.dll','AcMgd.dll','AdWindows.dll','AcCui.dll','AcTcMgd.dll') } |
  Select-Object Sdk,File,AsmVersion,FileVersion,Size | Format-Table -AutoSize | Out-String -Width 250
