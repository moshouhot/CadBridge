# CadBridge: diagnose reference-assembly path resolution across candidate hosts.
Write-Output ("PS bitness 64-bit: " + [System.Environment]::Is64BitProcess)
Write-Output ("OS 64-bit: " + [System.Environment]::Is64BitOperatingSystem)
Write-Output ""

function Probe([string]$dir) {
    $dll = Join-Path $dir 'acdbmgd.dll'
    $exe = Join-Path $dir 'acad.exe'
    $dirExists = Test-Path -LiteralPath $dir
    $dllExists = Test-Path -LiteralPath $dll
    Write-Output ("dir={0,-6} dll={1,-6} | {2}" -f $dirExists, $dllExists, $dir)
    if ($dllExists) {
        try {
            $vi = (Get-Item -LiteralPath $dll).VersionInfo
            $asm = ([System.Reflection.AssemblyName]::GetAssemblyName($dll)).Version.ToString()
            Write-Output ("     asmver={0} filever={1}" -f $asm, $vi.FileVersion)
        } catch {
            Write-Output ("     ERROR reading assembly: " + $_.Exception.Message)
        }
    }
}

$candidates = @(
    'D:\Program Files\Autodesk\AutoCAD_2024.1.9\AutoCAD 2024',
    'D:\Program Files\Autodesk\AutoCAD_2024.1.7\AutoCAD 2024',
    'D:\Program Files\Autodesk\AutoCAD 2024.1.9\AutoCAD 2024',
    'D:\Program Files\Autodesk\AutoCAD 2023.1.5\AutoCAD 2023',
    'D:\Program Files\Autodesk\AutoCAD 2022.1.5\AutoCAD 2022',
    'D:\Program Files\Autodesk\AutoCAD 2020',
    'D:\Program Files\Autodesk\AutoCAD 2017',
    'D:\Program Files\Autodesk\AutoCAD 2016',
    'D:\CAD APPLOAD\ObjectARX\ObjectARX-2021\inc',
    'D:\CAD APPLOAD\ObjectARX\ObjectARX-2025\inc'
)
foreach ($c in $candidates) { Probe $c }

Write-Output ""
Write-Output "--- enumerate AutoCAD_2024.1.9 via .NET (bypasses PowerShell provider) ---"
try {
    $inner = [System.IO.Directory]::GetDirectories('D:\Program Files\Autodesk\AutoCAD_2024.1.9')
    foreach ($d in $inner) { Write-Output ("  dir: " + $d) }
    $files = [System.IO.Directory]::GetFiles('D:\Program Files\Autodesk\AutoCAD_2024.1.9')
    foreach ($f in $files) { Write-Output ("  file: " + $f) }
} catch {
    Write-Output ("  ERROR: " + $_.Exception.GetType().Name + ": " + $_.Exception.Message)
}

Write-Output ""
Write-Output "--- .NET probe of the specific dll path ---"
$p = 'D:\Program Files\Autodesk\AutoCAD_2024.1.9\AutoCAD 2024\acdbmgd.dll'
Write-Output ("File.Exists: " + [System.IO.File]::Exists($p))
try {
    $fi = New-Object System.IO.FileInfo($p)
    Write-Output ("FileInfo exists: " + $fi.Exists + "  length: " + $fi.Length)
} catch {
    Write-Output ("FileInfo ERROR: " + $_.Exception.Message)
}
